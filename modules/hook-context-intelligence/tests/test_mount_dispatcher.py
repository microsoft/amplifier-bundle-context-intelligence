"""Tests for the mount() dispatcher in __init__.py.

Validates the thin-forwarder architecture:
  [ALWAYS]       config_resolver capability (enables downstream graph tool lookup)
  [ALWAYS]       LoggingHandler             (flat JSONL + optional server dispatch)
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.events import ALL_EVENTS


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
    capabilities: dict[str, Any] = {}

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

    # mount() is async — must be an AsyncMock so it can be awaited
    coordinator.mount = AsyncMock()

    def _register_capability(name: str, value: Any) -> None:
        capabilities[name] = value

    coordinator.register_capability = MagicMock(side_effect=_register_capability)

    # Build get_capability side_effect that handles both
    # 'session.working_dir' and 'observability.events', and stored capabilities
    def _get_capability(name: str) -> Any:
        if name == "session.working_dir" and working_dir is not None:
            return working_dir
        if name == "observability.events" and capability_events is not None:
            return lambda: capability_events
        return capabilities.get(name)

    coordinator.get_capability = MagicMock(side_effect=_get_capability)

    return coordinator


async def _mount_and_ready(coordinator: MagicMock, config: dict | None = None) -> Any:
    """Run mount() then on_session_ready() — the normal two-phase lifecycle."""
    from amplifier_module_hook_context_intelligence import mount, on_session_ready

    cleanup = await mount(coordinator, config=config or {})
    await on_session_ready(coordinator)
    return cleanup


# ---------------------------------------------------------------------------
# TestLoggingOnlyPath
# ---------------------------------------------------------------------------
class TestLoggingOnlyPath:
    """LoggingHandler is always registered; no graph handlers exist."""

    async def test_mount_and_ready_returns_cleanup_callable(self) -> None:
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        result = await _mount_and_ready(coordinator)
        assert callable(result)

    async def test_logging_handler_registered_for_all_events(self) -> None:
        events = ["session:start", "session:end", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        await _mount_and_ready(coordinator)

        # LoggingHandler should be registered for ALL_EVENTS (base) plus any custom events
        register_calls = coordinator.hooks.register.call_args_list
        logging_calls = [c for c in register_calls if c.kwargs.get("name") == "LoggingHandler"]
        assert len(logging_calls) >= len(ALL_EVENTS)

    async def test_logging_handler_registered_at_priority_100(self) -> None:
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        await _mount_and_ready(coordinator)

        register_calls = coordinator.hooks.register.call_args_list
        logging_calls = [c for c in register_calls if c.kwargs.get("name") == "LoggingHandler"]
        assert len(logging_calls) >= 1
        for call in logging_calls:
            assert call.kwargs.get("priority") == 100


# ---------------------------------------------------------------------------
# TestCleanup
# ---------------------------------------------------------------------------
class TestCleanup:
    """Cleanup callable tears down registered hooks."""

    async def test_cleanup_is_callable(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        cleanup = await mount(coordinator, config={})
        assert callable(cleanup)

    async def test_cleanup_unregisters_all_handlers(self) -> None:
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end"]],
        )
        cleanup = await _mount_and_ready(coordinator)
        assert cleanup is not None
        await cleanup()

        # All unregister functions should have been called
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()


# ---------------------------------------------------------------------------
# TestEventDiscovery
# ---------------------------------------------------------------------------
class TestEventDiscovery:
    """Event discovery starts from ALL_EVENTS base and extends with two channels."""

    async def test_discovery_includes_all_events_base(self) -> None:
        """ALL_EVENTS must be the base — even with empty discovery channels, all 51+ events register."""
        # No contributed events, no capability events — only ALL_EVENTS base
        coordinator = _make_coordinator()
        await _mount_and_ready(coordinator)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])

        # Every event in ALL_EVENTS must appear in registrations
        for event in ALL_EVENTS:
            assert event in registered_events, (
                f"Expected {event!r} in registrations from ALL_EVENTS base"
            )
        assert len(registered_events) >= len(ALL_EVENTS)

    async def test_discovery_extends_with_contributions_channel(self) -> None:
        """Custom events from collect_contributions extend the ALL_EVENTS base."""
        custom_event = "custom:module:event"
        coordinator = _make_coordinator(contributed_events=[[custom_event]])
        await _mount_and_ready(coordinator)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])

        # ALL_EVENTS base must be present
        assert len(registered_events) >= len(ALL_EVENTS)
        # Custom event from contributions must also be present
        assert custom_event in registered_events

    async def test_discovery_extends_with_legacy_capability_channel(self) -> None:
        """Custom events from get_capability extend the ALL_EVENTS base."""
        custom_event = "legacy:custom:event"
        coordinator = _make_coordinator(capability_events=[custom_event])
        await _mount_and_ready(coordinator)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])

        # ALL_EVENTS base must be present
        assert len(registered_events) >= len(ALL_EVENTS)
        # Custom event from legacy capability must also be present
        assert custom_event in registered_events

    async def test_discovery_deduplicates_overlapping_events(self) -> None:
        """If a channel contributes an event already in ALL_EVENTS, it appears once."""
        # Contribute an event that's already in ALL_EVENTS
        duplicate_event = ALL_EVENTS[0]  # e.g. 'session:start'
        coordinator = _make_coordinator(contributed_events=[[duplicate_event]])
        await _mount_and_ready(coordinator)

        registered_events = []
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.append(call.args[0])

        # The duplicate event should appear exactly once
        assert registered_events.count(duplicate_event) == 1

    async def test_discovery_applies_exclusion_filter(self) -> None:
        """Exclusion patterns suppress events from registration."""
        coordinator = _make_coordinator()
        # Exclude all session:* events
        config = {"exclude_events": ["session:*"]}
        await _mount_and_ready(coordinator, config=config)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])

        # session:start and session:end should be excluded
        assert "session:start" not in registered_events
        assert "session:end" not in registered_events

    async def test_mount_always_returns_callable(self) -> None:
        """With ALL_EVENTS base, mount() should never return None — must return a cleanup callable."""
        from amplifier_module_hook_context_intelligence import mount

        # No discovery channels at all — ALL_EVENTS guarantees non-empty
        coordinator = _make_coordinator()
        result = await mount(coordinator, config={})

        assert result is not None, "mount() must never return None when ALL_EVENTS base is used"
        assert callable(result), "mount() must return a cleanup callable"

    async def test_union_of_all_three_layers(self) -> None:
        """Discovery returns ALL_EVENTS ∪ contributions ∪ legacy capability."""
        coordinator = _make_coordinator(
            contributed_events=[["custom:contrib_event"]],
            capability_events=["custom:legacy_event"],
        )
        await _mount_and_ready(coordinator)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])
        assert set(ALL_EVENTS).issubset(registered_events)
        assert "custom:contrib_event" in registered_events
        assert "custom:legacy_event" in registered_events

    async def test_additional_events_registered_regardless_of_capability_order(self) -> None:
        """additional_events from config are registered even without observability contributions.

        Regression test: delegate:agent_spawned is NOT in ALL_EVENTS and is NOT
        contributed by any module at mount time (simulating the hook mounting before
        tool-delegate). With additional_events configured, it must still appear
        in the registered events.
        """
        # No contributed events, no capability events — only ALL_EVENTS base
        coordinator = _make_coordinator()
        config = {"additional_events": ["delegate:agent_spawned", "delegate:agent_completed"]}
        await _mount_and_ready(coordinator, config=config)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])

        assert "delegate:agent_spawned" in registered_events, (
            "delegate:agent_spawned should be registered via additional_events config"
        )
        assert "delegate:agent_completed" in registered_events, (
            "delegate:agent_completed should be registered via additional_events config"
        )

    async def test_additional_events_can_be_excluded(self) -> None:
        """exclude_events filter applies to additional_events entries too."""
        coordinator = _make_coordinator()
        config = {
            "additional_events": ["delegate:agent_spawned", "delegate:agent_completed"],
            "exclude_events": ["delegate:*"],
        }
        await _mount_and_ready(coordinator, config=config)

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])

        assert "delegate:agent_spawned" not in registered_events, (
            "delegate:agent_spawned should be excluded by delegate:* pattern"
        )
        assert "delegate:agent_completed" not in registered_events, (
            "delegate:agent_completed should be excluded by delegate:* pattern"
        )


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


# ---------------------------------------------------------------------------
# TestCapabilityRegistration
# ---------------------------------------------------------------------------
class TestCapabilityRegistration:
    """Hook registers HookConfigResolver as a coordinator capability."""

    async def test_config_resolver_capability_registered_on_mount(self) -> None:
        """mount() registers the config_resolver capability with a HookConfigResolver instance."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.config_resolver import HookConfigResolver

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        reg_calls = coordinator.register_capability.call_args_list
        cap_calls = [c for c in reg_calls if c.args[0] == "context_intelligence.hook_config_resolver"]
        assert len(cap_calls) == 1, (
            "register_capability should be called once with 'context_intelligence.config_resolver'"
        )
        assert isinstance(cap_calls[0].args[1], HookConfigResolver)

    async def test_hook_state_capability_registered_on_mount(self) -> None:
        """mount() registers the _hook_state capability as a dict with required keys."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        reg_calls = coordinator.register_capability.call_args_list
        state_calls = [c for c in reg_calls if c.args[0] == "context_intelligence._hook_state"]
        assert len(state_calls) == 1, (
            "register_capability should be called once with 'context_intelligence._hook_state'"
        )
        state_value = state_calls[0].args[1]
        assert isinstance(state_value, dict)
        assert "logging_handler" in state_value
        assert "unregister_fns" in state_value
        assert "resolver" in state_value

    async def test_cleanup_vacates_both_capabilities(self) -> None:
        """Cleanup callable vacates both capabilities by registering None for each."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()
        cleanup = await mount(coordinator, config={})
        coordinator.register_capability.reset_mock()

        await cleanup()

        # Build a map of capability name -> value from cleanup's register_capability calls
        null_calls: dict[str, Any] = {
            c.args[0]: c.args[1] for c in coordinator.register_capability.call_args_list
        }
        assert null_calls["context_intelligence.hook_config_resolver"] is None
        assert null_calls["context_intelligence._hook_state"] is None
