"""Tests for the 6-state mount flow state machine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from amplifier_module_hook_context_intelligence.mount import MountFlow, MountState
from amplifier_module_hook_context_intelligence.protocol import EventHandler
from amplifier_module_hook_context_intelligence.services import HookStateService


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


class TestInitToStateCreated:
    def test_mount_flow_starts_at_init(self):
        flow = MountFlow(config={})
        assert flow.state == MountState.INIT

    def test_create_services(self):
        flow = MountFlow(config={"exclude_events": ["foo:bar"]})
        flow.create_services(None)
        assert flow.state == MountState.STATE_CREATED
        assert isinstance(flow.services, HookStateService)
        assert flow.services.config.is_excluded("foo:bar")


class TestStateCreatedToHandlersInstantiated:
    def test_instantiate_handlers(self):
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        assert flow.state == MountState.HANDLERS_INSTANTIATED
        assert len(flow.entity_handlers) == 6
        assert flow.default_handler is not None

    def test_all_handlers_conform_to_protocol(self):
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        for handler in flow.entity_handlers:
            assert isinstance(handler, EventHandler)
        assert isinstance(flow.default_handler, EventHandler)

    def test_claimed_events_computed(self):
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        assert len(flow.claimed_events) >= 18

    def test_default_handler_starts_empty(self):
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        assert flow.default_handler is not None
        assert flow.default_handler.handled_events == set()


class TestHandlersInstantiatedToEventsDiscovered:
    async def test_discover_events_from_contributions(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end"], ["tool:pre", "tool:post"]]
        )
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert flow.state == MountState.EVENTS_DISCOVERED
        assert "session:start" in flow.remaining_events
        assert "tool:pre" in flow.remaining_events

    async def test_discover_events_from_legacy_capability(self):
        coordinator = _make_coordinator(capability_events=["custom:event1", "custom:event2"])
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "custom:event1" in flow.remaining_events

    async def test_discover_events_union_of_both_channels(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
            capability_events=["custom:event"],
        )
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "session:start" in flow.remaining_events
        assert "custom:event" in flow.remaining_events

    async def test_exclusion_filter_applied(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "content_block:delta"]]
        )
        flow = MountFlow(config={"exclude_events": ["content_block:delta"]})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "session:start" in flow.remaining_events
        assert "content_block:delta" not in flow.remaining_events

    async def test_exclusion_wildcard_filter(self):
        coordinator = _make_coordinator(
            contributed_events=[["session-naming:foo", "session-naming:bar", "session:start"]]
        )
        flow = MountFlow(config={"exclude_events": ["session-naming:*"]})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "session-naming:foo" not in flow.remaining_events
        assert "session-naming:bar" not in flow.remaining_events
        assert "session:start" in flow.remaining_events

    async def test_empty_discovery_is_valid(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert flow.state == MountState.EVENTS_DISCOVERED
        assert flow.remaining_events == set()


class TestEventsDiscoveredToSpecificRegistered:
    async def test_register_specific_handlers(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]]
        )
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        assert flow.state == MountState.SPECIFIC_REGISTERED
        assert coordinator.hooks.register.call_count >= 3

    async def test_only_remaining_events_registered(self):
        coordinator = _make_coordinator(contributed_events=[["session:start"]])
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "session:start" in registered_events
        assert "session:end" not in registered_events

    async def test_wildcard_event_matching(self):
        coordinator = _make_coordinator(
            contributed_events=[["llm:request:anthropic", "llm:request:openai"]]
        )
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "llm:request:anthropic" in registered_events
        assert "llm:request:openai" in registered_events


class TestSpecificRegisteredToReady:
    async def test_register_default_handler(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "custom:unknown_event"]]
        )
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        assert flow.state == MountState.READY
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "custom:unknown_event" in registered_events

    async def test_default_handler_events_populated(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "custom:one", "custom:two"]]
        )
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        assert flow.default_handler is not None
        assert "custom:one" in flow.default_handler.handled_events
        assert "custom:two" in flow.default_handler.handled_events


class TestKeyInvariant:
    async def test_every_remaining_event_has_at_least_one_registration(self):
        events = [
            "session:start",
            "session:end",
            "tool:pre",
            "tool:post",
            "custom:event1",
            "custom:event2",
        ]
        coordinator = _make_coordinator(contributed_events=[events])
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        registered_events = {c.args[0] for c in coordinator.hooks.register.call_args_list}
        assert flow.remaining_events == registered_events

    async def test_deterministic_registrations(self):
        events = ["session:start", "tool:pre", "custom:event"]
        results = []
        for _ in range(2):
            coordinator = _make_coordinator(contributed_events=[events])
            flow = MountFlow(config={})
            flow.create_services(None)
            flow.instantiate_handlers()
            await flow.discover_events(coordinator)
            flow.register_specific_handlers(coordinator)
            flow.register_default_handler(coordinator)
            registered = sorted([c.args[0] for c in coordinator.hooks.register.call_args_list])
            results.append(registered)
        assert results[0] == results[1]


class TestFullMount:
    async def test_mount_returns_cleanup(self):
        events = ["session:start", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        flow = MountFlow(config={})
        cleanup = await flow.run(coordinator)
        assert flow.state == MountState.READY
        assert cleanup is not None
        assert callable(cleanup)

    async def test_cleanup_calls_unregister(self):
        events = ["session:start"]
        coordinator = _make_coordinator(contributed_events=[events])
        flow = MountFlow(config={})
        cleanup = await flow.run(coordinator)
        assert coordinator.hooks.register.call_count == 1
        cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()

    async def test_mount_with_no_events_reaches_ready(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        _cleanup = await flow.run(coordinator)
        assert flow.state == MountState.READY
        assert coordinator.hooks.register.call_count == 0
