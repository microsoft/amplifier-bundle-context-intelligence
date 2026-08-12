"""Tests for LoggingHandler — flat JSONL session file writer.

Zero dependency on graph infrastructure. Tests file I/O only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_core.models import HookResult


# ---------------------------------------------------------------------------
# _FakeResolver adapter
# ---------------------------------------------------------------------------
class _FakeResolver:
    """Minimal resolver adapter for testing LoggingHandler in isolation."""

    def __init__(
        self,
        base_path: Path,
        project_slug: str,
        workspace: str = "test-workspace",
        working_dir: str = "",
    ) -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.workspace = workspace
        self.working_dir = working_dir

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

        # working_dir comes from the resolver (session capability), not the event payload.
        handler = LoggingHandler(
            _FakeResolver(
                tmp_path, "proj", workspace="my-project", working_dir="/home/user/project"
            )
        )
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "parent_id": "p1",
                "timestamp": "2026-01-15T10:00:00Z",
                # working_dir in event data is NOT used for metadata; resolver value is.
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


# ---------------------------------------------------------------------------
# TestConfigParentId — CR-1: config-supplied parent_id fallback
# ---------------------------------------------------------------------------
class TestConfigParentId:
    """CR-1: LoggingHandler uses config-supplied parent_id as fallback when event data lacks it.

    Resolver-spawned phase sessions (via SessionFactory.create_phase_session) emit
    session:start without a parent_id in event data.  The resolver supplies parent_id
    through the hook config dict instead.  LoggingHandler must propagate that value
    into metadata.json whenever event data does not carry its own parent_id.

    Precedence: event-data parent_id > config parent_id.
    Existing event-data tests in TestSessionStart / TestSessionFork are unchanged.
    """

    class _FakeResolverWithParentId(_FakeResolver):
        """Minimal resolver that also exposes a parent_id attribute (CR-1)."""

        def __init__(self, base_path: Path, project_slug: str, parent_id: str = "") -> None:
            super().__init__(base_path, project_slug)
            self.parent_id = parent_id

    async def test_uses_config_parent_id_when_event_data_lacks_it(self, tmp_path: Path) -> None:
        """Resolver-spawned phase session: event data has no parent_id, hook config does.

        _ensure_metadata and _enrich_metadata_from_session_init must both fall back
        to self._parent_id when event data provides no parent_id / parent key.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = self._FakeResolverWithParentId(tmp_path, "proj", parent_id="parent-abc")
        handler = LoggingHandler(resolver)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
                # No parent_id in event data — resolver supplied it via hook config instead.
            },
        )
        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["parent_id"] == "parent-abc"

    async def test_event_data_parent_id_wins_over_config(self, tmp_path: Path) -> None:
        """Precedence: event data parent_id beats config-supplied parent_id.

        This preserves the existing delegate/sub-session flow where the kernel emits
        parent_id in event data.  The config-supplied value is only the fallback.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = self._FakeResolverWithParentId(tmp_path, "proj", parent_id="from-config")
        handler = LoggingHandler(resolver)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
                "parent_id": "from-event",  # Event data has its own parent_id.
            },
        )
        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["parent_id"] == "from-event"


# ---------------------------------------------------------------------------
# TestWorkingDirFromResolver — working_dir is a session attribute, not event data
# ---------------------------------------------------------------------------
class TestWorkingDirFromResolver:
    """working_dir in metadata.json comes from the resolver (session capability), not the event.

    This class covers three key guarantees:
    (a) working_dir is populated from the resolver even when the first event is NOT
        session:start and carries no working_dir in its payload.
    (b) A present-but-empty working_dir in the event payload does NOT win — the resolver
        value is used (event-payload empty string was the old bug).
    (c) When the resolver's working_dir is "" (capability unavailable), metadata gracefully
        stores "", and _enrich does not clobber a previously-set non-empty value.
    """

    async def test_working_dir_from_resolver_on_non_session_start_first_event(
        self, tmp_path: Path
    ) -> None:
        """(a) working_dir comes from resolver when first event is tool:call (no session:start)."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = _FakeResolver(tmp_path, "proj", working_dir="/resolver/working/dir")
        handler = LoggingHandler(resolver)

        # First event is NOT session:start — old code would have stored "" here.
        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:01Z",
                "tool_name": "read_file",
                # No working_dir in payload at all.
            },
        )

        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["working_dir"] == "/resolver/working/dir"

    async def test_present_but_empty_event_working_dir_does_not_win(self, tmp_path: Path) -> None:
        """(b) An empty working_dir in the event payload does not clobber the resolver value.

        The old code used data.get("working_dir", ""), so a payload that carries
        {"working_dir": ""} (present-but-empty) would return "" and store it.
        New code ignores event working_dir entirely; the resolver always wins.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = _FakeResolver(tmp_path, "proj", working_dir="/real/path")
        handler = LoggingHandler(resolver)

        # Event deliberately carries working_dir="" (present-but-empty).
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "",  # Present but empty — old code would have stored "".
            },
        )

        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        # Resolver's value wins over the event's empty string.
        assert meta["working_dir"] == "/real/path"

    async def test_resolver_working_dir_empty_stores_empty_gracefully(self, tmp_path: Path) -> None:
        """(c-i) When resolver.working_dir is '', metadata working_dir is '' (graceful)."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = _FakeResolver(tmp_path, "proj", working_dir="")
        handler = LoggingHandler(resolver)

        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
            },
        )

        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["working_dir"] == ""

    async def test_enrich_does_not_clobber_prior_value_with_empty_resolver(
        self, tmp_path: Path
    ) -> None:
        """(c-ii) _enrich does not overwrite a good working_dir with '' from an empty resolver.

        Scenario: first event stores a non-empty working_dir via the resolver, then
        _enrich_metadata_from_session_init is called (session:start arrives late) while
        the resolver's working_dir returns "".  The prior value must be preserved.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        # Phase 1: resolver has a working_dir — first event stores it.
        resolver = _FakeResolver(tmp_path, "proj", working_dir="/first/event/dir")
        handler = LoggingHandler(resolver)

        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:01Z",
            },
        )

        # Verify initial state.
        meta_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
        meta = json.loads(meta_path.read_text())
        assert meta["working_dir"] == "/first/event/dir"

        # Phase 2: resolver now returns "" (e.g. capability went away).
        resolver.working_dir = ""

        # session:start arrives (triggers _enrich).
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:02Z",
                # No working_dir in event payload either.
            },
        )

        meta = json.loads(meta_path.read_text())
        # Prior non-empty value must be preserved; "" from resolver must not clobber.
        assert meta["working_dir"] == "/first/event/dir"

    async def test_working_dir_from_resolver_overrides_event_on_session_start(
        self, tmp_path: Path
    ) -> None:
        """Resolver value wins even when event carries a different non-empty working_dir."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = _FakeResolver(tmp_path, "proj", working_dir="/resolver/path")
        handler = LoggingHandler(resolver)

        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/event/path",  # Different value in event — resolver wins.
            },
        )

        meta = json.loads(
            (
                tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
            ).read_text()
        )
        assert meta["working_dir"] == "/resolver/path"


