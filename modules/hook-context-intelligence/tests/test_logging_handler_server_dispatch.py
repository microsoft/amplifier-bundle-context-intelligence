"""Tests for LoggingHandler server dispatch feature.

Covers server-dispatch behavior added in the feat/server-dispatch branch:
- No dispatch when context_intelligence_server_url is absent
- asyncio.create_task is called when context_intelligence_server_url is present
- JSONL writing is unaffected in both cases
- HTTP failures are caught and logged as warnings
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


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
        dispatch_timeout: float = 30.0,
        dispatch_failure_threshold: int = 3,
    ) -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.context_intelligence_server_url = context_intelligence_server_url
        self.workspace = workspace
        self.dispatch_timeout = dispatch_timeout
        self.dispatch_failure_threshold = dispatch_failure_threshold

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


# ---------------------------------------------------------------------------
# TestServerDispatchDisabled
# ---------------------------------------------------------------------------
class TestServerDispatchDisabled:
    """No context_intelligence_server_url means no HTTP dispatch occurs."""

    async def test_no_dispatch_without_server_url(self, tmp_path: Path) -> None:
        """asyncio.create_task is NOT called when resolver has no context_intelligence_server_url."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

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
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

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
    """With context_intelligence_server_url set, asyncio.create_task is called for HTTP dispatch."""

    async def test_dispatch_creates_task(self, tmp_path: Path) -> None:
        """asyncio.create_task is called once when context_intelligence_server_url is configured."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="myws",
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
        """JSONL is written even when context_intelligence_server_url dispatch is active."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="myws",
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
    """HTTP failures in _dispatch_to_server are caught and logged as warnings with exc_info."""

    async def test_http_failure_logs_warning_with_exc_info(self, tmp_path: Path) -> None:
        """An exception during dispatch is logged as a warning with exc_info=True."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                workspace="myws",
            )
        )

        # Inject a mock client that raises on .post()
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("connection refused")
        handler._client = mock_client

        logger_name = "amplifier_module_hook_context_intelligence.handlers.logging_handler"
        with patch.object(logging.getLogger(logger_name), "warning") as mock_warning:
            await handler._dispatch_to_server(
                "session:start", {"session_id": "s1", "timestamp": "t0"}
            )

        mock_warning.assert_called_once()
        call_kwargs = mock_warning.call_args[1]
        assert call_kwargs.get("exc_info") is True


# ---------------------------------------------------------------------------
# TestCircuitBreaker
# ---------------------------------------------------------------------------
_LOGGER_NAME = "amplifier_module_hook_context_intelligence.handlers.logging_handler"


class TestCircuitBreaker:
    """Circuit breaker permanently disables dispatch after threshold consecutive failures."""

    async def test_trips_after_threshold_failures(self, tmp_path: Path) -> None:
        """3 consecutive failures trip the circuit breaker (_dispatch_enabled=False, _consecutive_failures=3)."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "warning"):
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is False
        assert handler._consecutive_failures == 3

    async def test_resets_on_success(self, tmp_path: Path) -> None:
        """2 failures then a success resets _consecutive_failures to 0 and keeps _dispatch_enabled=True."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
            )
        )

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.post.side_effect = [
            Exception("fail"),
            Exception("fail"),
            mock_response,
        ]
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "warning"):
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._consecutive_failures == 0
        assert handler._dispatch_enabled is True

    async def test_disabled_dispatch_is_silent(self, tmp_path: Path) -> None:
        """After the circuit trips, subsequent calls make no HTTP requests and log no warnings."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        # Trip the circuit breaker
        with patch.object(logging.getLogger(_LOGGER_NAME), "warning"):
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is False

        # Reset mock state
        mock_client.post.reset_mock()
        mock_client.post.side_effect = None

        # After tripping, subsequent calls should be silent
        with patch.object(logging.getLogger(_LOGGER_NAME), "warning") as mock_warning:
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        mock_client.post.assert_not_called()
        mock_warning.assert_not_called()

    async def test_trip_emits_final_warning(self, tmp_path: Path) -> None:
        """Exactly 1 warning containing 'dispatch disabled' is emitted when circuit trips."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "warning") as mock_warning:
            for _ in range(3):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        # Count warnings containing "dispatch disabled"
        disabled_warnings = [
            call for call in mock_warning.call_args_list if "dispatch disabled" in str(call)
        ]
        assert len(disabled_warnings) == 1

    async def test_configurable_threshold(self, tmp_path: Path) -> None:
        """threshold=5: 4 failures leaves dispatch enabled, 5th failure trips it."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=5,
            )
        )

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("conn refused")
        handler._client = mock_client

        with patch.object(logging.getLogger(_LOGGER_NAME), "warning"):
            for _ in range(4):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is True  # 4 failures, threshold not reached

        with patch.object(logging.getLogger(_LOGGER_NAME), "warning"):
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._dispatch_enabled is False  # 5th failure trips it

    async def test_non_2xx_treated_as_failure(self, tmp_path: Path) -> None:
        """HTTPStatusError from raise_for_status() increments the failure counter."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_failure_threshold=3,
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

        with patch.object(logging.getLogger(_LOGGER_NAME), "warning"):
            await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        assert handler._consecutive_failures == 1


# ---------------------------------------------------------------------------
# TestPersistentClient
# ---------------------------------------------------------------------------
class TestPersistentClient:
    """Persistent httpx.AsyncClient created lazily, reused, and closed on session end."""

    def test_lazy_client_creation(self, tmp_path: Path) -> None:
        """_client is None immediately after __init__."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

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
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
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
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
            )
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock(return_value=None))

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_ctor:
            for _ in range(5):
                await handler._dispatch_to_server("session:start", {"session_id": "s1"})

        mock_ctor.assert_called_once()
        assert mock_client.post.call_count == 5

    async def test_client_created_with_configured_timeout(self, tmp_path: Path) -> None:
        """httpx.AsyncClient is constructed with timeout=httpx.Timeout(dispatch_timeout)."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
                dispatch_timeout=45.0,
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
        assert call_kwargs["timeout"] == httpx.Timeout(45.0)

    async def test_client_cleanup_on_finalize(self, tmp_path: Path) -> None:
        """_finalize_metadata schedules aclose() on the persistent client."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(
            _FakeResolver(
                tmp_path,
                "proj",
                context_intelligence_server_url="http://localhost:8080",
            )
        )

        mock_client = AsyncMock()
        handler._client = mock_client

        session_dir = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence"
        session_dir.mkdir(parents=True)

        handler._finalize_metadata(session_dir, {"status": "completed", "timestamp": "t1"})

        # Yield to let scheduled tasks run
        await asyncio.sleep(0)

        mock_client.aclose.assert_called_once()

    async def test_no_cleanup_when_no_client(self, tmp_path: Path) -> None:
        """_finalize_metadata works when _client is None; metadata is still written."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

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
