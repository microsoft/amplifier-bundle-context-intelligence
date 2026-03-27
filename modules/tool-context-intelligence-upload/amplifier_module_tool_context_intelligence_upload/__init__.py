"""Amplifier tool module for context-intelligence-upload.

Provides tools for uploading session context data to the Context Intelligence
ingestion pipeline. Phase 1 placeholder implementation — full functionality
will be added in Phase 2.
"""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core import ToolResult

logger = logging.getLogger(__name__)

__amplifier_module_type__ = "tool"


class _ContextIntelligenceUploadStartPlaceholder:
    """Placeholder tool for starting a context intelligence upload job.

    Registers with the coordinator to satisfy protocol compliance (Phase 1).
    Phase 2 will replace this with full implementation.
    """

    @property
    def name(self) -> str:
        return "context_intelligence_upload_start"

    @property
    def description(self) -> str:
        return (
            "Start an upload job to ingest session context data into the Context Intelligence "
            "pipeline. Accepts a path to the session data, with optional server_url and api_key "
            "overrides. Phase 2 implementation pending."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the session data to upload.",
                },
                "server_url": {
                    "type": "string",
                    "description": "Optional URL of the Context Intelligence server. "
                    "Defaults to the configured server.",
                },
                "api_key": {
                    "type": "string",
                    "description": "Optional API key for authentication. "
                    "Defaults to the configured key.",
                },
            },
            "required": ["path"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        """Return not-yet-implemented message."""
        return ToolResult(
            success=False,
            output=(
                "context_intelligence_upload_start: not yet implemented. "
                "Phase 2 will add full upload functionality."
            ),
        )


class _ContextIntelligenceUploadStatusPlaceholder:
    """Placeholder tool for checking the status of a context intelligence upload job.

    Registers with the coordinator to satisfy protocol compliance (Phase 1).
    Phase 2 will replace this with full implementation.
    """

    @property
    def name(self) -> str:
        return "context_intelligence_upload_status"

    @property
    def description(self) -> str:
        return (
            "Check the status of a context intelligence upload job by job ID. "
            "Phase 2 implementation pending."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The job ID returned by context_intelligence_upload_start.",
                },
            },
            "required": ["job_id"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        """Return not-yet-implemented message."""
        return ToolResult(
            success=False,
            output=(
                "context_intelligence_upload_status: not yet implemented. "
                "Phase 2 will add full status-check functionality."
            ),
        )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mount both context intelligence upload tools into the coordinator.

    Registers placeholder tools to satisfy protocol compliance during Phase 1.
    """
    start_tool = _ContextIntelligenceUploadStartPlaceholder()
    status_tool = _ContextIntelligenceUploadStatusPlaceholder()

    await coordinator.mount("tools", start_tool, name=start_tool.name)
    await coordinator.mount("tools", status_tool, name=status_tool.name)

    logger.info(
        "tool-context-intelligence-upload mounted: registered '%s' and '%s' (Phase 2 pending)",
        start_tool.name,
        status_tool.name,
    )

    return {
        "name": "tool-context-intelligence-upload",
        "version": "0.1.0",
        "provides": [start_tool.name, status_tool.name],
    }
