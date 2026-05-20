"""Tests for LoggingHandler server dispatch feature.

Covers server-dispatch behavior added in the feat/server-dispatch branch:
- No dispatch when context_intelligence_server_url is absent
- asyncio.create_task is called when context_intelligence_server_url is present
- JSONL writing is unaffected in both cases
- HTTP failures are caught and logged as debug messages (not warnings — remote server is optional)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    LoggingHandler,
    _compute_idempotency_key,
)


# ---------------------------------------------------------------------------
# _FakeResolver with optional context_intelligence_server_url / workspace
# ---------------------------------------------------------------------------
class _FakeResolver:
    """Minimal resolver adapter with optional context_intelligence_server_url and workspace."""

    def __init__(
        self,
        base_path: Path,
        project_slug: str,
        context_intelligence_server_url: str | None = None,
        workspace: str | None = None,
        dispatch_timeout: float = 10.0,
        dispatch_failure_threshold: int = 3,
        dispatch_queue_capacity: int = 256,
        close_drain_timeout: float = 0.5,
        context_intelligence_api_key: str | None = None,
        forwarding_enabled: bool = True,
    ) -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.context_intelligence_server_url = context_intelligence_server_url
        self.workspace = workspace
        self.dispatch_timeout = dispatch_timeout
        self.dispatch_failure_threshold = dispatch_failure_threshold
        self.dispatch_queue_capacity = dispatch_queue_capacity
        self.close_drain_timeout = close_drain_timeout
        self.context_intelligence_api_key = context_intelligence_api_key
        self.forwarding_enabled = forwarding_enabled

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


# ---------------------------------------------------------------------------
# TestServerDispatchDisabled
# ---------------------------------------------------------------------------
class TestServerDispatchDisabled:
    """No context_intelligence_server_url means no HTTP dispatch occurs."""

    async def test_no_dispatch_without_server_url(self, tmp_path: Path) -> None:
        """asyncio.create_task is NOT called when resolver has no context_intelligence_server_url."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))  # no server_url

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.asyncio.create_task"
        ) as mock_create_task:
            await handler(
                "session:start",
                {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
            )

        mock_create_task.assert_not_called()

    async def test_jsonl_still_written_without_server_url(self, tmp_path: Path) -> None:
        """JSONL is written even when no context_intelligence_server_url is configured."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))  # no server_url
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )

        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists()
        record = json.loads(jsonl_path.read_text().strip())
        assert record["event"] == "session:start"


# ---------------------------------------------------------------------------
# TestServerDispatchEnabled
# ---------------------------------------------------------------------------
class TestServerDispatchEnabled:
    """With context_intelligence_server_url and context_intelligence_api_key both set, asyncio.create_task is called for HTTP dispatch."""

    async def test_dispatch_creates_task(self, tmp_path: Path) -> None:
        """asyncio.create_task is called once when context_intelligence_server_url is configured."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="myws",
                context_intelligence_api_key="test-api-key",
            )
        )

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.asyncio.create_task"
        ) as mock_create_task:
            # side_effect closes the coroutine to avoid "never awaited" RuntimeWarning
            mock_create_task.side_effect = lambda coro: coro.close() or MagicMock()
            await handler(
                "session:start",
                {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
            )

        mock_create_task.assert_called_once()

    async def test_jsonl_still_written_with_server_url(self, tmp_path: Path) -> None:
        """JSONL is written even when context_intelligence_server_url dispatch and api_key are both active."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="myws",
                context_intelligence_api_key="test-api-key",
            )
        )

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.asyncio.create_task"
        ) as mock_create_task:
            mock_create_task.side_effect = lambda coro: coro.close() or MagicMock()
            await handler(
                "session:start",
                {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
            )

        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists()
        record = json.loads(jsonl_path.read_text().strip())
        assert record["event"] == "session:start"


# ---------------------------------------------------------------------------
# TestServerDispatchFailure
# ---------------------------------------------------------------------------
class TestServerDispatchFailure:
    """HTTP failures in _dispatch_to_server are caught and logged cleanly."""

    async def test_http_failure_logs_debug_not_warning(self, tmp_path: Path) -> None:
        """An exception during dispatch is logged at DEBUG level only — no WARNING emitted.

        Since the remote server is optional, failures are intentionally silent at WARNING
        level to avoid polluting the user's terminal. The failure is logged as a single
        DEBUG call with exc_info=True so the traceback is available to developers without
        cluttering the user's terminal with multiple debug messages.
        """
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="myws",
                context_intelligence_api_key="test-api-key",
            )
        )

        # Inject a mock client that raises on .post()
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("connection refused")
        handler._client = mock_client

        logger_name = "amplifier_module_hook_context_intelligence.handlers.logging_handler"
        with (
            patch.object(logging.getLogger(logger_name), "warning") as mock_warning,
            patch.object(logging.getLogger(logger_name), "debug") as mock_debug,
        ):
            await handler._dispatch_to_server(
                "session:start", {"session_id": "s1", "timestamp": "t0"}
            )

        # No WARNING emitted — remote server is optional, failures must not pollute user output
        mock_warning.assert_not_called()

        # At least one DEBUG call on failure; the last call carries exc_info=True so the
        # full traceback is available to developers inspecting debug logs.
        assert mock_debug.call_count >= 1
        last_debug_kwargs = mock_debug.call_args_list[-1][1]
        assert last_debug_kwargs.get("exc_info") is True


class TestIdempotencyKey:
    """Hook HTTP payloads carry deterministic idempotency keys."""

    def test_same_payload_produces_same_key(self) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-03-17T10:00:00.123456+00:00",
            "tool_call_id": "call-1",
            "payload": {"b": 2, "a": 1},
        }

        key_a = _compute_idempotency_key("tool:pre", "ws", data)
        key_b = _compute_idempotency_key("tool:pre", "ws", data)

        assert key_a == key_b

    def test_different_payload_produces_different_key(self) -> None:
        base = {
            "session_id": "s1",
            "timestamp": "2026-03-17T10:00:00.123456+00:00",
        }

        key_a = _compute_idempotency_key("tool:pre", "ws", {**base, "tool_call_id": "call-1"})
        key_b = _compute_idempotency_key("tool:pre", "ws", {**base, "tool_call_id": "call-2"})

        assert key_a != key_b


# ---------------------------------------------------------------------------
# TestCircuitBreaker
# ---------------------------------------------------------------------------
_LOGGER_NAME = "amplifier_module_hook_context_intelligence.handlers.logging_handler"


class TestCircuitBreaker:
    """Circuit breaker permanently disables dispatch after threshold consecutive failures."""

    async def test_trips_after_threshold_failures(self, tmp_path: Path) -> None:
        """3 consecutive failures trip the circuit breaker (_dispatch_enabled=False, _consecutive_failures=3)."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "debug"):
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is False
        assert handler._consecutive_failures == 3

    async def test_resets_on_success(self, tmp_path: Path) -> None:
        """2 failures then a success resets _consecutive_failures to 0 and keeps _dispatch_enabled=True."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = [
            Exception("fail"),
            Exception("fail"),
            mock_response,
        ]
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "debug"):
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._consecutive_failures == 0
        assert handler._dispatch_enabled is True

    async def test_disabled_dispatch_is_silent(self, tmp_path: Path) -> None:
        """After the circuit trips, subsequent calls make no HTTP requests and log no debug messages."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        # Trip the circuit breaker
        with patch.object(logging.getLogger(_LOGGER_NAME), "debug"):
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is False

        # Reset mock state
        mock_client.post.reset_mock()
        mock_client.post.side_effect = None

        # After tripping, subsequent calls should be fully silent
        with patch.object(logging.getLogger(_LOGGER_NAME), "debug") as mock_debug:
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        mock_client.post.assert_not_called()
        mock_debug.assert_not_called()

    async def test_trip_emits_final_debug(self, tmp_path: Path) -> None:
        """Exactly 1 debug message containing 'dispatch disabled' is emitted when circuit trips."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "debug") as mock_debug:
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        # Count debug calls containing "dispatch disabled" (the circuit-trip message)
        disabled_msgs = [
            call for call in mock_debug.call_args_list if "dispatch disabled" in str(call)
        ]
        assert len(disabled_msgs) == 1

    async def test_configurable_threshold(self, tmp_path: Path) -> None:
        """threshold=5: 4 failures leaves dispatch enabled, 5th failure trips it."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=5,
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "debug"):
            for _ in range(4):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is True  # 4 failures, threshold not reached

        with patch.object(logging.getLogger(_LOGGER_NAME), "debug"):
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is False  # 5th failure trips it

    async def test_non_2xx_treated_as_failure(self, tmp_path: Path) -> None:
        """HTTPStatusError from raise_for_status() increments the failure counter."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_request = httpx.Request("POST", "http://localhost:8080/events")
        mock_resp_obj = MagicMock()
        mock_resp_obj.status_code = 500
        status_error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=mock_request,
            response=mock_resp_obj,
        )

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = status_error

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "debug"):
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._consecutive_failures == 1


# ---------------------------------------------------------------------------
# TestPersistentClient
# ---------------------------------------------------------------------------
class TestPersistentClient:
    """Persistent httpx.AsyncClient created lazily, reused, and closed via close()."""

    def test_lazy_client_creation(self, tmp_path: Path) -> None:
        """_client is None immediately after __init__."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
            )
        )
        assert handler._client is None

    async def test_client_created_on_first_dispatch(self, tmp_path: Path) -> None:
        """httpx.AsyncClient constructor is called exactly once on first dispatch."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock(return_value=None))

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_ctor:
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        mock_ctor.assert_called_once()

    async def test_client_reused_across_dispatches(self, tmp_path: Path) -> None:
        """httpx.AsyncClient constructor called once; post() called 5 times across 5 dispatches."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock(return_value=None))

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_ctor:
            for _ in range(5):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        mock_ctor.assert_called_once()
        assert mock_client.post.call_count == 5

    async def test_client_created_with_phase_specific_timeout(self, tmp_path: Path) -> None:
        """httpx.AsyncClient uses short connect/read/pool timeouts and dispatch_timeout for writes."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_timeout=45.0,
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock(return_value=None))

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_ctor:
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        call_kwargs = mock_ctor.call_args[1]
        assert call_kwargs["timeout"] == httpx.Timeout(
            connect=0.5,
            write=45.0,
            read=3.0,
            pool=0.5,
        )

    async def test_dispatch_payload_includes_idempotency_key(self, tmp_path: Path) -> None:
        """Server POST payload includes a deterministic top-level idempotency_key."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="ws",
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = mock_response
        handler._client = mock_client

        data = {
            "session_id": "s1",
            "timestamp": "2026-03-17T10:00:00.123456+00:00",
            "tool_call_id": "call-1",
        }
        await handler._dispatch_to_server("tool:pre", data)

        payload = mock_client.post.await_args.kwargs["json"]
        assert payload["idempotency_key"] == _compute_idempotency_key("tool:pre", "ws", data)

    async def test_dispatch_payload_workspace_none_normalized_to_empty_string(
        self, tmp_path: Path
    ) -> None:
        """When workspace is None, the HTTP payload workspace field must be '' not None."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace=None,  # no workspace configured
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = mock_response
        handler._client = mock_client

        await handler._dispatch_to_server("tool:pre", {"session_id": "s1"})

        payload = mock_client.post.await_args.kwargs["json"]
        assert payload["workspace"] == "", (
            "workspace must be normalized to '' when resolver has no workspace, not None"
        )

    async def test_closed_client_silently_skips(self, tmp_path: Path) -> None:
        """A RuntimeError about a closed client is silently skipped, not counted as failure."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                context_intelligence_api_key="test-api-key",
            )
        )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = RuntimeError(
            "Cannot send a request, as the client has been closed."
        )
        handler._client = mock_client

        await handler._dispatch_to_server("session:end", {"session_id": "s1"})

        # Should NOT increment failure counter -- this is a teardown race, not a server issue
        assert handler._consecutive_failures == 0
        assert handler._dispatch_enabled is True

    async def test_no_cleanup_when_no_client(self, tmp_path: Path) -> None:
        """_finalize_metadata works when _client is None; metadata is still written."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))  # no server_url
        assert handler._client is None

        session_dir = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence"
        session_dir.mkdir(parents=True)

        # Should not raise even with no client
        handler._finalize_metadata(session_dir, {"status": "completed", "timestamp": "t1"})

        meta_path = session_dir / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["status"] == "completed"


# ---------------------------------------------------------------------------
# TestDispatchQueue
# ---------------------------------------------------------------------------
class TestDispatchQueue:
    """Server dispatch uses a single worker and a bounded queue."""

    async def test_queue_empty_on_init(self, tmp_path: Path) -> None:
        """The dispatch queue starts empty and no worker exists yet."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
            )
        )
        assert handler._dispatch_queue.qsize() == 0
        assert handler._dispatch_worker_task is None

    async def test_worker_created_on_dispatch(self, tmp_path: Path) -> None:
        """Calling __call__ with server_url creates the shared worker."""
        import asyncio

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="ws",
                context_intelligence_api_key="test-api-key",
            )
        )

        # Inject a mock client that succeeds
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock(return_value=None))
        handler._client = mock_client

        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )

        # Task was created — it may have already completed, but the set was
        # populated (tasks self-remove via done_callback).
        # Give the event loop two ticks: one for the task to finish,
        # one for the done_callback to fire and remove it from the set.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert handler._dispatch_worker_task is not None
        assert handler._dispatch_worker_task.done() is False
        assert handler._dispatch_queue.qsize() == 0

        await handler.close()

    async def test_no_worker_without_server_url(self, tmp_path: Path) -> None:
        """No worker is created when context_intelligence_server_url is absent."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )
        assert handler._dispatch_worker_task is None

    async def test_queue_full_disables_dispatch(self, tmp_path: Path) -> None:
        """A saturated queue disables dispatch instead of blocking the hook."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_queue_capacity=1,
            )
        )

        handler._ensure_dispatch_worker = lambda: None  # type: ignore[method-assign]
        handler._dispatch_queue.put_nowait(("session:start", {"session_id": "s1"}))

        await handler("tool:call", {"session_id": "s1", "timestamp": "t1"})

        assert handler._dispatch_enabled is False
        await handler.close()


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() drains queued dispatch work briefly and closes the HTTP client."""

    async def test_close_closes_client(self, tmp_path: Path) -> None:
        """close() calls aclose() on the HTTP client."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
            )
        )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        handler._client = mock_client

        await handler.close()

        mock_client.aclose.assert_awaited_once()

    async def test_close_safe_when_no_client(self, tmp_path: Path) -> None:
        """close() is a no-op when _client is None."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        assert handler._client is None

        # Should not raise
        await handler.close()

    async def test_close_safe_when_client_already_closed(self, tmp_path: Path) -> None:
        """close() skips aclose() when client is already closed."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
            )
        )

        mock_client = AsyncMock()
        mock_client.is_closed = True
        handler._client = mock_client

        await handler.close()

        mock_client.aclose.assert_not_awaited()

    async def test_close_drains_pending_dispatch(self, tmp_path: Path) -> None:
        """close() waits briefly for queued work before closing the client."""
        import asyncio

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="ws",
                context_intelligence_api_key="test-api-key",
            )
        )

        # Create a slow dispatch that takes a bit
        slow_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def slow_post(*args, **kwargs):
            await slow_future
            return MagicMock(raise_for_status=MagicMock(return_value=None))

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = slow_post
        handler._client = mock_client

        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )

        assert handler._dispatch_worker_task is not None

        # Resolve the slow future so the task can complete
        slow_future.set_result(None)

        await handler.close()

        assert handler._dispatch_worker_task is None

    async def test_close_cancels_straggler_worker(self, tmp_path: Path) -> None:
        """close() cancels the worker when queued dispatches exceed the drain timeout."""
        import asyncio

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="ws",
                close_drain_timeout=0.01,
                context_intelligence_api_key="test-api-key",
            )
        )

        # Create a task that will never complete on its own
        async def stuck_post(*args, **kwargs):
            await asyncio.sleep(999)

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = stuck_post
        handler._client = mock_client

        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )

        assert handler._dispatch_worker_task is not None

        await handler.close()

        assert handler._dispatch_worker_task is None


