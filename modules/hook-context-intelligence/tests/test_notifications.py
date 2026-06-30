"""Tests for notification messages — Tasks 9 and 10.

DEGRADED warning (once per episode), RECOVERY notice (on first delivery after degrade),
and auth-escalation (consecutive 401s after failure_threshold) emitted by _worker().

Task 10 additions:
- OVERFLOW: rate-limited loud warning with 'buffer full' and real storage path
- PERMANENT: loud warning for 403 ('check credentials') or 400/other ('malformed event, skipped')
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DELIVERED,
    _TRANSIENT,
    _DestinationDispatcher,
)

LOGGER_PATH = "amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _dispatcher(
    name: str = "test-dest",
    backoff_initial: float = 1.0,
    backoff_max: float = 30.0,
    backoff_jitter: bool = False,
    queue_capacity: int = 256,
    close_drain_timeout: float = 2.0,
    failure_threshold: int = 3,
    **kwargs: Any,
) -> _DestinationDispatcher:
    return _DestinationDispatcher(
        name=name,
        url="http://localhost:8080",
        api_key="test-key",
        workspace="ws",
        dispatch_timeout=10.0,
        failure_threshold=failure_threshold,
        queue_capacity=queue_capacity,
        close_drain_timeout=close_drain_timeout,
        backoff_initial=backoff_initial,
        backoff_max=backoff_max,
        backoff_jitter=backoff_jitter,
        **kwargs,
    )


def _mock_client(side_effects: list[Any]) -> AsyncMock:
    """AsyncMock httpx client whose .post() returns responses in sequence."""
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = side_effects
    return client


def _make_outcome_post(outcomes: list[str]) -> Any:
    """Build a fake _post coroutine that consumes outcomes in order."""
    idx = 0

    async def fake_post(event: str, data: dict[str, Any]) -> str:
        nonlocal idx
        result = outcomes[idx]
        idx += 1
        return result

    return fake_post


# ---------------------------------------------------------------------------
# TestDegradedNotification
# ---------------------------------------------------------------------------


class TestDegradedNotification:
    """DEGRADED warning emitted once per episode, not on every retry."""

    async def test_degraded_emitted_once_per_episode(self) -> None:
        """3 failures then success -> exactly 1 DEGRADED warning."""
        d = _dispatcher()
        d._post = _make_outcome_post([_TRANSIENT, _TRANSIENT, _TRANSIENT, _DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        degraded_calls = [
            c
            for c in mock_logger.warning.call_args_list
            if "unreachable, retrying with backoff" in str(c)
        ]
        assert len(degraded_calls) == 1, (
            f"Expected exactly 1 DEGRADED warning, got {len(degraded_calls)}: "
            f"{mock_logger.warning.call_args_list}"
        )
        await d.close()

    async def test_degraded_once_per_episode_when_flapping(self) -> None:
        """fail->recover->fail->recover -> 2 DEGRADED warnings (one per episode)."""
        d = _dispatcher()
        d._post = _make_outcome_post([_TRANSIENT, _DELIVERED, _TRANSIENT, _DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            d.enqueue("e2", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        degraded_calls = [
            c
            for c in mock_logger.warning.call_args_list
            if "unreachable, retrying with backoff" in str(c)
        ]
        assert len(degraded_calls) == 2, (
            f"Expected exactly 2 DEGRADED warnings (one per episode), "
            f"got {len(degraded_calls)}: {mock_logger.warning.call_args_list}"
        )
        await d.close()


# ---------------------------------------------------------------------------
# TestRecoveryNotification
# ---------------------------------------------------------------------------


class TestRecoveryNotification:
    """RECOVERY info logged on first successful delivery after degradation."""

    async def test_recovery_emitted_once_after_degradation(self) -> None:
        """TRANSIENT then DELIVERED -> exactly 1 recovery info, _degraded_warned=False."""
        d = _dispatcher()
        d._post = _make_outcome_post([_TRANSIENT, _DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        recovery_calls = [
            c
            for c in mock_logger.info.call_args_list
            if "Reconnected to" in str(c) and "resuming delivery" in str(c)
        ]
        assert len(recovery_calls) == 1, (
            f"Expected exactly 1 RECOVERY info, got {len(recovery_calls)}: "
            f"{mock_logger.info.call_args_list}"
        )
        assert d._degraded_warned is False, "_degraded_warned must be False after recovery"
        await d.close()

    async def test_no_recovery_without_prior_degradation(self) -> None:
        """Clean delivery -> no recovery info logged."""
        d = _dispatcher()
        d._post = _make_outcome_post([_DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        recovery_calls = [c for c in mock_logger.info.call_args_list if "Reconnected to" in str(c)]
        assert len(recovery_calls) == 0, (
            f"Expected no recovery info for clean delivery, got {len(recovery_calls)}: "
            f"{mock_logger.info.call_args_list}"
        )
        await d.close()

    async def test_recovery_notice_has_no_event_count(self) -> None:
        """Recovery message contains no '%d' and no 'events' — count would be dishonest."""
        d = _dispatcher()
        d._post = _make_outcome_post([_TRANSIENT, _DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        recovery_calls = [c for c in mock_logger.info.call_args_list if "Reconnected to" in str(c)]
        assert len(recovery_calls) == 1, "Expected exactly 1 recovery info"
        # The format string is the first positional arg of the log call.
        fmt = recovery_calls[0][0][0]
        assert "%d" not in fmt, f"Recovery message format contains '%d': {fmt!r}"
        assert "events" not in fmt, f"Recovery message format contains 'events': {fmt!r}"
        await d.close()

    async def test_flapping_recovery_emits_two_recovery_notices(self) -> None:
        """fail->succeed->fail->succeed across two events -> 2 RECOVERY notices."""
        d = _dispatcher()
        d._post = _make_outcome_post([_TRANSIENT, _DELIVERED, _TRANSIENT, _DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            d.enqueue("e2", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        recovery_calls = [
            c
            for c in mock_logger.info.call_args_list
            if "Reconnected to" in str(c) and "resuming delivery" in str(c)
        ]
        assert len(recovery_calls) == 2, (
            f"Expected 2 RECOVERY notices across the flap, got {len(recovery_calls)}: "
            f"{mock_logger.info.call_args_list}"
        )
        await d.close()

    async def test_recovery_then_immediate_redegrade_restarts_backoff(self) -> None:
        """2 DEGRADED and sleeps == [1.0, 2.0, 1.0] proving backoff restarts from n=0."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=30.0, backoff_jitter=False)
        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        # e1: fail(→1.0), fail(→2.0), succeed(RECOVERY+reset); e2: fail(→1.0), succeed
        d._client = _mock_client(
            [
                _make_response(503),  # e1 fail 1 — DEGRADED #1, sleep 1.0
                _make_response(503),  # e1 fail 2 — debug, sleep 2.0
                _make_response(200),  # e1 success — RECOVERY, reset _consecutive_failures
                _make_response(503),  # e2 fail 1 — DEGRADED #2 (fresh), sleep 1.0
                _make_response(200),  # e2 success
            ]
        )

        with patch("asyncio.sleep", new=capture_sleep):
            with patch(LOGGER_PATH) as mock_logger:
                d.enqueue("e1", {"session_id": "s1"})
                d.enqueue("e2", {"session_id": "s1"})
                await asyncio.wait_for(d._queue.join(), timeout=2.0)

        degraded_calls = [
            c
            for c in mock_logger.warning.call_args_list
            if "unreachable, retrying with backoff" in str(c)
        ]
        assert len(degraded_calls) == 2, (
            f"Expected 2 DEGRADED warnings, got {len(degraded_calls)}: "
            f"{mock_logger.warning.call_args_list}"
        )
        assert sleep_calls == [1.0, 2.0, 1.0], (
            f"Expected sleeps [1.0, 2.0, 1.0] (backoff restarts from n=0 after recovery), "
            f"got {sleep_calls}"
        )
        await d.close()


