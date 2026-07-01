"""Tests for _DestinationDispatcher — per-destination HTTP client, queue, and circuit breaker (D9)."""

from __future__ import annotations

import asyncio
import pytest
import httpx
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DELIVERED,
    _READ_TIMEOUT,
    _TRANSIENT,
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

    def test_backoff_and_storage_params_stored(self) -> None:
        """Backoff knobs and storage_path are plumbed through to instance attributes."""
        d = _DestinationDispatcher(
            name="test",
            url="http://localhost:8080",
            api_key="test-key",
            workspace="ws",
            dispatch_timeout=10.0,
            failure_threshold=3,
            queue_capacity=256,
            close_drain_timeout=0.5,
            backoff_initial=2.0,
            backoff_max=20.0,
            backoff_jitter=False,
            storage_path="/tmp/ci-sessions",
        )
        assert d._backoff_initial == 2.0
        assert d._backoff_max == 20.0
        assert d._backoff_jitter is False
        assert str(d._storage_path) == "/tmp/ci-sessions"


class TestDispatcherEnqueue:
    async def test_enqueue_starts_worker(self) -> None:
        d = _dispatcher()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
        d._client = mock_client

        d.enqueue("session:start", {"session_id": "s1"})
        await asyncio.sleep(0)  # let worker start
        assert d._worker_task is not None
        await d.close()


class TestDispatcherBreaker:
    """_post return values: TRANSIENT on network errors, DELIVERED on success/teardown.

    Migrated from the old consecutive-failures counter model to the new three-way
    outcome return type (_DELIVERED | _TRANSIENT | _PERMANENT). _consecutive_failures
    management now lives in the worker loop (Task 5).
    """

    async def test_transient_then_delivered(self) -> None:
        """Two ConnectError calls return _TRANSIENT; the final 200 returns _DELIVERED."""
        d = _dispatcher(failure_threshold=5)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = [
            httpx.ConnectError("conn refused"),
            httpx.ConnectError("conn refused"),
            mock_response,
        ]
        d._client = mock_client

        results = []
        for _ in range(3):
            results.append(await d._post("session:start", {"session_id": "s1"}))

        assert results[0] == _TRANSIENT
        assert results[1] == _TRANSIENT
        assert results[2] == _DELIVERED

    async def test_closed_client_runtime_error_returns_delivered(self) -> None:
        """RuntimeError('client has been closed') is treated as teardown — returns _DELIVERED."""
        d = _dispatcher()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = RuntimeError(
            "Cannot send a request, as the client has been closed."
        )
        d._client = mock_client

        result = await d._post("session:end", {"session_id": "s1"})
        assert result == _DELIVERED


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
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response
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


class TestNoPermanentLatch:
    """_DestinationDispatcher must NEVER permanently disable via a boolean latch."""

    def test_no_enabled_attribute(self) -> None:
        d = _dispatcher()
        assert not hasattr(d, "_enabled")

    def test_degraded_warned_false_on_init(self) -> None:
        d = _dispatcher()
        assert d._degraded_warned is False

    def test_current_none_on_init(self) -> None:
        d = _dispatcher()
        assert d._current is None

    def test_overflow_dropped_zero_on_init(self) -> None:
        d = _dispatcher()
        assert d._overflow_dropped == 0

    def test_auth_failures_zero_on_init(self) -> None:
        d = _dispatcher()
        assert d._auth_failures == 0

    def test_log_gates_zero_on_init(self) -> None:
        d = _dispatcher()
        assert d._last_overflow_log == 0.0
        assert d._last_permanent_log == 0.0

    def test_full_queue_does_not_disable(self) -> None:
        """Queue overflow bumps _overflow_dropped but NEVER sets _enabled=False (which no longer exists)."""
        d = _dispatcher(queue_capacity=1)
        # Prevent worker from draining
        d._ensure_worker = lambda: None  # type: ignore[method-assign]
        d._queue.put_nowait(("dummy", {}))
        d.enqueue("overflow", {"session_id": "s1"})
        assert d._overflow_dropped == 1
        assert d._queue.qsize() == 1
        assert not hasattr(d, "_enabled")


class TestDestinationIsolation:
    """Failures in one destination must NOT permanently disable another."""

    async def test_one_degraded_destination_does_not_affect_another(self) -> None:
        """dest B delivers ['e1','e2'] while dest A is down."""
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
        ok_response = MagicMock()
        ok_response.status_code = 200
        mock_client_b.post.return_value = ok_response
        d_b._client = mock_client_b

        events = ["e1", "e2"]
        with patch("amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"):
            for e in events:
                d_a.enqueue(e, {"session_id": "s1"})
                d_b.enqueue(e, {"session_id": "s1"})
            # Allow workers to process
            await asyncio.sleep(0.1)

        # B should have delivered both events; A degraded but not disabled
        assert mock_client_b.post.await_count == len(events)
        assert not hasattr(d_a, "_enabled")
        assert not hasattr(d_b, "_enabled")


class TestReadTimeout:
    """_DestinationDispatcher read_timeout plumbing."""

    def test_dispatcher_read_timeout_defaults_to_legacy_constant(self) -> None:
        """Omitting read_timeout defaults _read_timeout to _READ_TIMEOUT (3.0)."""
        d = _dispatcher()
        assert d._read_timeout == _READ_TIMEOUT

    async def test_dispatcher_uses_configured_read_timeout_in_client(self) -> None:
        """Passing read_timeout=20.0 stores it and uses it in the httpx.Timeout read field."""
        d = _DestinationDispatcher(
            name="test",
            url="http://localhost:8080",
            api_key="test-key",
            workspace="ws",
            dispatch_timeout=10.0,
            failure_threshold=3,
            queue_capacity=256,
            close_drain_timeout=0.5,
            read_timeout=20.0,
        )
        assert d._read_timeout == 20.0

        captured_timeout: list[httpx.Timeout] = []

        async def _capture_client_timeout(*args: Any, **kwargs: Any) -> None:
            captured_timeout.append(kwargs.get("timeout") or (args[0] if args else None))
            raise httpx.ConnectError("capture only")

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = _capture_client_timeout
        d._client = mock_client

        # Drive _post once to trigger the httpx.Timeout build; expect TRANSIENT.
        result = await d._post("session:start", {"session_id": "s1"})
        assert result == _TRANSIENT

        # Now verify the client was built with the right read and write timeouts.
        # The client creation is lazy—it happens before the first post.
        # We patch httpx.AsyncClient to capture the timeout at construction.
        captured_build: list[httpx.Timeout] = []

        original_async_client = httpx.AsyncClient

        class _CapturingClient:
            def __init__(self, *a: Any, **kw: Any) -> None:
                t = kw.get("timeout")
                if isinstance(t, httpx.Timeout):
                    captured_build.append(t)
                self._inner = original_async_client(*a, **kw)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
            _CapturingClient,
        ):
            # Reset client to None so _post triggers creation.
            d._client = None
            await d._post("session:start", {"session_id": "s1"})

        assert len(captured_build) == 1, "httpx.AsyncClient must be constructed once"
        t = captured_build[0]
        assert t.read == 20.0, f"expected read=20.0, got {t.read}"
        assert t.write == 10.0, f"expected write=10.0, got {t.write}"
