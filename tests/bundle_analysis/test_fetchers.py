"""Tests for context_intelligence.bundle_analysis.fetchers.

Covers:
- RawSignalEvent dataclass shape and discriminated kinds
- RawSignalEvent.from_graph_row classification
- RawSignalEvent.from_jsonl_event classification
- GraphFetcher async fetch with AsyncMock client
- JSONLFetcher fetch with tmp_path filesystem
"""

from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write a list of dicts as JSONL to *path*, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _session_jsonl(base: Path, workspace: str, session_id: str) -> Path:
    return base / workspace / "sessions" / session_id / "context-intelligence" / "events.jsonl"


# ---------------------------------------------------------------------------
# TestRawSignalEventShape
# ---------------------------------------------------------------------------


class TestRawSignalEventShape:
    def test_agent_spawned_only_populates_agent(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        evt = RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")
        assert evt.kind == "agent_spawned"
        assert evt.agent == "foundation:explorer"
        assert evt.skill_source is None
        assert evt.recipe_path is None
        assert evt.resolutions is None

    def test_skill_loaded_only_populates_skill_source(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        evt = RawSignalEvent(kind="skill_loaded", skill_source="/path/to/skill")
        assert evt.kind == "skill_loaded"
        assert evt.skill_source == "/path/to/skill"
        assert evt.agent is None
        assert evt.recipe_path is None
        assert evt.resolutions is None

    def test_recipe_execute_only_populates_recipe_path(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        evt = RawSignalEvent(
            kind="recipe_execute", recipe_path="@recipes:examples/code-review.yaml"
        )
        assert evt.kind == "recipe_execute"
        assert evt.recipe_path == "@recipes:examples/code-review.yaml"
        assert evt.agent is None
        assert evt.skill_source is None
        assert evt.resolutions is None

    def test_mentions_resolved_only_populates_resolutions(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        resolutions = [{"mention": "@explorer", "resolved": "foundation:explorer"}]
        evt = RawSignalEvent(kind="mentions_resolved", resolutions=resolutions)
        assert evt.kind == "mentions_resolved"
        assert evt.resolutions == resolutions
        assert evt.agent is None
        assert evt.skill_source is None
        assert evt.recipe_path is None

    def test_default_fields_are_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        evt = RawSignalEvent(kind="agent_spawned")
        assert evt.agent is None
        assert evt.skill_source is None
        assert evt.recipe_path is None
        assert evt.resolutions is None

    def test_tool_call_only_populates_tool_name(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        evt = RawSignalEvent(kind="tool_call", tool_name="bash")
        assert evt.tool_name == "bash"
        assert evt.agent is None
        assert evt.skill_source is None
        assert evt.recipe_path is None
        assert evt.resolutions is None
        assert evt.mode_name is None

    def test_mode_activated_only_populates_mode_name(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        evt = RawSignalEvent(kind="mode_activated", mode_name="brainstorm")
        assert evt.mode_name == "brainstorm"
        assert evt.tool_name is None
        assert evt.agent is None


# ---------------------------------------------------------------------------
# TestFromGraphRow
# ---------------------------------------------------------------------------


class TestFromGraphRow:
    def test_agent_spawned_row(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "delegate:agent_spawned",
            "agent": "foundation:explorer",
            "tool_name": None,
            "tool_input_json": None,
            "data_json": None,
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is not None
        assert evt.kind == "agent_spawned"
        assert evt.agent == "foundation:explorer"

    def test_skill_loaded_row_with_dict_data_json(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "skill:loaded",
            "agent": None,
            "tool_name": None,
            "tool_input_json": None,
            "data_json": {"source": "/home/user/.amplifier/cache/skills/superpowers-abc/skill.py"},
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is not None
        assert evt.kind == "skill_loaded"
        assert evt.skill_source == "/home/user/.amplifier/cache/skills/superpowers-abc/skill.py"

    def test_skill_loaded_row_with_json_string_data_json(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        data = json.dumps({"source": "/home/user/.amplifier/cache/skills/superpowers-abc/skill.py"})
        row = {
            "event_name": "skill:loaded",
            "agent": None,
            "tool_name": None,
            "tool_input_json": None,
            "data_json": data,  # JSON string — must be coerced
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is not None
        assert evt.kind == "skill_loaded"
        assert evt.skill_source == "/home/user/.amplifier/cache/skills/superpowers-abc/skill.py"

    def test_recipe_execute_row_with_dict_tool_input_json(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "tool:pre",
            "agent": None,
            "tool_name": "recipes",
            "tool_input_json": {
                "operation": "execute",
                "recipe_path": "@recipes:examples/code-review.yaml",
            },
            "data_json": None,
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is not None
        assert evt.kind == "recipe_execute"
        assert evt.recipe_path == "@recipes:examples/code-review.yaml"

    def test_recipe_execute_row_with_json_string_tool_input_json(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        tool_input = json.dumps(
            {"operation": "execute", "recipe_path": "@recipes:examples/code-review.yaml"}
        )
        row = {
            "event_name": "tool:pre",
            "agent": None,
            "tool_name": "recipes",
            "tool_input_json": tool_input,  # JSON string — must be coerced
            "data_json": None,
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is not None
        assert evt.kind == "recipe_execute"
        assert evt.recipe_path == "@recipes:examples/code-review.yaml"

    def test_recipes_non_execute_operation_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "tool:pre",
            "agent": None,
            "tool_name": "recipes",
            "tool_input_json": {"operation": "list", "recipe_path": None},
            "data_json": None,
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is None

    def test_mentions_resolved_row(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        resolutions = [{"mention": "@explorer", "resolved": "foundation:explorer"}]
        row = {
            "event_name": "mentions:resolved",
            "agent": None,
            "tool_name": None,
            "tool_input_json": None,
            "data_json": {"resolutions": resolutions},
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is not None
        assert evt.kind == "mentions_resolved"
        assert evt.resolutions == resolutions

    def test_mentions_resolved_with_empty_resolutions_returns_event_with_empty_list(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "mentions:resolved",
            "agent": None,
            "tool_name": None,
            "tool_input_json": None,
            "data_json": {},  # no resolutions field → defaults to []
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is not None
        assert evt.kind == "mentions_resolved"
        assert evt.resolutions == []

    def test_unrelated_row_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "llm:request",
            "agent": None,
            "tool_name": None,
            "tool_input_json": None,
            "data_json": None,
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is None

    def test_malformed_skill_data_json_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "skill:loaded",
            "agent": None,
            "tool_name": None,
            "tool_input_json": None,
            "data_json": "not-valid-json{{{{",  # unparseable string
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is None

    def test_skill_loaded_missing_source_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        row = {
            "event_name": "skill:loaded",
            "agent": None,
            "tool_name": None,
            "tool_input_json": None,
            "data_json": {"other_field": "value"},  # no source key
        }
        evt = RawSignalEvent.from_graph_row(row)
        assert evt is None


# ---------------------------------------------------------------------------
# TestFromJsonlEvent
# ---------------------------------------------------------------------------


class TestFromJsonlEvent:
    def test_agent_spawned_record(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "delegate:agent_spawned",
            "data": {"agent": "foundation:explorer"},
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "agent_spawned"
        assert evt.agent == "foundation:explorer"

    def test_skill_loaded_record(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "skill:loaded",
            "data": {"source": "/home/user/.amplifier/cache/skills/superpowers-abc/skill.py"},
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "skill_loaded"
        assert evt.skill_source == "/home/user/.amplifier/cache/skills/superpowers-abc/skill.py"

    def test_recipe_execute_record_with_dict_tool_input(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "tool:pre",
            "data": {
                "tool_name": "recipes",
                "tool_input": {
                    "operation": "execute",
                    "recipe_path": "@recipes:examples/code-review.yaml",
                },
            },
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "recipe_execute"
        assert evt.recipe_path == "@recipes:examples/code-review.yaml"

    def test_recipe_execute_record_with_json_string_tool_input(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "tool:pre",
            "data": {
                "tool_name": "recipes",
                "tool_input": json.dumps(
                    {"operation": "execute", "recipe_path": "@recipes:examples/code-review.yaml"}
                ),
            },
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "recipe_execute"
        assert evt.recipe_path == "@recipes:examples/code-review.yaml"

    def test_recipes_non_execute_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "tool:pre",
            "data": {
                "tool_name": "recipes",
                "tool_input": {"operation": "list"},
            },
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is None

    def test_tool_pre_non_recipe_creates_tool_call_event(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "tool:pre",
            "data": {
                "tool_name": "bash",
                "tool_call_id": "toolu_abc",
                "tool_input": {"command": "ls"},
                "parallel_group_id": None,
                "session_id": "test-session",
                "parent_id": None,
                "timestamp": "2026-01-01T00:00:00Z",
            },
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "tool_call"
        assert evt.tool_name == "bash"

    def test_tool_pre_recipe_execute_still_creates_recipe_execute(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "tool:pre",
            "data": {
                "tool_name": "recipes",
                "tool_input": {
                    "operation": "execute",
                    "recipe_path": "@superpowers:recipes/foo.yaml",
                },
            },
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "recipe_execute"
        assert evt.recipe_path == "@superpowers:recipes/foo.yaml"

    def test_mode_activated_event_creates_mode_activated(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "mode:activated",
            "data": {
                "mode": "brainstorm",
                "name": "brainstorm",
                "session_id": "test-session",
                "parent_id": None,
                "timestamp": "2026-01-01T00:00:00Z",
            },
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "mode_activated"
        assert evt.mode_name == "brainstorm"

    def test_mode_changed_event_creates_mode_activated_with_to_mode(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "mode:changed",
            "data": {
                "to_mode": "write-plan",
                "new": "write-plan",
                "from_mode": "brainstorm",
                "old": "brainstorm",
                "session_id": "test-session",
                "parent_id": None,
                "timestamp": "2026-01-01T00:00:00Z",
            },
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "mode_activated"
        assert evt.mode_name == "write-plan"

    def test_tool_pre_missing_tool_name_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {"event": "tool:pre", "data": {"tool_call_id": "abc"}}
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is None

    def test_mode_activated_missing_mode_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {"event": "mode:activated", "data": {"name": "brainstorm"}}
        # data.mode key missing (only data.name present)
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is None

    def test_mentions_resolved_record(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        resolutions = [{"mention": "@zen-architect", "resolved": "foundation:zen-architect"}]
        record = {
            "event": "mentions:resolved",
            "data": {"resolutions": resolutions},
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is not None
        assert evt.kind == "mentions_resolved"
        assert evt.resolutions == resolutions

    def test_irrelevant_record_returns_none(self):
        from context_intelligence.bundle_analysis.fetchers import RawSignalEvent

        record = {
            "event": "llm:response",
            "data": {"tokens": 42},
        }
        evt = RawSignalEvent.from_jsonl_event(record)
        assert evt is None


# ---------------------------------------------------------------------------
# TestJSONLFetcher
# ---------------------------------------------------------------------------


class TestJSONLFetcher:
    def test_session_scope_reads_single_file(self, tmp_path):
        from context_intelligence.bundle_analysis.fetchers import JSONLFetcher

        records = [
            {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}},
            {"event": "delegate:agent_spawned", "data": {"agent": "superpowers:implementer"}},
        ]
        path = _session_jsonl(tmp_path, "ws", "sess-001")
        _write_jsonl(path, records)

        fetcher = JSONLFetcher()
        events = fetcher.fetch(workspace="ws", session_id="sess-001", base_path=tmp_path)

        assert len(events) == 2
        agents = {e.agent for e in events}
        assert "foundation:explorer" in agents
        assert "superpowers:implementer" in agents

    def test_workspace_scope_globs_every_session(self, tmp_path):
        from context_intelligence.bundle_analysis.fetchers import JSONLFetcher

        _write_jsonl(
            _session_jsonl(tmp_path, "ws", "sess-001"),
            [{"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}],
        )
        _write_jsonl(
            _session_jsonl(tmp_path, "ws", "sess-002"),
            [{"event": "delegate:agent_spawned", "data": {"agent": "superpowers:implementer"}}],
        )

        fetcher = JSONLFetcher()
        events = fetcher.fetch(workspace="ws", session_id=None, base_path=tmp_path)

        agents = {e.agent for e in events}
        assert "foundation:explorer" in agents
        assert "superpowers:implementer" in agents

    def test_missing_workspace_returns_empty_list(self, tmp_path):
        from context_intelligence.bundle_analysis.fetchers import JSONLFetcher

        fetcher = JSONLFetcher()
        events = fetcher.fetch(workspace="nonexistent-ws", session_id=None, base_path=tmp_path)

        assert events == []

    def test_missing_session_file_returns_empty_list(self, tmp_path):
        from context_intelligence.bundle_analysis.fetchers import JSONLFetcher

        # Create sessions dir but no file inside
        sessions_dir = tmp_path / "ws" / "sessions"
        sessions_dir.mkdir(parents=True)

        fetcher = JSONLFetcher()
        events = fetcher.fetch(workspace="ws", session_id="missing-sess", base_path=tmp_path)

        assert events == []

    def test_malformed_jsonl_lines_are_skipped(self, tmp_path):
        from context_intelligence.bundle_analysis.fetchers import JSONLFetcher

        path = _session_jsonl(tmp_path, "ws", "sess-001")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            fh.write("{not valid json\n")
            fh.write(
                json.dumps(
                    {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}
                )
                + "\n"
            )
            fh.write("another bad line\n")

        fetcher = JSONLFetcher()
        events = fetcher.fetch(workspace="ws", session_id="sess-001", base_path=tmp_path)

        assert len(events) == 1
        assert events[0].agent == "foundation:explorer"

    def test_default_base_path_uses_home_amplifier_projects(self, tmp_path, monkeypatch):
        from context_intelligence.bundle_analysis.fetchers import JSONLFetcher

        # Override HOME so default path resolves to tmp_path
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        fetcher = JSONLFetcher()
        # Should not raise; missing dir → returns []
        events = fetcher.fetch(workspace="ws", session_id=None, base_path=None)
        assert events == []


# ---------------------------------------------------------------------------
# TestGraphFetcherRemoved
# ---------------------------------------------------------------------------


class TestGraphFetcherRemoved:
    def test_graph_fetcher_not_importable(self):
        """GraphFetcher must no longer exist on the fetchers module."""
        import context_intelligence.bundle_analysis.fetchers as fetchers_module

        assert not hasattr(fetchers_module, "GraphFetcher"), (
            "GraphFetcher should have been deleted from fetchers.py"
        )

    def test_queries_dir_helper_removed(self):
        """_queries_dir() helper must no longer exist on the fetchers module."""
        import context_intelligence.bundle_analysis.fetchers as fetchers_module

        assert not hasattr(fetchers_module, "_queries_dir"), (
            "_queries_dir should have been deleted from fetchers.py"
        )

    def test_queries_directory_removed(self):
        """The queries/ subdirectory must have been deleted from the package."""
        import context_intelligence.bundle_analysis.fetchers as fetchers_module
        from pathlib import Path

        queries_dir = Path(fetchers_module.__file__).parent / "queries"
        assert not queries_dir.exists(), f"queries/ directory should not exist at {queries_dir}"
