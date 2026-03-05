"""Tests for HookStateService, GraphState, and HookConfig."""

from __future__ import annotations


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
        assert graph.current_session is None
        assert graph.current_run is None
        assert graph.current_step is None
        assert graph.step_counter == 0

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

    async def test_execute_query_raises_not_implemented(self):
        import pytest

        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        with pytest.raises(NotImplementedError):
            await graph.execute_query("MATCH (n) RETURN n")


class TestHookStateService:
    def test_construction(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import (
            HookConfig,
            HookStateService,
        )

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphStore)
        assert isinstance(service.config, HookConfig)

    async def test_graph_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        await service.graph.upsert_node("test", labels={"Test"}, properties={})
        assert await service.graph.get_node("test") is not None

    def test_config_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={"exclude_events": ["foo:bar"]})
        assert service.config.is_excluded("foo:bar") is True
