"""Tests for LoggingHandler server dispatch feature.

Covers server-dispatch behavior added in the feat/server-dispatch branch:
- No dispatch when context_intelligence_server_url is absent
- asyncio.create_task is called when context_intelligence_server_url is present
- JSONL writing is unaffected in both cases
- HTTP failures are caught and logged as warnings
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


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
    ) -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.context_intelligence_server_url = context_intelligence_server_url
        self.workspace = workspace

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
    """HTTP failures in _dispatch_to_server are caught and logged as warnings."""

    async def test_http_failure_logs_warning(self, tmp_path: Path) -> None:
        """An httpx error during dispatch is logged as a warning with 'server_dispatch_failed'."""
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

        # Build a mock httpx.AsyncClient that raises on .post()
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("connection refused")

        mock_client_class = MagicMock()
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

        logger_name = "amplifier_module_hook_context_intelligence.handlers.logging_handler"
        with (
            patch(
                "amplifier_module_hook_context_intelligence.handlers.logging_handler.httpx.AsyncClient",
                mock_client_class,
            ),
            patch.object(logging.getLogger(logger_name), "warning") as mock_warning,
        ):
            # Call _dispatch_to_server directly to test its error-handling path
            await handler._dispatch_to_server(
                "session:start", {"session_id": "s1", "timestamp": "t0"}
            )

        mock_warning.assert_called_once()
        assert "server_dispatch_failed" in mock_warning.call_args[0][0]
