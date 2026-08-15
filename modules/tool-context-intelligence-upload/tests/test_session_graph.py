"""Tests for session_graph.py — metadata discovery and topological sort."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_tool_context_intelligence_upload.session_graph import (
    ScopeError,
    _discover_sessions,
    discover_and_sort,
    resolve_upload_sessions,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_metadata(tmp_path: Path, sid: str, data: dict[str, Any]) -> Path:
    """Write metadata.json under tmp_path/sessions/{sid}/context-intelligence/.

    NOTE: this helper always names the session directory after ``sid``. Some
    tests (name-independence) need the directory name to differ from the
    session_id read from metadata; use ``_write_metadata_in_dir`` for those.
    """
    directory = tmp_path / "sessions" / sid / "context-intelligence"
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "metadata.json"
    p.write_text(json.dumps(data))
    return p


def _write_metadata_in_dir(session_dir: Path, data: dict[str, Any]) -> Path:
    """Write metadata.json under session_dir/context-intelligence/.

    Unlike ``_write_metadata``, the caller controls the session directory
    name directly \u2014 it need not match ``data['session_id']``.
    """
    directory = session_dir / "context-intelligence"
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


# ---------------------------------------------------------------------------
# TestResolveUploadSessions \u2014 descendants-only closure + scope resolution
# ---------------------------------------------------------------------------


class TestResolveUploadSessions:
    """Scope resolution: sub-session tree walk, descendants-only closure."""

    def test_flat_siblings_closure_from_root_dir(self, tmp_path):
        """KEY REGRESSION: root+child+grandchild as flat siblings; pointing at
        the root's session DIR must return the whole descendants closure."""
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        _write_metadata(
            tmp_path,
            "child",
            {"format": "context-intelligence", "session_id": "child", "parent_id": "root"},
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

        root_dir = tmp_path / "sessions" / "root"
        scope = resolve_upload_sessions(root_dir)

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["root", "child", "grandchild"]
        assert scope.mode == "single"
        assert scope.selected_root_ids == ["root"]
        assert scope.selected_count == 3
        assert scope.total_discovered == 3

    def test_flat_siblings_closure_from_root_metadata_file(self, tmp_path):
        """Same regression, but pointing at root's metadata.json file directly."""
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        _write_metadata(
            tmp_path,
            "child",
            {"format": "context-intelligence", "session_id": "child", "parent_id": "root"},
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

        root_meta_file = tmp_path / "sessions" / "root" / "context-intelligence" / "metadata.json"
        scope = resolve_upload_sessions(root_meta_file)

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["root", "child", "grandchild"]

    def test_name_independence_dir_name_differs_from_session_id(self, tmp_path):
        """Directory name may carry an agent suffix and need not equal session_id."""
        session_dir = tmp_path / "sessions" / "0000-x_agentfoo"
        _write_metadata_in_dir(
            session_dir, {"format": "context-intelligence", "session_id": "sess-R"}
        )

        scope = resolve_upload_sessions(session_dir)

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["sess-R"]
        assert scope.selected_root_ids == ["sess-R"]

    def test_sibling_isolation(self, tmp_path):
        """Selecting root R1 must never pull in sibling root R2's subtree."""
        _write_metadata(tmp_path, "r1", {"format": "context-intelligence", "session_id": "r1"})
        _write_metadata(
            tmp_path,
            "c1",
            {"format": "context-intelligence", "session_id": "c1", "parent_id": "r1"},
        )
        _write_metadata(tmp_path, "r2", {"format": "context-intelligence", "session_id": "r2"})
        _write_metadata(
            tmp_path,
            "c2",
            {"format": "context-intelligence", "session_id": "c2", "parent_id": "r2"},
        )

        scope = resolve_upload_sessions(tmp_path / "sessions" / "r1")

        ids = {meta["session_id"] for _, meta in scope.sessions}
        assert ids == {"r1", "c1"}
        assert scope.total_discovered == 4

    def test_whole_mode_pointing_at_sessions_dir(self, tmp_path):
        """Pointing directly at sessions/ selects ALL roots + descendants."""
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        _write_metadata(
            tmp_path,
            "child",
            {"format": "context-intelligence", "session_id": "child", "parent_id": "root"},
        )

        scope = resolve_upload_sessions(tmp_path / "sessions")

        assert scope.mode == "whole"
        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["root", "child"]

    def test_whole_mode_pointing_above_sessions_dir(self, tmp_path):
        """Pointing above sessions/ (existing behavior) still walks the whole tree."""
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        _write_metadata(
            tmp_path,
            "child",
            {"format": "context-intelligence", "session_id": "child", "parent_id": "root"},
        )

        scope = resolve_upload_sessions(tmp_path)

        assert scope.mode == "whole"
        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["root", "child"]

        # discover_and_sort must still work unmodified for this same layout.
        legacy = discover_and_sort(tmp_path)
        legacy_ids = [meta["session_id"] for _, meta in legacy]
        assert legacy_ids == ["root", "child"]

    def test_cycle_guard_self_parent(self, tmp_path):
        """A session whose parent_id references itself must not infinite-loop."""
        _write_metadata(
            tmp_path,
            "loopy",
            {"format": "context-intelligence", "session_id": "loopy", "parent_id": "loopy"},
        )

        scope = resolve_upload_sessions(tmp_path / "sessions" / "loopy")

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["loopy"]

    def test_cycle_guard_mutual_parent(self, tmp_path):
        """A <-> B mutual parent_id references must terminate, each node once."""
        _write_metadata(
            tmp_path,
            "a",
            {"format": "context-intelligence", "session_id": "a", "parent_id": "b"},
        )
        _write_metadata(
            tmp_path,
            "b",
            {"format": "context-intelligence", "session_id": "b", "parent_id": "a"},
        )

        scope = resolve_upload_sessions(tmp_path / "sessions" / "a")

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert sorted(ids) == ["a", "b"]
        assert len(ids) == len(set(ids))

    def test_wrong_suffix_directory_not_discovered(self, tmp_path):
        """Only <session>/context-intelligence/metadata.json counts \u2014 not
        <session>/artifacts/metadata.json even with a matching format."""
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        bogus_dir = tmp_path / "sessions" / "root" / "artifacts"
        bogus_dir.mkdir(parents=True)
        (bogus_dir / "metadata.json").write_text(
            json.dumps({"format": "context-intelligence", "session_id": "bogus"})
        )

        scope = resolve_upload_sessions(tmp_path / "sessions" / "root")

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["root"]
        assert scope.total_discovered == 1

    def test_duplicate_session_id_deduped_with_warning(self, tmp_path, capsys):
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        _write_metadata_in_dir(
            tmp_path / "sessions" / "root-dup",
            {"format": "context-intelligence", "session_id": "root"},
        )

        scope = resolve_upload_sessions(tmp_path / "sessions")

        assert scope.total_discovered == 1
        captured = capsys.readouterr()
        assert "duplicate" in captured.err.lower()

    def test_malformed_json_sibling_skipped_rest_discovered(self, tmp_path):
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        bad_dir = tmp_path / "sessions" / "bad" / "context-intelligence"
        bad_dir.mkdir(parents=True)
        (bad_dir / "metadata.json").write_text("{not valid json")

        scope = resolve_upload_sessions(tmp_path / "sessions")

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["root"]

    def test_scope_error_when_no_sessions_found(self, tmp_path):
        with pytest.raises(ScopeError):
            resolve_upload_sessions(tmp_path)

    def test_scope_error_when_single_mode_id_unresolvable(self, tmp_path):
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        ghost_dir = tmp_path / "sessions" / "ghost"
        ghost_dir.mkdir(parents=True)  # no context-intelligence/metadata.json inside

        with pytest.raises(ScopeError):
            resolve_upload_sessions(ghost_dir)

    def test_dangling_parent_when_selecting_mid_tree_node(self, tmp_path):
        """Selecting a mid-tree node whose parent is excluded from the closure
        must report that parent_id as dangling (benign placeholder)."""
        _write_metadata(tmp_path, "root", {"format": "context-intelligence", "session_id": "root"})
        _write_metadata(
            tmp_path,
            "mid",
            {"format": "context-intelligence", "session_id": "mid", "parent_id": "root"},
        )
        _write_metadata(
            tmp_path,
            "leaf",
            {"format": "context-intelligence", "session_id": "leaf", "parent_id": "mid"},
        )

        scope = resolve_upload_sessions(tmp_path / "sessions" / "mid")

        ids = [meta["session_id"] for _, meta in scope.sessions]
        assert ids == ["mid", "leaf"]
        assert scope.dangling_parent_ids == ["root"]
