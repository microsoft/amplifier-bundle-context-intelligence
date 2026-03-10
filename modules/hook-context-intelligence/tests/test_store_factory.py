"""Tests for store_factory – factory function for graph store backends."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.store_factory import create_graph_store
from tests.conftest import NEO4J_AUTH, NEO4J_DATABASE, NEO4J_URI


class TestCreateGraphStore:
    """Verify create_graph_store dispatches correctly."""

    def test_returns_duckdb_store_for_explicit_type(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = create_graph_store({"type": "duckdb", "config": {"connection": ":memory:"}})
        assert isinstance(store, DuckDBGraphStore)

    def test_default_type_is_file(self, tmp_path):
        """Empty config should default to type='file', not duckdb."""
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

        store = create_graph_store({"config": {"graph_store_root": str(tmp_path)}})
        assert isinstance(store, FileGraphStore)

    def test_duckdb_default_connection_is_memory(self):
        store = create_graph_store({"type": "duckdb"})
        assert store._connection_str == ":memory:"  # type: ignore[attr-defined]

    def test_duckdb_passes_connection_through(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store = create_graph_store({"type": "duckdb", "config": {"connection": db_path}})
        assert store._connection_str == db_path  # type: ignore[attr-defined]

    def test_raises_for_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown graph_store type: bogus"):
            create_graph_store({"type": "bogus"})

    def test_duckdb_conforms_to_graph_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = create_graph_store({"type": "duckdb"})
        assert isinstance(store, GraphStore)

    def test_file_conforms_to_graph_store_protocol(self, tmp_path):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = create_graph_store({"config": {"graph_store_root": str(tmp_path)}})
        assert isinstance(store, GraphStore)

    # -- graph_forest_name tests (require Tasks 5+6 to complete backends) --

    def test_graph_forest_name_defaults_to_default(self):
        """When graph_forest_name is omitted, it defaults to 'default'."""
        store = create_graph_store({"type": "duckdb", "config": {"connection": ":memory:"}})
        assert store.graph_forest_name == "default"

    def test_graph_forest_name_passed_to_duckdb(self):
        """Explicit graph_forest_name at config level is passed to DuckDB backend."""
        store = create_graph_store(
            {
                "type": "duckdb",
                "graph_forest_name": "my-project",
                "config": {"connection": ":memory:"},
            }
        )
        assert store.graph_forest_name == "my-project"

    def test_graph_forest_name_passed_to_file(self, tmp_path):
        """Explicit graph_forest_name at config level is passed to file backend."""
        store = create_graph_store(
            {
                "type": "file",
                "graph_forest_name": "my-project",
                "config": {"graph_store_root": str(tmp_path)},
            }
        )
        assert store.graph_forest_name == "my-project"

    # -- Neo4j factory tests -------------------------------------------------

    async def test_returns_neo4j_store_for_explicit_type(self):
        """create_graph_store with type='neo4j' returns a Neo4jGraphStore instance."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = create_graph_store(
            {
                "type": "neo4j",
                "config": {
                    "uri": NEO4J_URI,
                    "auth": NEO4J_AUTH,
                    "database": NEO4J_DATABASE,
                },
            }
        )
        try:
            assert isinstance(store, Neo4jGraphStore)
        finally:
            await store.close()

    async def test_graph_forest_name_passed_to_neo4j(self):
        """Explicit graph_forest_name is forwarded to the Neo4j backend."""
        store = create_graph_store(
            {
                "type": "neo4j",
                "graph_forest_name": "my-project",
                "config": {
                    "uri": NEO4J_URI,
                    "auth": NEO4J_AUTH,
                    "database": NEO4J_DATABASE,
                },
            }
        )
        try:
            assert store.graph_forest_name == "my-project"
        finally:
            await store.close()

    async def test_neo4j_store_is_queryable(self):
        """Neo4j store returned by factory conforms to the QueryableStore protocol."""
        from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

        store = create_graph_store(
            {
                "type": "neo4j",
                "config": {
                    "uri": NEO4J_URI,
                    "auth": NEO4J_AUTH,
                    "database": NEO4J_DATABASE,
                },
            }
        )
        try:
            assert isinstance(store, QueryableStore)
        finally:
            await store.close()

    async def test_neo4j_database_defaults_to_neo4j(self):
        """Omitting 'database' from config still creates the store (defaults to 'neo4j')."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = create_graph_store(
            {
                "type": "neo4j",
                "config": {
                    "uri": NEO4J_URI,
                    "auth": NEO4J_AUTH,
                },
            }
        )
        try:
            assert isinstance(store, Neo4jGraphStore)
            assert store._database == "neo4j"  # type: ignore[attr-defined]
        finally:
            await store.close()


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


class TestCreateCompositeStore:
    """Verify create_composite_store builds CompositeGraphStore from configs."""

    def test_creates_composite_from_multiple_configs(self):
        from amplifier_module_hook_context_intelligence.composite_store import CompositeGraphStore
        from amplifier_module_hook_context_intelligence.store_factory import create_composite_store

        configs = [
            {"type": "duckdb", "config": {"connection": ":memory:"}},
            {"type": "duckdb", "config": {"connection": ":memory:"}},
        ]
        composite = create_composite_store(configs)
        assert isinstance(composite, CompositeGraphStore)
        assert len(composite._stores) == 2

    def test_creates_composite_from_single_config(self):
        from amplifier_module_hook_context_intelligence.composite_store import CompositeGraphStore
        from amplifier_module_hook_context_intelligence.store_factory import create_composite_store

        configs = [{"type": "duckdb", "config": {"connection": ":memory:"}}]
        composite = create_composite_store(configs)
        assert isinstance(composite, CompositeGraphStore)
        assert len(composite._stores) == 1

    def test_empty_config_list_raises_value_error(self):
        from amplifier_module_hook_context_intelligence.store_factory import create_composite_store

        with pytest.raises(ValueError, match="at least one"):
            create_composite_store([])

    def test_graph_forest_name_forwarded_to_stores(self):
        from amplifier_module_hook_context_intelligence.store_factory import create_composite_store

        configs = [
            {
                "type": "duckdb",
                "graph_forest_name": "my-forest",
                "config": {"connection": ":memory:"},
            },
        ]
        composite = create_composite_store(configs)
        assert composite.graph_forest_name == "my-forest"

    def test_conforms_to_graph_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.store_factory import create_composite_store

        configs = [{"type": "duckdb", "config": {"connection": ":memory:"}}]
        composite = create_composite_store(configs)
        assert isinstance(composite, GraphStore)
