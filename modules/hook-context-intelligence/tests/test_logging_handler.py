"""Tests for LoggingHandler — flat JSONL session file writer.

Zero dependency on graph infrastructure. Tests file I/O only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from amplifier_core.models import HookResult


# ---------------------------------------------------------------------------
# _FakeResolver adapter
# ---------------------------------------------------------------------------
class _FakeResolver:
    """Minimal resolver adapter for testing LoggingHandler in isolation."""

    def __init__(self, base_path: Path, project_slug: str, workspace: str = "test-workspace") -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.workspace = workspace

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


# ---------------------------------------------------------------------------
# TestConstruction
# ---------------------------------------------------------------------------
class TestConstruction:
    """LoggingHandler can be constructed with minimal args."""

    def test_creates_handler(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        assert handler is not None

    def test_handled_events_starts_empty(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        assert handler.handled_events == set()

    def test_handled_events_is_mutable_set(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        assert isinstance(handler.handled_events, set)
        handler.handled_events.add("foo")
        assert "foo" in handler.handled_events


# ---------------------------------------------------------------------------
# TestSessionStart
# ---------------------------------------------------------------------------
class TestSessionStart:
    """session:start creates session dir and writes metadata + JSONL."""

    async def test_creates_session_dir(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/home/user/project",
            },
        )
        session_dir = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence"
        assert session_dir.is_dir()

    async def test_writes_metadata_json_with_correct_fields(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj", workspace="my-project"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "parent_id": "p1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/home/user/project",
            },
        )
        meta_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["format"] == "context-intelligence"
        assert meta["version"] == "1.0.0"
        assert meta["session_id"] == "s1"
        assert meta["workspace"] == "my-project"
        assert meta["parent_id"] == "p1"
        assert meta["started_at"] == "2026-01-15T10:00:00Z"
        assert meta["status"] == "running"
        assert meta["working_dir"] == "/home/user/project"

    async def test_includes_optional_fields_when_present(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
                "agent_name": "test-agent",
                "recipe_name": "my-recipe",
            },
        )
        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["agent_name"] == "test-agent"
        assert meta["recipe_name"] == "my-recipe"

    async def test_omits_optional_fields_when_absent(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert "agent_name" not in meta
        assert "parallel_group_id" not in meta
        assert "recipe_name" not in meta
        assert "recipe_step" not in meta

    async def test_appends_event_to_jsonl(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "session:start"

    async def test_returns_hook_result_continue(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        result = await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        assert isinstance(result, HookResult)
        assert result.action == "continue"


# ---------------------------------------------------------------------------
# TestSessionFork
# ---------------------------------------------------------------------------
class TestSessionFork:
    """session:fork creates session dir using 'parent' key (not 'parent_id')."""

    async def test_creates_session_dir_on_fork(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:fork",
            {
                "session_id": "child1",
                "parent": "parent1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        session_dir = tmp_path / "proj" / "sessions" / "child1" / "context-intelligence"
        assert session_dir.is_dir()

    async def test_writes_metadata_on_fork_with_parent_key(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:fork",
            {
                "session_id": "child1",
                "parent": "parent1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "child1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["format"] == "context-intelligence"
        assert meta["version"] == "1.0.0"
        assert meta["session_id"] == "child1"
        assert meta["parent_id"] == "parent1"
        assert meta["status"] == "running"


# ---------------------------------------------------------------------------
# TestSessionEnd
# ---------------------------------------------------------------------------
class TestSessionEnd:
    """session:end updates metadata with status + ended_at."""

    async def test_updates_metadata_status_and_ended_at(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        # First start a session
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        # Then end it
        await handler(
            "session:end",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:05:00Z",
                "status": "completed",
            },
        )
        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["format"] == "context-intelligence"
        assert meta["version"] == "1.0.0"
        assert meta["status"] == "completed"
        assert meta["ended_at"] == "2026-01-15T10:05:00Z"

    async def test_appends_end_event_to_jsonl(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        await handler(
            "session:end",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:05:00Z",
                "status": "completed",
            },
        )
        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 2
        end_record = json.loads(lines[1])
        assert end_record["event"] == "session:end"


# ---------------------------------------------------------------------------
# TestRegularEvents
# ---------------------------------------------------------------------------
class TestRegularEvents:
    """Regular events append to events.jsonl."""

    async def test_appends_to_existing_session(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:01Z",
                "tool_name": "read_file",
            },
        )
        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 2
        record = json.loads(lines[1])
        assert record["event"] == "tool:call"

    async def test_creates_session_dir_on_first_event_if_missing(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        # No session:start — regular event arrives first
        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:01Z",
                "tool_name": "read_file",
            },
        )
        session_dir = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence"
        assert session_dir.is_dir()
        jsonl_path = session_dir / "events.jsonl"
        assert jsonl_path.exists()

    async def test_multiple_events_append_in_order(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )
        await handler("tool:call", {"session_id": "s1", "timestamp": "t1"})
        await handler("tool:result", {"session_id": "s1", "timestamp": "t2"})
        await handler("llm:response", {"session_id": "s1", "timestamp": "t3"})

        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 4
        events = [json.loads(line)["event"] for line in lines]
        assert events == ["session:start", "tool:call", "tool:result", "llm:response"]


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------
class TestErrorHandling:
    """Missing/empty session_id skips silently."""

    async def test_missing_session_id_skips_silently(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        result = await handler("session:start", {"timestamp": "2026-01-15T10:00:00Z"})
        assert isinstance(result, HookResult)
        assert result.action == "continue"
        # No directories should have been created
        sessions_dir = tmp_path / "proj" / "sessions"
        assert not sessions_dir.exists()

    async def test_empty_session_id_skips_silently(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        result = await handler(
            "session:start", {"session_id": "", "timestamp": "2026-01-15T10:00:00Z"}
        )
        assert isinstance(result, HookResult)
        assert result.action == "continue"
        sessions_dir = tmp_path / "proj" / "sessions"
        assert not sessions_dir.exists()

    async def test_disk_write_error_logs_warning_not_exception(self, tmp_path: Path) -> None:
        """When _append_event raises, handler uses logger.warning (not logger.exception)."""
        from amplifier_module_hook_context_intelligence.handlers import logging_handler
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        with (
            patch.object(handler, "_append_event", side_effect=OSError("disk full")),
            patch.object(logging_handler.logger, "warning") as mock_warning,
            patch.object(logging_handler.logger, "exception") as mock_exception,
        ):
            result = await handler(
                "tool:call",
                {"session_id": "s1", "timestamp": "2026-01-15T10:00:00Z"},
            )

        # Should still return continue
        assert isinstance(result, HookResult)
        assert result.action == "continue"
        # logger.warning must be called with the new message
        mock_warning.assert_called_once()
        call_args = mock_warning.call_args
        assert call_args[0][0] == "LoggingHandler disk write error processing %s"
        # logger.exception must NOT be called
        mock_exception.assert_not_called()


# ---------------------------------------------------------------------------
# TestSanitizeForJson
# ---------------------------------------------------------------------------
class TestSanitizeForJson:
    """_sanitize_for_json handles various types gracefully."""

    def test_dict_passthrough(self) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _sanitize_for_json,
        )

        data: dict[str, Any] = {"a": 1, "b": "hello", "c": True, "d": None}
        result = _sanitize_for_json(data)
        assert result == {"a": 1, "b": "hello", "c": True, "d": None}

    def test_non_serializable_falls_back_to_str(self) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _sanitize_for_json,
        )

        class Custom:
            def __str__(self) -> str:
                return "custom-repr"

        data: dict[str, Any] = {"obj": Custom()}
        result = _sanitize_for_json(data)
        assert result["obj"] == "custom-repr"

    def test_pydantic_model_uses_model_dump(self) -> None:
        from pydantic import BaseModel

        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _sanitize_for_json,
        )

        class MyModel(BaseModel):
            name: str
            value: int

        data: dict[str, Any] = {"model": MyModel(name="test", value=42)}
        result = _sanitize_for_json(data)
        assert result["model"] == {"name": "test", "value": 42}

    def test_set_becomes_sorted_list(self) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _sanitize_for_json,
        )

        data: dict[str, Any] = {"tags": {"b", "a", "c"}}
        result = _sanitize_for_json(data)
        assert result["tags"] == ["a", "b", "c"]

    def test_nested_dict_recursion(self) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _sanitize_for_json,
        )

        data: dict[str, Any] = {"outer": {"inner": {"deep": 1}}}
        result = _sanitize_for_json(data)
        assert result == {"outer": {"inner": {"deep": 1}}}

    def test_list_recursion(self) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _sanitize_for_json,
        )

        data: dict[str, Any] = {"items": [{"a": 1}, {"b": 2}]}
        result = _sanitize_for_json(data)
        assert result == {"items": [{"a": 1}, {"b": 2}]}


# ---------------------------------------------------------------------------
# TestRecordFormat
# ---------------------------------------------------------------------------
class TestRecordFormat:
    """Each JSONL line has exactly {event, workspace, timestamp, data}."""

    async def test_record_has_exactly_four_keys(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj", workspace="my-ws"))
        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:01Z",
                "tool_name": "read_file",
            },
        )
        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        line = jsonl_path.read_text().strip()
        record = json.loads(line)
        assert set(record.keys()) == {"event", "workspace", "timestamp", "data"}
        assert record["event"] == "tool:call"
        assert record["workspace"] == "my-ws"
        assert record["timestamp"] == "2026-01-15T10:00:01Z"
        assert isinstance(record["data"], dict)

    async def test_record_workspace_empty_string_when_none(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj", workspace=""))
        await handler("tool:call", {"session_id": "s1", "timestamp": "t1"})
        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        record = json.loads(jsonl_path.read_text().strip())
        assert record["workspace"] == ""
