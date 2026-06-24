"""Context Intelligence read tools — graph_query and blob_read.

Both tools share one ToolConfigResolver, so sources has a single
config namespace: overrides.tool-context-intelligence-query.config.sources.

Two tools, one mount(): idiomatic multi-tool module (same as tool-filesystem
which mounts read_file / write_file / edit_file from one mount() call).
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"


async def mount(coordinator: Any, config: Any) -> None:
    """Mount both CI read tools, sharing one ToolConfigResolver.

    The resolver is built ONCE from the module's config and injected into
    both tools.  Tool constructors no longer accept config — the resolver IS
    the config surface.

    The hook resolver is NOT fetched here; each tool fetches it lazily at
    first execute() because tools mount before hooks (kernel phase order is
    orchestrator → context → providers → tools → hooks — CONTRACTS.md §Module
    Lifecycle Methods).  Using on_session_ready() here would force cross-callback
    instance references (multi-session anti-pattern), so lazy fetch is the
    correct and intentional design.
    """
    from context_intelligence.tool_resolver import ToolConfigResolver

    from .blob_read_tool import BlobReadTool
    from .graph_query_tool import GraphQueryTool

    resolver = ToolConfigResolver(config or {}, coordinator)  # built ONCE
    gq = GraphQueryTool(coordinator, resolver)
    br = BlobReadTool(coordinator, resolver)
    await coordinator.mount("tools", gq, name=gq.name)  # "graph_query"
    await coordinator.mount("tools", br, name=br.name)  # "blob_read"
    return None  # kernel ignores non-callable returns; resolver is pure → no cleanup
