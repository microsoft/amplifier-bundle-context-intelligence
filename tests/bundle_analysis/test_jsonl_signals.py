"""Tests for context_intelligence.bundle_analysis.jsonl_signals.

jsonl_signals.py must:
- Extract agents from delegate:agent_spawned events (bundle:component format)
- Extract agents from delegate:agent_resumed events (bundle:component format)
- Extract skills from skill:loaded events via source path bundle slug
- NOT attribute bare tool names to any bundle
- Return an empty dict for events with no bundle-attributable data
- Scope to a single session when session_id is provided
- Glob all sessions when session_id is omitted
- Return empty dict gracefully when the workspace directory doesn't exist
- Strip the 16-hex-char hash suffix from bundle cache directory slugs
"""

from __future__ import annotations

import json
from pathlib import Path

from context_intelligence.bundle_analysis.jsonl_signals import (
    _bundle_name_from_slug,
    _bundle_name_from_source_path,
    run_signals_from_jsonl,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, events: list[dict]) -> None:
    """Write a list of event dicts as JSONL to *path*, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(json.dumps(evt) + "\n")


def _session_jsonl(base: Path, workspace: str, session_id: str) -> Path:
    """Return the events.jsonl path for a given workspace + session."""
    return base / workspace / "sessions" / session_id / "context-intelligence" / "events.jsonl"


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


class TestBundleNameFromSlug:
    def test_slug_to_bundle_name_strips_hash(self):
        assert _bundle_name_from_slug("superpowers-a6aca0133cf890bf") == "superpowers"

    def test_strips_hash_from_long_slug(self):
        assert (
            _bundle_name_from_slug("amplifier-bundle-context-intelligence-ecd41f3e6fa67bd2")
            == "amplifier-bundle-context-intelligence"
        )

    def test_no_hash_suffix_unchanged(self):
        # A slug without a 16-hex suffix should be returned unchanged
        assert _bundle_name_from_slug("foundation") == "foundation"

    def test_short_hex_suffix_not_stripped(self):
        # Only exactly 16-char hex suffix is stripped
        assert _bundle_name_from_slug("mything-abc123") == "mything-abc123"


class TestBundleNameFromSourcePath:
    def test_cache_skills_path(self):
        source = "/home/user/.amplifier/cache/skills/superpowers-a6aca0133cf890bf/skills"
        assert _bundle_name_from_source_path(source) == "superpowers"

    def test_cache_skills_directory_path(self):
        source = (
            "/home/user/.amplifier/cache/skills/"
            "superpowers-a6aca0133cf890bf/skills/brainstorming"
        )
        assert _bundle_name_from_source_path(source) == "superpowers"

    def test_returns_none_for_unrecognised_path(self):
        assert _bundle_name_from_source_path("/tmp/random/path") is None

    def test_returns_none_for_empty_string(self):
        assert _bundle_name_from_source_path("") is None


# ---------------------------------------------------------------------------
# Integration tests for run_signals_from_jsonl
# ---------------------------------------------------------------------------


class TestExtractAgents:
    def test_extracts_agent_from_delegate_spawned(self, tmp_path):
        """delegate:agent_spawned with bundle:component → agents count incremented."""
        events = [
            {
                "event": "delegate:agent_spawned",
                "data": {"agent": "foundation:explorer"},
            }
        ]
        p = _session_jsonl(tmp_path, "my-workspace", "sess-001")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(
            workspace="my-workspace",
            session_id="sess-001",
            base_path=tmp_path,
        )

        assert "foundation" in result
        assert result["foundation"]["agents"] == 1

    def test_extracts_agent_from_delegate_resumed(self, tmp_path):
        """delegate:agent_resumed also increments agents count."""
        events = [
            {
                "event": "delegate:agent_resumed",
                "data": {"agent": "foundation:zen-architect"},
            }
        ]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result["foundation"]["agents"] == 1

    def test_multiple_agent_events_accumulate(self, tmp_path):
        events = [
            {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}},
            {"event": "delegate:agent_spawned", "data": {"agent": "foundation:zen-architect"}},
            {"event": "delegate:agent_spawned", "data": {"agent": "superpowers:brainstormer"}},
        ]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result["foundation"]["agents"] == 2
        assert result["superpowers"]["agents"] == 1

    def test_agent_without_colon_is_ignored(self, tmp_path):
        """A bare agent name (no bundle prefix) must not be attributed."""
        events = [{"event": "delegate:agent_spawned", "data": {"agent": "bare-agent"}}]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result == {}


class TestExtractSkills:
    def test_extracts_skill_from_source_path(self, tmp_path):
        """skill:loaded with a cache source path → skills count for resolved bundle."""
        events = [
            {
                "event": "skill:loaded",
                "data": {
                    "skill_name": "brainstorming",
                    "source": "/home/user/.amplifier/cache/skills/superpowers-a6aca0133cf890bf/skills",
                    "skill_directory": (
                        "/home/user/.amplifier/cache/skills/"
                        "superpowers-a6aca0133cf890bf/skills/brainstorming"
                    ),
                },
            }
        ]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert "superpowers" in result
        assert result["superpowers"]["skills"] == 1

    def test_skill_without_cache_path_is_ignored(self, tmp_path):
        """A skill:loaded event with no recognisable cache path is not attributed."""
        events = [
            {
                "event": "skill:loaded",
                "data": {"skill_name": "orphan", "source": "/tmp/random"},
            }
        ]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result == {}


class TestNonAttributableEvents:
    def test_bare_tool_name_not_attributed(self, tmp_path):
        """tool:pre with a bare tool name (todo, delegate) → no bundle extracted."""
        events = [
            {"event": "tool:pre", "data": {"tool": "todo"}},
            {"event": "tool:pre", "data": {"tool": "delegate"}},
            {"event": "tool:post", "data": {"tool": "todo"}},
        ]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result == {}

    def test_empty_when_no_relevant_events(self, tmp_path):
        """Events that carry no bundle information produce an empty result."""
        events = [
            {"event": "llm:request", "data": {"model": "claude-3-5-sonnet"}},
            {"event": "llm:response", "data": {"tokens": 500}},
            {"event": "session:start", "data": {}},
        ]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result == {}

    def test_zero_modes_recipes_tools(self, tmp_path):
        """Even when agents/skills are extracted, modes/recipes/tools are always 0."""
        events = [
            {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}},
        ]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result["foundation"]["modes"] == 0
        assert result["foundation"]["recipes"] == 0
        assert result["foundation"]["tools"] == 0


class TestScopeHandling:
    def test_session_scope(self, tmp_path):
        """When session_id is provided, only that session's file is read."""
        events_a = [{"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}]
        events_b = [{"event": "delegate:agent_spawned", "data": {"agent": "superpowers:brainstormer"}}]

        _write_jsonl(_session_jsonl(tmp_path, "ws", "session-a"), events_a)
        _write_jsonl(_session_jsonl(tmp_path, "ws", "session-b"), events_b)

        result = run_signals_from_jsonl(
            workspace="ws", session_id="session-a", base_path=tmp_path
        )

        # Only session-a's events should appear
        assert "foundation" in result
        assert "superpowers" not in result

    def test_workspace_scope_globs(self, tmp_path):
        """When no session_id, all sessions in the workspace are aggregated."""
        events_a = [{"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}]
        events_b = [{"event": "delegate:agent_spawned", "data": {"agent": "superpowers:brainstormer"}}]

        _write_jsonl(_session_jsonl(tmp_path, "ws", "session-a"), events_a)
        _write_jsonl(_session_jsonl(tmp_path, "ws", "session-b"), events_b)

        result = run_signals_from_jsonl(workspace="ws", base_path=tmp_path)

        assert result["foundation"]["agents"] == 1
        assert result["superpowers"]["agents"] == 1

    def test_missing_workspace_returns_empty(self, tmp_path):
        """Gracefully returns {} when the workspace directory doesn't exist."""
        result = run_signals_from_jsonl(
            workspace="nonexistent-workspace", base_path=tmp_path
        )
        assert result == {}

    def test_session_file_not_found_returns_empty(self, tmp_path):
        """Gracefully returns {} when the session's events.jsonl doesn't exist."""
        # Create the sessions dir but no session subdirectory
        (tmp_path / "ws" / "sessions").mkdir(parents=True)

        result = run_signals_from_jsonl(
            workspace="ws", session_id="missing-session", base_path=tmp_path
        )
        assert result == {}


class TestRobustness:
    def test_malformed_json_lines_skipped(self, tmp_path):
        """Lines that are not valid JSON are skipped without raising."""
        p = _session_jsonl(tmp_path, "ws", "s1")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '{"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}\n'
            "THIS IS NOT JSON\n"
            '{"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}\n'
        )

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        # Two valid lines → 2 agent events
        assert result["foundation"]["agents"] == 2

    def test_empty_file_returns_empty(self, tmp_path):
        p = _session_jsonl(tmp_path, "ws", "s1")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert result == {}

    def test_result_has_all_component_keys(self, tmp_path):
        """Each extracted bundle entry has all five component keys."""
        events = [{"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}]
        p = _session_jsonl(tmp_path, "ws", "s1")
        _write_jsonl(p, events)

        result = run_signals_from_jsonl(workspace="ws", session_id="s1", base_path=tmp_path)
        assert set(result["foundation"].keys()) == {"agents", "skills", "modes", "recipes", "tools"}
