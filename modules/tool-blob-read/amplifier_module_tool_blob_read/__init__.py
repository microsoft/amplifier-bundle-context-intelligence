"""Blob read tool module — reads binary/text blobs from the context-intelligence server.

Implements the Amplifier Tool protocol.  Configuration is resolved lazily
via the ``context_intelligence.config_resolver`` coordinator capability
registered by the hook-context-intelligence module.
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"


async def mount(coordinator: Any, config: Any) -> dict[str, Any]:  # noqa: ARG001
    from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

    tool = BlobReadTool(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    return {"tool": tool.name, "status": "mounted"}
