"""Agent-scoped lockdown hook for server-data-ops (the delete agent).

This hook is registered on the `tool:pre` lifecycle event and denies exactly
four tools: `write_file`, `edit_file`, `apply_patch`, `graph_query`. Every
other tool call is left untouched (`continue`).

Why this exists, and why it lives here rather than as a behavior-level
`exclude_tools` policy: tool inheritance in this ecosystem is ADDITIVE by
default (a spawned agent gets everything its parent session has, unless the
PARENT's own tool-delegate config excludes it) -- see
amplifier-app-cli's session_spawner.py `_filter_tools()`. A behavior-level
`exclude_tools` restriction is a BROAD policy: it applies to every agent
composed by that behavior, and collides with any other agent in the same
behavior that legitimately needs the excluded tools (it must then
re-declare them explicitly to opt back in). That is backwards for a
security-sensitive restriction that belongs to exactly ONE agent
(server-data-ops, the delete agent) -- the restriction should be owned and
carried by the CONSUMING agent itself, not imposed on every agent that
happens to share a behavior with it.

A `tool:pre` deny hook declared in the agent's OWN frontmatter (`hooks:`,
sibling to `tools:`) bounds WHICH SESSION MOUNTS this hook module: only
server-data-ops's own session, since only its own agent definition declares
it. That much is true regardless of anything else. It also holds even if a
future change to server-data-ops's own `tools:` list (or to what it
inherits) were to re-introduce one of these tools -- the deny is enforced
at call time, not just at composition time.

SUBTREE LEAK, found via a DTU eval and fixed alongside this hook: mounting
the hook on server-data-ops's own session does NOT, by itself, stop it from
also reaching every session server-data-ops SPAWNS. Hook inheritance from a
parent session to a delegated child is ADDITIVE BY DEFAULT -- exactly the
same rule as tool inheritance -- see amplifier-foundation's tool-delegate
module (`settings.exclude_hooks`, which mirrors `settings.exclude_tools`
field-for-field; both are consumed by `_spawn_new_session()` to build an
inheritance-filtering policy the app-layer spawn capability applies to the
child). Left unexcluded, this hook -- mounted on server-data-ops's own
session -- ALSO mounted on graph-analyst's spawned session whenever
server-data-ops delegated search to it, and denied graph-analyst's own,
entirely legitimate `graph_query` calls. That is precisely what broke Flow
2 / Flow 2-folder: search never ran, so no candidate sessions were ever
found to delete.

THE FIX: agents/server-data-ops.md's own `tools:` entry for `tool-delegate`
sets `config.settings.exclude_hooks: ["hook-server-data-ops-lockdown"]`
(this module's own id). That setting -- not anything in this file -- is
what stops this hook from being composed onto any session server-data-ops
spawns, while leaving it fully in force for server-data-ops's OWN tool
calls (its `hooks:` declaration is untouched; only its inheritance into
descendants is excluded). With that setting in place, this hook has no
effect on graph-analyst, session-navigator, or any other agent
server-data-ops delegates to -- but only because of that setting. Removing
it reopens the subtree leak even though nothing in this file's own deny
logic changes.

Why the fix is not "check which agent/session is calling" inside the
handler below: the `tool:pre` event's documented payload carries ONLY
`tool_name` and `tool_input` -- see the reference emit call in
`core:docs/contracts/ORCHESTRATOR_CONTRACT.md`
(`await hooks.emit("tool:pre", {"tool_name": ..., "tool_input": ...})`) and
the field table in `core:docs/contracts/HOOK_CONTRACT.md`
(`tool:pre | Before tool execution | tool_name, tool_input`). No session
id, agent name, or other identity field is part of the documented
contract, so a handler receiving `(event, data)` structurally cannot tell
"server-data-ops's own call" apart from "a descendant session's call."
Session-scoping has to happen at the delegation boundary
(`exclude_hooks`), not inside this handler.

Contract references (verified against amplifier-core docs before writing
this handler):
  - `core:docs/contracts/HOOK_CONTRACT.md` -- protocol is
    `async def __call__(event: str, data: dict[str, Any]) -> HookResult`;
    the `tool:pre` event's data dict carries the tool name under the key
    `tool_name` (line 272: `"tool_name": "Write"`) and the tool's arguments
    under `tool_input`. A denial is `HookResult(action="deny", reason=...)`
    (line 94).
  - `core:docs/HOOKS_API.md` -- `HookResult.action` is
    `Literal["continue", "deny", "modify", "inject_context", "ask_user"]`
    and `reason: str | None = None` (lines 44/48). The worked `tool:pre`
    example (lines 271-274) reads `data.get("tool_name")` and compares it
    against a list of tool names, confirming both the field name and the
    plain-string comparison pattern used below.
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


async def _deny_lockdown_tools(event: str, data: dict[str, Any]) -> Any:
    """`tool:pre` handler: deny DENIED_TOOLS, allow everything else.

    Only ever registered for the `tool:pre` event (see mount() below), so
    `event` is not branched on here -- the registration itself scopes when
    this handler runs.
    """
    from amplifier_core.models import HookResult  # local import: peer dependency

    if data.get("tool_name") in DENIED_TOOLS:
        return HookResult(action="deny", reason=DENY_REASON)
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
