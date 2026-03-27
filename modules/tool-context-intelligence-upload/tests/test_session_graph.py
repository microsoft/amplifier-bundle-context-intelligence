"""Tests for session_graph.py — metadata discovery and topological sort."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from amplifier_module_tool_context_intelligence_upload.session_graph import (
    _discover_sessions,
    discover_and_sort,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_metadata(tmp_path: Path, sid: str, data: dict[str, Any]) -> Path:
    """Write metadata.json under tmp_path/sessions/{sid}/context-intelligence/."""
    directory = tmp_path / "sessions" / sid / "context-intelligence"
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "metadata.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# TestDiscoverSessions — validation rules
# ---------------------------------------------------------------------------


class TestDiscoverSessions:
    """Validation rules: format filter, session_id, parent_id, error resilience."""

    def test_valid_metadata_accepted(self, tmp_path):
        _write_metadata(
            tmp_path,
            "sess-1",
            {"format": "context-intelligence", "session_id": "sess-1"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        assert meta["session_id"] == "sess-1"

    def test_wrong_format_skipped_silently(self, tmp_path, capsys):
        _write_metadata(
            tmp_path,
            "sess-1",
            {"format": "other-format", "session_id": "sess-1"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 0
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_missing_format_skipped_silently(self, tmp_path, capsys):
        _write_metadata(tmp_path, "sess-1", {"session_id": "sess-1"})
        results = _discover_sessions(tmp_path)
        assert len(results) == 0
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_missing_session_id_skipped_with_warning(self, tmp_path, capsys):
        _write_metadata(
            tmp_path,
            "sess-1",
            {"format": "context-intelligence"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 0
        captured = capsys.readouterr()
        # substring match — tolerates future rephrasing of the suffix
        assert "missing 'session_id'" in captured.err

    def test_missing_parent_id_treated_as_root(self, tmp_path):
        _write_metadata(
            tmp_path,
            "sess-1",
            {"format": "context-intelligence", "session_id": "sess-1"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        # parent_id absent in metadata — must be None (not empty string)
        assert meta.get("parent_id") is None

    def test_empty_parent_id_preserved_in_metadata(self, tmp_path):
        _write_metadata(
            tmp_path,
            "sess-1",
            {
                "format": "context-intelligence",
                "session_id": "sess-1",
                "parent_id": "",
            },
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        assert meta["parent_id"] == ""

    def test_invalid_json_skipped(self, tmp_path, capsys):
        bad_dir = tmp_path / "sessions" / "bad-sess" / "context-intelligence"
        bad_dir.mkdir(parents=True)
        (bad_dir / "metadata.json").write_text("{invalid json}")
        results = _discover_sessions(tmp_path)
        assert len(results) == 0
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_multiple_sessions_discovered(self, tmp_path):
        for i in range(3):
            _write_metadata(
                tmp_path,
                f"sess-{i}",
                {"format": "context-intelligence", "session_id": f"sess-{i}"},
            )
        results = _discover_sessions(tmp_path)
        assert len(results) == 3
        assert {meta["session_id"] for _, meta in results} == {"sess-0", "sess-1", "sess-2"}


# ---------------------------------------------------------------------------
# TestDiscoverAndSort — topological sort
# ---------------------------------------------------------------------------


class TestDiscoverAndSort:
    """BFS topological ordering: roots before children, orphan promotion."""

    def test_single_session_returns_list_of_one(self, tmp_path):
        _write_metadata(
            tmp_path,
            "root",
            {"format": "context-intelligence", "session_id": "root"},
        )
        results = discover_and_sort(tmp_path)
        assert len(results) == 1

    def test_parents_before_children(self, tmp_path):
        _write_metadata(
            tmp_path,
            "root",
            {"format": "context-intelligence", "session_id": "root"},
        )
        _write_metadata(
            tmp_path,
            "child",
            {
                "format": "context-intelligence",
                "session_id": "child",
                "parent_id": "root",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert ids.index("root") < ids.index("child")

    def test_grandchildren_after_children(self, tmp_path):
        _write_metadata(
            tmp_path,
            "root",
            {"format": "context-intelligence", "session_id": "root"},
        )
        _write_metadata(
            tmp_path,
            "child",
            {
                "format": "context-intelligence",
                "session_id": "child",
                "parent_id": "root",
            },
        )
        _write_metadata(
            tmp_path,
            "grandchild",
            {
                "format": "context-intelligence",
                "session_id": "grandchild",
                "parent_id": "child",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert ids == ["root", "child", "grandchild"]

    def test_orphan_promoted_to_root_with_warning(self, tmp_path, capsys):
        _write_metadata(
            tmp_path,
            "orphan",
            {
                "format": "context-intelligence",
                "session_id": "orphan",
                "parent_id": "nonexistent-parent",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "orphan" in ids
        captured = capsys.readouterr()
        assert "promoting to root" in captured.err

    def test_empty_path_returns_empty_list(self, tmp_path):
        results = discover_and_sort(tmp_path)
        assert results == []

    def test_mixed_roots_and_children(self, tmp_path):
        _write_metadata(
            tmp_path,
            "root-a",
            {"format": "context-intelligence", "session_id": "root-a"},
        )
        _write_metadata(
            tmp_path,
            "root-b",
            {"format": "context-intelligence", "session_id": "root-b"},
        )
        _write_metadata(
            tmp_path,
            "child-of-a",
            {
                "format": "context-intelligence",
                "session_id": "child-of-a",
                "parent_id": "root-a",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "root-a" in ids
        assert "child-of-a" in ids
        assert ids.index("root-a") < ids.index("child-of-a")

    def test_empty_parent_id_treated_as_root(self, tmp_path):
        """Empty parent_id must be treated as root (appears in output, not lost)."""
        _write_metadata(
            tmp_path,
            "sess-1",
            {
                "format": "context-intelligence",
                "session_id": "sess-1",
                "parent_id": "",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "sess-1" in ids
