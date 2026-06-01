"""Assertions that skill-fetch code has been fully stripped from the hook module.

Tests verify:
- The skill_fetcher sub-module is no longer importable.
- mount() registers no skill-related event handlers (skills:discovered, skill:unloaded).
- mount() still registers the context_intelligence.hook_config_resolver capability.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

_HookCalls = list[tuple[str, object, dict[str, object]]]


def _capture_register() -> tuple[MagicMock, _HookCalls]:
    """Return a (register_mock, calls) pair.

    The mock appends (event, handler, dict(kwargs)) to *calls* on each
    invocation and returns a fresh MagicMock() as the unregister handle.
    """
    calls: _HookCalls = []

    def _side_effect(event: str, handler: object, **kwargs: object) -> MagicMock:
        calls.append((event, handler, dict(kwargs)))
        return MagicMock()

    return MagicMock(side_effect=_side_effect), calls


class TestSkillFetcherModuleGone:
    def test_skill_fetcher_module_is_unimportable(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("amplifier_module_hook_context_intelligence.skill_fetcher")


class TestMountRegistersNoSkillHandlers:
    async def test_mount_registers_no_skill_event_handlers(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = MagicMock()
        coordinator.config = {}
        coordinator.collect_contributions = AsyncMock(return_value=[])
        coordinator.register_capability = MagicMock()
        register, calls = _capture_register()
        coordinator.hooks.register = register

        await mount(coordinator, config={})

        registered_events = {evt for evt, _h, _k in calls}
        assert "skills:discovered" not in registered_events
        assert "skill:unloaded" not in registered_events

    async def test_mount_still_registers_hook_config_resolver_capability(self) -> None:
        from amplifier_module_hook_context_intelligence import mount

        coordinator = MagicMock()
        coordinator.config = {}
        coordinator.collect_contributions = AsyncMock(return_value=[])
        coordinator.register_capability = MagicMock()
        register, calls = _capture_register()
        coordinator.hooks.register = register

        await mount(coordinator, config={})

        cap_names = [c.args[0] for c in coordinator.register_capability.call_args_list]
        assert "context_intelligence.hook_config_resolver" in cap_names
