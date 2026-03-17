"""BlobReadTool — fetches blob content from the context-intelligence server."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from amplifier_core import ToolResult

_URI_SCHEME = "ci-blob://"
_BLOB_DIR = Path("/tmp/ci-blobs")


def _sanitize_path_component(s: str) -> str:
    """Replace any char not in [a-zA-Z0-9._-] with underscore."""
    return re.sub(r"[^a-zA-Z0-9._\-]", "_", s)


class BlobReadTool:
    """Tool that reads blob content from ci-blob:// URIs or disk paths."""

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        self._resolver: Any = None

    @property
    def name(self) -> str:
        return "blob_read"

    @property
    def description(self) -> str:
        return (
            "Read blob content from a ci-blob:// URI or a disk path. "
            "Supports binary and text blobs stored in the context-intelligence server."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "The blob URI (ci-blob://) or disk path to read.",
                },
            },
            "required": ["uri"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        # (1) Lazy capability resolution
        if self._resolver is None:
            self._resolver = self._coordinator.get_capability(
                "context_intelligence.config_resolver"
            )
        if self._resolver is None:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence hook not configured",
                    "type": "configuration_error",
                },
            )

        # (2) Get server_url from resolver
        server_url: str | None = self._resolver.context_intelligence_server_url
        if not server_url:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence server URL not configured",
                    "type": "configuration_error",
                },
            )
        server_url = server_url.rstrip("/")

        # (3) Parse URI
        uri: str = input["uri"]
        if not uri.startswith(_URI_SCHEME):
            return ToolResult(
                success=False,
                error={
                    "message": f"URI must start with {_URI_SCHEME}",
                    "type": "uri_error",
                },
            )
        rest = uri[len(_URI_SCHEME) :]
        if "/" not in rest:
            return ToolResult(
                success=False,
                error={
                    "message": "URI must be in format ci-blob://session_id/key",
                    "type": "uri_error",
                },
            )
        slash_idx = rest.index("/")
        session_id = rest[:slash_idx]
        key = rest[slash_idx + 1 :]

        # (4) Sanitize both components for use in file path
        safe_session_id = _sanitize_path_component(session_id)
        safe_key = _sanitize_path_component(key)

        # (5) HTTP GET using ORIGINAL unsanitized values for the URL
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{server_url}/blobs/{session_id}/{key}")
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ToolResult(
                success=False,
                error={
                    "message": f"HTTP {e.response.status_code} error fetching blob",
                    "type": "http_error",
                },
            )
        except httpx.TransportError as e:
            return ToolResult(
                success=False,
                error={
                    "message": str(e),
                    "type": "connection_error",
                },
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error={
                    "message": str(e),
                    "type": "blob_error",
                },
            )

        # (6) Write resp.text to _BLOB_DIR / safe_session_id / f"{safe_key}.json"
        dest = _BLOB_DIR / safe_session_id / f"{safe_key}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(resp.text)

        # (7) Return success with path
        return ToolResult(success=True, output={"path": str(dest)})
