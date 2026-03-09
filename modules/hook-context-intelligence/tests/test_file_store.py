"""Tests for FileGraphStore — JSON file-based GraphStore implementation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from amplifier_module_hook_context_intelligence.file_store import FileGraphStore
from amplifier_module_hook_context_intelligence.graph_store import GraphStore
from amplifier_module_hook_context_intelligence.utils import make_edge_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FOREST = "test-forest"


def _make_store(tmp_path: Path, name: str = "graph") -> FileGraphStore:
    """Create a FileGraphStore rooted at *tmp_path* with a test forest."""
    return FileGraphStore(graph_store_root=str(tmp_path / name), graph_forest_name=_FOREST)


def _forest_dir(tmp_path: Path, name: str = "graph") -> Path:
    """Return the forest directory for the test helper store."""
    return tmp_path / name / _FOREST


def _node_file(base: Path, node_id: str) -> Path:
    return base / "nodes" / f"{node_id}.json"


def _edge_file(base: Path, source: str, target: str, edge_type: str) -> Path:
    edge_id = make_edge_id(source, target, edge_type)
    return base / "edges" / f"{edge_id}.json"


# ---------------------------------------------------------------------------
# 1. Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """FileGraphStore must satisfy the GraphStore protocol."""

    def test_isinstance_check(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert isinstance(store, GraphStore)


# ---------------------------------------------------------------------------
# 2. Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    """Constructor creates directories and initialises empty buffers."""

    def test_creates_node_and_edge_dirs(self, tmp_path: Path) -> None:
        _make_store(tmp_path)
        loc = _forest_dir(tmp_path)
        assert (loc / "nodes").is_dir()
        assert (loc / "edges").is_dir()

    def test_tilde_expansion(self) -> None:
        import shutil

        expected_root = Path.home() / "test-graph-store"
        try:
            store = FileGraphStore(graph_store_root="~/test-graph-store", graph_forest_name="f")
            assert store._graph_store_root == expected_root
            assert store._location == expected_root / "f"
        finally:
            shutil.rmtree(expected_root, ignore_errors=True)

    def test_empty_buffers(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store._node_buffer == {}
        assert store._edge_buffer == {}

    def test_args_required(self) -> None:
        with pytest.raises(TypeError):
            FileGraphStore()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 3. Buffer writes (no I/O)
# ---------------------------------------------------------------------------


class TestBufferWrites:
    """Upsert operations write to buffers only — no files on disk."""

    async def test_node_goes_to_buffer_not_disk(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_node("n1", {"Label"}, {"k": "v"})

        assert "n1" in store._node_buffer
        assert not _node_file(_forest_dir(tmp_path), "n1").exists()

    async def test_edge_goes_to_buffer_not_disk(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_edge("a", "b", "REL", {"w": 1})

        assert ("a", "b", "REL") in store._edge_buffer
        assert not _edge_file(_forest_dir(tmp_path), "a", "b", "REL").exists()

    async def test_label_merge(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_node("n1", {"A"}, {})
        await store.upsert_node("n1", {"B"}, {})

        assert store._node_buffer["n1"]["labels"] == {"A", "B"}

    async def test_property_merge(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_node("n1", {"A"}, {"x": 1})
        await store.upsert_node("n1", set(), {"y": 2})

        props = store._node_buffer["n1"]["properties"]
        assert props == {"x": 1, "y": 2}

    async def test_edge_property_merge(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_edge("a", "b", "REL", {"x": 1})
        await store.upsert_edge("a", "b", "REL", {"y": 2})

        props = store._edge_buffer[("a", "b", "REL")]["properties"]
        assert props == {"x": 1, "y": 2}


# ---------------------------------------------------------------------------
# 4. Buffer-first reads
# ---------------------------------------------------------------------------


class TestBufferFirstReads:
    """get_node / get_edge must check the buffer before disk."""

    async def test_buffered_node(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_node("n1", {"L"}, {"k": "v"})

        result = await store.get_node("n1")
        assert result is not None
        assert result["id"] == "n1"
        assert result["labels"] == {"L"}
        assert result["properties"]["k"] == "v"

    async def test_buffered_edge(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_edge("a", "b", "REL", {"w": 1})

        result = await store.get_edge("a", "b", "REL")
        assert result is not None
        assert result["source"] == "a"
        assert result["target"] == "b"
        assert result["type"] == "REL"
        assert result["properties"]["w"] == 1

    async def test_nonexistent_node(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = await store.get_node("missing")
        assert result is None

    async def test_nonexistent_edge(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = await store.get_edge("a", "b", "NOPE")
        assert result is None

    async def test_buffer_wins_over_stale_disk(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        loc = _forest_dir(tmp_path)

        # Write stale data directly to disk
        stale = {"id": "n1", "labels": ["Old"], "properties": {"k": "stale"}}
        _node_file(loc, "n1").write_text(json.dumps(stale))

        # Buffer has newer data
        await store.upsert_node("n1", {"New"}, {"k": "fresh"})

        result = await store.get_node("n1")
        assert result is not None
        assert result["properties"]["k"] == "fresh"
        assert "New" in result["labels"]


# ---------------------------------------------------------------------------
# 5. Flush
# ---------------------------------------------------------------------------


class TestFlush:
    """flush() persists buffers to JSON files."""

    async def test_writes_node_files(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        loc = _forest_dir(tmp_path)
        await store.upsert_node("n1", {"A", "B"}, {"k": "v"})
        await store.flush()

        data = json.loads(_node_file(loc, "n1").read_text())
        assert data["id"] == "n1"
        assert data["labels"] == ["A", "B"]  # sorted list
        assert data["properties"]["k"] == "v"

    async def test_writes_edge_files(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        loc = _forest_dir(tmp_path)
        await store.upsert_edge("a", "b", "REL", {"w": 1})
        await store.flush()

        data = json.loads(_edge_file(loc, "a", "b", "REL").read_text())
        assert data["source"] == "a"
        assert data["target"] == "b"
        assert data["type"] == "REL"
        assert data["properties"]["w"] == 1

    async def test_clears_buffers(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_node("n1", {"A"}, {})
        await store.flush()

        assert store._node_buffer == {}
        assert store._edge_buffer == {}

    async def test_get_node_from_disk_after_flush(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_node("n1", {"A"}, {"k": "v"})
        await store.flush()

        result = await store.get_node("n1")
        assert result is not None
        assert result["id"] == "n1"
        assert result["labels"] == {"A"}

    async def test_get_edge_from_disk_after_flush(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_edge("a", "b", "REL", {"w": 1})
        await store.flush()

        result = await store.get_edge("a", "b", "REL")
        assert result is not None
        assert result["source"] == "a"
        assert result["properties"]["w"] == 1

    async def test_empty_flush_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.flush()  # should not raise

    async def test_flush_restores_buffers_on_write_failure(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await store.upsert_node("n1", {"A"}, {"k": "v"})
        await store.upsert_edge("a", "b", "REL", {"w": 1})

        # Patch _atomic_write to simulate an I/O error during flush
        with patch.object(FileGraphStore, "_atomic_write", side_effect=OSError("disk full")):
            await store.flush()

        # Buffers should be restored for retry
        assert "n1" in store._node_buffer
        assert ("a", "b", "REL") in store._edge_buffer

    async def test_merge_with_existing_file(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        loc = _forest_dir(tmp_path)

        # First flush: initial data
        await store.upsert_node("n1", {"A"}, {"x": 1})
        await store.flush()

        # Second flush: additional data
        await store.upsert_node("n1", {"B"}, {"y": 2})
        await store.flush()

        data = json.loads(_node_file(loc, "n1").read_text())
        assert set(data["labels"]) == {"A", "B"}
        assert data["properties"]["x"] == 1
        assert data["properties"]["y"] == 2


# ---------------------------------------------------------------------------
# 6. close
# ---------------------------------------------------------------------------


class TestClose:
    """close() must flush pending data."""

    async def test_flushes_pending_data(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        loc = _forest_dir(tmp_path)
        await store.upsert_node("n1", {"A"}, {"k": "v"})
        await store.close()

        assert _node_file(loc, "n1").exists()


# ---------------------------------------------------------------------------
# 7. Persistence (close + reopen)
# ---------------------------------------------------------------------------


class TestPersistence:
    """Data survives close and reopen."""

    async def test_data_survives_close_and_reopen(self, tmp_path: Path) -> None:
        store1 = _make_store(tmp_path)
        await store1.upsert_node("n1", {"A"}, {"k": "v"})
        await store1.upsert_edge("a", "b", "REL", {"w": 1})
        await store1.close()

        store2 = _make_store(tmp_path)
        node = await store2.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"A"}
        assert node["properties"]["k"] == "v"

        edge = await store2.get_edge("a", "b", "REL")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["properties"]["w"] == 1


# ---------------------------------------------------------------------------
# 8. Forest awareness
# ---------------------------------------------------------------------------


class TestForestAwareness:
    """FileGraphStore constructor accepts graph_store_root + graph_forest_name."""

    def test_constructor_accepts_root_and_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="my-forest")
        assert store._graph_store_root == tmp_path
        assert store._graph_forest_name == "my-forest"

    def test_working_directory_is_root_slash_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="proj-a")
        assert store._location == tmp_path / "proj-a"

    def test_nodes_dir_inside_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="proj-a")
        assert store._nodes_dir == tmp_path / "proj-a" / "nodes"

    def test_edges_dir_inside_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="proj-a")
        assert store._edges_dir == tmp_path / "proj-a" / "edges"

    def test_dirs_created_on_construction(self, tmp_path: Path) -> None:
        FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="proj-a")
        assert (tmp_path / "proj-a" / "nodes").is_dir()
        assert (tmp_path / "proj-a" / "edges").is_dir()

    def test_tilde_expansion_in_root(self) -> None:
        import shutil

        home = Path.home()
        expected_root = home / "test-forest-root"
        try:
            store = FileGraphStore(
                graph_store_root="~/test-forest-root",
                graph_forest_name="f1",
            )
            assert store._graph_store_root == expected_root
            assert store._location == expected_root / "f1"
        finally:
            shutil.rmtree(expected_root, ignore_errors=True)

    def test_graph_forest_name_is_readonly(self, tmp_path: Path) -> None:
        store = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="my-forest")
        assert store.graph_forest_name == "my-forest"
        with pytest.raises(AttributeError):
            store.graph_forest_name = "other"  # type: ignore[misc]

    async def test_data_isolated_between_forests(self, tmp_path: Path) -> None:
        store_a = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="forest-a")
        store_b = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="forest-b")

        await store_a.upsert_node("n1", {"A"}, {"k": "from-a"})
        await store_a.flush()

        # store_b should NOT see store_a's data
        result = await store_b.get_node("n1")
        assert result is None

    async def test_persistence_across_forest_reopen(self, tmp_path: Path) -> None:
        store1 = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="persist-f")
        await store1.upsert_node("n1", {"A"}, {"k": "v"})
        await store1.upsert_edge("a", "b", "REL", {"w": 1})
        await store1.close()

        store2 = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="persist-f")
        node = await store2.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"A"}
        assert node["properties"]["k"] == "v"

        edge = await store2.get_edge("a", "b", "REL")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["properties"]["w"] == 1
