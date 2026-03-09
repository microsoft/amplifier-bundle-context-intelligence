"""Tests for the GraphStore base protocol and QueryableStore extension."""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# GraphStore base protocol
# ---------------------------------------------------------------------------


def test_graph_store_is_runtime_checkable():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    assert hasattr(GraphStore, "__protocol_attrs__") or hasattr(GraphStore, "_is_runtime_protocol")


def test_conforming_class_passes_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class FakeStore:
        @property
        def graph_forest_name(self) -> str:
            return "test"

        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

    store = FakeStore()
    assert isinstance(store, GraphStore)


def test_class_with_execute_query_still_passes_graph_store():
    """Extra methods (like execute_query) must not break GraphStore conformance."""
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class StoreWithQuery:
        @property
        def graph_forest_name(self) -> str:
            return "test"

        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def execute_query(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

    store = StoreWithQuery()
    assert isinstance(store, GraphStore)


def test_missing_upsert_node_fails_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class BadStore:
        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

    store = BadStore()
    assert not isinstance(store, GraphStore)


def test_missing_flush_fails_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class BadStore:
        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def close(self) -> None: ...

    store = BadStore()
    assert not isinstance(store, GraphStore)


def test_graph_store_protocol_has_graph_forest_name_property():
    """A class with all 6 base methods but NO graph_forest_name fails isinstance."""
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class MissingForestName:
        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

    store = MissingForestName()
    assert not isinstance(store, GraphStore)


def test_graph_state_conforms_to_graph_store():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore
    from amplifier_module_hook_context_intelligence.services import GraphState

    graph = GraphState()
    assert isinstance(graph, GraphStore)


# ---------------------------------------------------------------------------
# QueryableStore extension
# ---------------------------------------------------------------------------


def test_queryable_store_is_runtime_checkable():
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    assert hasattr(QueryableStore, "__protocol_attrs__") or hasattr(
        QueryableStore, "_is_runtime_protocol"
    )


def test_queryable_conforming_class_passes_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    class FakeQueryable:
        @property
        def graph_forest_name(self) -> str:
            return "test"

        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

        @property
        def supported_dialects(self) -> frozenset[str]:
            return frozenset({"sql"})

        async def execute_query(
            self,
            query: str,
            params: dict[str, Any] | None = None,
            dialect: str | None = None,
        ) -> list[dict[str, Any]]: ...

    store = FakeQueryable()
    assert isinstance(store, QueryableStore)


def test_queryable_missing_supported_dialects_fails():
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    class MissingDialects:
        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

        async def execute_query(
            self,
            query: str,
            params: dict[str, Any] | None = None,
            dialect: str | None = None,
        ) -> list[dict[str, Any]]: ...

    store = MissingDialects()
    assert not isinstance(store, QueryableStore)


def test_base_graph_store_is_not_queryable():
    """A class with only the 6 base GraphStore methods is NOT QueryableStore."""
    from amplifier_module_hook_context_intelligence.graph_store import (
        GraphStore,
        QueryableStore,
    )

    class BaseOnly:
        @property
        def graph_forest_name(self) -> str:
            return "test"

        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

    store = BaseOnly()
    assert isinstance(store, GraphStore)
    assert not isinstance(store, QueryableStore)


def test_queryable_execute_query_accepts_graph_forest_name_param():
    """Document the expected execute_query signature including graph_forest_name.

    Python protocol isinstance checks don't validate parameter names, so this
    test documents the expected signature rather than enforcing it at runtime.
    """
    import inspect

    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    class QueryableWithForest:
        @property
        def graph_forest_name(self) -> str:
            return "test"

        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...

        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

        @property
        def supported_dialects(self) -> frozenset[str]:
            return frozenset({"sql"})

        async def execute_query(
            self,
            query: str,
            params: dict[str, Any] | None = None,
            dialect: str | None = None,
            graph_forest_name: str | None = None,
        ) -> list[dict[str, Any]]: ...

    store = QueryableWithForest()
    assert isinstance(store, QueryableStore)

    # Verify the protocol itself declares graph_forest_name in execute_query
    sig = inspect.signature(QueryableStore.execute_query)
    assert "graph_forest_name" in sig.parameters, (
        "QueryableStore.execute_query must declare a graph_forest_name parameter"
    )
    param = sig.parameters["graph_forest_name"]
    assert param.default is None, "graph_forest_name must default to None"


def test_duckdb_store_is_queryable():
    from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    store = DuckDBGraphStore(graph_forest_name="test")
    assert isinstance(store, QueryableStore)
