"""Agent-scoped lockdown hook for server-data-ops (the delete agent).

Registered on `tool:pre`; denies write_file/edit_file/apply_patch/graph_query
outright, and denies any `delegate` call whose target is not exactly
`context-intelligence:graph-analyst`. Everything else is left untouched
(`continue`).

Declared on the consuming agent's own `hooks:` (not a behavior-level
`exclude_tools`) so the restriction is owned by server-data-ops alone and
never collides with sibling agents that legitimately need these tools.

Hook inheritance to a delegated child is additive by default, same as tool
inheritance -- left unexcluded, this hook would also mount on
graph-analyst's spawned session and deny its own graph_query calls.
agents/server-data-ops.md's tool-delegate `exclude_hooks` entry is what
prevents that; this file's deny logic alone does not.

The delegate-target check exists because the `agents:` frontmatter
allowlist is only enforced when a PARENT spawns this agent -- it does
nothing when server-data-ops runs as the root agent, which let it
delegate to arbitrary agents (verified: `delegate(agent="foundation:file-ops")`
wrote a file to disk unchecked). This hook fires on server-data-ops's own
`tool:pre` calls regardless of root-vs-spawned, so the restriction has to
live here to hold in both cases.

The check inspects only `tool_name`/`tool_input` (the documented `tool:pre`
payload -- see core:docs/contracts/HOOK_CONTRACT.md) rather than session/agent
identity, because no such field exists in the contract; scoping is therefore
enforced at the delegation boundary (exclude_hooks), not inside this handler.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

__amplifier_module_type__ = "hook"
__all__ = ["mount"]

# The four tools server-data-ops must never call, regardless of how it
# came to have them available: two direct file-write tools, apply_patch
# (the third way to write files), and graph_query (direct graph access --
# this agent delegates all searching to graph-analyst instead).
DENIED_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "apply_patch", "graph_query"})

DENY_REASON = (
    "server-data-ops is a delete agent: it never edits files and never "
    "queries the graph directly (it delegates search to graph-analyst)."
)

# The only agent server-data-ops may delegate to. Must be the namespaced
# form (matches behaviors/context-intelligence-analysis.yaml's registration
# and the delegate tool's own input["agent"] value) -- a bare "graph-analyst"
# would never match a real delegate call.
ALLOWED_DELEGATE_AGENT = "context-intelligence:graph-analyst"

DELEGATE_DENY_REASON = (
    "server-data-ops may only delegate to graph-analyst (for search); it "
    "cannot delegate to other agents to perform actions it is itself "
    "restricted from."
)


async def _deny_lockdown_tools(event: str, data: dict[str, Any]) -> Any:
    """`tool:pre` handler: deny DENIED_TOOLS and off-target delegation.

    Only ever registered for the `tool:pre` event (see mount() below), so
    `event` is not branched on here.

    A `delegate` call is denied unless its target agent is exactly
    ALLOWED_DELEGATE_AGENT. A missing or empty `agent` field is denied too
    (fail closed): it cannot be confirmed to be the allowed target, and
    "cannot confirm" must resolve to deny for a security-sensitive gate.
    """
    from amplifier_core.models import HookResult  # local import: peer dependency

    tool_name = data.get("tool_name")

    if tool_name in DENIED_TOOLS:
        return HookResult(action="deny", reason=DENY_REASON)

    if tool_name == "delegate":
        tool_input = data.get("tool_input") or {}
        target_agent = tool_input.get("agent")
        if target_agent != ALLOWED_DELEGATE_AGENT:
            return HookResult(action="deny", reason=DELEGATE_DENY_REASON)

    return HookResult(action="continue")


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> Any:
    """Register the tool:pre lockdown handler.

    Returns a cleanup callable that unregisters the handler, matching the
    hook contract's cleanup convention (core:docs/contracts/HOOK_CONTRACT.md
    "Entry Point Pattern").
    """
    unregister = coordinator.hooks.register(
        "tool:pre",
        _deny_lockdown_tools,
        priority=10,
        name="server-data-ops-lockdown",
    )

    def cleanup() -> None:
        unregister()

    return cleanup
