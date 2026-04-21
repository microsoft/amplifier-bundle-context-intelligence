"""BlobReadTool — fetches blob content from the context-intelligence server."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from amplifier_core import ToolResult
from context_intelligence.client import AsyncCIClient

_URI_SCHEME = "ci-blob://"
_BLOB_DIR = Path("/tmp/ci-blobs")


def _sanitize_path_component(s: str) -> str:
    """Replace any char not in [a-zA-Z0-9._-] with underscore."""
    return re.sub(r"[^a-zA-Z0-9._\-]", "_", s)


class BlobReadTool:
    """Tool that fetches a ci-blob:// URI from the server and writes it to disk."""

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        self._resolver: Any = None

    @property
    def name(self) -> str:
        return "blob_read"

    @property
    def description(self) -> str:
        return (
            "Fetch a ci-blob:// URI from the server and write it to disk. "
            "Returns the file path. Use bash+jq to inspect the file as the content would be likely large."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "A ci-blob:// URI to fetch (e.g. ci-blob://session_id/key).",
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

        # (5) Construct AsyncCIClient
        api_key: str | None = self._resolver.context_intelligence_api_key
        async_client = AsyncCIClient(server_url=server_url, api_key=api_key or "")

        # (6) Fetch blob using original unsanitized values for the server request
        data = await async_client.fetch_blob(session_id, key)

        # (7) Return http_error if data is None
        if data is None:
            return ToolResult(
                success=False,
                error={
                    "message": "HTTP error fetching blob",
                    "type": "http_error",
                },
            )

        # (8) Write to disk: json.dumps for dict/list, raw string otherwise
        dest = _BLOB_DIR / safe_session_id / f"{safe_key}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (dict, list)):
            dest.write_text(json.dumps(data))
        else:
            dest.write_text(data)

        # (9) Return success with path
        return ToolResult(success=True, output={"path": str(dest)})
