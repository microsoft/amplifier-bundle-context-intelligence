"""Tests for context_intelligence_upload_status — reads progress file.

Verifies that existing progress files are read and returned correctly,
and that missing progress files return status='not_found'.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_context_intelligence_upload import mount
from amplifier_module_tool_context_intelligence_upload.progress import ProgressTracker


class TestUploadStatus:
    """Verify progress file reading behaviour of context_intelligence_upload_status."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_status_tool(self, coordinator: MagicMock):
        """Mount tools and return the status tool from the second mount call."""
        await mount(coordinator)
        return coordinator.mount.call_args_list[1].args[1]

    def _make_coordinator(self) -> MagicMock:
        """Return a mock coordinator."""
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.get_capability = MagicMock(return_value=None)
        return coordinator

    # ------------------------------------------------------------------
    # Test 1: progress file exists
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_progress_json_when_file_exists(self, tmp_path: Path):
        """Status tool reads and returns progress JSON when the file exists."""
        # Create a progress file via ProgressTracker
        file_path = tmp_path / "context-intelligence-upload-test-job-123.json"
        tracker = ProgressTracker("test-job-123", file_path, sessions_total=3)
        tracker.start_session("s1", events_total=50)
        tracker.event_sent()

        # Mount tools and get status tool from second mount call
        coordinator = self._make_coordinator()
        status_tool = await self._get_status_tool(coordinator)

        # Patch progress_file_path so execute() reads our tmp_path file
        with patch(
            "amplifier_module_tool_context_intelligence_upload.progress_file_path",
            return_value=file_path,
        ):
            result = await status_tool.execute({"job_id": "test-job-123"})

        assert result.success is True
        output = json.loads(result.output)
        assert output["status"] == "running"
        assert output["current_session_id"] == "s1"
        assert output["current_session_events_sent"] == 1

    # ------------------------------------------------------------------
    # Test 2: progress file does not exist
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_not_found_when_file_missing(self):
        """Status tool returns status='not_found' when the progress file does not exist."""
        coordinator = self._make_coordinator()
        status_tool = await self._get_status_tool(coordinator)

        result = await status_tool.execute({"job_id": "nonexistent-job-id"})

        assert result.success is True
        output = json.loads(result.output)
        assert output["status"] == "not_found"
