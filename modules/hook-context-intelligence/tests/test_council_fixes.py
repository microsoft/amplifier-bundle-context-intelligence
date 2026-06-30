"""Tests for Task A: WriteTimeout transient classification and 3xx redirect handling.

Bug A1: httpx.WriteTimeout was not caught in the transient except tuple, causing it to
propagate as an unclassified exception and DROP the event. Fix: catch httpx.TimeoutException
(base class of ConnectTimeout/ReadTimeout/WriteTimeout/PoolTimeout) so all timeout
variants are retried.

Bug A2: _classify_http_outcome returned _DELIVERED for any status < 400, silently treating
301/302/307/308 redirects as successful deliveries. Fix: 3xx -> _PERMANENT with a loud
redirect warning to help operators diagnose misconfigured URLs.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DELIVERED,  # noqa: F401 — imported for shared test use
    _PERMANENT,
    _TRANSIENT,
    _DestinationDispatcher,
    _classify_http_outcome,
    LoggingHandler,  # noqa: F401 — imported for shared test use
)

# Cross-cutting patch paths
MOD = "amplifier_module_hook_context_intelligence.handlers.logging_handler"
LOGGER_PATH = f"{MOD}.logger"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _dispatcher(**overrides: object) -> _DestinationDispatcher:
    """Create a _DestinationDispatcher with task-specific defaults."""
    defaults: dict[str, object] = dict(
        name="test-dest",
        url="http://localhost:8080",
        api_key="test-key",
        workspace="ws",
        dispatch_timeout=10.0,
        failure_threshold=3,
        queue_capacity=256,
        close_drain_timeout=2.0,
        backoff_initial=1.0,
        backoff_max=30.0,
        backoff_jitter=False,
    )
    defaults.update(overrides)
    return _DestinationDispatcher(**defaults)  # type: ignore[arg-type]


def _client_raising(exc: BaseException) -> AsyncMock:
    """AsyncMock httpx.AsyncClient that raises exc on .post()."""
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(side_effect=exc)
    return client


def _rendered(call: object) -> str:
    """Render a printf-style logger call args to a string."""
    args = call.args  # type: ignore[attr-defined]
    if not args:
        return ""
    if len(args) == 1:
        return str(args[0])
    try:
        return args[0] % args[1:]
    except TypeError:
        return str(args[0])


# ---------------------------------------------------------------------------
# Bug A1: WriteTimeout must return _TRANSIENT (not propagate)
# ---------------------------------------------------------------------------


async def test_write_timeout_is_transient_not_dropped() -> None:
    """httpx.WriteTimeout must return _TRANSIENT so the worker retries the event.

    Before the fix, WriteTimeout was not in the transient except tuple and would
    propagate out of _post as an unclassified exception, causing the event to be
    dropped by the supervisor (not retried).
    """
    d = _dispatcher()
    d._client = _client_raising(httpx.WriteTimeout("write timeout on upload"))

    result = await d._post("test:event", {"session_id": "s1"})

    assert result == _TRANSIENT


# ---------------------------------------------------------------------------
# Bug A2: 3xx redirects must be _PERMANENT (not _DELIVERED)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_code",
    [300, 301, 302, 307, 308],
    ids=["300", "301", "302", "307", "308"],
)
def test_redirect_is_permanent(status_code: int) -> None:
    """HTTP 3xx must return _PERMANENT (not _DELIVERED).

    Before the fix, status < 400 returned _DELIVERED. A misconfigured URL
    returning 301 was silently treated as a successful delivery, which could
    fire a false RECOVERY signal and leak auth tokens via redirect following.
    """
    result = _classify_http_outcome(status_code)

    assert result == _PERMANENT


async def test_redirect_logs_misconfig_and_skips() -> None:
    """When _post returns _PERMANENT with _last_status in [300,400), the worker must
    log a loud 'unexpected redirect' warning (not the generic 'malformed event' warning).

    The redirect warning informs operators that the destination URL is likely misconfigured
    (e.g. HTTP->HTTPS enforce, trailing-slash redirect, DNS alias) and that the event was
    skipped (not retried) to avoid leaking bearer tokens by following redirects.
    """
    d = _dispatcher()
    d._sleep_backoff = AsyncMock()

    async def fake_post(event: str, data: dict) -> str:  # type: ignore[type-arg]
        d._last_status = 302
        return _PERMANENT

    d._post = fake_post  # type: ignore[method-assign]

    with patch(LOGGER_PATH) as mock_logger:
        d._queue.put_nowait(("test:event", {"session_id": "s1"}))
        task = asyncio.create_task(d._worker())
        await d._queue.join()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    redirect_warnings = [
        c for c in mock_logger.warning.call_args_list if "unexpected redirect" in _rendered(c)
    ]
    assert len(redirect_warnings) == 1


# ---------------------------------------------------------------------------
# Bug B: queue_capacity must be clamped >= 1 inside _DestinationDispatcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_cap", [0, -1, -100], ids=["zero", "neg1", "neg100"])
def test_queue_capacity_clamped_in_constructor(bad_cap: int) -> None:
    """queue_capacity <= 0 must be clamped to 1 inside _DestinationDispatcher.__init__.

    asyncio.Queue(maxsize=0) is UNBOUNDED — any value <= 0 silently removes the
    memory guard. The clamp must be a class invariant, not a caller responsibility.
    """
    d = _dispatcher(queue_capacity=bad_cap)

    assert d._queue.maxsize == 1


# ---------------------------------------------------------------------------
# Bug C: persistent-401 escalation — reachable at failure_threshold=1, re-warns periodically
# ---------------------------------------------------------------------------


def _auth_then_deliver(d: _DestinationDispatcher, n_401: int) -> None:
    """Attach a fake _post to d: first n_401 calls return _TRANSIENT (401), then _DELIVERED."""
    calls: list[int] = [0]

    async def fake_post(event: str, data: dict) -> str:  # type: ignore[type-arg]
        if calls[0] < n_401:
            calls[0] += 1
            d._last_status = 401
            return _TRANSIENT
        return _DELIVERED

    d._post = fake_post  # type: ignore[method-assign]


async def test_auth_escalation_fires_at_threshold_one() -> None:
    """At failure_threshold=1 with one 401 followed by delivery, >= 1 'rejecting auth' warning.

    Regression for Bug C: the old code used strict == AND put the check in an elif after
    the one-time DEGRADED branch. At failure_threshold=1, the first 401 always took the
    'if not self._degraded_warned:' branch, making the elif unreachable and thus never
    emitting the auth escalation warning.
    """
    d = _dispatcher(failure_threshold=1)
    _auth_then_deliver(d, 1)
    d._sleep_backoff = AsyncMock()

    with patch(LOGGER_PATH) as mock_logger:
        d._queue.put_nowait(("test:event", {"session_id": "s1"}))
        task = asyncio.create_task(d._worker())
        await d._queue.join()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    auth_warnings = [
        c for c in mock_logger.warning.call_args_list if "rejecting auth" in _rendered(c)
    ]
    assert len(auth_warnings) >= 1, (
        f"Expected >= 1 'rejecting auth' warning at failure_threshold=1, "
        f"got {len(auth_warnings)}: {mock_logger.warning.call_args_list}"
    )


async def test_auth_escalation_rewarns_periodically() -> None:
    """With time.monotonic advancing 61s per call, 3x 401s emit >= 2 'rejecting auth' warnings.

    Regression for Bug C: the old code used strict == so the auth warning could fire at
    most once, ever. A rotated/dead key would retry forever with at most one warning —
    a silent multi-day outage. The fix uses >= and rate-limits with _LOG_RATE_LIMIT_SECONDS
    (60s), re-emitting periodically as long as 401s continue.
    """
    d = _dispatcher(failure_threshold=1)
    _auth_then_deliver(d, 3)
    d._sleep_backoff = AsyncMock()

    # Monotonic ticks advance by 61s per call — always >= _LOG_RATE_LIMIT_SECONDS (60s).
    ticks = iter(range(61, 100000, 61))

    with patch(f"{MOD}.time.monotonic", side_effect=ticks):
        with patch(LOGGER_PATH) as mock_logger:
            d._queue.put_nowait(("test:event", {"session_id": "s1"}))
            task = asyncio.create_task(d._worker())
            await d._queue.join()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    auth_warnings = [
        c for c in mock_logger.warning.call_args_list if "rejecting auth" in _rendered(c)
    ]
    assert len(auth_warnings) >= 2, (
        f"Expected >= 2 'rejecting auth' warnings with periodic re-warn, "
        f"got {len(auth_warnings)}: {mock_logger.warning.call_args_list}"
    )


# ---------------------------------------------------------------------------
# Bug D: printed recovery command must include --path flag
# ---------------------------------------------------------------------------


async def test_overflow_message_has_runnable_path() -> None:
    """Overflow warning must include --path flag for copy-paste correctness.

    Without the fix, the message prints 'context-intelligence-upload /data/sessions'
    (a bare path), which argparse rejects at the CLI. With the fix it prints
    'context-intelligence-upload --path /data/sessions'.
    """
    d = _dispatcher(queue_capacity=1, storage_path="/data/sessions")

    with patch(LOGGER_PATH) as mock_logger:
        # First enqueue fills the single-slot queue; second overflows it.
        d.enqueue("test:event", {"x": 1})
        d.enqueue("test:event", {"x": 2})  # triggers overflow — synchronous, no yield

    # Cleanup the worker task created by _ensure_worker().
    if d._worker_task is not None:
        d._worker_task.cancel()
        try:
            await d._worker_task
        except asyncio.CancelledError:
            pass

    overflow_warnings = [
        c for c in mock_logger.warning.call_args_list if "buffer full" in _rendered(c)
    ]
    assert len(overflow_warnings) >= 1, (
        f"Expected >= 1 overflow warning, got all warnings: {mock_logger.warning.call_args_list}"
    )
    last_msg = _rendered(overflow_warnings[-1])
    assert "--path /data/sessions" in last_msg, (
        f"Expected '--path /data/sessions' in overflow warning, got: {last_msg!r}"
    )
    assert "<path>" not in last_msg, (
        f"Expected no placeholder '<path>' in overflow warning, got: {last_msg!r}"
    )


async def test_shutdown_message_has_runnable_path() -> None:
    """Shutdown warning must include --path flag for copy-paste correctness.

    Without the fix, the message prints 'context-intelligence-upload /data/sessions'
    (a bare path), which argparse rejects at the CLI. With the fix it prints
    'context-intelligence-upload --path /data/sessions'.

    _post hangs via asyncio.sleep(10) after signalling that it has started, so
    the event stays in-flight when close() times out after 0.05 s, guaranteeing
    total > 0 and the shutdown WARNING is emitted.
    """
    started = asyncio.Event()

    async def hanging_post(event: str, data: dict) -> str:  # type: ignore[type-arg]
        started.set()
        await asyncio.sleep(10)
        return _DELIVERED  # unreachable; satisfies return type

    d = _dispatcher(
        storage_path="/data/sessions",
        close_drain_timeout=0.05,
        backoff_initial=0.001,
        backoff_max=0.001,
    )
    d._post = hanging_post  # type: ignore[method-assign]

    d.enqueue("test:event", {"x": 1})
    await started.wait()  # worker has dequeued the event and is stuck inside _post

    with patch(LOGGER_PATH) as mock_logger:
        await d.close()

    shutdown_warnings = [
        c for c in mock_logger.warning.call_args_list if "shutdown" in _rendered(c)
    ]
    assert len(shutdown_warnings) >= 1, (
        f"Expected >= 1 shutdown warning, got all warnings: {mock_logger.warning.call_args_list}"
    )
    last_msg = _rendered(shutdown_warnings[-1])
    assert "--path /data/sessions" in last_msg, (
        f"Expected '--path /data/sessions' in shutdown warning, got: {last_msg!r}"
    )
    assert "<path>" not in last_msg, (
        f"Expected no placeholder '<path>' in shutdown warning, got: {last_msg!r}"
    )
