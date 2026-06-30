"""Tests for notification messages — Task 9.

DEGRADED warning (once per episode), RECOVERY notice (on first delivery after degrade),
and auth-escalation (consecutive 401s after failure_threshold) emitted by _worker().
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
        assert d._degraded_warned is False, (
            "_degraded_warned must be False after recovery"
        )
        await d.close()

    async def test_no_recovery_without_prior_degradation(self) -> None:
        """Clean delivery -> no recovery info logged."""
        d = _dispatcher()
        d._post = _make_outcome_post([_DELIVERED])  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        recovery_calls = [
            c for c in mock_logger.info.call_args_list if "Reconnected to" in str(c)
        ]
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

        recovery_calls = [
            c for c in mock_logger.info.call_args_list if "Reconnected to" in str(c)
        ]
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
