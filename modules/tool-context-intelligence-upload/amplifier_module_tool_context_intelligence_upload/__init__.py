"""Amplifier tool module for context-intelligence-upload.

Provides two tools for managing background uploads of session context data to
the Context Intelligence ingestion pipeline:

- ``context_intelligence_upload_start``: Spawns a detached background subprocess
  that runs the upload CLI, resolving ``server_url`` and ``api_key`` from the
  ``context_intelligence.config_resolver`` capability when not supplied directly.

- ``context_intelligence_upload_status``: Reads the progress file written by the
  background subprocess and returns the current upload state as JSON.

ConfigResolver behaviour
------------------------
Both ``server_url`` and ``api_key`` can be provided explicitly in the tool input.
When either is absent, ``_resolve_config(coordinator)`` is called.  It reads the
``context_intelligence.config_resolver`` capability (registered by the hook module)
and returns ``(context_intelligence_server_url, context_intelligence_api_key)``
from that resolver.  If the capability is not registered, ``(None, None)`` is
returned and the tool reports a helpful error.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import uuid
from typing import Any

from amplifier_core import ToolResult

from .progress import ProgressTracker, progress_file_path

logger = logging.getLogger(__name__)

__amplifier_module_type__ = "tool"


# ---------------------------------------------------------------------------
# Config resolution helper
# ---------------------------------------------------------------------------


def _resolve_config(coordinator: Any) -> tuple[str | None, str | None]:
    """Resolve server_url and api_key from the coordinator's ConfigResolver capability.

    Calls ``coordinator.get_capability('context_intelligence.config_resolver')``
    and extracts ``context_intelligence_server_url`` and
    ``context_intelligence_api_key`` from the returned resolver object.

    Returns a ``(server_url, api_key)`` tuple.  Either or both values may be
    ``None`` if the capability is not registered or the resolver has no values.
    """
    resolver = coordinator.get_capability("context_intelligence.config_resolver")
    if resolver is None:
        return (None, None)
    return (
        resolver.context_intelligence_server_url,
        resolver.context_intelligence_api_key,
    )


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStart
# ---------------------------------------------------------------------------


class ContextIntelligenceUploadStart:
    """Tool that starts a background context-intelligence upload job.

    Spawns a detached subprocess (``start_new_session=True``, stdio redirected
    to ``DEVNULL``) running the upload CLI so the caller is not blocked while
    the upload proceeds.  Progress is tracked in a JSON file on disk that can
    be polled with ``context_intelligence_upload_status``.

    ``server_url`` and ``api_key`` are resolved in priority order:
    1. Explicit values in the tool input.
    2. Values from the ``context_intelligence.config_resolver`` capability.

    If neither source provides a value, a failure ``ToolResult`` is returned
    with a helpful error message.
    """

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator

    @property
    def name(self) -> str:
        return "context_intelligence_upload_start"

    @property
    def description(self) -> str:
        return (
            "Start a background upload job that ingests session context data into the "
            "Context Intelligence pipeline.  The upload runs as a detached background "
            "subprocess so the tool returns immediately with a job_id and progress_file "
            "path.  Use context_intelligence_upload_status to poll for completion.\n\n"
            "server_url and api_key are optional: when not provided they are resolved "
            "from the context_intelligence.config_resolver capability (set by the hook "
            "module via context_intelligence_server_url / context_intelligence_api_key "
            "config keys).  workspace is passed through from each event's own field; "
            "no workspace transformation is applied by this tool."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path to the session data directory (or a single metadata.json file) "
                        "to upload.  The CLI discovers all context-intelligence sessions "
                        "under this path and uploads them in BFS topological order."
                    ),
                },
                "server_url": {
                    "type": "string",
                    "description": (
                        "Base URL of the Context Intelligence server "
                        "(e.g. https://ci.example.com).  Optional: falls back to "
                        "context_intelligence_server_url from the config resolver."
                    ),
                },
                "api_key": {
                    "type": "string",
                    "description": (
                        "Bearer token for authentication.  Optional: falls back to "
                        "context_intelligence_api_key from the config resolver."
                    ),
                },
            },
            "required": ["path"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        """Spawn a detached upload subprocess and return job metadata.

        Resolution order for server_url / api_key:
        1. Value from *input_data*.
        2. Value from ``context_intelligence.config_resolver`` capability.

        Returns ``ToolResult(success=False)`` with a helpful message if
        *server_url* or *api_key* cannot be resolved.
        """
        path: str = input_data["path"]

        # Resolve credentials
        resolved_url, resolved_key = _resolve_config(self._coordinator)

        server_url: str | None = input_data.get("server_url") or resolved_url
        api_key: str | None = input_data.get("api_key") or resolved_key

        if not server_url:
            return ToolResult(
                success=False,
                output=(
                    "server_url is required but was not provided and could not be resolved "
                    "from the context_intelligence.config_resolver capability.  "
                    "Pass server_url explicitly or set context_intelligence_server_url "
                    "in the hook module configuration."
                ),
            )

        if not api_key:
            return ToolResult(
                success=False,
                output=(
                    "api_key is required but was not provided and could not be resolved "
                    "from the context_intelligence.config_resolver capability.  "
                    "Pass api_key explicitly or set context_intelligence_api_key "
                    "in the hook module configuration."
                ),
            )

        # Generate a stable job identifier
        job_id = str(uuid.uuid4())
        progress_fp = progress_file_path(job_id)

        # Build the subprocess command
        cmd = [
            sys.executable,
            "-m",
            "amplifier_module_tool_context_intelligence_upload",
            "--path",
            path,
            "--server-url",
            server_url,
            "--api-key",
            api_key,
            "--job-id",
            job_id,
            "--progress",
            str(progress_fp),
        ]

        # Spawn detached; we do not wait for it
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        logger.info(
            "context_intelligence_upload_start: spawned job %s for path=%s",
            job_id,
            path,
        )

        output = {
            "job_id": job_id,
            "progress_file": str(progress_fp),
            "message": (
                f"Upload job {job_id} started in the background.  "
                f"Poll context_intelligence_upload_status with job_id='{job_id}' "
                f"to check progress."
            ),
        }
        return ToolResult(success=True, output=json.dumps(output))


# ---------------------------------------------------------------------------
# ContextIntelligenceUploadStatus
# ---------------------------------------------------------------------------


class ContextIntelligenceUploadStatus:
    """Tool that checks the progress of a context-intelligence upload job.

    Reads the JSON progress file written by the background subprocess started
    by ``context_intelligence_upload_start`` and returns its current state.
    Returns ``{"status": "not_found"}`` when the progress file does not yet
    exist (e.g. the job has not started writing yet, or the job_id is wrong).
    """

    @property
    def name(self) -> str:
        return "context_intelligence_upload_status"

    @property
    def description(self) -> str:
        return (
            "Check the progress of a background context-intelligence upload job.  "
            "Reads the progress file written by the upload subprocess and returns "
            'the current state as JSON.  Returns {"status": "not_found"} if the '
            "progress file does not exist yet.  Use the job_id returned by "
            "context_intelligence_upload_start."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": ("The job ID returned by context_intelligence_upload_start."),
                },
            },
            "required": ["job_id"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        """Read the progress file for *job_id* and return its state as JSON.

        Returns ``{"status": "not_found"}`` when the file does not exist.
        """
        job_id: str = input_data["job_id"]
        file_path = progress_file_path(job_id)
        state = ProgressTracker.read_file(file_path)
        if state is None:
            return ToolResult(success=True, output=json.dumps({"status": "not_found"}))
        return ToolResult(success=True, output=json.dumps(state))


# ---------------------------------------------------------------------------
# mount
# ---------------------------------------------------------------------------


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mount both context intelligence upload tools into the coordinator.

    Creates ``ContextIntelligenceUploadStart`` (passing the coordinator for
    config resolution) and ``ContextIntelligenceUploadStatus``, then registers
    each via ``coordinator.mount('tools', tool, name=tool.name)``.
    """
    start_tool = ContextIntelligenceUploadStart(coordinator)
    status_tool = ContextIntelligenceUploadStatus()

    await coordinator.mount("tools", start_tool, name=start_tool.name)
    await coordinator.mount("tools", status_tool, name=status_tool.name)

    logger.info(
        "tool-context-intelligence-upload mounted: registered '%s' and '%s'",
        start_tool.name,
        status_tool.name,
    )

    return {
        "name": "tool-context-intelligence-upload",
        "version": "0.1.0",
        "provides": [start_tool.name, status_tool.name],
    }
