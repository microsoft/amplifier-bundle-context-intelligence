"""Bundle usage tool module — wraps context_intelligence.bundle_analysis.

Implements the Amplifier Tool protocol. Configuration is resolved lazily
via the ``context_intelligence.config_resolver`` coordinator capability
registered by the hook-context-intelligence module — hooks mount after
tools, so capability lookup must NOT happen at mount() time.
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"


async def mount(coordinator: Any, config: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """Mount the bundle_usage tool. Stores coordinator only — no capability lookup."""
    from .bundle_usage_tool import BundleUsageTool

    tool = BundleUsageTool(coordinator=coordinator)
    await coordinator.mount("tools", tool, name=tool.name)
    return {"tool": tool.name, "status": "mounted"}
