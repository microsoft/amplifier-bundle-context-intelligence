"""Tests for HookStateService, GraphState, and HookConfig."""

from __future__ import annotations

import pytest


class TestHookConfig:
    def test_construction_with_empty_config(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={})
        assert config.exclude_events == set()

    def test_construction_with_exclude_events(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(
            raw_config={"exclude_events": ["content_block:delta", "thinking:delta"]}
        )
        assert config.exclude_events == {"content_block:delta", "thinking:delta"}

    def test_is_excluded_exact_match(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={"exclude_events": ["session:start"]})
        assert config.is_excluded("session:start") is True
        assert config.is_excluded("session:end") is False

    def test_is_excluded_wildcard_match(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={"exclude_events": ["session-naming:*"]})
        assert config.is_excluded("session-naming:foo") is True
        assert config.is_excluded("session-naming:bar") is True
        assert config.is_excluded("session:start") is False


class TestGraphState:
    def test_construction(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph._graph_forest_name == "default"
        assert graph._nodes == {}
        assert graph._edges == {}

    async def test_upsert_node_creates_node(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_node("s1", labels={"Session"}, properties={"started": True})
        node = await graph.get_node("s1")
        assert node is not None
        assert node["labels"] == {"Session"}
        assert node["properties"]["started"] is True

    async def test_upsert_node_updates_existing(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_node("s1", labels={"Session"}, properties={"started": True})
        await graph.upsert_node("s1", labels={"Session"}, properties={"ended": True})
        node = await graph.get_node("s1")
        assert node is not None
        assert node["properties"]["started"] is True
        assert node["properties"]["ended"] is True

    async def test_upsert_node_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        result = await graph.upsert_node("s1", labels={"Session"}, properties={})
        assert result is None

    async def test_upsert_node_merges_labels(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_node("s1", labels={"Session", "Root"}, properties={})
        await graph.upsert_node("s1", labels={"Resumed"}, properties={})
        node = await graph.get_node("s1")
        assert node is not None
        assert node["labels"] == {"Session", "Root", "Resumed"}

    async def test_upsert_edge_creates_edge(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_edge("s1", "r1", edge_type="CONTAINS_RUN", properties={})
        edge = await graph.get_edge("s1", "r1", edge_type="CONTAINS_RUN")
        assert edge is not None

    async def test_upsert_edge_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        result = await graph.upsert_edge("s1", "r1", edge_type="CONTAINS_RUN", properties={})
        assert result is None

    async def test_get_nonexistent_node_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert await graph.get_node("nonexistent") is None

    async def test_get_nonexistent_edge_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert await graph.get_edge("a", "b", edge_type="X") is None

    async def test_flush_is_noop(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.flush()

    async def test_close_is_noop(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.close()

    def test_graph_forest_name_default(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph.graph_forest_name == "default"

    def test_graph_forest_name_explicit(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState(graph_forest_name="my-project")
        assert graph.graph_forest_name == "my-project"

    def test_graph_forest_name_is_readonly(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        with pytest.raises(AttributeError):
            graph.graph_forest_name = "other"


class TestHookStateService:
    def test_construction_with_explicit_duckdb(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import (
            HookConfig,
            HookStateService,
        )

        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
        )
        assert isinstance(service.graph, GraphStore)
        assert isinstance(service.config, HookConfig)

    async def test_graph_accessible_with_explicit_duckdb(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
        )
        await service.graph.upsert_node("test", labels={"Test"}, properties={})
        assert await service.graph.get_node("test") is not None

    def test_config_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(
            raw_config={
                "exclude_events": ["foo:bar"],
                "graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}},
            }
        )
        assert service.config.is_excluded("foo:bar") is True


class TestHookStateServicePrebuiltStore:
    def test_uses_prebuilt_store_when_provided(self):
        from amplifier_module_hook_context_intelligence.services import (
            GraphState,
            HookStateService,
        )

        prebuilt = GraphState(graph_forest_name="prebuilt")
        service = HookStateService(raw_config={}, graph_store=prebuilt)
        assert service.graph is prebuilt

    def test_falls_back_to_factory_when_not_provided(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
        )
        assert isinstance(service.graph, GraphStore)

    def test_prebuilt_composite_store_accepted(self):
        from amplifier_module_hook_context_intelligence.composite_store import CompositeGraphStore
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.services import HookStateService

        backing = DuckDBGraphStore(connection=":memory:", graph_forest_name="composite-test")
        composite = CompositeGraphStore(stores=[backing])
        service = HookStateService(raw_config={}, graph_store=composite)
        assert service.graph is composite
