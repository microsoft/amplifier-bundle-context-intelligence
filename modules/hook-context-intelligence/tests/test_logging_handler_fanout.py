"""Tests for LoggingHandler fan-out behavior after the _DestinationDispatcher refactor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    LoggingHandler,
    _DestinationDispatcher,
)


class _FakeResolver:
    """Minimal resolver for LoggingHandler."""

    def __init__(self, base_path: Path, project_slug: str = "proj") -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.workspace: str | None = "ws"
        self.parent_id: str = ""
        self.resolve_instance_id: str = ""

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


class TestJSONLAlwaysWritten:
    """JSONL is always written regardless of dispatcher state (D10)."""

    async def test_jsonl_written_with_zero_dispatchers(self, tmp_path: Path) -> None:
        handler = LoggingHandler(_FakeResolver(tmp_path))
        # No dispatchers installed — local JSONL only
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )
        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists(), "JSONL must be written even with zero dispatchers"
        record = json.loads(jsonl_path.read_text().strip())
        assert record["event"] == "session:start"

    async def test_jsonl_written_with_dispatchers(self, tmp_path: Path) -> None:
        handler = LoggingHandler(_FakeResolver(tmp_path))
        # Install mock dispatchers
        mock_d = MagicMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_d])
        await handler(
            "session:start",
            {"session_id": "s2", "timestamp": "t0", "working_dir": "/w"},
        )
        jsonl_path = tmp_path / "proj" / "sessions" / "s2" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists()


class TestFanOutToDispatchers:
    """__call__ fans out to ALL installed dispatchers."""

    async def test_enqueue_called_on_all_dispatchers(self, tmp_path: Path) -> None:
        handler = LoggingHandler(_FakeResolver(tmp_path))
        mock_a = MagicMock(spec=_DestinationDispatcher)
        mock_b = MagicMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_a, mock_b])

        await handler(
            "session:start",
            {"session_id": "s3", "timestamp": "t0", "working_dir": "/w"},
        )

        mock_a.enqueue.assert_called_once()
        mock_b.enqueue.assert_called_once()
        # Same event and sanitized data passed to both
        assert mock_a.enqueue.call_args == mock_b.enqueue.call_args

    async def test_no_dispatcher_no_enqueue(self, tmp_path: Path) -> None:
        handler = LoggingHandler(_FakeResolver(tmp_path))
        # No set_dispatchers call — default is empty list
        await handler(
            "session:start",
            {"session_id": "s4", "timestamp": "t0", "working_dir": "/w"},
        )
        # No assertion needed — just confirm no AttributeError raised

    async def test_set_dispatchers_replaces_list(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        handler = LoggingHandler(_FakeResolver(tmp_path))
        # Use AsyncMock so close() is awaitable when set_dispatchers closes old dispatchers.
        mock_a = AsyncMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_a])
        assert len(handler._dispatchers) == 1

        mock_b = AsyncMock(spec=_DestinationDispatcher)
        mock_c = AsyncMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_b, mock_c])
        assert len(handler._dispatchers) == 2
        # mock_a.close() must have been called when dispatchers were replaced
        mock_a.close.assert_awaited_once()
