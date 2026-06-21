"""Tests for the mount() entry point — basic contract."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def _make_coordinator() -> MagicMock:
    """Mock coordinator that captures register_capability values."""
    coordinator = MagicMock()
    coordinator.config = {}
    capabilities: dict[str, Any] = {}

    def _register_capability(name: str, value: Any) -> None:
        capabilities[name] = value

    coordinator.register_capability = MagicMock(side_effect=_register_capability)
    coordinator.get_capability = MagicMock(side_effect=lambda name: capabilities.get(name))
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock())
    coordinator.collect_contributions = AsyncMock(return_value=[])
    coordinator._capabilities = capabilities
    return coordinator


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
    from amplifier_module_hook_context_intelligence import mount

    coordinator = _make_coordinator()
    result = await mount(coordinator, config={})
    assert callable(result)


async def test_mount_registers_hook_state_capability():
    """mount() must register _hook_state containing logging_handler, unregister_fns, resolver."""
    from amplifier_module_hook_context_intelligence import mount
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler

    coordinator = _make_coordinator()
    await mount(coordinator, config={})

    state = coordinator._capabilities.get("context_intelligence._hook_state")
    assert state is not None, "_hook_state capability must be registered by mount()"
    assert "logging_handler" in state
    assert "unregister_fns" in state
    assert "resolver" in state
    assert isinstance(state["logging_handler"], LoggingHandler)
    assert isinstance(state["unregister_fns"], list)
    assert state["unregister_fns"] == [], "unregister_fns must be empty before on_session_ready"


async def test_mount_registers_no_logging_handler_events():
    """mount() must NOT register LoggingHandler for any events — that is on_session_ready's job."""
    from amplifier_module_hook_context_intelligence import mount

    coordinator = _make_coordinator()
    await mount(coordinator, config={})

    logging_calls = [
        c
        for c in coordinator.hooks.register.call_args_list
        if c.kwargs.get("name") == "LoggingHandler"
    ]
    assert len(logging_calls) == 0, (
        f"mount() registered {len(logging_calls)} LoggingHandler events — must be 0"
    )
