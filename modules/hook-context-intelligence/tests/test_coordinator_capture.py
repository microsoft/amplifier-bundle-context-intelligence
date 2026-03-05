"""Tests for coordinator capture on HookStateService and MountFlow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from amplifier_module_hook_context_intelligence.mount import MountFlow
from amplifier_module_hook_context_intelligence.services import HookStateService


class TestHookStateServiceCoordinator:
    def test_coordinator_stored_when_passed(self):
        coordinator = MagicMock()
        service = HookStateService(raw_config={}, coordinator=coordinator)
        assert service.coordinator is coordinator

    def test_coordinator_defaults_to_none(self):
        service = HookStateService(raw_config={})
        assert service.coordinator is None


class TestMountFlowCoordinatorPassthrough:
    def test_create_services_passes_coordinator(self):
        flow = MountFlow(config={})
        coordinator = MagicMock()
        flow.create_services(coordinator)
        assert flow.services is not None
        assert flow.services.coordinator is coordinator

    def test_create_services_with_none_coordinator(self):
        flow = MountFlow(config={})
        flow.create_services(None)
        assert flow.services is not None
        assert flow.services.coordinator is None

    async def test_run_passes_coordinator_to_services(self):
        coordinator = MagicMock()
        coordinator.hooks.register = MagicMock(return_value=MagicMock())
        coordinator.collect_contributions = AsyncMock(return_value=[])
        coordinator.get_capability = MagicMock(return_value=None)

        flow = MountFlow(config={})
        await flow.run(coordinator)
        assert flow.services is not None
        assert flow.services.coordinator is coordinator
