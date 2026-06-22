"""Tests for _DestinationDispatcher — per-destination HTTP client, queue, and circuit breaker (D9)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DestinationDispatcher,
)


def _dispatcher(
    name: str = "test",
    url: str = "http://localhost:8080",
    api_key: str = "test-key",
    workspace: str | None = "ws",
    dispatch_timeout: float = 10.0,
    failure_threshold: int = 3,
    queue_capacity: int = 256,
    close_drain_timeout: float = 0.5,
) -> _DestinationDispatcher:
    return _DestinationDispatcher(
        name=name,
        url=url,
        api_key=api_key,
        workspace=workspace,
        dispatch_timeout=dispatch_timeout,
        failure_threshold=failure_threshold,
        queue_capacity=queue_capacity,
        close_drain_timeout=close_drain_timeout,
    )


class TestDispatcherInit:
    def test_enabled_on_init(self) -> None:
        d = _dispatcher()
        assert d._enabled is True

    def test_client_none_on_init(self) -> None:
        d = _dispatcher()
        assert d._client is None

    def test_worker_none_on_init(self) -> None:
        d = _dispatcher()
        assert d._worker_task is None

    def test_queue_empty_on_init(self) -> None:
        d = _dispatcher()
        assert d._queue.qsize() == 0

    def test_url_trailing_slash_stripped(self) -> None:
        d = _dispatcher(url="http://localhost:8080/")
        assert d._url == "http://localhost:8080"


class TestDispatcherEnqueue:
    async def test_enqueue_starts_worker(self) -> None:
        d = _dispatcher()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock())
        d._client = mock_client

        d.enqueue("session:start", {"session_id": "s1"})
        await asyncio.sleep(0)  # let worker start
        assert d._worker_task is not None
        await d.close()

    def test_disabled_dispatcher_does_not_enqueue(self) -> None:
        d = _dispatcher()
        d._enabled = False
        d.enqueue("session:start", {"session_id": "s1"})
        assert d._queue.qsize() == 0

    async def test_full_queue_disables_dispatcher(self) -> None:
        d = _dispatcher(queue_capacity=1)
        # Prevent worker from draining
        d._ensure_worker = lambda: None  # type: ignore[method-assign]
        d._queue.put_nowait(("dummy", {}))
        d.enqueue("overflow", {"session_id": "s1"})
        assert d._enabled is False


class TestDispatcherBreaker:
    """Circuit breaker opens per-destination after failure_threshold consecutive failures."""

    async def test_breaker_opens_after_threshold(self) -> None:
        d = _dispatcher(failure_threshold=3)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = Exception("conn refused")
        d._client = mock_client

        with patch("amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"):
            for _ in range(3):
                await d._post("session:start", {"session_id": "s1"})

        assert d._enabled is False
        assert d._consecutive_failures == 3

    async def test_success_resets_counter(self) -> None:
        d = _dispatcher(failure_threshold=5)
        mock_response = MagicMock(raise_for_status=MagicMock())
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = [Exception("fail"), Exception("fail"), mock_response]
        d._client = mock_client

        with patch("amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"):
            for _ in range(3):
                await d._post("session:start", {"session_id": "s1"})

        assert d._consecutive_failures == 0
        assert d._enabled is True

    async def test_disabled_post_is_noop(self) -> None:
        d = _dispatcher(failure_threshold=3)
        d._enabled = False
        mock_client = AsyncMock()
        d._client = mock_client
        await d._post("session:start", {"session_id": "s1"})
        mock_client.post.assert_not_called()

    async def test_closed_client_runtime_error_skipped(self) -> None:
        d = _dispatcher()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = RuntimeError(
            "Cannot send a request, as the client has been closed."
        )
        d._client = mock_client

        await d._post("session:end", {"session_id": "s1"})
        assert d._consecutive_failures == 0
        assert d._enabled is True


class TestDispatcherIsolation:
    """Failures in dispatcher A must NOT affect dispatcher B."""

    async def test_breaker_a_does_not_affect_b(self) -> None:
        d_a = _dispatcher(name="a", failure_threshold=3)
        d_b = _dispatcher(name="b", failure_threshold=3)

        # A always fails
        mock_client_a = AsyncMock()
        mock_client_a.is_closed = False
        mock_client_a.post.side_effect = Exception("a is down")
        d_a._client = mock_client_a

        # B always succeeds
        mock_client_b = AsyncMock()
        mock_client_b.is_closed = False
        mock_client_b.post.return_value = MagicMock(raise_for_status=MagicMock())
        d_b._client = mock_client_b

        with patch("amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"):
            for _ in range(3):
                await d_a._post("session:start", {"session_id": "s1"})
            await d_b._post("session:start", {"session_id": "s1"})

        assert d_a._enabled is False, "A's breaker should be open"
        assert d_b._enabled is True, "B should be unaffected"
        mock_client_b.post.assert_awaited_once()


class TestSetDispatchersWorkerLeak:
    """set_dispatchers() must close previously-installed dispatchers (B: latent worker-leak fix)."""

    async def test_second_set_dispatchers_closes_first_batch(self) -> None:
        """Calling set_dispatchers a second time closes dispatchers from the first call."""
        import types
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        # Minimal resolver stub — LoggingHandler uses getattr() for all attrs.
        resolver = types.SimpleNamespace(
            workspace="ws",
            session_dir=lambda sid: __import__("pathlib").Path("/tmp") / sid,
        )

        handler = LoggingHandler(resolver)

        # First batch: two dispatchers with async close() mocks.
        d1 = _dispatcher(name="d1")
        d2 = _dispatcher(name="d2")

        close_calls: list[str] = []

        async def close_d1() -> None:
            close_calls.append("d1")

        async def close_d2() -> None:
            close_calls.append("d2")

        d1.close = close_d1  # type: ignore[method-assign]
        d2.close = close_d2  # type: ignore[method-assign]

        # Install first batch.
        await handler.set_dispatchers([d1, d2])
        assert handler._dispatchers == [d1, d2]
        assert close_calls == [], "first call must not close anything (no old dispatchers)"

        # Install second batch — first batch must be closed.
        d3 = _dispatcher(name="d3")
        await handler.set_dispatchers([d3])
        assert handler._dispatchers == [d3]
        assert sorted(close_calls) == ["d1", "d2"], (
            "set_dispatchers must close all dispatchers from the previous call"
        )

    async def test_first_set_dispatchers_is_noop_close(self) -> None:
        """First call to set_dispatchers (empty old list) does not try to close anything."""
        import types
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = types.SimpleNamespace(
            workspace="ws",
            session_dir=lambda sid: __import__("pathlib").Path("/tmp") / sid,
        )
        handler = LoggingHandler(resolver)
        assert handler._dispatchers == []

        d = _dispatcher(name="only")
        # Should not raise and should not close d (it was never installed before).
        await handler.set_dispatchers([d])
        assert handler._dispatchers == [d]


class TestDispatcherClose:
    async def test_close_drains_and_cancels_worker(self) -> None:
        d = _dispatcher(close_drain_timeout=1.0)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock())
        d._client = mock_client

        d.enqueue("session:start", {"session_id": "s1"})
        await asyncio.sleep(0)  # let worker start
        await d.close()

        assert d._worker_task is None

    async def test_close_safe_with_no_worker(self) -> None:
        d = _dispatcher()
        # No enqueue called, worker never started
        await d.close()  # should not raise

    async def test_close_acloses_client(self) -> None:
        d = _dispatcher()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        d._client = mock_client

        await d.close()
        mock_client.aclose.assert_awaited_once()

    async def test_close_skips_aclose_if_client_already_closed(self) -> None:
        d = _dispatcher()
        mock_client = AsyncMock()
        mock_client.is_closed = True
        d._client = mock_client

        await d.close()
        mock_client.aclose.assert_not_awaited()

    async def test_close_drain_timeout_cancels_straggler(self) -> None:
        d = _dispatcher(close_drain_timeout=0.01)

        async def stuck_post(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(999)

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = stuck_post
        d._client = mock_client

        d.enqueue("session:start", {"session_id": "s1"})
        await asyncio.sleep(0.05)  # let worker start and block

        with patch("amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"):
            await d.close()

        assert d._worker_task is None