# ---------------------------------------------------------------------------
# TestFakeResolverApiKey
# ---------------------------------------------------------------------------
class TestFakeResolverApiKey:
    """_FakeResolver supports context_intelligence_api_key parameter."""

    def test_api_key_defaults_to_none(self, tmp_path: Path) -> None:
        """context_intelligence_api_key defaults to None (backward-compatible)."""
        resolver = _FakeResolver(tmp_path, "proj")
        assert resolver.context_intelligence_api_key is None

    def test_api_key_can_be_set(self, tmp_path: Path) -> None:
        """context_intelligence_api_key can be passed as a keyword argument."""
        resolver = _FakeResolver(tmp_path, "proj", context_intelligence_api_key="secret-key")
        assert resolver.context_intelligence_api_key == "secret-key"

    def test_api_key_after_close_drain_timeout(self, tmp_path: Path) -> None:
        """context_intelligence_api_key is the last param (after close_drain_timeout)."""
        resolver = _FakeResolver(
            tmp_path,
            "proj",
            close_drain_timeout=1.0,
            context_intelligence_api_key="my-api-key",
        )
        assert resolver.close_drain_timeout == 1.0
        assert resolver.context_intelligence_api_key == "my-api-key"


# ---------------------------------------------------------------------------
# TestAuthHeader
# ---------------------------------------------------------------------------
class TestAuthHeader:
    """httpx.AsyncClient includes Authorization: Bearer header when api_key is set."""

    async def test_client_created_with_auth_header_when_api_key_set(self, tmp_path: Path) -> None:
        """httpx.AsyncClient is constructed with headers={'Authorization': 'Bearer my-secret-token'}."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                context_intelligence_api_key="my-secret-token",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock(return_value=None))

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_ctor:
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        call_kwargs = mock_ctor.call_args[1]
        assert "headers" in call_kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer my-secret-token"

    async def test_no_client_created_when_api_key_absent(self, tmp_path: Path) -> None:
        """httpx.AsyncClient is never constructed when api_key is None (dispatch disabled at init)."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                # no api_key — dispatch is disabled in __init__
            )
        )

        assert handler._dispatch_enabled is False

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
        ) as mock_ctor:
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        mock_ctor.assert_not_called()

    async def test_dispatch_works_with_api_key(self, tmp_path: Path) -> None:
        """Full dispatch cycle with api_key set: post() is called and consecutive_failures == 0."""
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                context_intelligence_api_key="my-secret-token",
            )
        )

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = mock_response
        handler._client = mock_client

        await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._consecutive_failures == 0
        mock_client.post.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestMissingApiKey
