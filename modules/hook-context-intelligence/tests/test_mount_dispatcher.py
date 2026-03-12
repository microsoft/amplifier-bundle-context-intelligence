"""Tests for the mount() dispatcher in __init__.py.

Validates the two-path architecture:
  [ALWAYS]       LoggingHandler  (flat JSONL)
  [CONDITIONAL]  GraphDataHook   (wraps existing 7 graph handlers)
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Mock coordinator helper
# ---------------------------------------------------------------------------
def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    capability_events: list[str] | None = None,
    working_dir: str | None = None,
) -> MagicMock:
    """Build a mock coordinator with configurable event discovery and working_dir."""
    coordinator = MagicMock()
    coordinator.config = {}
    unregister_fns: list[MagicMock] = []

    def _register_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        unreg = MagicMock()
        unregister_fns.append(unreg)
        return unreg

    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(side_effect=_register_side_effect)
    coordinator._unregister_fns = unregister_fns

    if contributed_events is None:
        contributed_events = []
    coordinator.collect_contributions = AsyncMock(return_value=contributed_events)

    # Build get_capability side_effect that handles both
    # 'session.working_dir' and 'observability.events'
    def _get_capability(name: str) -> Any:
        if name == "session.working_dir" and working_dir is not None:
            return working_dir
        if name == "observability.events" and capability_events is not None:
            return lambda: capability_events
        return None

    coordinator.get_capability = MagicMock(side_effect=_get_capability)

    return coordinator


# ---------------------------------------------------------------------------
# TestLoggingOnlyPath
# ---------------------------------------------------------------------------
class TestLoggingOnlyPath:
    """When graph is disabled, only LoggingHandler is registered."""

    async def test_mount_returns_cleanup_with_no_graph(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        result = await mount(coordinator, config={})
        assert callable(result)

    async def test_logging_handler_registered_for_all_events(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        events = ["session:start", "session:end", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        await mount(coordinator, config={})

        # LoggingHandler should be registered for ALL discovered events
        register_calls = coordinator.hooks.register.call_args_list
        logging_calls = [c for c in register_calls if c.kwargs.get("name") == "LoggingHandler"]
        assert len(logging_calls) == len(events)

    async def test_logging_handler_registered_at_priority_100(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        await mount(coordinator, config={})

        register_calls = coordinator.hooks.register.call_args_list
        logging_calls = [c for c in register_calls if c.kwargs.get("name") == "LoggingHandler"]
        assert len(logging_calls) >= 1
        for call in logging_calls:
            assert call.kwargs.get("priority") == 100

    async def test_no_graph_handlers_when_enable_graph_false(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        events = ["session:start", "session:end"]
        coordinator = _make_coordinator(contributed_events=[events])
        await mount(coordinator, config={"enable_graph": False})

        # Only logging registrations (one per event)
        assert coordinator.hooks.register.call_count == len(events)

    async def test_no_graph_handlers_when_graph_store_missing(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        events = ["session:start", "session:end"]
        coordinator = _make_coordinator(contributed_events=[events])
        await mount(coordinator, config={"enable_graph": True})

        # Only logging registrations (no graph_store key)
        assert coordinator.hooks.register.call_count == len(events)


# ---------------------------------------------------------------------------
# TestLoggingPlusGraphPath
# ---------------------------------------------------------------------------
class TestLoggingPlusGraphPath:
    """When graph is enabled with store, both paths are active."""

    async def test_both_paths_active(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from amplifier_module_hook_context_intelligence import mount

        events = ["session:start", "session:end", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        config = {
            "enable_graph": True,
            "graph_store": {
                "type": "neo4j",
                "graph_forest_name": "default",
                "config": {
                    "uri": "neo4j://localhost:7687",
                    "username": "neo4j",
                    "password": "test",
                    "database": "neo4j",
                },
            },
        }
        mock_store = MagicMock()
        mock_store.close = AsyncMock()
        with patch(
            "amplifier_module_hook_context_intelligence.graph_data_hook.Neo4jGraphStore",
            return_value=mock_store,
        ):
            result = await mount(coordinator, config=config)
        assert callable(result)

        # Total registrations should be more than just logging (events + graph)
        assert coordinator.hooks.register.call_count > len(events)


# ---------------------------------------------------------------------------
# TestCleanup
# ---------------------------------------------------------------------------
class TestCleanup:
    """Cleanup callable tears down both paths."""

    async def test_cleanup_is_callable(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        cleanup = await mount(coordinator, config={})
        assert callable(cleanup)

    async def test_cleanup_unregisters_logging_handler(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end"]],
        )
        cleanup = await mount(coordinator, config={})
        assert cleanup is not None
        cleanup()

        # All unregister functions should have been called
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()


# ---------------------------------------------------------------------------
# TestEventDiscovery
# ---------------------------------------------------------------------------
class TestEventDiscovery:
    """Event discovery uses both channels and applies exclusion filter."""

    async def test_uses_both_discovery_channels(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
            capability_events=["tool:pre"],
        )
        await mount(coordinator, config={})

        # Both events should have been discovered and registered
        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])
        assert "session:start" in registered_events
        assert "tool:pre" in registered_events

    async def test_exclusion_filter_applied(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["session:start", "debug:internal", "debug:trace"]],
        )
        config = {"exclude_events": ["debug:*"]}
        await mount(coordinator, config=config)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])
        assert "session:start" in registered_events
        assert "debug:internal" not in registered_events
        assert "debug:trace" not in registered_events

    async def test_no_events_returns_none(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()  # No events
        result = await mount(coordinator, config={})
        assert result is None


# ---------------------------------------------------------------------------
# TestModuleContract
# ---------------------------------------------------------------------------
class TestModuleContract:
    """Module-level contract preserved after rewrite."""

    def test_module_type_is_hook(self) -> None:
        from amplifier_module_hook_context_intelligence import __amplifier_module_type__

        assert __amplifier_module_type__ == "hook"

    def test_mount_is_coroutine(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        assert inspect.iscoroutinefunction(mount)

    def test_mount_signature_has_coordinator_and_config(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        sig = inspect.signature(mount)
        params = list(sig.parameters.keys())
        assert params[0] == "coordinator"
        assert params[1] == "config"
