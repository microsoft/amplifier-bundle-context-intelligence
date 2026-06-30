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
        c for c in mock_logger.warning.call_args_list
        if "unexpected redirect" in _rendered(c)
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
