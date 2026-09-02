"""Context Intelligence server data-ops tools -- session_summary,
delete_session, and whoami.

All three tools share one ToolConfigResolver (single config namespace:
overrides.tool-server-data-ops.config.sources).

WhoamiTool itself lives in the shared context_intelligence library, not in
this module's own package, because it is also mounted by
tool-context-intelligence-query -- importing the same class keeps both
mounts in lock-step with zero duplication.
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"
__all__ = ["mount"]


async def mount(coordinator: Any, config: Any) -> None:
    """Mount all three server-data-ops tools, sharing one ToolConfigResolver.

    The resolver is built ONCE from the module's config and injected into
    all three tools.  Tool constructors do not accept config -- the resolver
    IS the config surface.

    The hook resolver is NOT fetched here; each tool fetches it lazily at
    first execute() because tools mount before hooks (kernel phase order is
    orchestrator -> context -> providers -> tools -> hooks -- CONTRACTS.md
    section Module Lifecycle Methods).
    """
    from context_intelligence.tool_resolver import ToolConfigResolver
    from context_intelligence.whoami_tool import WhoamiTool

    from .delete_session_tool import DeleteSessionTool
    from .session_summary_tool import SessionSummaryTool

    resolver = ToolConfigResolver(config or {}, coordinator)
    # WARN-only diagnostic pass -- never raises; hard validation is per-source
    # at query time (see tool_resolver.py: validate_source()).
    resolver.validate_sources()
    summary = SessionSummaryTool(coordinator, resolver)
    delete = DeleteSessionTool(coordinator, resolver)
    whoami = WhoamiTool(coordinator, resolver)
    await coordinator.mount("tools", summary, name=summary.name)
    await coordinator.mount("tools", delete, name=delete.name)
    await coordinator.mount("tools", whoami, name=whoami.name)
