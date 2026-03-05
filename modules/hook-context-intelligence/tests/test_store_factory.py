"""Tests for store_factory – factory function for graph store backends."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.store_factory import create_graph_store


class TestCreateGraphStore:
    """Verify create_graph_store dispatches correctly."""

    def test_returns_duckdb_store_for_explicit_type(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = create_graph_store({"type": "duckdb"})
        assert isinstance(store, DuckDBGraphStore)

    def test_returns_duckdb_store_for_empty_config(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = create_graph_store({})
        assert isinstance(store, DuckDBGraphStore)

    def test_default_connection_is_memory(self):
        store = create_graph_store({})
        assert store._connection_str == ":memory:"  # type: ignore[attr-defined]

    def test_passes_connection_string_through(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store = create_graph_store({"type": "duckdb", "connection": db_path})
        assert store._connection_str == db_path  # type: ignore[attr-defined]

    def test_raises_for_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown graph_store type: neo4j"):
            create_graph_store({"type": "neo4j"})

    def test_result_conforms_to_graph_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = create_graph_store({})
        assert isinstance(store, GraphStore)


class TestHookStateServiceIntegration:
    """Verify HookStateService uses the store factory."""

    def test_default_config_creates_duckdb_store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, DuckDBGraphStore)

    async def test_nested_graph_store_config_passed_through(self, tmp_path):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        db_path = str(tmp_path / "test.db")
        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "connection": db_path}}
        )
        try:
            assert service.graph._connection_str == db_path  # type: ignore[attr-defined]
        finally:
            await service.graph.close()

    def test_unknown_store_type_raises(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        with pytest.raises(ValueError, match="Unknown graph_store type"):
            HookStateService(raw_config={"graph_store": {"type": "bogus"}})