# ---------------------------------------------------------------------------
# TestWorkingDirEnvelope \u2014 Phase-1 emit: working_dir is a TOP-LEVEL wire-envelope
# field (alongside workspace), sourced from the resolver -- NOT event data
# ---------------------------------------------------------------------------
class TestWorkingDirEnvelope:
    """working_dir travels as a TOP-LEVEL HTTP envelope field, not nested event data.

    Companion to TestWorkingDirFromResolver above (which covers the local
    metadata.json copy). This class covers the HTTP dispatch path: the same
    "session attribute, not event data" guarantee now also holds for the
    wire envelope POSTed to the context-intelligence server.
    """

    async def test_working_dir_absent_from_data_passed_to_dispatcher_enqueue(
        self, tmp_path: Path
    ) -> None:
        """LoggingHandler never injects working_dir into the event data handed to dispatchers.

        working_dir is resolver-sourced session metadata; it must not leak into the
        sanitized event `data` that dispatchers enqueue (that data becomes the nested
        `data` blob inside the wire envelope -- see test below for envelope shape).
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        resolver = _FakeResolver(tmp_path, "proj", working_dir="/resolver/working/dir")
        handler = LoggingHandler(resolver)

        captured: dict[str, Any] = {}

        class _SpyDispatcher:
            def enqueue(self, event: str, data: dict[str, Any]) -> None:
                captured["event"] = event
                captured["data"] = data

        await handler.set_dispatchers([_SpyDispatcher()])  # type: ignore[list-item]

        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:01Z",
                "tool_name": "read_file",
            },
        )

        assert "working_dir" not in captured["data"]

    async def test_dispatcher_envelope_carries_working_dir_top_level(self, tmp_path: Path) -> None:
        """The real _DestinationDispatcher, wired with resolver.working_dir exactly as
        production mount() does (see __init__.py), puts working_dir at the TOP LEVEL
        of the posted envelope for the same event data LoggingHandler enqueues -- and
        it is still absent from the nested `data`.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
            _DestinationDispatcher,
        )

        resolver = _FakeResolver(tmp_path, "proj", working_dir="/resolver/working/dir")
        handler = LoggingHandler(resolver)

        dispatcher = _DestinationDispatcher(
            name="test",
            url="http://localhost:8080",
            api_key="test-key",
            workspace=resolver.workspace,
            working_dir=resolver.working_dir,  # mirrors production mount() wiring
            dispatch_timeout=10.0,
            failure_threshold=3,
            queue_capacity=256,
            close_drain_timeout=0.5,
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = mock_response
        dispatcher._client = mock_client

        await handler.set_dispatchers([dispatcher])

        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:01Z",
                "tool_name": "read_file",
            },
        )
        await asyncio.sleep(0)  # let the dispatcher's worker process the queued event
        await dispatcher.close()

        _, kwargs = mock_client.post.call_args
        posted_payload = kwargs["json"]
        assert posted_payload["working_dir"] == "/resolver/working/dir"
        assert "working_dir" not in posted_payload["data"]
