"""Tests for GraphDataHook — thin orchestrator wrapping MountFlow + CompositeGraphStore."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_hook_context_intelligence.composite_store import CompositeGraphStore
from amplifier_module_hook_context_intelligence.graph_data_hook import GraphDataHook
from amplifier_module_hook_context_intelligence.mount import MountState

_SINGLE_STORE_CONFIG: dict[str, Any] = {
    "graph_stores": [{"type": "duckdb", "config": {"connection": ":memory:"}}],
}


def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    capability_events: list[str] | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = {}
    unregister_fns: list[MagicMock] = []

    def _register_side_effect(*args, **kwargs):
        unreg = MagicMock()
        unregister_fns.append(unreg)
        return unreg

    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(side_effect=_register_side_effect)
    coordinator._unregister_fns = unregister_fns

    if contributed_events is None:
        contributed_events = []
    coordinator.collect_contributions = AsyncMock(return_value=contributed_events)

    if capability_events is not None:
        coordinator.get_capability = MagicMock(return_value=lambda: capability_events)
    else:
        coordinator.get_capability = MagicMock(return_value=None)

    return coordinator


class TestGraphDataHookMount:
    """GraphDataHook.mount() runs MountFlow to READY and returns cleanup."""

    async def test_mount_returns_cleanup_callable(self):
        hook = GraphDataHook(_SINGLE_STORE_CONFIG)
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        cleanup = await hook.mount(coordinator)
        assert callable(cleanup)

    async def test_mount_runs_mount_flow_to_ready(self):
        hook = GraphDataHook(_SINGLE_STORE_CONFIG)
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        await hook.mount(coordinator)
        assert hook._flow.state == MountState.READY

    async def test_mount_registers_handlers(self):
        hook = GraphDataHook(_SINGLE_STORE_CONFIG)
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        await hook.mount(coordinator)
        assert coordinator.hooks.register.call_count >= 3

    async def test_cleanup_calls_unregister(self):
        hook = GraphDataHook(_SINGLE_STORE_CONFIG)
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        cleanup = await hook.mount(coordinator)
        cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()


class TestGraphDataHookCompositeStore:
    """GraphDataHook creates a CompositeGraphStore from config['graph_stores']."""

    def test_creates_composite_from_multiple_configs(self):
        config = {
            "graph_stores": [
                {"type": "duckdb", "config": {"connection": ":memory:"}},
                {"type": "duckdb", "config": {"connection": ":memory:"}},
            ],
        }
        hook = GraphDataHook(config)
        assert isinstance(hook._composite_store, CompositeGraphStore)

    async def test_handlers_see_composite_store(self):
        hook = GraphDataHook(_SINGLE_STORE_CONFIG)
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        await hook.mount(coordinator)
        assert hook._flow.services is not None
        assert isinstance(hook._flow.services.graph, CompositeGraphStore)
