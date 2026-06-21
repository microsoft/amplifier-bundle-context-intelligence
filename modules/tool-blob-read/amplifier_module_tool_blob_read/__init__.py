"""Blob read tool module — reads binary/text blobs from the context-intelligence server.

Implements the Amplifier Tool protocol.  Configuration is resolved lazily
via the ``context_intelligence.hook_config_resolver`` coordinator capability
registered by the hook-context-intelligence module.
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"


async def mount(coordinator: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Mount the blob_read tool.

    Passes ``config`` into BlobReadTool so it can resolve server_url and
    api_key directly when hook-context-intelligence is not mounted
    (analytics-only mode).  When the hook IS mounted its
    ``context_intelligence.hook_config_resolver`` capability takes priority.
    """
    from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

    tool = BlobReadTool(coordinator=coordinator, config=config)
    await coordinator.mount("tools", tool, name=tool.name)
    return {"tool": tool.name, "status": "mounted"}
