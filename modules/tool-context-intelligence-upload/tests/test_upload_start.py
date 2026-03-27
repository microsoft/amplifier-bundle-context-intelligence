"""Tests for context_intelligence_upload_start — subprocess spawn behaviour.

Verifies subprocess command structure, detached session flag, DEVNULL stdio,
return value structure, missing-config error handling, and ConfigResolver
integration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_context_intelligence_upload import mount


class TestUploadStartSpawn:
    """Verify subprocess spawning behaviour of context_intelligence_upload_start."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_start_tool(self, coordinator: MagicMock):
        """Mount tools and return the start tool from the first mount call."""
        await mount(coordinator)
        return coordinator.mount.call_args_list[0].args[1]

    def _make_coordinator(self, resolver=None) -> MagicMock:
        """Return a mock coordinator with optional capability resolver."""
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability = MagicMock(return_value=resolver)
        return coordinator

    # ------------------------------------------------------------------
    # Test 1: command structure
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_spawns_subprocess_with_correct_command(self):
        """Subprocess command must contain all required CLI arguments."""
        coordinator = self._make_coordinator()
        start_tool = await self._get_start_tool(coordinator)

        test_path = "/tmp/test-sessions"
        test_url = "http://ci.example.com"
        test_key = "my-api-key"

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await start_tool.execute(
                {
                    "path": test_path,
                    "server_url": test_url,
                    "api_key": test_key,
                }
            )

        assert result.success is True
        mock_popen.assert_called_once()

        cmd = mock_popen.call_args[0][0]
        assert sys.executable in cmd
        assert "-m" in cmd
        assert "amplifier_module_tool_context_intelligence_upload" in cmd
        assert "--path" in cmd
        assert test_path in cmd
        assert "--server-url" in cmd
        assert test_url in cmd
        assert "--api-key" in cmd
        assert test_key in cmd
        assert "--job-id" in cmd
        assert "--progress" in cmd

    # ------------------------------------------------------------------
    # Test 2: start_new_session flag
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_spawns_with_start_new_session(self):
        """Popen must be called with start_new_session=True."""
        coordinator = self._make_coordinator()
        start_tool = await self._get_start_tool(coordinator)

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await start_tool.execute(
                {
                    "path": "/tmp/sessions",
                    "server_url": "http://ci.example.com",
                    "api_key": "my-key",
                }
            )

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("start_new_session") is True

    # ------------------------------------------------------------------
    # Test 3: DEVNULL stdio
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_spawns_with_devnull_stdio(self):
        """Popen must be called with stdout=DEVNULL and stderr=DEVNULL."""
        coordinator = self._make_coordinator()
        start_tool = await self._get_start_tool(coordinator)

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            await start_tool.execute(
                {
                    "path": "/tmp/sessions",
                    "server_url": "http://ci.example.com",
                    "api_key": "my-key",
                }
            )

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("stdout") == subprocess.DEVNULL
        assert call_kwargs.get("stderr") == subprocess.DEVNULL

    # ------------------------------------------------------------------
    # Test 4: return value structure
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_job_id_and_progress_file(self):
        """Output JSON must contain job_id, progress_file, and a meaningful message."""
        coordinator = self._make_coordinator()
        start_tool = await self._get_start_tool(coordinator)

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await start_tool.execute(
                {
                    "path": "/tmp/sessions",
                    "server_url": "http://ci.example.com",
                    "api_key": "my-key",
                }
            )

        assert result.success is True
        output = json.loads(result.output)
        assert "job_id" in output
        assert "progress_file" in output
        assert "message" in output

        message = output["message"]
        # Message should describe that an upload was started in the background
        assert "Upload" in message
        assert "started" in message
        assert "background" in message

    # ------------------------------------------------------------------
    # Test 5: failure when server_url is missing
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_fails_without_server_url(self):
        """execute() must fail with a helpful error when server_url is absent and no ConfigResolver."""
        coordinator = self._make_coordinator()  # get_capability returns None
        start_tool = await self._get_start_tool(coordinator)

        result = await start_tool.execute({"path": "/tmp/sessions"})

        assert result.success is False
        assert "server_url" in result.output

    # ------------------------------------------------------------------
    # Test 6: ConfigResolver integration
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_resolves_server_url_from_config_resolver(self):
        """execute() must use server_url and api_key from ConfigResolver when not in input."""
        mock_resolver = MagicMock()
        mock_resolver.context_intelligence_server_url = "http://resolved.example.com"
        mock_resolver.context_intelligence_api_key = "resolved-api-key"

        coordinator = self._make_coordinator(resolver=mock_resolver)
        start_tool = await self._get_start_tool(coordinator)

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = await start_tool.execute({"path": "/tmp/sessions"})

        assert result.success is True
        cmd = mock_popen.call_args[0][0]
        assert "http://resolved.example.com" in cmd
        assert "resolved-api-key" in cmd
