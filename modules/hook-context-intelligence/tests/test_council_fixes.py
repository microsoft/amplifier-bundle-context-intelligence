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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _AUTH_GIVEUP_ATTEMPTS,
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


# ---------------------------------------------------------------------------
# Shared helper: minimal resolver for LoggingHandler fan-out tests
# ---------------------------------------------------------------------------


class _FakeResolver:
    """Minimal resolver duck-type for LoggingHandler constructor.

    Provides working_dir, workspace, and session_dir(session_id) so that
    LoggingHandler can be constructed without a real Amplifier resolver.
    """

    working_dir = "/wd"
    workspace = "ws"

    def __init__(self, base: Path) -> None:
        self._base = base

    def session_dir(self, session_id: str) -> Path:
        return self._base / session_id


# ---------------------------------------------------------------------------
# Bug E1+E2: fan-out loop must isolate dispatcher failures
# ---------------------------------------------------------------------------


async def test_fanout_isolates_dispatcher_failure(tmp_path: Path) -> None:
    """A failing dispatcher enqueue must not abort delivery to other dispatchers.

    Before the fix, an exception from one dispatcher's enqueue() propagated out
    of LoggingHandler.__call__, preventing subsequent dispatchers from receiving
    the event (starvation). The fan-out loop must wrap each enqueue() call in
    try/except so that one dispatcher's failure is logged and the loop continues.
    """
    handler = LoggingHandler(_FakeResolver(tmp_path))
    bad = MagicMock()
    bad.enqueue.side_effect = RuntimeError("boom")
    good = MagicMock()
    handler._dispatchers = [bad, good]

    # Must not raise — bad dispatcher failure must be isolated.
    await handler("session:start", {"session_id": "sess1", "workspace": "ws"})

    bad.enqueue.assert_called_once()
    good.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# Pin E3: closed-client RuntimeError must return _DELIVERED (teardown guard)
# ---------------------------------------------------------------------------


async def test_closed_client_runtimeerror_is_delivered() -> None:
    """RuntimeError('...closed...') from _post must return _DELIVERED.

    Pin test: the teardown guard at the RuntimeError catch in _post classifies a
    'client has been closed' error as _DELIVERED so events are not re-queued
    during session teardown. This pin ensures a future httpx message-format
    change breaks loudly in CI instead of silently reclassifying good events.

    Verification: temporarily changing 'closed' to 'CLOSED' at the guard check
    causes the RuntimeError to propagate (re-raise path), and this test FAILS.
    Revert confirms the guard is the only reason it passes.
    """
    d = _dispatcher()
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(
        side_effect=RuntimeError("Cannot send a request, as the client has been closed.")
    )
    d._client = client

    result = await d._post("test:event", {"session_id": "s1"})

    assert result == _DELIVERED


# ---------------------------------------------------------------------------
# Bug F: sticky-_last_status sentinel + bounded-401 give-up
#
# Root cause proven from server logs: the CI server returned ZERO 401s while a
# session emitted "still rejecting auth (HTTP 401) after N attempts". _last_status
# was only written on a real HTTP response (never on the timeout/network path), so
# after ONE 401 every subsequent TIMEOUT inherited status 401 and was miscounted as
# an auth failure -- a network blip wearing a 401 label. Separately, a genuine 401
# was classified retry-forever (_TRANSIENT), so one doomed event blocked the single
# worker (and the whole queue) indefinitely.
# ---------------------------------------------------------------------------


async def test_timeout_clears_last_status_sentinel() -> None:
    """A timeout/network error must reset _last_status to None inside _post.

    This is the core of the fix: a timeout carries no HTTP status, so it must NOT
    let a prior 401 be inherited by the next transient outcome (which the worker
    would then mis-count as an auth failure and mislabel as a credential problem).
    """
    d = _dispatcher()
    d._last_status = 401  # a prior GENUINE 401 armed the sentinel
    d._client = _client_raising(httpx.ReadTimeout("read timeout"))

    result = await d._post("test:event", {"session_id": "s1"})

    assert result == _TRANSIENT
    assert d._last_status is None, (
        "timeout must clear _last_status so it cannot be inherited as a fake 401"
    )


