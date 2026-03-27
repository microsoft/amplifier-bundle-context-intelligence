"""Tests for the amplifier_module_tool_context_intelligence_upload package."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import amplifier_module_tool_context_intelligence_upload as module
from amplifier_module_tool_context_intelligence_upload import (
    ContextIntelligenceUploadStart,
    ContextIntelligenceUploadStatus,
    mount,
)


class TestModuleAttributes:
    """Verify module-level attributes."""

    def test_module_type_is_tool(self):
        """__amplifier_module_type__ must be 'tool'."""
        assert module.__amplifier_module_type__ == "tool"

    def test_module_has_docstring(self):
        """Module must have a docstring."""
        assert module.__doc__ is not None
        assert len(module.__doc__.strip()) > 0


class TestUploadStart:
    """Verify the start tool class satisfies the Tool protocol."""

    def setup_method(self):
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        self.tool = ContextIntelligenceUploadStart(coordinator)

    def test_name(self):
        assert self.tool.name == "context_intelligence_upload_start"

    def test_description_is_string(self):
        assert isinstance(self.tool.description, str)
        assert len(self.tool.description) > 0

    def test_input_schema_is_dict(self):
        assert isinstance(self.tool.input_schema, dict)

    def test_input_schema_has_path_required(self):
        schema = self.tool.input_schema
        assert "path" in schema.get("properties", {})
        assert "path" in schema.get("required", [])

    def test_input_schema_has_server_url_optional(self):
        schema = self.tool.input_schema
        assert "server_url" in schema.get("properties", {})
        assert "server_url" not in schema.get("required", [])

    def test_input_schema_has_api_key_optional(self):
        schema = self.tool.input_schema
        assert "api_key" in schema.get("properties", {})
        assert "api_key" not in schema.get("required", [])


class TestUploadStatus:
    """Verify the status tool class satisfies the Tool protocol."""

    def setup_method(self):
        self.tool = ContextIntelligenceUploadStatus()

    def test_name(self):
        assert self.tool.name == "context_intelligence_upload_status"

    def test_description_is_string(self):
        assert isinstance(self.tool.description, str)
        assert len(self.tool.description) > 0

    def test_input_schema_is_dict(self):
        assert isinstance(self.tool.input_schema, dict)

    def test_input_schema_has_job_id_required(self):
        schema = self.tool.input_schema
        assert "job_id" in schema.get("properties", {})
        assert "job_id" in schema.get("required", [])


class TestMount:
    """Verify mount() registers both tools correctly."""

    @pytest.mark.asyncio
    async def test_mount_returns_dict(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        result = await mount(coordinator)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_mount_returns_name(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        result = await mount(coordinator)
        assert "name" in result
        assert isinstance(result["name"], str)

    @pytest.mark.asyncio
    async def test_mount_returns_version(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        result = await mount(coordinator)
        assert "version" in result
        assert result["version"] == "0.1.0"

    @pytest.mark.asyncio
    async def test_mount_returns_provides_list(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        result = await mount(coordinator)
        assert "provides" in result
        assert isinstance(result["provides"], list)
        assert len(result["provides"]) == 2

    @pytest.mark.asyncio
    async def test_mount_calls_coordinator_mount_twice(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        await mount(coordinator)
        assert coordinator.mount.call_count == 2

    @pytest.mark.asyncio
    async def test_mount_registers_start_tool(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        await mount(coordinator)
        calls = coordinator.mount.call_args_list
        tool_names = [call[1].get("name") or call[0][1].name for call in calls]
        assert "context_intelligence_upload_start" in tool_names

    @pytest.mark.asyncio
    async def test_mount_registers_status_tool(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        await mount(coordinator)
        calls = coordinator.mount.call_args_list
        tool_names = [call[1].get("name") or call[0][1].name for call in calls]
        assert "context_intelligence_upload_status" in tool_names

    @pytest.mark.asyncio
    async def test_mount_first_arg_is_tools(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        await mount(coordinator)
        for call in coordinator.mount.call_args_list:
            assert call[0][0] == "tools"

    @pytest.mark.asyncio
    async def test_mount_provides_contains_both_tool_names(self):
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability.return_value = None
        result = await mount(coordinator)
        assert "context_intelligence_upload_start" in result["provides"]
        assert "context_intelligence_upload_status" in result["provides"]
