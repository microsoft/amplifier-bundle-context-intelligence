"""Tests for _DestinationDispatcher.close() shutdown behaviour — Task 11.

Contract:
  1. DEGRADED or undelivered-events shutdown → loud WARNING with honest count
     (queued + in-flight + overflow-dropped) and REAL storage path (not '<path>').
  2. Clean shutdown (count 0, not degraded) → NO WARNING.
  3. Drain is bounded by close_drain_timeout.
  4. Worker sleep is cancellation-safe: close() cancels a sleeping worker promptly
     rather than waiting out the full backoff interval.

DESIGN NOTE — Why these tests use backoff_initial=0.001, not 0 and not AsyncMock:
  - backoff_initial=0.0 causes OverflowError: the worker loops thousands of times in
    a 50ms test window, pushing _consecutive_failures so high that
    ``backoff_initial * (2 ** (failures - 1))`` overflows float.
  - AsyncMock for _sleep_backoff does NOT yield to the event loop, making cancel()
    undeliverable and causing the test to hang.
  - backoff_initial=0.001 + backoff_max=0.001 caps the sleep at 1ms per retry,
    limiting iterations to ~50 in a 50ms window — well within float range for
    2^49 — while still yielding control so cancel() is reliably delivered.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _TRANSIENT,
    _DestinationDispatcher,
)

LOGGER_PATH = "amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"
STORAGE_PATH = "/tmp/ci-test-sessions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _dispatcher(
    name: str = "test-dest",
    queue_capacity: int = 256,
    close_drain_timeout: float = 0.2,
    # 1ms backoff keeps iterations bounded (~50 per 50ms window) so
    # 2^(failures-1) stays within float range (2^49 ≈ 5.6e14 ≪ float_max).
    backoff_initial: float = 0.001,
    backoff_max: float = 0.001,
    backoff_jitter: bool = False,
    storage_path: str = STORAGE_PATH,
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
        storage_path=storage_path,
        **kwargs,
    )


async def _always_transient(event: str, data: dict[str, Any]) -> str:
    """Stub _post: always TRANSIENT. The real _sleep_backoff provides the yield point."""
    return _TRANSIENT


# ---------------------------------------------------------------------------
# TestShutdownWarning
# ---------------------------------------------------------------------------


class TestShutdownWarning:
    """DEGRADED or undelivered-events shutdown emits a loud WARNING with honest counts."""

    async def test_degraded_shutdown_emits_warning(self) -> None:
        """Degraded state at shutdown → loud WARNING is emitted."""
        d = _dispatcher(close_drain_timeout=0.2)
        d._post = _always_transient  # type: ignore[method-assign]
        d._degraded_warned = True  # pre-set degraded state

        d.enqueue("e1", {"session_id": "s1"})
        # Let worker start, dequeue e1, and enter the TRANSIENT retry loop
        await asyncio.sleep(0.05)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        shutdown_warnings = [
            c for c in mock_logger.warning.call_args_list
            if STORAGE_PATH in str(c)
        ]
        assert len(shutdown_warnings) >= 1, (
            f"Expected ≥1 shutdown WARNING mentioning storage path, "
            f"got all warnings: {mock_logger.warning.call_args_list}"
        )

    async def test_shutdown_warning_uses_real_storage_path_not_placeholder(self) -> None:
        """WARNING uses self._storage_path — never a placeholder like '<path>'."""
        real_path = "/real/ci/session/path"
        d = _dispatcher(close_drain_timeout=0.2, storage_path=real_path)
        d._post = _always_transient  # type: ignore[method-assign]
        d._degraded_warned = True

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.sleep(0.05)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        shutdown_warnings = [
            c for c in mock_logger.warning.call_args_list
            if real_path in str(c)
        ]
        assert len(shutdown_warnings) >= 1, (
            f"WARNING must reference real storage path {real_path!r}, "
            f"got: {mock_logger.warning.call_args_list}"
        )
        for call in shutdown_warnings:
            assert "<path>" not in str(call), (
                f"WARNING must not use placeholder '<path>': {call}"
            )

    async def test_shutdown_warning_honest_count_equals_queued_plus_inflight_plus_dropped(
        self,
    ) -> None:
        """Honest count = queued(qsize) + in-flight(0 or 1) + overflow-dropped.

        Setup: 3 events enqueued (worker dequeues e1 → in-flight; e2+e3 stay queued)
        plus 3 overflow-dropped events pre-seeded.
        Expected: queued=2, in_flight=1, dropped=3 → total=6.
        """
        d = _dispatcher(close_drain_timeout=0.2, queue_capacity=256)
        d._post = _always_transient  # type: ignore[method-assign]
        d._overflow_dropped = 3  # pre-seed overflow drops

        # e1 → worker dequeues (in-flight); e2, e3 → remain queued
        d.enqueue("e1", {"session_id": "s1"})
        d.enqueue("e2", {"session_id": "s1"})
        d.enqueue("e3", {"session_id": "s1"})

        # Allow worker to dequeue e1 and enter the TRANSIENT retry loop
        await asyncio.sleep(0.05)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        shutdown_warnings = [
            c for c in mock_logger.warning.call_args_list
            if STORAGE_PATH in str(c)
        ]
        assert len(shutdown_warnings) >= 1, (
            f"Expected shutdown WARNING, got all warnings: {mock_logger.warning.call_args_list}"
        )

        # Extract positional args: (fmt, name, total, queued, in_flight, dropped, storage_path)
        pos_args = shutdown_warnings[0][0]
        assert len(pos_args) >= 7, (
            f"Expected ≥7 positional args in shutdown warning, got {len(pos_args)}: {pos_args}"
        )
        _fmt, _name, total, queued, in_flight, dropped, storage = pos_args[:7]

        assert total == queued + in_flight + dropped, (
            f"Honest count must equal queued + in_flight + dropped: "
            f"{total} != {queued} + {in_flight} + {dropped}"
        )
        assert total == 6, (
            f"Expected total=6 (queued=2 + in_flight=1 + dropped=3), got total={total} "
            f"(queued={queued}, in_flight={in_flight}, dropped={dropped})"
        )
        assert str(storage) == STORAGE_PATH, (
            f"Expected storage={STORAGE_PATH!r}, got {storage!r}"
        )

    async def test_undelivered_events_without_degraded_emits_warning(self) -> None:
        """Undelivered events (not degraded) trigger WARNING because total > 0."""
        d = _dispatcher(close_drain_timeout=0.2)
        d._post = _always_transient  # type: ignore[method-assign]
        # Do NOT set _degraded_warned — verify count alone triggers warning
        assert d._degraded_warned is False

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.sleep(0.05)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        shutdown_warnings = [
            c for c in mock_logger.warning.call_args_list
            if STORAGE_PATH in str(c)
        ]
        assert len(shutdown_warnings) >= 1, (
            f"Undelivered events alone must trigger shutdown WARNING (even if not degraded): "
            f"{mock_logger.warning.call_args_list}"
        )

    async def test_shutdown_warning_is_at_warning_level_not_debug(self) -> None:
        """Shutdown message must be emitted at WARNING level — not debug or info."""
        d = _dispatcher(close_drain_timeout=0.2)
        d._post = _always_transient  # type: ignore[method-assign]
        d._degraded_warned = True

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.sleep(0.05)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        debug_with_path = [
            c for c in mock_logger.debug.call_args_list if STORAGE_PATH in str(c)
        ]
        assert len(debug_with_path) == 0, (
            f"Shutdown storage-path message must be at WARNING, not debug: {debug_with_path}"
        )
        warn_with_path = [
            c for c in mock_logger.warning.call_args_list if STORAGE_PATH in str(c)
        ]
        assert len(warn_with_path) >= 1, (
            f"Expected shutdown WARNING with storage path, "
            f"got warnings: {mock_logger.warning.call_args_list}"
        )

    async def test_overflow_dropped_plus_inflight_emits_warning(self) -> None:
        """overflow_dropped > 0 with in-flight event → WARNING (total > 0)."""
        d = _dispatcher(close_drain_timeout=0.2)
        d._post = _always_transient  # type: ignore[method-assign]
        d._overflow_dropped = 5  # 5 permanently lost events

        d.enqueue("e1", {"session_id": "s1"})  # in-flight, never delivered
        await asyncio.sleep(0.05)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        shutdown_warnings = [
            c for c in mock_logger.warning.call_args_list
            if STORAGE_PATH in str(c)
        ]
        assert len(shutdown_warnings) >= 1, (
            f"Overflow-dropped events must trigger shutdown WARNING: "
            f"{mock_logger.warning.call_args_list}"
        )
        pos_args = shutdown_warnings[0][0]
        assert len(pos_args) >= 6, f"Not enough warning args: {pos_args}"
        # dropped is pos_args[5] in: ("%s ...", name, total, queued, in_flight, dropped, path)
        dropped_in_warning = pos_args[5]
        assert dropped_in_warning == 5, (
            f"Expected dropped=5 in warning args, got {dropped_in_warning}: {pos_args}"
        )


# ---------------------------------------------------------------------------
# TestCleanShutdownNoWarning
# ---------------------------------------------------------------------------


class TestCleanShutdownNoWarning:
    """A clean shutdown (count 0, not degraded) emits no shutdown WARNING."""

    async def test_all_events_delivered_no_warning(self) -> None:
        """All events delivered within drain timeout → no shutdown WARNING."""
        d = _dispatcher(close_drain_timeout=2.0)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        ok_response = MagicMock()
        ok_response.status_code = 200
        mock_client.post.return_value = ok_response
        d._client = mock_client

        d.enqueue("e1", {"session_id": "s1"})
        # Drain fully before calling close
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        warn_with_path = [
            c for c in mock_logger.warning.call_args_list if STORAGE_PATH in str(c)
        ]
        assert len(warn_with_path) == 0, (
            f"Clean shutdown (all delivered) must not emit shutdown WARNING, "
            f"got: {warn_with_path}"
        )

    async def test_no_events_no_worker_no_warning(self) -> None:
        """No events enqueued, worker never started → no shutdown WARNING."""
        d = _dispatcher(close_drain_timeout=0.1)
        assert d._worker_task is None

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        warn_with_path = [
            c for c in mock_logger.warning.call_args_list if STORAGE_PATH in str(c)
        ]
        assert len(warn_with_path) == 0, (
            f"Idle dispatcher must not emit shutdown WARNING, got: {warn_with_path}"
        )

    async def test_zero_count_and_not_degraded_no_warning(self) -> None:
        """count=0 and _degraded_warned=False → no shutdown WARNING."""
        d = _dispatcher(close_drain_timeout=2.0)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        ok_response = MagicMock()
        ok_response.status_code = 200
        mock_client.post.return_value = ok_response
        d._client = mock_client

        assert d._degraded_warned is False
        assert d._overflow_dropped == 0

        d.enqueue("e1", {"session_id": "s1"})
        d.enqueue("e2", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._queue.qsize() == 0
        assert d._current is None
        assert d._overflow_dropped == 0

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()

        warn_with_path = [
            c for c in mock_logger.warning.call_args_list if STORAGE_PATH in str(c)
        ]
        assert len(warn_with_path) == 0, (
            f"count=0 + not degraded must produce no shutdown WARNING, got: {warn_with_path}"
        )


# ---------------------------------------------------------------------------
# TestDrainBounded
# ---------------------------------------------------------------------------


class TestDrainBounded:
    """Drain is bounded by close_drain_timeout."""

    async def test_stuck_worker_does_not_block_close_beyond_timeout(self) -> None:
        """close() returns within a small multiple of drain_timeout even with a stuck worker."""
        drain_timeout = 0.15
        d = _dispatcher(close_drain_timeout=drain_timeout)

        # Worker blocks indefinitely in _post (simulates a stuck network call)
        async def stuck_post(event: str, data: dict[str, Any]) -> str:
            await asyncio.sleep(999)
            return _TRANSIENT  # never reached

        d._post = stuck_post  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.sleep(0.05)  # let worker start and block in stuck_post

        with patch(LOGGER_PATH):
            t0 = time.monotonic()
            await d.close()
            elapsed = time.monotonic() - t0

        # Must complete well within 3x drain_timeout (+ some overhead for cancellation)
        bound = drain_timeout * 3 + 0.2
        assert elapsed < bound, (
            f"close() took {elapsed:.3f}s; expected < {bound:.3f}s "
            f"(drain_timeout={drain_timeout}s)"
        )

    async def test_drain_completes_early_when_all_delivered(self) -> None:
        """close() completes quickly when all events are delivered before the drain timeout."""
        d = _dispatcher(close_drain_timeout=5.0)  # generous timeout
        mock_client = AsyncMock()
        mock_client.is_closed = False
        ok_response = MagicMock()
        ok_response.status_code = 200
        mock_client.post.return_value = ok_response
        d._client = mock_client

        d.enqueue("e1", {"session_id": "s1"})
        d.enqueue("e2", {"session_id": "s1"})

        with patch(LOGGER_PATH):
            t0 = time.monotonic()
            await d.close()
            elapsed = time.monotonic() - t0

        # Should complete well under the 5s timeout (events deliver in milliseconds)
        assert elapsed < 2.0, (
            f"close() took {elapsed:.3f}s for 2 successful deliveries; expected < 2.0s"
        )


# ---------------------------------------------------------------------------
# TestCancellationSafeSleep
# ---------------------------------------------------------------------------


class TestCancellationSafeSleep:
    """Proves the worker's asyncio.sleep is cancellation-safe.

    Every other test uses backoff_initial=0.001 so _sleep_backoff calls
    asyncio.sleep(0.001) — a 1ms yield that allows cancel() to be delivered
    without blocking close() for long.  This dedicated test uses a HIGH
    backoff (30s), no jitter, and a tight drain timeout (0.15s).

    The test proves: close() cancels the 30s backoff sleep and returns in
    ~drain_timeout seconds, NOT ~30s.

    Named load-bearing invariant: worker sleep is cancellation-safe.
    """

    async def test_close_cancels_sleeping_worker_not_waits_out_backoff(self) -> None:
        """close() with tight drain_timeout cancels a 30s backoff sleep promptly.

        Specifically:
        - backoff_initial=30.0, backoff_jitter=False → asyncio.sleep(30) after 1st TRANSIENT
        - close_drain_timeout=0.15 → drain times out, then cancel() is called
        - _post is NOT mocked (uses _always_transient which returns instantly)
        - _sleep_backoff is NOT mocked — uses REAL asyncio.sleep(30)

        Expected: close() returns in ~0.15s (drain timeout), NOT ~30s (backoff).
        """
        HIGH_BACKOFF = 30.0   # worker sleeps this long after first TRANSIENT
        DRAIN_TIMEOUT = 0.15  # close() drain window — much shorter than the backoff

        d = _dispatcher(
            close_drain_timeout=DRAIN_TIMEOUT,
            backoff_initial=HIGH_BACKOFF,
            backoff_max=HIGH_BACKOFF,
            backoff_jitter=False,  # deterministic: always sleep exactly HIGH_BACKOFF seconds
        )
        # REAL asyncio.sleep in _sleep_backoff: do NOT mock it.
        d._post = _always_transient  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})

        # Give worker time to: dequeue e1 → call _post (instant TRANSIENT) →
        # increment consecutive_failures → log degraded warning →
        # enter asyncio.sleep(HIGH_BACKOFF=30s).
        # Each step takes microseconds; 80ms is comfortably beyond that.
        await asyncio.sleep(0.08)

        with patch(LOGGER_PATH):  # suppress shutdown WARNING noise in test output
            t0 = time.monotonic()
            await d.close()
            elapsed = time.monotonic() - t0

        # If the backoff sleep were NOT cancellable, close() would block for ~30s.
        # Successful cancellation: close() returns in approximately DRAIN_TIMEOUT seconds.
        assert elapsed < HIGH_BACKOFF / 2, (
            f"close() took {elapsed:.3f}s — worker backoff sleep was NOT cancelled! "
            f"(high_backoff={HIGH_BACKOFF}s, drain_timeout={DRAIN_TIMEOUT}s). "
            f"asyncio.sleep should be cancellable — this indicates a regression."
        )
        # Tighter bound: should be approximately drain_timeout + small overhead
        assert elapsed < DRAIN_TIMEOUT * 10 + 0.5, (
            f"close() took {elapsed:.3f}s — too slow even accounting for cancellation overhead "
            f"(drain_timeout={DRAIN_TIMEOUT}s, expected < {DRAIN_TIMEOUT * 10 + 0.5:.2f}s)"
        )
        # Sanity check: worker task cleaned up
        assert d._worker_task is None, "Worker task must be None after close()"
