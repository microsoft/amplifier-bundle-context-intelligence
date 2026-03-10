"""Tests for CompositeGraphStore — fan-out write store with failure isolation."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from amplifier_module_hook_context_intelligence.composite_store import CompositeGraphStore
from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
from amplifier_module_hook_context_intelligence.graph_store import GraphStore, QueryableStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_duckdb_store(forest: str = "default") -> DuckDBGraphStore:
    """Create a fresh in-memory DuckDBGraphStore."""
    return DuckDBGraphStore(connection=":memory:", graph_forest_name=forest)


def _make_failing_store() -> AsyncMock:
    """Create an AsyncMock store that raises RuntimeError on every write method."""
    store = AsyncMock(spec=GraphStore)
    store.graph_forest_name = "failing"
    store.upsert_node = AsyncMock(side_effect=RuntimeError("boom"))
    store.upsert_edge = AsyncMock(side_effect=RuntimeError("boom"))
    store.flush = AsyncMock(side_effect=RuntimeError("boom"))
    store.close = AsyncMock(side_effect=RuntimeError("boom"))
    store.get_node = AsyncMock(side_effect=RuntimeError("boom"))
    store.get_edge = AsyncMock(side_effect=RuntimeError("boom"))
    return store


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------
class TestProtocolConformance:
    """CompositeGraphStore must conform to GraphStore, NOT QueryableStore."""

    def test_conforms_to_graph_store(self):
        store = CompositeGraphStore([_make_duckdb_store()])
        assert isinstance(store, GraphStore)

    def test_does_not_conform_to_queryable_store(self):
        store = CompositeGraphStore([_make_duckdb_store()])
        assert not isinstance(store, QueryableStore)


# ---------------------------------------------------------------------------
# TestGraphForestName
# ---------------------------------------------------------------------------
class TestGraphForestName:
    """graph_forest_name from first store by default, explicit override."""

    def test_forest_name_from_first_store(self):
        s1 = _make_duckdb_store("forest-a")
        s2 = _make_duckdb_store("forest-b")
        composite = CompositeGraphStore([s1, s2])
        assert composite.graph_forest_name == "forest-a"

    def test_forest_name_explicit_override(self):
        s1 = _make_duckdb_store("forest-a")
        composite = CompositeGraphStore([s1], graph_forest_name="custom-forest")
        assert composite.graph_forest_name == "custom-forest"


# ---------------------------------------------------------------------------
# TestFanOutWrites
# ---------------------------------------------------------------------------
class TestFanOutWrites:
    """upsert_node/upsert_edge/flush/close fan out to ALL stores."""

    async def test_upsert_node_fans_out_to_all_stores(self):
        s1 = _make_duckdb_store("test")
        s2 = _make_duckdb_store("test")
        composite = CompositeGraphStore([s1, s2])

        await composite.upsert_node("n1", {"Label"}, {"key": "val"})

        # Both backing stores should have the node in their buffers
        node1 = await s1.get_node("n1")
        node2 = await s2.get_node("n1")
        assert node1 is not None
        assert node2 is not None
        assert node1["properties"]["key"] == "val"
        assert node2["properties"]["key"] == "val"

    async def test_upsert_edge_fans_out_to_all_stores(self):
        s1 = _make_duckdb_store("test")
        s2 = _make_duckdb_store("test")
        composite = CompositeGraphStore([s1, s2])

        await composite.upsert_edge("a", "b", "KNOWS", {"weight": 1})

        edge1 = await s1.get_edge("a", "b", "KNOWS")
        edge2 = await s2.get_edge("a", "b", "KNOWS")
        assert edge1 is not None
        assert edge2 is not None
        assert edge1["properties"]["weight"] == 1
        assert edge2["properties"]["weight"] == 1

    async def test_flush_fans_out_to_all_stores(self):
        s1 = _make_duckdb_store("test")
        s2 = _make_duckdb_store("test")
        composite = CompositeGraphStore([s1, s2])

        await composite.upsert_node("n1", {"Label"}, {"key": "val"})
        await composite.flush()

        # After flush, both stores should have persisted (verified via public API)
        node1 = await s1.get_node("n1")
        node2 = await s2.get_node("n1")
        assert node1 is not None
        assert node2 is not None

    async def test_close_fans_out_to_all_stores(self):
        s1 = AsyncMock(spec=GraphStore)
        s1.graph_forest_name = "test"
        s2 = AsyncMock(spec=GraphStore)
        s2.graph_forest_name = "test"
        composite = CompositeGraphStore([s1, s2])

        await composite.close()

        s1.close.assert_awaited_once()
        s2.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestFirstResponderReads
# ---------------------------------------------------------------------------
class TestFirstResponderReads:
    """get_node/get_edge returns first non-None result across stores."""

    async def test_get_node_returns_first_non_none(self):
        s1 = _make_duckdb_store("test")
        s2 = _make_duckdb_store("test")
        # Only store s2 has the node
        await s2.upsert_node("n1", {"Label"}, {"key": "val"})
        composite = CompositeGraphStore([s1, s2])

        result = await composite.get_node("n1")
        assert result is not None
        assert result["properties"]["key"] == "val"

    async def test_get_edge_returns_first_non_none(self):
        s1 = _make_duckdb_store("test")
        s2 = _make_duckdb_store("test")
        # Only store s2 has the edge
        await s2.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        composite = CompositeGraphStore([s1, s2])

        result = await composite.get_edge("a", "b", "KNOWS")
        assert result is not None
        assert result["properties"]["weight"] == 1

    async def test_get_node_returns_none_when_all_empty(self):
        s1 = _make_duckdb_store("test")
        s2 = _make_duckdb_store("test")
        composite = CompositeGraphStore([s1, s2])

        result = await composite.get_node("nonexistent")
        assert result is None

    async def test_get_edge_returns_none_when_all_empty(self):
        s1 = _make_duckdb_store("test")
        s2 = _make_duckdb_store("test")
        composite = CompositeGraphStore([s1, s2])

        result = await composite.get_edge("x", "y", "NOPE")
        assert result is None


# ---------------------------------------------------------------------------
# TestFailureIsolation
# ---------------------------------------------------------------------------
class TestFailureIsolation:
    """One store's error is logged but never propagated; others continue."""

    async def test_upsert_node_continues_after_one_store_fails(self, caplog):
        failing = _make_failing_store()
        healthy = _make_duckdb_store("test")
        composite = CompositeGraphStore([failing, healthy])

        with caplog.at_level(logging.ERROR):
            await composite.upsert_node("n1", {"Label"}, {"key": "val"})

        # Healthy store received the write
        node = await healthy.get_node("n1")
        assert node is not None
        assert node["properties"]["key"] == "val"
        # Error was logged
        assert "boom" in caplog.text

    async def test_upsert_edge_continues_after_one_store_fails(self, caplog):
        failing = _make_failing_store()
        healthy = _make_duckdb_store("test")
        composite = CompositeGraphStore([failing, healthy])

        with caplog.at_level(logging.ERROR):
            await composite.upsert_edge("a", "b", "KNOWS", {"weight": 1})

        edge = await healthy.get_edge("a", "b", "KNOWS")
        assert edge is not None
        assert edge["properties"]["weight"] == 1
        assert "boom" in caplog.text

    async def test_flush_continues_after_one_store_fails(self, caplog):
        failing = _make_failing_store()
        healthy = _make_duckdb_store("test")
        composite = CompositeGraphStore([failing, healthy])

        with caplog.at_level(logging.ERROR):
            await composite.flush()

        # No propagation — the call succeeds
        assert "boom" in caplog.text

    async def test_close_continues_after_one_store_fails(self, caplog):
        failing = _make_failing_store()
        healthy = AsyncMock(spec=GraphStore)
        healthy.graph_forest_name = "test"
        composite = CompositeGraphStore([failing, healthy])

        with caplog.at_level(logging.ERROR):
            await composite.close()

        # Healthy store still received close
        healthy.close.assert_awaited_once()
        assert "boom" in caplog.text


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    """Empty stores raises ValueError, single store passthrough."""

    def test_empty_stores_raises_value_error(self):
        with pytest.raises(ValueError, match="at least one"):
            CompositeGraphStore([])

    async def test_single_store_passthrough(self):
        s1 = _make_duckdb_store("test")
        composite = CompositeGraphStore([s1])

        await composite.upsert_node("n1", {"Label"}, {"key": "val"})
        node = await composite.get_node("n1")
        assert node is not None
        assert node["properties"]["key"] == "val"

        await composite.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        edge = await composite.get_edge("a", "b", "KNOWS")
        assert edge is not None
        assert edge["properties"]["weight"] == 1
