"""Tests for tool-blob-read module mount contract."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock


class TestModuleContract:
    """Module-level contract for a tool module."""

    def test_module_type_is_tool(self) -> None:
        from amplifier_module_tool_blob_read import __amplifier_module_type__

        assert __amplifier_module_type__ == "tool"

    def test_mount_is_coroutine(self) -> None:
        from amplifier_module_tool_blob_read import mount

        assert inspect.iscoroutinefunction(mount)

    def test_mount_signature_has_coordinator_and_config(self) -> None:
        from amplifier_module_tool_blob_read import mount

        sig = inspect.signature(mount)
        params = list(sig.parameters.keys())
        assert params[0] == "coordinator"
        assert params[1] == "config"


class TestMountBehavior:
    """mount() registers a Tool-protocol-compliant object via coordinator.mount()."""

    async def test_mount_calls_coordinator_mount_with_tools_category(self) -> None:
        from amplifier_module_tool_blob_read import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        coordinator.mount.assert_called_once()
        assert coordinator.mount.call_args.args[0] == "tools"

    async def test_mounted_tool_has_name_blob_read(self) -> None:
        from amplifier_module_tool_blob_read import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        assert coordinator.mount.call_args.kwargs["name"] == "blob_read"

    async def test_mounted_tool_is_protocol_compliant(self) -> None:
        from amplifier_module_tool_blob_read import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        tool = coordinator.mount.call_args.args[1]
        assert hasattr(tool, "name")
        assert hasattr(tool, "description")
        assert hasattr(tool, "get_schema")
        assert hasattr(tool, "execute")
        assert callable(tool.get_schema)
        assert inspect.iscoroutinefunction(tool.execute)
