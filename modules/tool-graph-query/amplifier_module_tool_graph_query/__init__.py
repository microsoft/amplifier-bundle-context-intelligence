"""Graph query tool module — Cypher queries against the context-intelligence server.

Implements the Amplifier Tool protocol.  Configuration is resolved lazily
via the ``context_intelligence.config_resolver`` coordinator capability
registered by the hook-context-intelligence module.
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"


async def mount(coordinator: Any, config: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """Mount the graph_query tool.

    Captures a coordinator reference for lazy capability resolution.
    The tool reads the config resolver at execute() time, not mount() time,
    because hooks mount after tools.
    """
    from .graph_query_tool import GraphQueryTool

    tool = GraphQueryTool(coordinator=coordinator, config=config)
    await coordinator.mount("tools", tool, name=tool.name)
    return {"tool": tool.name, "status": "mounted"}
