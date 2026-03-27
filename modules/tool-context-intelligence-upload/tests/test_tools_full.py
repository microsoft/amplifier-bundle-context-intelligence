"""Tests for the full tool implementations (Phase 2) in __init__.py.

These tests verify the production-ready behavior of ContextIntelligenceUploadStart
and ContextIntelligenceUploadStatus, including subprocess spawning, config
resolution, and progress file reading.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_core import ToolResult

from amplifier_module_tool_context_intelligence_upload import (
    ContextIntelligenceUploadStart,
    ContextIntelligenceUploadStatus,
    _resolve_config,
    mount,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(server_url: str | None = None, api_key: str | None = None) -> MagicMock:
    """Return a mock coordinator with an optional ConfigResolver capability."""
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()
    if server_url is not None or api_key is not None:
        resolver = MagicMock()
        resolver.context_intelligence_server_url = server_url
        resolver.context_intelligence_api_key = api_key
        coordinator.get_capability.return_value = resolver
    else:
        coordinator.get_capability.return_value = None
    return coordinator


# ---------------------------------------------------------------------------
# _resolve_config
# ---------------------------------------------------------------------------


class TestResolveConfig:
    """Verify the _resolve_config helper function."""

    def test_returns_tuple(self):
        coordinator = _make_coordinator(server_url="http://ci.example.com", api_key="key123")
        result = _resolve_config(coordinator)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_returns_server_url_from_resolver(self):
        coordinator = _make_coordinator(server_url="http://ci.example.com", api_key="key123")
        server_url, _ = _resolve_config(coordinator)
        assert server_url == "http://ci.example.com"

    def test_returns_api_key_from_resolver(self):
        coordinator = _make_coordinator(server_url="http://ci.example.com", api_key="key123")
        _, api_key = _resolve_config(coordinator)
        assert api_key == "key123"

    def test_returns_none_when_no_resolver(self):
        coordinator = _make_coordinator()
        server_url, api_key = _resolve_config(coordinator)
        assert server_url is None
        assert api_key is None

    def test_uses_correct_capability_name(self):
        coordinator = _make_coordinator(server_url="http://ci.example.com", api_key="key123")
        _resolve_config(coordinator)
        coordinator.get_capability.assert_called_with("context_intelligence.config_resolver")


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStart — identity
# ---------------------------------------------------------------------------


class TestUploadStartIdentity:
    """Verify name, description, and input_schema of the start tool."""

    def setup_method(self):
        self.coordinator = _make_coordinator()
        self.tool = ContextIntelligenceUploadStart(self.coordinator)

    def test_name(self):
        assert self.tool.name == "context_intelligence_upload_start"

    def test_description_is_non_empty_string(self):
        assert isinstance(self.tool.description, str)
        assert len(self.tool.description.strip()) > 0

    def test_description_mentions_background(self):
        # The spec says description should explain background upload behaviour
        desc_lower = self.tool.description.lower()
        assert "background" in desc_lower or "detach" in desc_lower or "spawn" in desc_lower

    def test_input_schema_type_object(self):
        schema = self.tool.input_schema
        assert schema.get("type") == "object"

    def test_input_schema_path_required(self):
        schema = self.tool.input_schema
        assert "path" in schema.get("properties", {})
        assert "path" in schema.get("required", [])

    def test_input_schema_server_url_optional(self):
        schema = self.tool.input_schema
        assert "server_url" in schema.get("properties", {})
        assert "server_url" not in schema.get("required", [])

    def test_input_schema_api_key_optional(self):
        schema = self.tool.input_schema
        assert "api_key" in schema.get("properties", {})
        assert "api_key" not in schema.get("required", [])


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStart — execute: missing credentials
# ---------------------------------------------------------------------------


class TestUploadStartMissingCredentials:
    """Verify helpful errors when credentials are missing."""

    @pytest.mark.asyncio
    async def test_missing_server_url_returns_failure(self):
        coordinator = _make_coordinator(server_url=None, api_key=None)
        tool = ContextIntelligenceUploadStart(coordinator)
        result = await tool.execute({"path": "/tmp/sessions", "api_key": "mykey"})
        assert isinstance(result, ToolResult)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_failure(self):
        coordinator = _make_coordinator(server_url=None, api_key=None)
        tool = ContextIntelligenceUploadStart(coordinator)
        result = await tool.execute(
            {"path": "/tmp/sessions", "server_url": "http://ci.example.com"}
        )
        assert isinstance(result, ToolResult)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_both_credentials_returns_failure(self):
        coordinator = _make_coordinator(server_url=None, api_key=None)
        tool = ContextIntelligenceUploadStart(coordinator)
        result = await tool.execute({"path": "/tmp/sessions"})
        assert isinstance(result, ToolResult)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_server_url_error_message_is_helpful(self):
        coordinator = _make_coordinator(server_url=None, api_key="key123")
        tool = ContextIntelligenceUploadStart(coordinator)
        result = await tool.execute({"path": "/tmp/sessions", "api_key": "key123"})
        assert result.output is not None
        assert len(result.output) > 0

    @pytest.mark.asyncio
    async def test_missing_api_key_error_message_is_helpful(self):
        coordinator = _make_coordinator(server_url="http://ci.example.com", api_key=None)
        tool = ContextIntelligenceUploadStart(coordinator)
        result = await tool.execute(
            {"path": "/tmp/sessions", "server_url": "http://ci.example.com"}
        )
        assert result.output is not None
        assert len(result.output) > 0


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStart — execute: config resolution fallback
# ---------------------------------------------------------------------------


class TestUploadStartConfigFallback:
    """Verify that execute() falls back to ConfigResolver for missing params."""

    @pytest.mark.asyncio
    async def test_uses_resolver_server_url_when_not_in_input(self):
        """If server_url not in input, falls back to ConfigResolver."""
        coordinator = _make_coordinator(server_url="http://ci.example.com", api_key="resolver-key")
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_uses_resolver_api_key_when_not_in_input(self):
        """If api_key not in input, falls back to ConfigResolver."""
        coordinator = _make_coordinator(server_url="http://ci.example.com", api_key="resolver-key")
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_explicit_input_overrides_resolver(self):
        """Input params override resolver values."""
        coordinator = _make_coordinator(
            server_url="http://resolver.example.com", api_key="resolver-key"
        )
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute(
                {
                    "path": "/tmp/sessions",
                    "server_url": "http://input.example.com",
                    "api_key": "input-key",
                }
            )
        assert result.success is True
        # Verify subprocess was called with input values, not resolver values
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "http://input.example.com" in cmd
        assert "input-key" in cmd


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStart — execute: subprocess spawning
# ---------------------------------------------------------------------------


class TestUploadStartSubprocess:
    """Verify subprocess spawning behavior."""

    def _make_configured_coordinator(self) -> MagicMock:
        return _make_coordinator(server_url="http://ci.example.com", api_key="test-key")

    @pytest.mark.asyncio
    async def test_spawns_subprocess_with_devnull_stdout(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/sessions"})
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("stdout") == subprocess.DEVNULL

    @pytest.mark.asyncio
    async def test_spawns_subprocess_with_devnull_stderr(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/sessions"})
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("stderr") == subprocess.DEVNULL

    @pytest.mark.asyncio
    async def test_spawns_subprocess_with_start_new_session(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/sessions"})
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("start_new_session") is True

    @pytest.mark.asyncio
    async def test_subprocess_command_includes_path(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/my-sessions"})
        cmd = mock_popen.call_args[0][0]
        assert "/tmp/my-sessions" in cmd

    @pytest.mark.asyncio
    async def test_subprocess_command_includes_server_url(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/sessions"})
        cmd = mock_popen.call_args[0][0]
        assert "http://ci.example.com" in cmd

    @pytest.mark.asyncio
    async def test_subprocess_command_includes_api_key(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/sessions"})
        cmd = mock_popen.call_args[0][0]
        assert "test-key" in cmd

    @pytest.mark.asyncio
    async def test_subprocess_command_uses_sys_executable(self):
        import sys

        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/sessions"})
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == sys.executable

    @pytest.mark.asyncio
    async def test_subprocess_command_uses_module_flag(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await tool.execute({"path": "/tmp/sessions"})
        cmd = mock_popen.call_args[0][0]
        assert "-m" in cmd
        assert "amplifier_module_tool_context_intelligence_upload" in cmd


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStart — execute: return value
# ---------------------------------------------------------------------------


class TestUploadStartReturnValue:
    """Verify the ToolResult returned on successful spawn."""

    def _make_configured_coordinator(self) -> MagicMock:
        return _make_coordinator(server_url="http://ci.example.com", api_key="test-key")

    @pytest.mark.asyncio
    async def test_returns_tool_result_success_true(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert isinstance(result, ToolResult)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_output_contains_job_id(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.output is not None
        data = json.loads(result.output)
        assert "job_id" in data
        assert len(data["job_id"]) > 0

    @pytest.mark.asyncio
    async def test_output_contains_progress_file(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.output is not None
        data = json.loads(result.output)
        assert "progress_file" in data

    @pytest.mark.asyncio
    async def test_output_contains_message(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.output is not None
        data = json.loads(result.output)
        assert "message" in data

    @pytest.mark.asyncio
    async def test_job_id_is_uuid4_format(self):
        import uuid

        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.output is not None
        data = json.loads(result.output)
        # Should be a valid UUID
        uuid_obj = uuid.UUID(data["job_id"])
        assert uuid_obj.version == 4

    @pytest.mark.asyncio
    async def test_job_id_matches_progress_file(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.output is not None
        data = json.loads(result.output)
        assert data["job_id"] in data["progress_file"]

    @pytest.mark.asyncio
    async def test_subprocess_command_includes_job_id(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.output is not None
        data = json.loads(result.output)
        cmd = mock_popen.call_args[0][0]
        assert data["job_id"] in cmd

    @pytest.mark.asyncio
    async def test_subprocess_command_includes_progress_file(self):
        coordinator = self._make_configured_coordinator()
        tool = ContextIntelligenceUploadStart(coordinator)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await tool.execute({"path": "/tmp/sessions"})
        assert result.output is not None
        data = json.loads(result.output)
        cmd = mock_popen.call_args[0][0]
        assert data["progress_file"] in cmd


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStatus — identity
# ---------------------------------------------------------------------------


class TestUploadStatusIdentity:
    """Verify name, description, and input_schema of the status tool."""

    def setup_method(self):
        self.tool = ContextIntelligenceUploadStatus()

    def test_name(self):
        assert self.tool.name == "context_intelligence_upload_status"

    def test_description_is_non_empty_string(self):
        assert isinstance(self.tool.description, str)
        assert len(self.tool.description.strip()) > 0

    def test_input_schema_type_object(self):
        schema = self.tool.input_schema
        assert schema.get("type") == "object"

    def test_input_schema_job_id_required(self):
        schema = self.tool.input_schema
        assert "job_id" in schema.get("properties", {})
        assert "job_id" in schema.get("required", [])


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStatus — execute
# ---------------------------------------------------------------------------


class TestUploadStatusExecute:
    """Verify status reading behavior."""

    def setup_method(self):
        self.tool = ContextIntelligenceUploadStatus()

    @pytest.mark.asyncio
    async def test_missing_progress_file_returns_not_found(self):
        """Returns {status: not_found} when progress file doesn't exist."""
        with patch(
            "amplifier_module_tool_context_intelligence_upload.ProgressTracker"
        ) as mock_pt_class:
            mock_pt_class.read_file.return_value = None
            result = await self.tool.execute({"job_id": "nonexistent-job-id"})
        assert isinstance(result, ToolResult)
        assert result.output is not None
        data = json.loads(result.output)
        assert data["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_existing_progress_file_returns_state(self):
        """Returns the full progress state when file exists."""
        progress_state = {
            "job_id": "test-job-123",
            "status": "running",
            "sessions_total": 10,
            "sessions_completed": 3,
        }
        with patch(
            "amplifier_module_tool_context_intelligence_upload.ProgressTracker"
        ) as mock_pt_class:
            mock_pt_class.read_file.return_value = progress_state
            result = await self.tool.execute({"job_id": "test-job-123"})
        assert isinstance(result, ToolResult)
        assert result.output is not None
        data = json.loads(result.output)
        assert data["status"] == "running"
        assert data["job_id"] == "test-job-123"

    @pytest.mark.asyncio
    async def test_completed_job_returns_completed_status(self):
        progress_state = {
            "job_id": "done-job",
            "status": "completed",
            "sessions_total": 5,
            "sessions_completed": 5,
        }
        with patch(
            "amplifier_module_tool_context_intelligence_upload.ProgressTracker"
        ) as mock_pt_class:
            mock_pt_class.read_file.return_value = progress_state
            result = await self.tool.execute({"job_id": "done-job"})
        assert result.output is not None
        data = json.loads(result.output)
        assert data["status"] == "completed"


# ---------------------------------------------------------------------------
# mount() with full implementations
# ---------------------------------------------------------------------------


class TestMountFullImplementation:
    """Verify mount() creates and registers full implementation tools."""

    @pytest.mark.asyncio
    async def test_mount_registers_start_as_full_implementation(self):
        coordinator = _make_coordinator()
        await mount(coordinator)
        calls = coordinator.mount.call_args_list
        # Find the start tool call
        start_calls = [
            c for c in calls if (c[1].get("name") == "context_intelligence_upload_start")
        ]
        assert len(start_calls) == 1
        # The tool should be an instance of the full class, not placeholder
        tool = start_calls[0][0][1]
        assert isinstance(tool, ContextIntelligenceUploadStart)

    @pytest.mark.asyncio
    async def test_mount_registers_status_as_full_implementation(self):
        coordinator = _make_coordinator()
        await mount(coordinator)
        calls = coordinator.mount.call_args_list
        # Find the status tool call
        status_calls = [
            c for c in calls if (c[1].get("name") == "context_intelligence_upload_status")
        ]
        assert len(status_calls) == 1
        tool = status_calls[0][0][1]
        assert isinstance(tool, ContextIntelligenceUploadStatus)

    @pytest.mark.asyncio
    async def test_mount_passes_coordinator_to_start_tool(self):
        """ContextIntelligenceUploadStart receives coordinator in __init__."""
        coordinator = _make_coordinator()
        with patch(
            "amplifier_module_tool_context_intelligence_upload.ContextIntelligenceUploadStart"
        ) as mock_cls:
            mock_instance = MagicMock()
            mock_instance.name = "context_intelligence_upload_start"
            mock_cls.return_value = mock_instance
            await mount(coordinator)
        mock_cls.assert_called_once_with(coordinator)
