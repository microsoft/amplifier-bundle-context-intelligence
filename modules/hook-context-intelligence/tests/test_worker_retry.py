"""Tests for _worker() retry loop and _sleep_backoff() — Task 5.

_worker() holds the in-flight event in self._current, retries on _TRANSIENT
outcomes with capped full-jitter backoff, and advances on _DELIVERED/_PERMANENT.
Order is preserved by construction: single worker, one in-flight event at a time.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DELIVERED,
    _DestinationDispatcher,
)


def _make_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _dispatcher(
    name: str = "test",
    backoff_initial: float = 1.0,
    backoff_max: float = 30.0,
    backoff_jitter: bool = False,
    queue_capacity: int = 256,
    close_drain_timeout: float = 2.0,
    **kwargs: Any,
) -> _DestinationDispatcher:
    return _DestinationDispatcher(
        name=name,
        url="http://localhost:8080",
        api_key="test-key",
        workspace="ws",
        dispatch_timeout=10.0,
        failure_threshold=3,
        queue_capacity=queue_capacity,
        close_drain_timeout=close_drain_timeout,
        backoff_initial=backoff_initial,
        backoff_max=backoff_max,
        backoff_jitter=backoff_jitter,
        **kwargs,
    )


def _mock_client(side_effects: list[Any]) -> AsyncMock:
    """Build an AsyncMock httpx client whose .post() returns responses in sequence."""
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = side_effects
    return client


# ---------------------------------------------------------------------------
# TestWorkerRetryOnTransient
# ---------------------------------------------------------------------------
class TestWorkerRetryOnTransient:
    """Worker retries the SAME event on _TRANSIENT outcomes without dropping it."""

    async def test_transient_then_delivered_retries_same_event(self) -> None:
        """Event that fails twice (TRANSIENT) is retried until DELIVERED."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(503),  # attempt 1 → TRANSIENT
                _make_response(503),  # attempt 2 → TRANSIENT
                _make_response(200),  # attempt 3 → DELIVERED
            ]
        )

        # Stub _sleep_backoff so tests run without real delays.
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._client.post.await_count == 3  # 2 retries + 1 success
        await d.close()

    async def test_ordering_preserved_under_retry(self) -> None:
        """e1 retried twice then delivered; e2 delivered immediately — order preserved."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(503),  # e1 attempt 1 → TRANSIENT
                _make_response(503),  # e1 attempt 2 → TRANSIENT
                _make_response(200),  # e1 attempt 3 → DELIVERED
                _make_response(200),  # e2 attempt 1 → DELIVERED
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        d.enqueue("e2", {"session_id": "s1"})

        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # 4 total posts in the exact order: e1 x3, e2 x1
        assert d._client.post.await_count == 4
        await d.close()

    async def test_no_silent_drops_on_transient(self) -> None:
        """Five TRANSIENT failures followed by success — event is never dropped."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(503),
                _make_response(503),
                _make_response(503),
                _make_response(503),
                _make_response(503),
                _make_response(200),  # eventually delivered
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # All 6 attempts completed — no silent drop
        assert d._client.post.await_count == 6
        await d.close()

    async def test_current_held_during_retry(self) -> None:
        """self._current holds the in-flight event while _sleep_backoff() is sleeping."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(503),  # TRANSIENT
                _make_response(200),  # DELIVERED
            ]
        )

        current_during_backoff: list[Any] = []

        async def capture_backoff() -> None:
            current_during_backoff.append(d._current)

        d._sleep_backoff = capture_backoff  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # _sleep_backoff was called once (after the TRANSIENT)
        assert len(current_during_backoff) == 1
        assert current_during_backoff[0] is not None
        event, payload = current_during_backoff[0]
        assert event == "e1"
        # After delivery, _current is cleared
        assert d._current is None
        await d.close()

    async def test_current_cleared_after_delivery(self) -> None:
        """self._current is None after successful delivery."""
        d = _dispatcher()
        d._client = _mock_client([_make_response(200)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._current is None
        await d.close()

    async def test_transient_increments_consecutive_failures(self) -> None:
        """Each TRANSIENT increments _consecutive_failures."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(503),  # failures → 1
                _make_response(503),  # failures → 2
                _make_response(503),  # failures → 3
                _make_response(200),  # delivered
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # After delivery, counter resets to 0
        assert d._consecutive_failures == 0
        await d.close()


