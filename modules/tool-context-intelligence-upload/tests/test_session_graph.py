"""Tests for session_graph.py — metadata discovery and topological sort."""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_module_tool_context_intelligence_upload.session_graph import (
    _discover_sessions,
    discover_and_sort,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_metadata(directory: Path, data: dict) -> Path:
    """Write a metadata.json file into *directory* and return the file path."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "metadata.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# _discover_sessions — format filtering
# ---------------------------------------------------------------------------


class TestDiscoverSessionsFormatFiltering:
    """Only sessions with format=='context-intelligence' should be included."""

    def test_includes_context_intelligence_format(self, tmp_path):
        write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        assert meta["session_id"] == "s1"

    def test_skips_missing_format(self, tmp_path):
        write_metadata(tmp_path / "sess1", {"session_id": "s1"})
        results = _discover_sessions(tmp_path)
        assert len(results) == 0

    def test_skips_wrong_format(self, tmp_path):
        write_metadata(tmp_path / "sess1", {"format": "other", "session_id": "s1"})
        results = _discover_sessions(tmp_path)
        assert len(results) == 0

    def test_skips_wrong_format_silently(self, tmp_path, capsys):
        write_metadata(tmp_path / "sess1", {"format": "other", "session_id": "s1"})
        _discover_sessions(tmp_path)
        captured = capsys.readouterr()
        # No stderr for wrong format
        assert captured.err == ""


# ---------------------------------------------------------------------------
# _discover_sessions — session_id field handling
# ---------------------------------------------------------------------------


class TestDiscoverSessionsSessionId:
    """session_id field handling rules."""

    def test_uses_session_id_when_present(self, tmp_path):
        write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "abc-123"},
        )
        results = _discover_sessions(tmp_path)
        _, meta = results[0]
        assert meta["session_id"] == "abc-123"

    def test_skips_when_session_id_missing(self, tmp_path):
        write_metadata(tmp_path / "sess1", {"format": "context-intelligence"})
        results = _discover_sessions(tmp_path)
        assert len(results) == 0

    def test_warns_to_stderr_when_session_id_missing(self, tmp_path, capsys):
        write_metadata(tmp_path / "sess1", {"format": "context-intelligence"})
        _discover_sessions(tmp_path)
        captured = capsys.readouterr()
        assert captured.err != ""


# ---------------------------------------------------------------------------
# _discover_sessions — parent_id field handling
# ---------------------------------------------------------------------------


class TestDiscoverSessionsParentId:
    """parent_id field handling rules — all root/child classification metadata preserved."""

    def test_parent_id_missing_treated_as_root(self, tmp_path):
        write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1

    def test_parent_id_empty_string_treated_as_root(self, tmp_path):
        write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1", "parent_id": ""},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        assert meta["parent_id"] == ""

    def test_parent_id_non_empty_is_child(self, tmp_path):
        write_metadata(
            tmp_path / "sess2",
            {
                "format": "context-intelligence",
                "session_id": "s2",
                "parent_id": "s1",
            },
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        assert meta["parent_id"] == "s1"

    def test_no_error_when_parent_id_missing(self, tmp_path, capsys):
        write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        _discover_sessions(tmp_path)
        captured = capsys.readouterr()
        assert captured.err == ""


# ---------------------------------------------------------------------------
# _discover_sessions — file/directory targeting
# ---------------------------------------------------------------------------


class TestDiscoverSessionsTargeting:
    """When target_path is a metadata.json file, read just that file."""

    def test_reads_single_file_when_target_is_metadata_json(self, tmp_path):
        meta_file = write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = _discover_sessions(meta_file)
        assert len(results) == 1

    def test_rglob_when_target_is_directory(self, tmp_path):
        write_metadata(
            tmp_path / "a",
            {"format": "context-intelligence", "session_id": "a"},
        )
        write_metadata(
            tmp_path / "b",
            {"format": "context-intelligence", "session_id": "b"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 2

    def test_finds_nested_metadata(self, tmp_path):
        write_metadata(
            tmp_path / "deep" / "nested",
            {"format": "context-intelligence", "session_id": "deep"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        assert meta["session_id"] == "deep"

    def test_returns_session_dir_not_metadata_file(self, tmp_path):
        write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = _discover_sessions(tmp_path)
        session_dir, _ = results[0]
        assert session_dir.name != "metadata.json"
        assert session_dir.is_dir()

    def test_single_file_returns_parent_dir(self, tmp_path):
        meta_file = write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = _discover_sessions(meta_file)
        session_dir, _ = results[0]
        assert session_dir == tmp_path / "sess1"


# ---------------------------------------------------------------------------
# _discover_sessions — error resilience
# ---------------------------------------------------------------------------


class TestDiscoverSessionsErrorResilience:
    """Invalid JSON and OSErrors are skipped silently."""

    def test_skips_invalid_json(self, tmp_path):
        bad = tmp_path / "bad" / "metadata.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{invalid json}")
        results = _discover_sessions(tmp_path)
        assert len(results) == 0

    def test_invalid_json_no_stderr(self, tmp_path, capsys):
        bad = tmp_path / "bad" / "metadata.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{invalid json}")
        _discover_sessions(tmp_path)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_skips_oserror_silently(self, tmp_path, monkeypatch):
        """Simulate OSError by patching open."""
        write_metadata(
            tmp_path / "sess1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        original_open = Path.open

        def mock_open(self, *args, **kwargs):
            if self.name == "metadata.json":
                raise OSError("simulated read error")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", mock_open)
        results = _discover_sessions(tmp_path)
        assert len(results) == 0

    def test_valid_session_not_affected_by_neighbor_with_bad_json(self, tmp_path):
        bad = tmp_path / "bad" / "metadata.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{invalid json}")
        write_metadata(
            tmp_path / "good",
            {"format": "context-intelligence", "session_id": "good1"},
        )
        results = _discover_sessions(tmp_path)
        assert len(results) == 1
        _, meta = results[0]
        assert meta["session_id"] == "good1"


# ---------------------------------------------------------------------------
# discover_and_sort — basic ordering
# ---------------------------------------------------------------------------


class TestDiscoverAndSortOrdering:
    """BFS topological ordering: roots alpha first, children BFS."""

    def test_single_root_returned(self, tmp_path):
        write_metadata(
            tmp_path / "s1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = discover_and_sort(tmp_path)
        assert len(results) == 1

    def test_roots_sorted_alphabetically(self, tmp_path):
        for sid in ["z-root", "a-root", "m-root"]:
            write_metadata(
                tmp_path / sid,
                {"format": "context-intelligence", "session_id": sid},
            )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert ids == ["a-root", "m-root", "z-root"]

    def test_child_comes_after_parent(self, tmp_path):
        write_metadata(
            tmp_path / "parent",
            {
                "format": "context-intelligence",
                "session_id": "parent",
                "parent_id": "",
            },
        )
        write_metadata(
            tmp_path / "child",
            {
                "format": "context-intelligence",
                "session_id": "child",
                "parent_id": "parent",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert ids.index("parent") < ids.index("child")

    def test_bfs_ordering_grandchild(self, tmp_path):
        """root -> child -> grandchild should appear in that order."""
        write_metadata(
            tmp_path / "root",
            {"format": "context-intelligence", "session_id": "root"},
        )
        write_metadata(
            tmp_path / "child",
            {
                "format": "context-intelligence",
                "session_id": "child",
                "parent_id": "root",
            },
        )
        write_metadata(
            tmp_path / "grandchild",
            {
                "format": "context-intelligence",
                "session_id": "grandchild",
                "parent_id": "child",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert ids == ["root", "child", "grandchild"]

    def test_bfs_order_two_children_before_grandchild(self, tmp_path):
        """BFS: root, child_a, child_b, grandchild (child of child_a)."""
        write_metadata(
            tmp_path / "root",
            {"format": "context-intelligence", "session_id": "root"},
        )
        write_metadata(
            tmp_path / "child_a",
            {
                "format": "context-intelligence",
                "session_id": "child_a",
                "parent_id": "root",
            },
        )
        write_metadata(
            tmp_path / "child_b",
            {
                "format": "context-intelligence",
                "session_id": "child_b",
                "parent_id": "root",
            },
        )
        write_metadata(
            tmp_path / "grandchild",
            {
                "format": "context-intelligence",
                "session_id": "grandchild",
                "parent_id": "child_a",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        # root first, then both children (before grandchild)
        assert ids[0] == "root"
        assert "grandchild" not in ids[:3] or (
            ids.index("child_a") < ids.index("grandchild")
            and ids.index("child_b") < ids.index("grandchild")
        )
        assert ids.index("child_a") < ids.index("grandchild")
        assert ids.index("child_b") < ids.index("grandchild")

    def test_parent_id_missing_treated_as_root_in_sort(self, tmp_path):
        write_metadata(
            tmp_path / "s1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "s1" in ids

    def test_parent_id_empty_treated_as_root_in_sort(self, tmp_path):
        write_metadata(
            tmp_path / "s1",
            {
                "format": "context-intelligence",
                "session_id": "s1",
                "parent_id": "",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "s1" in ids

    def test_returns_tuples_of_path_and_dict(self, tmp_path):
        write_metadata(
            tmp_path / "s1",
            {"format": "context-intelligence", "session_id": "s1"},
        )
        results = discover_and_sort(tmp_path)
        assert len(results) == 1
        session_dir, meta = results[0]
        assert isinstance(session_dir, Path)
        assert isinstance(meta, dict)

    def test_empty_directory_returns_empty_list(self, tmp_path):
        results = discover_and_sort(tmp_path)
        assert results == []


# ---------------------------------------------------------------------------
# discover_and_sort — orphan promotion
# ---------------------------------------------------------------------------


class TestDiscoverAndSortOrphans:
    """Orphans (parent_id references non-existent session) promoted to root."""

    def test_orphan_included_in_results(self, tmp_path):
        write_metadata(
            tmp_path / "orphan",
            {
                "format": "context-intelligence",
                "session_id": "orphan",
                "parent_id": "nonexistent",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "orphan" in ids

    def test_orphan_warns_to_stderr(self, tmp_path, capsys):
        write_metadata(
            tmp_path / "orphan",
            {
                "format": "context-intelligence",
                "session_id": "orphan",
                "parent_id": "nonexistent",
            },
        )
        discover_and_sort(tmp_path)
        captured = capsys.readouterr()
        assert captured.err != ""

    def test_orphan_treated_as_root_for_sorting(self, tmp_path):
        """Orphan is included at root level alphabetically."""
        write_metadata(
            tmp_path / "aaa",
            {"format": "context-intelligence", "session_id": "aaa"},
        )
        write_metadata(
            tmp_path / "orphan",
            {
                "format": "context-intelligence",
                "session_id": "orphan",
                "parent_id": "nonexistent",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "orphan" in ids
        assert "aaa" in ids
        # Both should be present as roots sorted alphabetically
        assert ids.index("aaa") < ids.index("orphan")

    def test_child_of_orphan_still_processed(self, tmp_path):
        """A child whose parent is an orphan (promoted to root) should still appear."""
        write_metadata(
            tmp_path / "orphan",
            {
                "format": "context-intelligence",
                "session_id": "orphan",
                "parent_id": "nonexistent",
            },
        )
        write_metadata(
            tmp_path / "child_of_orphan",
            {
                "format": "context-intelligence",
                "session_id": "child_of_orphan",
                "parent_id": "orphan",
            },
        )
        results = discover_and_sort(tmp_path)
        ids = [meta["session_id"] for _, meta in results]
        assert "orphan" in ids
        assert "child_of_orphan" in ids
        assert ids.index("orphan") < ids.index("child_of_orphan")
