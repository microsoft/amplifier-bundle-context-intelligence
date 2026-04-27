"""Tests for the on_session_ready() lifecycle hook.

Verifies that event discovery and LoggingHandler registration happen
after all modules have mounted, not during mount().
"""

from __future__ import annotations

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
    """Build a mock coordinator that captures register_capability values."""
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

    def _register_capability(name: str, value: Any) -> None:
        capabilities[name] = value

    coordinator.register_capability = MagicMock(side_effect=_register_capability)

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
# TestOnSessionReadyRegistration
# ---------------------------------------------------------------------------

class TestOnSessionReadyRegistration:
    """LoggingHandler is registered only after on_session_ready(), not mount()."""

    async def test_no_logging_registrations_after_mount_only(self) -> None:
        """mount() alone must NOT register any LoggingHandler events."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        logging_calls = [
            c for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        ]
        assert len(logging_calls) == 0, (
            "mount() must not register LoggingHandler events — that belongs in on_session_ready()"
        )

    async def test_all_events_registered_after_on_session_ready(self) -> None:
        """on_session_ready() registers LoggingHandler for ALL_EVENTS at minimum."""
        coordinator = _make_coordinator()
        await _mount_and_ready(coordinator)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        for event in ALL_EVENTS:
            assert event in registered, f"Expected {event!r} registered after on_session_ready"

    async def test_registered_at_priority_100(self) -> None:
        coordinator = _make_coordinator()
        await _mount_and_ready(coordinator)

        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                assert call.kwargs.get("priority") == 100


# ---------------------------------------------------------------------------
# TestOnSessionReadyEventDiscovery
# ---------------------------------------------------------------------------

class TestOnSessionReadyEventDiscovery:
    """on_session_ready() picks up contributions from late-mounting modules."""

    async def test_late_contributed_event_is_registered(self) -> None:
        """Events contributed via observability.events are registered even if
        the contributing module mounts after hook-context-intelligence."""
        late_event = "late-module:custom-event"
        # contributed_events simulates a module that mounted after this hook
        coordinator = _make_coordinator(contributed_events=[[late_event]])
        await _mount_and_ready(coordinator)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        assert late_event in registered, (
            f"Late-contributed {late_event!r} must be registered by on_session_ready"
        )

    async def test_legacy_capability_events_registered(self) -> None:
        """Events from the legacy observability.events capability are registered."""
        legacy_event = "legacy:capability:event"
        coordinator = _make_coordinator(capability_events=[legacy_event])
        await _mount_and_ready(coordinator)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        assert legacy_event in registered

    async def test_additional_events_from_config_registered(self) -> None:
        """additional_events config key is included (backward compat)."""
        coordinator = _make_coordinator()
        config = {"additional_events": ["custom:explicit-event", "custom:another-event"]}
        await _mount_and_ready(coordinator, config=config)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        assert "custom:explicit-event" in registered
        assert "custom:another-event" in registered

    async def test_all_three_sources_combined(self) -> None:
        """ALL_EVENTS + contributions + legacy capability all present."""
        coordinator = _make_coordinator(
            contributed_events=[["contrib:event"]],
            capability_events=["legacy:event"],
        )
        config = {"additional_events": ["config:event"]}
        await _mount_and_ready(coordinator, config=config)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        assert set(ALL_EVENTS).issubset(registered)
        assert "contrib:event" in registered
        assert "legacy:event" in registered
        assert "config:event" in registered


# ---------------------------------------------------------------------------
# TestOnSessionReadyExcludeFilter
# ---------------------------------------------------------------------------

class TestOnSessionReadyExcludeFilter:
    """exclude_events filter is applied only when non-empty."""

    async def test_exclude_filter_removes_matching_events(self) -> None:
        coordinator = _make_coordinator()
        config = {"exclude_events": ["session:*"]}
        await _mount_and_ready(coordinator, config=config)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        assert "session:start" not in registered
        assert "session:end" not in registered

    async def test_exclude_filter_glob_pattern(self) -> None:
        """fnmatch glob patterns work: exclude delegate:* removes delegate events."""
        coordinator = _make_coordinator(contributed_events=[["delegate:agent_spawned"]])
        config = {"exclude_events": ["delegate:*"]}
        await _mount_and_ready(coordinator, config=config)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        assert "delegate:agent_spawned" not in registered

    async def test_empty_exclude_registers_all(self) -> None:
        """When exclude_events is empty, all events are registered without filtering."""
        coordinator = _make_coordinator()
        config = {"exclude_events": []}
        await _mount_and_ready(coordinator, config=config)

        registered = {
            c.args[0]
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("name") == "LoggingHandler"
        }
        for event in ALL_EVENTS:
            assert event in registered


# ---------------------------------------------------------------------------
# TestOnSessionReadyErrorHandling
# ---------------------------------------------------------------------------

class TestOnSessionReadyErrorHandling:
    """Edge cases and defensive behavior."""

    async def test_missing_hook_state_logs_warning_and_returns(self) -> None:
        """If _hook_state capability is absent, on_session_ready logs warning and returns cleanly."""
        from amplifier_module_hook_context_intelligence import on_session_ready

        coordinator = _make_coordinator()
        # Do NOT call mount() — _hook_state will be absent

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "amplifier_module_hook_context_intelligence.log"
        ) as mock_log:
            await on_session_ready(coordinator)
            mock_log.warning.assert_called_once()
            warning_msg = mock_log.warning.call_args[0][0]
            assert "hook state not found" in warning_msg

        # No registrations should have happened
        assert coordinator.hooks.register.call_count == 0


# ---------------------------------------------------------------------------
# TestOnSessionReadyCleanup
# ---------------------------------------------------------------------------

class TestOnSessionReadyCleanup:
    """Handlers registered in on_session_ready() are torn down by cleanup()."""

    async def test_cleanup_unregisters_on_session_ready_handlers(self) -> None:
        """The cleanup function returned by mount() must unregister handlers
        added during on_session_ready()."""
        coordinator = _make_coordinator()
        cleanup = await _mount_and_ready(coordinator)

        # Some handlers were registered during on_session_ready
        assert len(coordinator._unregister_fns) >= len(ALL_EVENTS)

        # None called yet
        for unreg in coordinator._unregister_fns:
            unreg.assert_not_called()

        # Cleanup must call them all
        await cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()