# ---------------------------------------------------------------------------
# TestWorkerAdvancesOnNonTransient
# ---------------------------------------------------------------------------
class TestWorkerAdvancesOnNonTransient:
    """Worker advances to the next event on _DELIVERED and _PERMANENT outcomes."""

    async def test_advances_on_delivered(self) -> None:
        """After _DELIVERED, worker picks up and delivers the next queued event."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(200),  # e1 → DELIVERED
                _make_response(200),  # e2 → DELIVERED
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        d.enqueue("e2", {"session_id": "s1"})

        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._client.post.await_count == 2
        await d.close()

    async def test_advances_on_permanent(self) -> None:
        """_PERMANENT skips the event without retry and processes the next one."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(403),  # e1 → PERMANENT (skip)
                _make_response(200),  # e2 → DELIVERED
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        d.enqueue("e2", {"session_id": "s1"})

        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # Only 2 posts: e1 (permanent, 1 attempt), e2 (delivered, 1 attempt)
        assert d._client.post.await_count == 2
        # _sleep_backoff never called — no TRANSIENT
        d._sleep_backoff.assert_not_awaited()  # type: ignore[union-attr]
        await d.close()

    async def test_current_cleared_after_permanent(self) -> None:
        """self._current is None after a PERMANENT outcome."""
        d = _dispatcher()
        d._client = _mock_client([_make_response(403)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._current is None
        await d.close()


# ---------------------------------------------------------------------------
# TestConsecutiveFailuresCounter
# ---------------------------------------------------------------------------
class TestConsecutiveFailuresCounter:
    """_consecutive_failures resets to 0 on success or PERMANENT; accumulates on TRANSIENT."""

    async def test_resets_to_zero_on_delivered(self) -> None:
        """Counter resets to 0 after a successful DELIVERED outcome."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(503),  # failures=1
                _make_response(503),  # failures=2
                _make_response(200),  # delivered → reset to 0
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._consecutive_failures == 0
        await d.close()

    async def test_resets_to_zero_on_permanent(self) -> None:
        """Counter resets to 0 after a PERMANENT outcome."""
        d = _dispatcher()
        d._client = _mock_client(
            [
                _make_response(503),  # failures=1
                _make_response(403),  # permanent → reset to 0
            ]
        )
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._consecutive_failures == 0
        await d.close()

    async def test_counter_reset_between_events(self) -> None:
        """e1 accumulates failures; after delivery, e2 starts fresh at counter=0."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=30.0, backoff_jitter=False)
        backoff_calls_per_event: list[int] = []

        async def capture_backoff() -> None:
            # Record the failure count at the time of each backoff call
            backoff_calls_per_event.append(d._consecutive_failures)

        d._sleep_backoff = capture_backoff  # type: ignore[method-assign]

        d._client = _mock_client(
            [
                _make_response(503),  # e1: failures=1
                _make_response(503),  # e1: failures=2
                _make_response(200),  # e1: delivered → reset
                _make_response(503),  # e2: failures=1 (fresh start)
                _make_response(200),  # e2: delivered
            ]
        )

        d.enqueue("e1", {"session_id": "s1"})
        d.enqueue("e2", {"session_id": "s1"})

        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # Backoff was called 3 times: failures=1, failures=2 (for e1), failures=1 (for e2 fresh)
        assert backoff_calls_per_event == [1, 2, 1]
        await d.close()


# ---------------------------------------------------------------------------
# TestSleepBackoffMethod
# ---------------------------------------------------------------------------
class TestSleepBackoffMethod:
    """Direct unit tests for _sleep_backoff() — backoff formula and jitter."""

    async def test_no_jitter_sequence_with_initial_1_max_10(self) -> None:
        """No-jitter sequence: 1.0, 2.0, 4.0, 8.0, 10.0 (capped at max=10)."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=10.0, backoff_jitter=False)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        # failures counts 1..5, exponent = failures - 1 → 0..4
        # cap = min(1.0 * 2^(f-1), 10.0)
        with patch("asyncio.sleep", new=capture):
            for failures in range(1, 6):
                d._consecutive_failures = failures
                await d._sleep_backoff()

        assert sleep_calls == [1.0, 2.0, 4.0, 8.0, 10.0]

    async def test_no_jitter_capped_at_backoff_max(self) -> None:
        """Delay never exceeds backoff_max regardless of failure count."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=5.0, backoff_jitter=False)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", new=capture):
            for failures in range(1, 6):
                d._consecutive_failures = failures
                await d._sleep_backoff()

        # failures 4 → cap=8.0 → clamped to 5.0; failures 5 → cap=16.0 → clamped to 5.0
        assert sleep_calls == [1.0, 2.0, 4.0, 5.0, 5.0]

    async def test_jitter_delay_in_range_zero_to_cap(self) -> None:
        """With jitter=True the delay is in [0, cap]; full-jitter means uniform random."""
        d = _dispatcher(backoff_initial=2.0, backoff_max=100.0, backoff_jitter=True)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", new=capture):
            d._consecutive_failures = 1  # cap = min(2.0 * 2^0, 100.0) = 2.0
            await d._sleep_backoff()

        assert len(sleep_calls) == 1
        assert 0.0 <= sleep_calls[0] <= 2.0

    async def test_jitter_delay_multiple_calls_in_range(self) -> None:
        """Multiple jittered delays are all within their respective caps."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=100.0, backoff_jitter=True)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", new=capture):
            for failures in range(1, 5):
                d._consecutive_failures = failures
                await d._sleep_backoff()

        # Caps: 1.0, 2.0, 4.0, 8.0
        caps = [1.0, 2.0, 4.0, 8.0]
        for delay, cap in zip(sleep_calls, caps):
            assert 0.0 <= delay <= cap, f"delay {delay} outside [0, {cap}]"

    async def test_initial_equals_backoff_initial_at_one_failure(self) -> None:
        """At failures=1 (first retry), delay equals backoff_initial (no jitter)."""
        d = _dispatcher(backoff_initial=3.5, backoff_max=100.0, backoff_jitter=False)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", new=capture):
            d._consecutive_failures = 1
            await d._sleep_backoff()

        assert sleep_calls == [3.5]

    async def test_large_failure_count_stays_capped(self) -> None:
        """Very large failure counts never exceed backoff_max."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=30.0, backoff_jitter=False)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", new=capture):
            d._consecutive_failures = 100  # astronomically large
            await d._sleep_backoff()

        assert sleep_calls == [30.0]