# ---------------------------------------------------------------------------
class TestMissingApiKey:
    """When server URL is configured but api_key is missing, dispatch is disabled with a single debug log."""

    def test_server_url_set_api_key_none_disables_dispatch(self, tmp_path: Path) -> None:
        """_dispatch_enabled is False and debug logged once when server_url set but api_key is None."""
        with patch.object(logging.getLogger(_LOGGER_NAME), "debug") as mock_debug:
            handler = LoggingHandler(
                _FakeResolver(
                    tmp_path,
                    "proj",
                    context_intelligence_server_url="http://localhost:8080",
                    context_intelligence_api_key=None,
                )
            )
        assert handler._dispatch_enabled is False
        mock_debug.assert_called_once()
        assert "api_key is missing" in str(mock_debug.call_args)

    def test_server_url_set_api_key_empty_string_disables_dispatch(self, tmp_path: Path) -> None:
        """_dispatch_enabled is False and debug logged once when server_url set but api_key is empty string."""
        with patch.object(logging.getLogger(_LOGGER_NAME), "debug") as mock_debug:
            handler = LoggingHandler(
                _FakeResolver(
                    tmp_path,
                    "proj",
                    context_intelligence_server_url="http://localhost:8080",
                    context_intelligence_api_key="",
                )
            )
        assert handler._dispatch_enabled is False
        mock_debug.assert_called_once()
        assert "api_key is missing" in str(mock_debug.call_args)

    def test_server_url_set_api_key_valid_enables_dispatch(self, tmp_path: Path) -> None:
        """_dispatch_enabled is True and no warning when server_url and api_key are both set."""
        with patch.object(logging.getLogger(_LOGGER_NAME), "warning") as mock_warning:
            handler = LoggingHandler(
                _FakeResolver(
                    tmp_path,
                    "proj",
                    context_intelligence_server_url="http://localhost:8080",
                    context_intelligence_api_key="valid-key",
                )
            )
        assert handler._dispatch_enabled is True
        mock_warning.assert_not_called()

    def test_no_server_url_no_api_key_dispatch_enabled_no_warning(self, tmp_path: Path) -> None:
        """_dispatch_enabled is False and no warning when server_url is None (no server configured)."""
        with patch.object(logging.getLogger(_LOGGER_NAME), "warning") as mock_warning:
            handler = LoggingHandler(
                _FakeResolver(
                    tmp_path,
                    "proj",
                    # no server_url
                    context_intelligence_api_key=None,
                )
            )
        assert handler._dispatch_enabled is False
        mock_warning.assert_not_called()


# ---------------------------------------------------------------------------
# TestFakeResolverForwardingEnabled
# ---------------------------------------------------------------------------
class TestFakeResolverForwardingEnabled:
    """_FakeResolver must expose forwarding_enabled (default True) for gate tests."""

    def test_forwarding_enabled_defaults_to_true(self, tmp_path: Path) -> None:
        """_FakeResolver.forwarding_enabled is True by default."""
        resolver = _FakeResolver(tmp_path, "proj")
        assert resolver.forwarding_enabled is True

    def test_forwarding_enabled_can_be_set_to_false(self, tmp_path: Path) -> None:
        """_FakeResolver.forwarding_enabled can be set to False."""
        resolver = _FakeResolver(tmp_path, "proj", forwarding_enabled=False)
        assert resolver.forwarding_enabled is False
