"""Tests for amplifier_module_tool_bundle_usage.__init__ module contract.

Mirrors test_mount.py from tool-graph-query: verifies the module-level
protocol and mount() behaviour expected by the Amplifier Tool protocol.

BundleUsageTool is mocked here since it lives in bundle_usage_tool.py
which is a separate implementation task.
"""

from __future__ import annotations

import inspect
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock


def _make_mock_bundle_usage_tool_class(name: str = "bundle_usage") -> MagicMock:
    """Return a mock BundleUsageTool class whose instances have `name = 'bundle_usage'`."""
    instance = MagicMock()
    instance.name = name
    cls = MagicMock(return_value=instance)
    return cls


def _inject_fake_bundle_usage_tool_module(monkeypatch) -> MagicMock:
    """Inject a fake bundle_usage_tool submodule so mount() can import BundleUsageTool."""
    mock_cls = _make_mock_bundle_usage_tool_class()

    fake_submodule = ModuleType("amplifier_module_tool_bundle_usage.bundle_usage_tool")
    fake_submodule.BundleUsageTool = mock_cls  # type: ignore[attr-defined]

    monkeypatch.setitem(
        sys.modules,
        "amplifier_module_tool_bundle_usage.bundle_usage_tool",
        fake_submodule,
    )
    return mock_cls


class TestModuleContract:
    """Module-level contract for a tool module."""

    def test_module_type_is_tool(self) -> None:
        from amplifier_module_tool_bundle_usage import __amplifier_module_type__

        assert __amplifier_module_type__ == "tool"

    def test_mount_is_coroutine(self) -> None:
        from amplifier_module_tool_bundle_usage import mount

        assert inspect.iscoroutinefunction(mount)

    def test_mount_signature_has_coordinator_and_config(self) -> None:
        from amplifier_module_tool_bundle_usage import mount

        sig = inspect.signature(mount)
        params = list(sig.parameters.keys())
        assert params[0] == "coordinator"
        assert params[1] == "config"


class TestMountBehavior:
    """mount() registers a Tool-protocol-compliant object via coordinator.mount().

    BundleUsageTool is mocked via monkeypatch so tests stay isolated to __init__.py.
    """

    async def test_mount_calls_coordinator_mount_with_tools_category(self, monkeypatch) -> None:
        _inject_fake_bundle_usage_tool_module(monkeypatch)
        from amplifier_module_tool_bundle_usage import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        coordinator.mount.assert_called_once()
        assert coordinator.mount.call_args.args[0] == "tools"

    async def test_mounted_tool_has_name_bundle_usage(self, monkeypatch) -> None:
        _inject_fake_bundle_usage_tool_module(monkeypatch)
        from amplifier_module_tool_bundle_usage import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        assert coordinator.mount.call_args.kwargs["name"] == "bundle_usage"

    async def test_mount_does_not_call_get_capability(self, monkeypatch) -> None:
        """Hooks register AFTER tools — mount must NOT resolve the capability."""
        _inject_fake_bundle_usage_tool_module(monkeypatch)
        from amplifier_module_tool_bundle_usage import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        coordinator.get_capability.assert_not_called()

    async def test_mount_returns_metadata_dict(self, monkeypatch) -> None:
        _inject_fake_bundle_usage_tool_module(monkeypatch)
        from amplifier_module_tool_bundle_usage import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        result = await mount(coordinator, config={})
        assert isinstance(result, dict)
        assert result["tool"] == "bundle_usage"
        assert result["status"] == "mounted"
