"""Tests for __init__.py — mount contract and tool protocol."""

from __future__ import annotations

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock

import amplifier_module_tool_context_intelligence_upload as module
from amplifier_module_tool_context_intelligence_upload import mount


class TestModuleContract:
    """Verify module-level contract: type marker, mount coroutine, and signature."""

    def test_amplifier_module_type_is_tool(self):
        """__amplifier_module_type__ must equal 'tool'."""
        assert module.__amplifier_module_type__ == "tool"

    def test_mount_is_coroutine_function(self):
        """mount must be a coroutine function (async def)."""
        assert inspect.iscoroutinefunction(mount)

    def test_mount_signature_has_coordinator_and_config(self):
        """mount signature must have 'coordinator' and 'config' as the first two parameters."""
        sig = inspect.signature(mount)
        params = list(sig.parameters.keys())
        assert len(params) >= 2, "mount must have at least two parameters"
        assert params[0] == "coordinator"
        assert params[1] == "config"


class TestMountBehavior:
    """Verify mount() runtime behavior: call counts, namespaces, metadata, and tool protocol."""

    @pytest.mark.asyncio
    async def test_mount_calls_coordinator_mount_exactly_twice(self):
        """mount() must call coordinator.mount exactly twice (once per tool)."""
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator)
        assert coordinator.mount.call_count == 2

    @pytest.mark.asyncio
    async def test_first_tool_registered_is_upload_start_in_tools_namespace(self):
        """First coordinator.mount call must register 'context_intelligence_upload_start' in 'tools'."""
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator)
        first_call = coordinator.mount.call_args_list[0]
        namespace = first_call[0][0]
        first_tool = first_call[0][1]
        assert namespace == "tools"
        assert first_tool.name == "context_intelligence_upload_start"

    @pytest.mark.asyncio
    async def test_second_tool_registered_is_upload_status_in_tools_namespace(self):
        """Second coordinator.mount call must register 'context_intelligence_upload_status' in 'tools'."""
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator)
        second_call = coordinator.mount.call_args_list[1]
        namespace = second_call[0][0]
        second_tool = second_call[0][1]
        assert namespace == "tools"
        assert second_tool.name == "context_intelligence_upload_status"

    @pytest.mark.asyncio
    async def test_mount_returns_metadata_with_name_and_provides(self):
        """mount() must return a dict with name='tool-context-intelligence-upload' and provides containing both tool names."""
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        result = await mount(coordinator)
        assert isinstance(result, dict)
        assert result.get("name") == "tool-context-intelligence-upload"
        provides = result.get("provides")
        assert isinstance(provides, list)
        assert "context_intelligence_upload_start" in provides
        assert "context_intelligence_upload_status" in provides

    @pytest.mark.asyncio
    async def test_registered_tools_satisfy_tool_protocol(self):
        """Both registered tools must have name (str), description (str), input_schema (dict), execute (coroutine)."""
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator)
        for call in coordinator.mount.call_args_list:
            tool = call[0][1]
            # name: non-empty str
            assert isinstance(tool.name, str)
            assert len(tool.name) > 0
            # description: non-empty str
            assert isinstance(tool.description, str)
            assert len(tool.description) > 0
            # input_schema: dict
            assert isinstance(tool.input_schema, dict)
            # execute: coroutine function
            assert inspect.iscoroutinefunction(tool.execute)
