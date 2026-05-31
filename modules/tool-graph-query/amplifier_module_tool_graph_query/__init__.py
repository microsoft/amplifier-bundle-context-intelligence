"""Graph query tool module — Cypher queries against the context-intelligence server.

Implements the Amplifier Tool protocol.  Configuration is resolved lazily
via the ``context_intelligence.hook_config_resolver`` coordinator capability
registered by the hook-context-intelligence module.
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"


async def mount(coordinator: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Mount the graph_query tool.

    Passes ``config`` into GraphQueryTool so it can resolve server_url,
    api_key and workspace directly when hook-context-intelligence is not
    mounted (analytics-only mode).  When the hook IS mounted its
    ``context_intelligence.hook_config_resolver`` capability takes priority.
    """
    from .graph_query_tool import GraphQueryTool

    tool = GraphQueryTool(coordinator=coordinator, config=config)
    await coordinator.mount("tools", tool, name=tool.name)
    return {"tool": tool.name, "status": "mounted"}