def _one_401_then_timeouts_then_deliver(d: _DestinationDispatcher, n_timeouts: int) -> None:
    """Fake _post: 1 genuine 401, then n_timeouts timeouts, then delivery.

    Mirrors the real _post contract post-fix: a 401 sets _last_status=401; a
    timeout clears it to None (see test_timeout_clears_last_status_sentinel).
    """
    calls: list[int] = [0]

    async def fake_post(event: str, data: dict) -> str:  # type: ignore[type-arg]
        i = calls[0]
        calls[0] += 1
        if i == 0:
            d._last_status = 401
            return _TRANSIENT
        if i <= n_timeouts:
            d._last_status = None  # a timeout has no HTTP status
            return _TRANSIENT
        return _DELIVERED

    d._post = fake_post  # type: ignore[method-assign]


async def test_timeouts_after_401_do_not_refire_auth_warning() -> None:
    """One genuine 401 then many timeouts must yield exactly ONE 'rejecting auth' warning.

    The 60s rate-limit is held fully open (monotonic advances 61s per call), so the
    only thing that can suppress re-warning is the fix: timeouts are not auth
    failures and must not re-fire the escalation. Before the fix the sticky 401
    status made every timeout re-emit "still rejecting auth (HTTP 401)".
    """
    d = _dispatcher(failure_threshold=1)
    _one_401_then_timeouts_then_deliver(d, 5)
    d._sleep_backoff = AsyncMock()

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
    assert len(auth_warnings) == 1, (
        "timeouts after a 401 must NOT re-fire the auth warning; expected exactly 1, "
        f"got {len(auth_warnings)}: {[_rendered(c) for c in auth_warnings]}"
    )


