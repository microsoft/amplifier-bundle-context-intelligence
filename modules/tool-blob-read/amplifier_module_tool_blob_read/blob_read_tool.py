"""BlobReadTool — fetches blob content from the context-intelligence server."""

from __future__ import annotations

from typing import Any

from amplifier_core import ToolResult


class BlobReadTool:
    """Tool that reads blob content from ci-blob:// URIs or disk paths."""

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        self._resolver = None

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
        return ToolResult(
            success=False,
            error={"message": "Not yet implemented", "type": "not_implemented"},
        )