# ---------------------------------------------------------------------------
# TestAuthEscalation
# ---------------------------------------------------------------------------


class TestAuthEscalation:
    """Auth-class (401) failure escalation after failure_threshold consecutive 401s."""

    async def test_auth_escalation_after_repeated_401(self) -> None:
        """failure_threshold=3, four 401s -> exactly 1 escalated auth warning."""
        # Sequence with failure_threshold=3:
        #   401 #1: _auth_failures=1, _degraded_warned=False -> DEGRADED warning
        #   401 #2: _auth_failures=2, _degraded_warned=True, <threshold -> debug
        #   401 #3: _auth_failures=3, _degraded_warned=True, ==threshold -> AUTH ESCALATION
        #   401 #4: _auth_failures=4, _degraded_warned=True, >threshold -> debug
        #   200: RECOVERY, reset
        d = _dispatcher(failure_threshold=3)
        d._client = _mock_client(
            [
                _make_response(401),
                _make_response(401),
                _make_response(401),
                _make_response(401),
                _make_response(200),
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        auth_escalation_calls = [
            c
            for c in mock_logger.warning.call_args_list
            if "looks like an auth problem" in str(c) and "Check credentials" in str(c)
        ]
        assert len(auth_escalation_calls) == 1, (
            f"Expected exactly 1 auth escalation warning, "
            f"got {len(auth_escalation_calls)}: {mock_logger.warning.call_args_list}"
        )
        await d.close()


# ---------------------------------------------------------------------------
# TestOverflowNotification (Task 10)
# ---------------------------------------------------------------------------


class TestOverflowNotification:
    """Overflow (queue full) notifications — loud, rate-limited, real storage path."""

    async def test_overflow_logs_loud_with_real_path(self) -> None:
        """queue_capacity=1: one overflow -> warning with 'buffer full' and real path."""
        storage_path = "/tmp/ci-test-sessions"
        d = _dispatcher(
            queue_capacity=1,
            storage_path=storage_path,
        )
        d._post = _make_outcome_post([_DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            # First enqueue fills the queue; starts worker (doesn't run yet — no await)
            d.enqueue("e1", {"session_id": "s1"})
            # Second enqueue overflows (worker hasn't had a chance to run)
            d.enqueue("e2", {"session_id": "s2"})
            # Yield control so worker can drain the single queued event
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._overflow_dropped == 1, f"Expected _overflow_dropped==1, got {d._overflow_dropped}"

        overflow_calls = [c for c in mock_logger.warning.call_args_list if "buffer full" in str(c)]
        assert len(overflow_calls) == 1, (
            f"Expected exactly 1 overflow warning, got {len(overflow_calls)}: "
            f"{mock_logger.warning.call_args_list}"
        )

        rendered = str(overflow_calls[0])
        assert "buffer full" in rendered, f"'buffer full' not in warning: {rendered!r}"
        assert storage_path in rendered, (
            f"Real storage path {storage_path!r} not in warning: {rendered!r}"
        )
        assert "<path>" not in rendered, (
            f"Literal '<path>' found in warning (must be real path): {rendered!r}"
        )
        await d.close()

    async def test_overflow_log_is_rate_limited(self) -> None:
        """256 consecutive overflows -> exactly 1 'buffer full' log, _overflow_dropped==256."""
        d = _dispatcher(queue_capacity=1, storage_path="/tmp/ci-test-sessions")
        # Provide enough _DELIVERED outcomes so the worker can drain the one queued event.
        d._post = _make_outcome_post([_DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            # Fill the single-slot queue (worker starts but doesn't run yet)
            d.enqueue("fill", {"session_id": "s0"})
            # 256 consecutive overflows — all happen before the event loop yields
            for i in range(256):
                d.enqueue(f"e{i}", {"session_id": f"s{i}"})
            # Let worker drain the one queued event
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._overflow_dropped == 256, (
            f"Expected _overflow_dropped==256, got {d._overflow_dropped}"
        )

        overflow_calls = [c for c in mock_logger.warning.call_args_list if "buffer full" in str(c)]
        assert len(overflow_calls) == 1, (
            f"Expected exactly 1 rate-limited overflow warning (got {len(overflow_calls)}): "
            f"{mock_logger.warning.call_args_list}"
        )
        await d.close()

    async def test_overflow_while_degraded_combined(self) -> None:
        """OVERFLOW while DEGRADED: _degraded_warned True + _overflow_dropped==1 + loud log."""
        storage_path = "/tmp/ci-test-sessions"
        d = _dispatcher(
            queue_capacity=2,
            storage_path=storage_path,
        )

        # Use a mock HTTP client that always returns 503 (TRANSIENT) so e1 never delivers.
        sleep_entered = asyncio.Event()
        proceed = asyncio.Event()

        async def blocking_sleep() -> None:
            sleep_entered.set()
            await proceed.wait()

        d._sleep_backoff = blocking_sleep  # type: ignore[method-assign]
        d._post = _make_outcome_post(  # type: ignore[method-assign]
            [_TRANSIENT, _TRANSIENT, _TRANSIENT, _TRANSIENT, _TRANSIENT]
        )

        with patch(LOGGER_PATH) as mock_logger:
            # e1 enters the worker immediately; worker gets TRANSIENT -> sets _degraded_warned
            d.enqueue("e1", {"session_id": "s1"})

            # Wait until the worker has set _degraded_warned and entered sleep
            await asyncio.wait_for(sleep_entered.wait(), timeout=2.0)
            assert d._degraded_warned is True, "_degraded_warned must be True before overflow"

            # Fill the capacity-2 queue while the worker is blocked in sleep
            d.enqueue("e2", {"session_id": "s2"})
            d.enqueue("e3", {"session_id": "s3"})

            # e4 overflows — synchronous, so we can assert immediately
            d.enqueue("e4", {"session_id": "s4"})
            assert d._overflow_dropped == 1, (
                f"Expected _overflow_dropped==1, got {d._overflow_dropped}"
            )

            # Verify overflow log was emitted with real path before we let the worker proceed
            overflow_calls = [
                c for c in mock_logger.warning.call_args_list if "buffer full" in str(c)
            ]
            assert len(overflow_calls) == 1, (
                f"Expected 1 overflow warning, got {len(overflow_calls)}: "
                f"{mock_logger.warning.call_args_list}"
            )
            rendered = str(overflow_calls[0])
            assert storage_path in rendered, (
                f"Real storage path {storage_path!r} not in warning: {rendered!r}"
            )
            assert "<path>" not in rendered, f"Literal '<path>' found in warning: {rendered!r}"

        # Release the blocking sleep and cancel the worker
        proceed.set()
        await d.close()

        assert d._degraded_warned is True  # never recovered (worker was cancelled)
        assert d._overflow_dropped == 1


# ---------------------------------------------------------------------------
# TestPermanentNotification (Task 10)
# ---------------------------------------------------------------------------


class TestPermanentNotification:
    """PERMANENT outcomes (non-retryable 4xx) produce loud, rate-limited log messages."""

    async def test_permanent_403_logs_check_credentials(self) -> None:
        """HTTP 403 -> warning containing 'check credentials'."""
        d = _dispatcher()
        d._client = _mock_client([_make_response(403)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        perm_calls = [
            c for c in mock_logger.warning.call_args_list if "check credentials" in str(c)
        ]
        assert len(perm_calls) == 1, (
            f"Expected 1 PERMANENT warning for 403 with 'check credentials', "
            f"got {len(perm_calls)}: {mock_logger.warning.call_args_list}"
        )
        await d.close()

    async def test_permanent_400_logs_malformed(self) -> None:
        """HTTP 400 -> warning containing 'malformed event, skipped'."""
        d = _dispatcher()
        d._client = _mock_client([_make_response(400)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        perm_calls = [
            c for c in mock_logger.warning.call_args_list if "malformed event, skipped" in str(c)
        ]
        assert len(perm_calls) == 1, (
            f"Expected 1 PERMANENT warning for 400 with 'malformed event, skipped', "
            f"got {len(perm_calls)}: {mock_logger.warning.call_args_list}"
        )
        await d.close()

    async def test_permanent_burst_is_rate_limited(self) -> None:
        """50 x HTTP 400 -> exactly 1 PERMANENT 'malformed event, skipped' log."""
        d = _dispatcher()
        d._client = _mock_client([_make_response(400)] * 50)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            for i in range(50):
                d.enqueue(f"e{i}", {"session_id": f"s{i}"})
            await asyncio.wait_for(d._queue.join(), timeout=5.0)

        perm_calls = [
            c for c in mock_logger.warning.call_args_list if "malformed event, skipped" in str(c)
        ]
        assert len(perm_calls) == 1, (
            f"Expected exactly 1 rate-limited PERMANENT warning (got {len(perm_calls)}): "
            f"{mock_logger.warning.call_args_list}"
        )