async def test_persistent_401_gives_up_and_unblocks_queue() -> None:
    """A never-succeeding genuine 401 must be bounded so the queue keeps draining.

    Before the fix, a 401 was retried forever (_TRANSIENT) -- the inner loop never
    broke, so one doomed event blocked the single worker and every later event
    behind it. After the fix, after _AUTH_GIVEUP_ATTEMPTS consecutive 401s the event
    is skipped (durable in events.jsonl) and the worker advances. If the fix were
    absent, queue.join() would never complete and asyncio.wait_for would time out.
    """
    d = _dispatcher()  # failure_threshold=3 default; give-up ceiling is 10
    d._sleep_backoff = AsyncMock()

    async def always_401(event: str, data: dict) -> str:  # type: ignore[type-arg]
        d._last_status = 401
        return _TRANSIENT

    d._post = always_401  # type: ignore[method-assign]

    with patch(LOGGER_PATH) as mock_logger:
        d._queue.put_nowait(("e1", {"session_id": "s1"}))
        d._queue.put_nowait(("e2", {"session_id": "s2"}))
        task = asyncio.create_task(d._worker())
        # Without bounded give-up this join() never returns -> TimeoutError -> fail.
        await asyncio.wait_for(d._queue.join(), timeout=5.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert d._queue.qsize() == 0, "both doomed 401 events must drain; the queue must unblock"
    giveup_warnings = [c for c in mock_logger.warning.call_args_list if "giving up" in _rendered(c)]
    assert len(giveup_warnings) >= 1, (
        f"expected a 'giving up' warning after {_AUTH_GIVEUP_ATTEMPTS} consecutive 401s, "
        f"got: {[_rendered(c) for c in mock_logger.warning.call_args_list]}"
    )


async def test_delivery_resets_auth_counter_so_events_never_reach_giveup() -> None:
    """A success (202) must zero _auth_failures so 401s can't accumulate across events.

    Council (tester-breaker/crusty): if a 202 did not reset the counter, N non-consecutive
    401s spread over a healthy-most-of-the-time server would eventually trip the give-up
    ceiling and skip a deliverable event. Two events, each with (_AUTH_GIVEUP_ATTEMPTS - 2)
    genuine 401s then a 202, must BOTH deliver and produce NO give-up.
    """
    per_event_401 = _AUTH_GIVEUP_ATTEMPTS - 2
    d = _dispatcher()
    d._sleep_backoff = AsyncMock()

    state: dict[str, int] = {"idx": 0, "n_this_event": 0}

    async def fake_post(event: str, data: dict) -> str:  # type: ignore[type-arg]
        if state["n_this_event"] < per_event_401:
            state["n_this_event"] += 1
            d._last_status = 401
            return _TRANSIENT
        state["n_this_event"] = 0  # reset for the next event
        d._last_status = 202
        return _DELIVERED

    d._post = fake_post  # type: ignore[method-assign]

    with patch(LOGGER_PATH) as mock_logger:
        d._queue.put_nowait(("e1", {"session_id": "s1"}))
        d._queue.put_nowait(("e2", {"session_id": "s2"}))
        task = asyncio.create_task(d._worker())
        await asyncio.wait_for(d._queue.join(), timeout=5.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert d._auth_failures == 0, "delivery must reset the auth-failure counter"
    giveup = [c for c in mock_logger.warning.call_args_list if "giving up" in _rendered(c)]
    assert giveup == [], (
        "a delivered event between 401s must prevent give-up; "
        f"got give-up warnings: {[_rendered(c) for c in giveup]}"
    )


async def test_giveup_fires_on_exactly_the_nth_genuine_401() -> None:
    """Give-up must trigger on exactly the _AUTH_GIVEUP_ATTEMPTS-th genuine 401 (boundary pin).

    Council (tester-breaker): pin the off-by-one. The increment precedes the >= check, so
    _post is called exactly _AUTH_GIVEUP_ATTEMPTS times for one never-succeeding event
    before the worker gives up and advances -- not one fewer, not one more.
    """
    d = _dispatcher()
    d._sleep_backoff = AsyncMock()
    calls = {"n": 0}

    async def always_401(event: str, data: dict) -> str:  # type: ignore[type-arg]
        calls["n"] += 1
        d._last_status = 401
        return _TRANSIENT

    d._post = always_401  # type: ignore[method-assign]

    with patch(LOGGER_PATH):
        d._queue.put_nowait(("e1", {"session_id": "s1"}))
        task = asyncio.create_task(d._worker())
        await asyncio.wait_for(d._queue.join(), timeout=5.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert calls["n"] == _AUTH_GIVEUP_ATTEMPTS, (
        f"give-up must fire on exactly the {_AUTH_GIVEUP_ATTEMPTS}th genuine 401, "
        f"but _post was called {calls['n']} times"
    )


async def test_worker_delivers_next_event_after_giving_up_on_a_doomed_one() -> None:
    """After give-up on a doomed 401 event, the worker must still DELIVER the next event.

    Council (tester-breaker/ROB): prove the give-up `break` exits only the retry loop, not
    the worker -- a following, healthy event must be delivered, not stranded.
    """
    d = _dispatcher()
    d._sleep_backoff = AsyncMock()
    delivered: list[str] = []

    async def fake_post(event: str, data: dict) -> str:  # type: ignore[type-arg]
        if event == "doomed":
            d._last_status = 401
            return _TRANSIENT
        d._last_status = 202
        delivered.append(event)
        return _DELIVERED

    d._post = fake_post  # type: ignore[method-assign]

    with patch(LOGGER_PATH):
        d._queue.put_nowait(("doomed", {"session_id": "s1"}))
        d._queue.put_nowait(("healthy", {"session_id": "s2"}))
        task = asyncio.create_task(d._worker())
        await asyncio.wait_for(d._queue.join(), timeout=5.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert delivered == ["healthy"], (
        f"worker must survive give-up and deliver the next event; delivered={delivered}"
    )