# ---------------------------------------------------------------------------
# TestBackoffSequenceEndToEnd
# ---------------------------------------------------------------------------
class TestBackoffSequenceEndToEnd:
    """End-to-end: backoff sequence via real _sleep_backoff() calls inside _worker()."""

    async def test_worker_sleeps_correct_sequence_no_jitter(self) -> None:
        """Worker calls _sleep_backoff with increasing failure counts; no-jitter delays match."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=10.0, backoff_jitter=False)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        d._client = _mock_client(
            [
                _make_response(503),  # failures=1 → sleep 1.0
                _make_response(503),  # failures=2 → sleep 2.0
                _make_response(503),  # failures=3 → sleep 4.0
                _make_response(200),  # delivered
            ]
        )

        with patch("asyncio.sleep", new=capture):
            d.enqueue("e1", {"session_id": "s1"})
            # Run until queue drained; asyncio.sleep is patched to be instant
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert sleep_calls == [1.0, 2.0, 4.0]
        await d.close()

    async def test_worker_counter_resets_correctly_end_to_end(self) -> None:
        """After e1 delivered (counter reset), e2's first failure starts backoff from initial."""
        d = _dispatcher(backoff_initial=1.0, backoff_max=30.0, backoff_jitter=False)
        sleep_calls: list[float] = []

        async def capture(delay: float) -> None:
            sleep_calls.append(delay)

        d._client = _mock_client(
            [
                _make_response(503),  # e1: failures=1 → sleep 1.0
                _make_response(503),  # e1: failures=2 → sleep 2.0
                _make_response(200),  # e1: delivered → counter reset to 0
                _make_response(503),  # e2: failures=1 → sleep 1.0 (reset!)
                _make_response(200),  # e2: delivered
            ]
        )

        with patch("asyncio.sleep", new=capture):
            d.enqueue("e1", {"session_id": "s1"})
            d.enqueue("e2", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # e1: [1.0, 2.0], e2: [1.0] — counter reset proves fresh start
        assert sleep_calls == [1.0, 2.0, 1.0]
        await d.close()


# ---------------------------------------------------------------------------
# TestWorkerSupervisorUnclassifiedExceptions
# ---------------------------------------------------------------------------
class TestWorkerSupervisorUnclassifiedExceptions:
    """Worker survives unclassified exceptions — Task 6 / TB-01.

    If _post raises an unclassified exception the worker must:
    - log loudly (logger.exception or logger.error)
    - drop the poisoned event (task_done + clear _current)
    - re-enter the outer loop (keep draining)

    CancelledError must still propagate so close() works.
    """

    async def test_worker_survives_unclassified_exception(self) -> None:
        """Poison event raises ValueError; subsequent events still arrive.

        fake_post raises ValueError('unclassified boom') for event 'poison'
        but returns _DELIVERED for other events. After the exception:
        - loud log fires (logger.exception or logger.error called)
        - 'poison' is dropped (not retried forever)
        - 'good1' and 'good2' are processed (worker keeps draining)
        """
        d = _dispatcher()
        d._sleep_backoff = AsyncMock()  # no backoff delays

        received: list[str] = []

        async def fake_post(event: str, data: dict[str, Any]) -> str:
            if event == "poison":
                raise ValueError("unclassified boom")
            received.append(event)
            return _DELIVERED

        d._post = fake_post  # type: ignore[method-assign]

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"
        ) as mock_logger:
            d.enqueue("poison", {"session_id": "s1"})
            d.enqueue("good1", {"session_id": "s1"})
            d.enqueue("good2", {"session_id": "s1"})

            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # Loud log must have fired (exception or error)
        assert mock_logger.exception.called or mock_logger.error.called, (
            "Expected logger.exception or logger.error to be called for unclassified exception"
        )
        # Poison dropped, good events processed in order
        assert received == ["good1", "good2"], (
            f"Expected ['good1', 'good2'] but got {received}"
        )
        await d.close()

    async def test_cancelled_error_not_swallowed(self) -> None:
        """CancelledError from _post propagates — it is not swallowed into the loop.

        If CancelledError were swallowed, the worker would keep looping and sit
        at await queue.get() forever (worker.done() == False). If it propagates
        correctly, the worker exits (worker.done() == True).
        """
        d = _dispatcher()

        async def fake_post_raise_cancelled(event: str, data: dict[str, Any]) -> str:
            raise asyncio.CancelledError()

        d._post = fake_post_raise_cancelled  # type: ignore[method-assign]

        # Put directly in queue — do NOT use enqueue() which spawns an internal
        # worker that would race with the test worker for the queue item.
        d._queue.put_nowait(("e1", {"session_id": "s1"}))

        # Start a standalone worker task.
        worker = asyncio.create_task(d._worker())

        # Give the worker time to dequeue "e1" and hit CancelledError.
        await asyncio.sleep(0.1)

        # If CancelledError propagated, the worker exited → done() is True.
        # If CancelledError was swallowed, the worker loops back to queue.get()
        # (blocks forever on empty queue) → done() is False.
        assert worker.done(), (
            "Worker is still running after CancelledError from _post — "
            "CancelledError was swallowed instead of propagating"
        )

        # Clean up the finished worker task to suppress any unhandled-exception warning.
        try:
            await worker
        except (asyncio.CancelledError, Exception):
            pass

        await d.close()
