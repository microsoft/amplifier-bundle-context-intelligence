"""Graph query tool module — Cypher queries against the context-intelligence server.

Implements the Amplifier Tool protocol.  Configuration is resolved lazily
via the ``context_intelligence.hook_config_resolver`` coordinator capability
registered by the hook-context-intelligence module.

This module also owns analytics-path skill-content sync: it exposes a
module-level ``on_session_ready`` (see ``skill_sync``) that the kernel runs
after all modules mount, syncing the context-intelligence-graph-query skill
in the graph-analyst sub-session where it is consumed.
"""

from __future__ import annotations

from typing import Any

from .skill_sync import _GRAPH_QUERY_TOOL_CAPABILITY, on_session_ready

__amplifier_module_type__ = "tool"

__all__ = ["mount", "on_session_ready"]


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
    coordinator.register_capability(_GRAPH_QUERY_TOOL_CAPABILITY, tool)
    return {"tool": tool.name, "status": "mounted"}
