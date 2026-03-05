"""Tests for the mount() entry point — basic contract."""

from __future__ import annotations

import inspect


def test_module_type_is_hook():
    from amplifier_module_hook_context_intelligence import __amplifier_module_type__

    assert __amplifier_module_type__ == "hook"


def test_mount_is_coroutine():
    from amplifier_module_hook_context_intelligence import mount

    assert inspect.iscoroutinefunction(mount)


def test_mount_signature_accepts_coordinator_and_config():
    from amplifier_module_hook_context_intelligence import mount

    sig = inspect.signature(mount)
    params = list(sig.parameters.keys())
    assert params[0] == "coordinator"
    assert params[1] == "config"


async def test_mount_returns_cleanup_callable():
    from unittest.mock import MagicMock
    from amplifier_module_hook_context_intelligence import mount

    coordinator = MagicMock()
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock())
    coordinator.collect_contributions = MagicMock(return_value=[])
    coordinator.get_capability = MagicMock(return_value=None)

    result = await mount(coordinator, config={})
    assert result is None or callable(result)
