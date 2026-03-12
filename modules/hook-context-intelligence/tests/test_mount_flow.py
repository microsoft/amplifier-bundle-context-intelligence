"""Tests for the 5-state mount flow state machine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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


class TestStateEnumReduced:
    """MountState enum must have exactly 5 members — EVENTS_DISCOVERED removed."""

    def test_mount_state_has_five_members(self):
        assert len(MountState) == 5

    def test_events_discovered_not_in_enum(self):
        member_names = {m.name for m in MountState}
        assert "EVENTS_DISCOVERED" not in member_names

    def test_expected_members_present(self):
        member_names = {m.name for m in MountState}
        expected = {
            "INIT",
            "STATE_CREATED",
            "HANDLERS_INSTANTIATED",
            "SPECIFIC_REGISTERED",
            "READY",
        }
        assert member_names == expected


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


class TestHandlersInstantiatedToSpecificRegistered:
    """Sets flow.remaining_events directly — no discover_events() call."""

    def test_register_specific_handlers(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        flow.remaining_events = {"session:start", "session:end", "tool:pre"}
        flow.register_specific_handlers(coordinator)
        assert flow.state == MountState.SPECIFIC_REGISTERED
        assert coordinator.hooks.register.call_count >= 3

    def test_only_remaining_events_registered(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        flow.remaining_events = {"session:start"}
        flow.register_specific_handlers(coordinator)
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "session:start" in registered_events
        assert "session:end" not in registered_events

    def test_wildcard_event_matching(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        flow.remaining_events = {"content_block:start", "content_block:delta"}
        flow.register_specific_handlers(coordinator)
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "content_block:start" in registered_events
        assert "content_block:delta" in registered_events


class TestSpecificRegisteredToReady:
    def test_register_default_handler(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        flow.remaining_events = {"session:start", "custom:unknown_event"}
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        assert flow.state == MountState.READY
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "custom:unknown_event" in registered_events

    def test_default_handler_events_populated(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        flow.remaining_events = {"session:start", "custom:one", "custom:two"}
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        assert flow.default_handler is not None
        assert "custom:one" in flow.default_handler.handled_events
        assert "custom:two" in flow.default_handler.handled_events


class TestKeyInvariant:
    def test_every_remaining_event_has_at_least_one_registration(self):
        events = [
            "session:start",
            "session:end",
            "tool:pre",
            "tool:post",
            "custom:event1",
            "custom:event2",
        ]
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services(None)
        flow.instantiate_handlers()
        flow.remaining_events = set(events)
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        registered_events = {c.args[0] for c in coordinator.hooks.register.call_args_list}
        assert flow.remaining_events == registered_events

    def test_deterministic_registrations(self):
        events = ["session:start", "tool:pre", "custom:event"]
        results = []
        for _ in range(2):
            coordinator = _make_coordinator()
            flow = MountFlow(config={})
            flow.create_services(None)
            flow.instantiate_handlers()
            flow.remaining_events = set(events)
            flow.register_specific_handlers(coordinator)
            flow.register_default_handler(coordinator)
            registered = sorted([c.args[0] for c in coordinator.hooks.register.call_args_list])
            results.append(registered)
        assert results[0] == results[1]


class TestPreconditionErrors:
    """Precondition violations must raise RuntimeError, not AssertionError.

    RuntimeError survives python -O (which strips assert statements).
    """

    def test_instantiate_handlers_without_services_raises_runtime_error(self):
        flow = MountFlow(config={})
        # services is None — must raise RuntimeError, not AssertionError
        with pytest.raises(RuntimeError, match="create_services.*must be called first"):
            flow.instantiate_handlers()

    def test_register_default_handler_without_handlers_raises_runtime_error(self):
        flow = MountFlow(config={})
        flow.remaining_events = set()
        # default_handler is None — must raise RuntimeError
        with pytest.raises(RuntimeError, match="instantiate_handlers.*must be called first"):
            flow.register_default_handler(MagicMock())


class TestFullMount:
    async def test_mount_returns_cleanup(self):
        events = {"session:start", "tool:pre"}
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        cleanup = await flow.run(coordinator, events)
        assert flow.state == MountState.READY
        assert cleanup is not None
        assert callable(cleanup)

    async def test_cleanup_calls_unregister(self):
        events = {"session:start"}
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        cleanup = await flow.run(coordinator, events)
        assert coordinator.hooks.register.call_count == 1
        cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()

    async def test_mount_with_no_events_reaches_ready(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        _cleanup = await flow.run(coordinator, set())
        assert flow.state == MountState.READY
        assert coordinator.hooks.register.call_count == 0

    async def test_run_applies_exclusion_filter(self):
        """run() applies HookConfig.is_excluded() to filter the passed events."""
        coordinator = _make_coordinator()
        flow = MountFlow(config={"exclude_events": ["content_block:delta"]})
        _cleanup = await flow.run(coordinator, {"session:start", "content_block:delta"})
        assert "session:start" in flow.remaining_events
        assert "content_block:delta" not in flow.remaining_events


class TestDiscoverEventsRemoved:
    """The discover_events() method must not exist on MountFlow."""

    def test_no_discover_events_method(self):
        flow = MountFlow(config={})
        assert not hasattr(flow, "discover_events"), (
            "discover_events() must be removed — events come via run(coordinator, events)"
        )

    def test_run_signature_requires_events(self):
        """run() must require an 'events' parameter."""
        import inspect

        sig = inspect.signature(MountFlow.run)
        params = list(sig.parameters.keys())
        assert "events" in params, f"run() params are {params}, expected 'events'"


class TestRunExclusionBehavior:
    """run() applies HookConfig exclusion to the passed-in events."""

    async def test_wildcard_exclusion_via_run(self):
        events = {"session:start", "session-naming:foo", "session-naming:bar"}
        coordinator = _make_coordinator()
        flow = MountFlow(config={"exclude_events": ["session-naming:*"]})
        await flow.run(coordinator, events)
        assert "session-naming:foo" not in flow.remaining_events
        assert "session-naming:bar" not in flow.remaining_events
        assert "session:start" in flow.remaining_events

    async def test_empty_events_reaches_ready(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        await flow.run(coordinator, set())
        assert flow.state == MountState.READY
        assert flow.remaining_events == set()


class TestMountFlowPrebuiltStore:
    """MountFlow accepts an optional pre-built GraphStore and passes it through."""

    def test_accepts_graph_store_parameter(self):
        store = object()
        flow = MountFlow(config={}, graph_store=store)
        assert flow._graph_store is store

    def test_prebuilt_store_used_by_services(self):
        store = object()
        flow = MountFlow(config={}, graph_store=store)
        flow.create_services(None)
        assert flow.services is not None
        assert flow.services.graph is store

    def test_no_store_uses_factory(self):
        flow = MountFlow(config={})
        flow.create_services(None)
        assert flow.services is not None
        assert flow.services.graph is not None

    async def test_full_mount_with_prebuilt_store(self):
        store = object()
        coordinator = _make_coordinator()
        flow = MountFlow(config={}, graph_store=store)
        _cleanup = await flow.run(coordinator, {"session:start"})
        assert flow.state == MountState.READY
        assert flow.services is not None
        assert flow.services.graph is store


class TestMountFlowResolverPath:
    """MountFlow accepts optional resolver and passes it to HookStateService."""

    def test_accepts_resolver_parameter(self):
        """MountFlow(config={}, resolver=...) must store _resolver."""
        resolver = MagicMock()
        resolver._config = {}
        flow = MountFlow(config={}, resolver=resolver)
        assert flow._resolver is resolver

    def test_no_resolver_defaults_to_none(self):
        """When resolver is not provided, _resolver is None."""
        flow = MountFlow(config={})
        assert flow._resolver is None

    def test_create_services_with_resolver_uses_resolver_path(self):
        """When resolver is set, create_services passes resolver to HookStateService."""
        from amplifier_module_hook_context_intelligence.services import HookStateService

        resolver = MagicMock()
        resolver._config = {"exclude_events": ["resolver:event"]}
        flow = MountFlow(config={}, resolver=resolver)
        flow.create_services(coordinator=None)
        assert flow.services is not None
        assert isinstance(flow.services, HookStateService)
        # Config must come from resolver._config
        assert flow.services.config.is_excluded("resolver:event")

    def test_create_services_with_resolver_skips_coordinator(self):
        """When resolver is provided, coordinator is not stored on services."""
        resolver = MagicMock()
        resolver._config = {}
        flow = MountFlow(config={}, resolver=resolver)
        flow.create_services(coordinator=None)
        assert flow.services is not None
        assert flow.services.coordinator is None

    def test_create_services_with_resolver_and_graph_store(self):
        """When resolver is provided along with a prebuilt graph_store, it is used."""
        from amplifier_module_hook_context_intelligence.services import GraphState

        resolver = MagicMock()
        resolver._config = {}
        store = GraphState(graph_forest_name="via-resolver")
        flow = MountFlow(config={}, graph_store=store, resolver=resolver)
        flow.create_services(coordinator=None)
        assert flow.services is not None
        assert flow.services.graph is store

    async def test_full_mount_with_resolver(self):
        """Full mount run works end-to-end when resolver is provided."""
        resolver = MagicMock()
        resolver._config = {}
        coordinator = _make_coordinator()
        flow = MountFlow(config={}, resolver=resolver)
        _cleanup = await flow.run(coordinator, {"session:start"})
        assert flow.state == MountState.READY
        assert flow.services is not None
