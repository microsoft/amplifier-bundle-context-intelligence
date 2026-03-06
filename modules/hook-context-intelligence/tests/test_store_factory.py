"""Tests for store_factory – factory function for graph store backends."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.store_factory import create_graph_store


class TestCreateGraphStore:
    """Verify create_graph_store dispatches correctly."""

    def test_returns_duckdb_store_for_explicit_type(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = create_graph_store({"type": "duckdb", "config": {"connection": ":memory:"}})
        assert isinstance(store, DuckDBGraphStore)

    def test_default_type_is_file(self):
        """Empty config should default to type='file', not duckdb."""
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

        store = create_graph_store({})
        assert isinstance(store, FileGraphStore)

    def test_duckdb_default_connection_is_memory(self):
        store = create_graph_store({"type": "duckdb"})
        assert store._connection_str == ":memory:"  # type: ignore[attr-defined]

    def test_duckdb_passes_connection_through(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store = create_graph_store({"type": "duckdb", "config": {"connection": db_path}})
        assert store._connection_str == db_path  # type: ignore[attr-defined]

    def test_raises_for_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown graph_store type: neo4j"):
            create_graph_store({"type": "neo4j"})

    def test_duckdb_conforms_to_graph_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = create_graph_store({"type": "duckdb"})
        assert isinstance(store, GraphStore)

    def test_file_conforms_to_graph_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = create_graph_store({})
        assert isinstance(store, GraphStore)

    def test_file_missing_location_raises(self):
        """FileGraphStore requires a location; omitting it should raise."""
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore  # noqa: F401

        with pytest.raises(TypeError):
            create_graph_store({"type": "file", "config": {}})


class TestHookStateServiceIntegration:
    """Verify HookStateService uses the store factory."""

    def test_explicit_duckdb_config_creates_duckdb_store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
        )
        assert isinstance(service.graph, DuckDBGraphStore)

    def test_nested_config_passed_through(self, tmp_path):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        db_path = str(tmp_path / "test.db")
        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": db_path}}}
        )
        assert service.graph._connection_str == db_path  # type: ignore[attr-defined]

    def test_unknown_store_type_raises(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        with pytest.raises(ValueError, match="Unknown graph_store type"):
            HookStateService(raw_config={"graph_store": {"type": "bogus"}})
