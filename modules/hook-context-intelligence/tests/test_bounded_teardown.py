"""Tests for bounded, non-blocking teardown of _DestinationDispatcher.close().

Regression coverage for a production hang: a host process (wiki-weaver engine
subprocess) finished its entire pipeline (pipeline:complete -> session:end) and
then wedged for 2.7 hours at 0% CPU without exiting. `ss` showed a single
socket in CLOSE-WAIT held by the process: httpx ``AsyncClient.aclose()`` was
blocked forever on a half-closed connection during the hook's final flush/close
-- there was no deadline on the teardown path. A related recurring signature:
``Task exception was never retrieved ... RuntimeError('Event loop is closed')``
from ``AsyncClient.aclose`` -- async teardown tasks dying unretrieved.

Contract under test:
  1. close() completes within a hard deadline even when aclose() never returns
     (including when aclose resists cancellation -- asyncio.wait, not wait_for).
  2. A teardown timeout emits a WARNING and returns cleanly: no hang, no
     exception escape. Delivery is best-effort and must never block exit.
  3. Delivery-path tasks get a done-callback that retrieves exceptions -- no
     'Task exception was never retrieved'.
  4. RuntimeError('Event loop is closed') during aclose is tolerated: logged at
     DEBUG, swallowed, never propagated.
  5. End-to-end: against a real server that accepts connections and never
     responds (hung-socket simulation), close() still returns within bound.

DESIGN NOTE -- backoff_initial=0.001 (not 0, not AsyncMock) follows
tests/test_shutdown.py: 0.0 overflows the exponent in a tight loop, and
AsyncMock never yields to the loop so cancel() is undeliverable.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any
from unittest.mock import patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DestinationDispatcher,
)

LOGGER_PATH = "amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"
HARD_TIMEOUT_PATH = (
    "amplifier_module_hook_context_intelligence.handlers.logging_handler._CLOSE_HARD_TIMEOUT"
)
STORAGE_PATH = "/tmp/ci-test-sessions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dispatcher(
    url: str = "http://localhost:8080",
    close_drain_timeout: float = 0.2,
    **kwargs: Any,
) -> _DestinationDispatcher:
    return _DestinationDispatcher(
        name="test-dest",
        url=url,
        api_key="test-key",
        workspace="ws",
        dispatch_timeout=10.0,
        failure_threshold=3,
        queue_capacity=256,
        close_drain_timeout=close_drain_timeout,
        backoff_initial=0.001,
        backoff_max=0.001,
        backoff_jitter=False,
        storage_path=STORAGE_PATH,
        **kwargs,
    )


class _HangingCloseClient:
    """httpx.AsyncClient stand-in whose aclose() hangs forever (cancellable)."""

    is_closed = False

    async def aclose(self) -> None:
        await asyncio.sleep(999)


class _CancelResistantCloseClient:
    """aclose() swallows the first cancellation and keeps running past the
    deadline -- the case where wait_for would block but asyncio.wait must not."""

    def __init__(self) -> None:
        self.is_closed = False
        self.finished = asyncio.Event()

    async def aclose(self) -> None:
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            # Resist cancellation for longer than the close deadline, then
            # finish so the test can reap the orphaned task cleanly.
            await asyncio.sleep(0.6)
            self.finished.set()
            raise


class _LoopClosedRaceClient:
    """aclose() raises the production teardown-race signature."""

    is_closed = False

    async def aclose(self) -> None:
        raise RuntimeError("Event loop is closed")


# ---------------------------------------------------------------------------
# 1 + 2: bounded aclose with warning, clean return
# ---------------------------------------------------------------------------


class TestBoundedAclose:
    async def test_close_returns_within_deadline_when_aclose_hangs(self) -> None:
        """A CLOSE-WAIT-style aclose hang must not block close() (the 2.7h bug)."""
        d = _dispatcher()
        d._client = _HangingCloseClient()  # type: ignore[assignment]

        with patch(HARD_TIMEOUT_PATH, 0.2), patch(LOGGER_PATH):
            t0 = time.monotonic()
            await d.close()  # must NOT raise and must NOT hang
            elapsed = time.monotonic() - t0

        assert elapsed < 1.5, (
            f"close() took {elapsed:.3f}s with a hung aclose(); expected < 1.5s "
            f"(hard deadline 0.2s). The teardown path is unbounded again."
        )

    async def test_aclose_timeout_emits_warning_and_returns_cleanly(self) -> None:
        """Deadline hit -> WARNING (abandon delivery), no exception escape."""
        d = _dispatcher()
        d._client = _HangingCloseClient()  # type: ignore[assignment]

        with patch(HARD_TIMEOUT_PATH, 0.1), patch(LOGGER_PATH) as mock_logger:
            await d.close()

        abandon_warnings = [
            c for c in mock_logger.warning.call_args_list if "HTTP client close exceeded" in str(c)
        ]
        assert len(abandon_warnings) == 1, (
            f"Expected exactly one abandon-teardown WARNING, "
            f"got warnings: {mock_logger.warning.call_args_list}"
        )

    async def test_close_bounded_even_when_aclose_resists_cancellation(self) -> None:
        """asyncio.wait semantics: close() must NOT wait for the cancellation of
        the abandoned aclose task to complete (wait_for would)."""
        d = _dispatcher()
        client = _CancelResistantCloseClient()
        d._client = client  # type: ignore[assignment]

        with patch(HARD_TIMEOUT_PATH, 0.2), patch(LOGGER_PATH):
            t0 = time.monotonic()
            await d.close()
            elapsed = time.monotonic() - t0

        # aclose resists its cancellation for 0.6s AFTER the 0.2s deadline; a
        # bounded close returns at ~0.2s without waiting that out.
        assert elapsed < 0.55, (
            f"close() took {elapsed:.3f}s -- it waited for the cancel-resistant "
            f"aclose to finish. Bounded teardown must abandon it at the deadline."
        )
        # Reap the orphaned task so the event loop shuts down clean.
        await asyncio.wait_for(client.finished.wait(), timeout=2.0)
        await asyncio.sleep(0)

    async def test_fast_aclose_still_closes_normally(self) -> None:
        """Behavior preserved: a healthy client closes with no warning."""
        d = _dispatcher()

        class _FastClient:
            is_closed = False
            closed = False

            async def aclose(self) -> None:
                self.closed = True

        client = _FastClient()
        d._client = client  # type: ignore[assignment]

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()
        await asyncio.sleep(0)  # let the done-callback run

        assert client.closed is True, "aclose() must still be awaited on the happy path"
        assert mock_logger.warning.call_args_list == [], (
            f"Clean teardown must emit no warnings: {mock_logger.warning.call_args_list}"
        )
        assert d._client is None, "client handle must be released after close()"


# ---------------------------------------------------------------------------
# 4: closed-event-loop teardown race tolerated
# ---------------------------------------------------------------------------


class TestClosedEventLoopRace:
    async def test_event_loop_closed_runtime_error_swallowed_at_debug(self) -> None:
        """The production 'Event loop is closed' aclose race: logged at DEBUG,
        swallowed, never propagated, never left unretrieved."""
        d = _dispatcher()
        d._client = _LoopClosedRaceClient()  # type: ignore[assignment]

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()  # must NOT raise
            # done-callbacks run via call_soon after the task completes
            await asyncio.sleep(0.01)

        debug_hits = [
            c for c in mock_logger.debug.call_args_list if "Event loop is closed" in str(c)
        ]
        assert len(debug_hits) >= 1, (
            f"Loop-closed race must be retrieved and logged at DEBUG, "
            f"got debug calls: {mock_logger.debug.call_args_list}"
        )
        warning_hits = [
            c for c in mock_logger.warning.call_args_list if "Event loop is closed" in str(c)
        ]
        assert warning_hits == [], (
            f"Loop-closed race is expected teardown noise -- must NOT be a WARNING: {warning_hits}"
        )


# ---------------------------------------------------------------------------
# 3: delivery-path task exceptions are retrieved
# ---------------------------------------------------------------------------


class TestWorkerTaskExceptionRetrieval:
    async def test_worker_crash_outside_supervisor_is_retrieved_and_logged(self) -> None:
        """A defect that escapes the worker's own supervisor must be retrieved
        by the done-callback -- never 'Task exception was never retrieved'."""
        d = _dispatcher()

        async def exploding_worker() -> None:
            raise ValueError("boom-outside-supervisor")

        d._worker = exploding_worker  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d._ensure_worker()
            await asyncio.sleep(0.05)  # let the task die and the callback run

        retrieved = [
            c for c in mock_logger.warning.call_args_list if "boom-outside-supervisor" in str(c)
        ]
        assert len(retrieved) == 1, (
            f"Worker crash must be retrieved and logged exactly once by the "
            f"done-callback, got warnings: {mock_logger.warning.call_args_list}"
        )
        # The exception was consumed by the callback: re-reading it must not raise
        # and asyncio will not report it as unretrieved at loop shutdown.
        assert d._worker_task is not None
        assert isinstance(d._worker_task.exception(), ValueError)

    async def test_cancelled_worker_produces_no_exception_warning(self) -> None:
        """Normal close-time cancellation is not an error: callback stays silent."""
        d = _dispatcher()
        d._ensure_worker()  # real worker, blocks on queue.get()
        await asyncio.sleep(0.02)

        with patch(LOGGER_PATH) as mock_logger:
            await d.close()
            await asyncio.sleep(0.01)

        callback_warnings = [
            c
            for c in mock_logger.warning.call_args_list
            if "unhandled exception retrieved" in str(c)
        ]
        assert callback_warnings == [], (
            f"Cancellation at close is expected -- the done-callback must not warn: "
            f"{callback_warnings}"
        )


# ---------------------------------------------------------------------------
# bounded worker join
# ---------------------------------------------------------------------------


class TestBoundedWorkerJoin:
    async def test_close_bounded_when_worker_swallows_cancellation(self) -> None:
        """A worker wedged in an uncancellable await must delay close() by at
        most the hard deadline -- then be abandoned with a WARNING."""
        d = _dispatcher(close_drain_timeout=0.05)
        finished = asyncio.Event()

        async def stubborn_worker() -> None:
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                await asyncio.sleep(0.6)  # resist beyond the 0.2s deadline
                finished.set()
                raise

        d._worker = stubborn_worker  # type: ignore[method-assign]
        d._ensure_worker()
        await asyncio.sleep(0.02)

        with patch(HARD_TIMEOUT_PATH, 0.2), patch(LOGGER_PATH) as mock_logger:
            t0 = time.monotonic()
            await d.close()
            elapsed = time.monotonic() - t0

        assert elapsed < 0.6, (
            f"close() took {elapsed:.3f}s -- it waited out a worker that swallows "
            f"cancellation instead of abandoning it at the 0.2s deadline."
        )
        abandon_warnings = [
            c for c in mock_logger.warning.call_args_list if "did not stop within" in str(c)
        ]
        assert len(abandon_warnings) == 1, (
            f"Expected one abandoned-worker WARNING, got: {mock_logger.warning.call_args_list}"
        )
        # Reap the orphan so the loop shuts down clean.
        await asyncio.wait_for(finished.wait(), timeout=2.0)
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# 5: end-to-end against a real accept-then-never-respond server
# ---------------------------------------------------------------------------


class TestEndToEndHungServer:
    async def test_close_bounded_against_server_that_never_responds(self) -> None:
        """Full stack (real httpx client, real socket): server accepts the
        connection, reads the request, never sends a byte back. The in-flight
        POST is bounded by the read timeout; teardown of the wedged connection
        is bounded by the hard deadline. close() must return promptly."""
        stop = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            with contextlib.suppress(Exception):
                await reader.read(65536)  # consume the request
                await stop.wait()  # never respond
            with contextlib.suppress(Exception):
                writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            d = _dispatcher(
                url=f"http://127.0.0.1:{port}",
                close_drain_timeout=0.3,
                connect_timeout=0.3,
                read_timeout=0.3,
            )
            d.enqueue("session:end", {"session_id": "s1"})
            # Let the worker open the connection and block on the response read.
            await asyncio.sleep(0.15)

            with patch(HARD_TIMEOUT_PATH, 1.0), patch(LOGGER_PATH):
                t0 = time.monotonic()
                await d.close()
                elapsed = time.monotonic() - t0

            # drain(0.3) + worker join(<=1.0) + aclose(<=1.0) plus overhead --
            # anything near the old behavior (hours) fails immediately; keep a
            # generous CI margin while still proving boundedness.
            assert elapsed < 3.0, (
                f"close() took {elapsed:.3f}s against a hung server; teardown is "
                f"not bounded end-to-end."
            )
            assert d._worker_task is None, "worker task must be cleaned up after close()"
        finally:
            stop.set()
            server.close()
            await server.wait_closed()
