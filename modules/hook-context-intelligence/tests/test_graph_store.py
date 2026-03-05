"""Tests for the GraphStore async protocol."""

from __future__ import annotations

from typing import Any


def test_graph_store_is_runtime_checkable():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    assert hasattr(GraphStore, "__protocol_attrs__") or hasattr(GraphStore, "_is_runtime_protocol")


def test_conforming_class_passes_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class FakeStore:
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

    store = FakeStore()
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
        async def execute_query(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]: ...
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
        async def execute_query(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]: ...
        async def close(self) -> None: ...

    store = BadStore()
    assert not isinstance(store, GraphStore)


def test_graph_state_conforms_to_graph_store():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore
    from amplifier_module_hook_context_intelligence.services import GraphState

    graph = GraphState()
    assert isinstance(graph, GraphStore)
