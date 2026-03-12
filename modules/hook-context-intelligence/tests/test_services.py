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

    def test_graph_forest_name_is_settable(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph.graph_forest_name == "default"
        graph.graph_forest_name = "-workspace"
        assert graph.graph_forest_name == "-workspace"


class TestHookStateService:
    def test_construction_without_injected_store_uses_graphstate(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import (
            HookConfig,
            HookStateService,
        )

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphStore)
        assert isinstance(service.config, HookConfig)

    async def test_graph_accessible_without_injected_store(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        await service.graph.upsert_node("test", labels={"Test"}, properties={})
        assert await service.graph.get_node("test") is not None

    def test_config_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={"exclude_events": ["foo:bar"]})
        assert service.config.is_excluded("foo:bar") is True


class TestHookStateServiceFallback:
    def test_fallback_without_explicit_store_creates_graphstate(self):
        """When no graph_store is injected, the else branch must create GraphState()."""
        from amplifier_module_hook_context_intelligence.services import (
            GraphState,
            HookStateService,
        )

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphState)

    def test_no_store_factory_import_in_services_module(self):
        """services.py must not import from store_factory."""
        import inspect

        import amplifier_module_hook_context_intelligence.services as svc_module

        source = inspect.getsource(svc_module)
        assert "store_factory" not in source
        assert "create_graph_store" not in source


class TestHookStateServicePrebuiltStore:
    def test_uses_prebuilt_store_when_provided(self):
        from amplifier_module_hook_context_intelligence.services import (
            GraphState,
            HookStateService,
        )

        prebuilt = GraphState(graph_forest_name="prebuilt")
        service = HookStateService(raw_config={}, graph_store=prebuilt)
        assert service.graph is prebuilt

    def test_falls_back_to_graphstate_when_not_provided(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphStore)


class TestHookStateServiceResolverPath:
    """HookStateService accepts a resolver kwarg and builds config from resolver._config."""

    def test_accepts_resolver_kwarg(self):
        """HookStateService(resolver=...) must not raise."""
        from unittest.mock import MagicMock

        from amplifier_module_hook_context_intelligence.services import HookStateService

        resolver = MagicMock()
        resolver._config = {"exclude_events": ["foo:bar"]}
        # Should not raise
        service = HookStateService(resolver=resolver)
        assert service is not None

    def test_resolver_builds_config_from_resolver_config(self):
        """When resolver provided, HookConfig is built from resolver._config."""
        from unittest.mock import MagicMock

        from amplifier_module_hook_context_intelligence.services import HookStateService

        resolver = MagicMock()
        resolver._config = {"exclude_events": ["excluded:event"]}
        service = HookStateService(resolver=resolver)
        assert service.config.is_excluded("excluded:event") is True

    def test_resolver_path_uses_injected_graph_store(self):
        """When resolver provided, graph_store kwarg is still honoured."""
        from unittest.mock import MagicMock

        from amplifier_module_hook_context_intelligence.services import (
            GraphState,
            HookStateService,
        )

        resolver = MagicMock()
        resolver._config = {}
        prebuilt = GraphState(graph_forest_name="injected")
        service = HookStateService(resolver=resolver, graph_store=prebuilt)
        assert service.graph is prebuilt

    def test_resolver_path_skips_coordinator_storage(self):
        """When resolver provided, coordinator attribute should be None."""
        from unittest.mock import MagicMock

        from amplifier_module_hook_context_intelligence.services import HookStateService

        resolver = MagicMock()
        resolver._config = {}
        service = HookStateService(resolver=resolver)
        assert service.coordinator is None

    def test_raw_config_defaults_to_none(self):
        """raw_config can be omitted entirely when resolver is provided."""
        from unittest.mock import MagicMock

        from amplifier_module_hook_context_intelligence.services import HookStateService

        resolver = MagicMock()
        resolver._config = {}
        # Must not raise even though raw_config was not provided
        service = HookStateService(resolver=resolver)
        assert service is not None


class TestHookStateServiceBlobStore:
    def test_blob_store_default_is_none(self):
        """HookStateService(raw_config={}) has blob_store defaulting to None."""
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        assert service.blob_store is None

    def test_blob_store_can_be_injected(self):
        """HookStateService(raw_config={}, blob_store=fake_store) stores the injected value."""
        from amplifier_module_hook_context_intelligence.services import HookStateService

        fake_store = object()
        service = HookStateService(raw_config={}, blob_store=fake_store)
        assert service.blob_store is fake_store
