"""Tests for the mount() dispatcher in __init__.py.

Validates the thin-forwarder architecture:
  [ALWAYS]       config_resolver capability (registered for tool-graph-query)
  [ALWAYS]       LoggingHandler             (flat JSONL + optional server dispatch)
  [CONDITIONAL]  BlobTool                   (registered when context_intelligence_server_url set)
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
    """LoggingHandler is always registered; no graph handlers exist."""

    async def test_mount_returns_cleanup_callable(self) -> None:
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

        # LoggingHandler should be registered for ALL_EVENTS (base) plus any custom events
        register_calls = coordinator.hooks.register.call_args_list
        logging_calls = [c for c in register_calls if c.kwargs.get("name") == "LoggingHandler"]
        assert len(logging_calls) >= len(ALL_EVENTS)

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
    """Event discovery starts from ALL_EVENTS base and extends with two channels."""

    async def test_discovery_includes_all_events_base(self) -> None:
        """ALL_EVENTS must be the base — even with empty discovery channels, all 51+ events register."""
        from amplifier_module_hook_context_intelligence import mount

        # No contributed events, no capability events — only ALL_EVENTS base
        coordinator = _make_coordinator()
        await mount(coordinator, config={})

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
        from amplifier_module_hook_context_intelligence import mount

        custom_event = "custom:module:event"
        coordinator = _make_coordinator(contributed_events=[[custom_event]])
        await mount(coordinator, config={})

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
        from amplifier_module_hook_context_intelligence import mount

        custom_event = "legacy:custom:event"
        coordinator = _make_coordinator(capability_events=[custom_event])
        await mount(coordinator, config={})

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
        from amplifier_module_hook_context_intelligence import mount

        # Contribute an event that's already in ALL_EVENTS
        duplicate_event = ALL_EVENTS[0]  # e.g. 'session:start'
        coordinator = _make_coordinator(contributed_events=[[duplicate_event]])
        await mount(coordinator, config={})

        registered_events = []
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.append(call.args[0])

        # The duplicate event should appear exactly once
        assert registered_events.count(duplicate_event) == 1

    async def test_discovery_applies_exclusion_filter(self) -> None:
        """Exclusion patterns suppress events from registration."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()
        # Exclude all session:* events
        config = {"exclude_events": ["session:*"]}
        await mount(coordinator, config=config)

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
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(
            contributed_events=[["custom:contrib_event"]],
            capability_events=["custom:legacy_event"],
        )
        await mount(coordinator, config={})

        registered_events = set()
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                registered_events.add(call.args[0])
        assert set(ALL_EVENTS).issubset(registered_events)
        assert "custom:contrib_event" in registered_events
        assert "custom:legacy_event" in registered_events


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
# TestBlobToolRegistration
# ---------------------------------------------------------------------------
class TestBlobToolRegistration:
    """BlobTool is registered with coordinator.tools only when context_intelligence_server_url is configured."""

    async def test_blob_tool_not_registered_without_server_url(self) -> None:
        """When config has no context_intelligence_server_url, no blob tools should be registered."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()
        coordinator.tools = MagicMock()

        await mount(coordinator, config={})

        # No blob tool registrations should have been made
        registered_names = [call.args[0] for call in coordinator.tools.register.call_args_list]
        assert "blob_list" not in registered_names
        assert "blob_dump" not in registered_names

    async def test_blob_tool_registered_with_server_url(self) -> None:
        """When context_intelligence_server_url is configured, blob_list and blob_dump are registered."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()
        coordinator.tools = MagicMock()

        await mount(
            coordinator,
            config={"context_intelligence_server_url": "http://localhost:8000"},
        )

        # Both blob tools should have been registered
        registered_names = [call.args[0] for call in coordinator.tools.register.call_args_list]
        assert "blob_list" in registered_names
        assert "blob_dump" in registered_names


# ---------------------------------------------------------------------------
# TestCapabilityRegistration
# ---------------------------------------------------------------------------
class TestCapabilityRegistration:
    """Hook registers ConfigResolver as a coordinator capability."""

    async def test_capability_registered_on_mount(self) -> None:
        """mount() registers the config_resolver capability with a ConfigResolver instance."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        reg_calls = coordinator.register_capability.call_args_list
        cap_calls = [c for c in reg_calls if c.args[0] == "context_intelligence.config_resolver"]
        assert len(cap_calls) == 1, (
            "register_capability should be called once with 'context_intelligence.config_resolver'"
        )
        assert isinstance(cap_calls[0].args[1], ConfigResolver)

    async def test_cleanup_vacates_capability(self) -> None:
        """Cleanup callable vacates the capability by registering None."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()
        cleanup = await mount(coordinator, config={})
        coordinator.register_capability.reset_mock()

        cleanup()

        coordinator.register_capability.assert_called_once_with(
            "context_intelligence.config_resolver", None
        )


