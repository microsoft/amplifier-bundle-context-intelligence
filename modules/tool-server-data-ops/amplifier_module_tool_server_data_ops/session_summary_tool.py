"""SessionSummaryTool -- agent-facing tool for previewing a session before delete.

Implements the Amplifier Tool protocol. Configuration and provenance are
resolved via ``resolve_query_connection`` -- a SINGLE-HIT selection over the
connectable pool (tool ``sources`` union hook ``destinations``), the exact same
helper the read tools (graph_query, blob_read) use:

  1. Explicit ``source=<name>`` -- resolves against the WHOLE pool (can name a
     tool source OR a hook upload destination).
  2. No name -- default semantics: 1 source -> use it; 2+ sources -> fail loud
     (the ONLY default-path fail-loud); 0 sources + N destinations -> use the
     FIRST destination in config order; 0 of either -> env (tier 3). See
     ``resolve_query_connection``'s docstring in
     context_intelligence/tool_resolver.py for the authoritative rule.

Every result (success or failure) carries a ``source`` field naming the
endpoint that answered / was attempted, so which endpoint served a
default-path pick is always visible to the user. Callers can also pass
``list_sources: true`` to discover the connectable set without calling the
server.

The hook resolver is fetched lazily at first ``execute()`` call so that late
mount order is handled correctly (tools mount before hooks).

The ``ToolConfigResolver`` is injected at construction time by ``mount()``
(one shared instance for both server-data-ops tools -- single config namespace).

This tool never talks to the server directly -- the only path to the server
is through ``AsyncCIClient`` (the shared library). This is a READ (preview,
no changes made): it fetches the facts about a session so the agent can show
the user what would be removed before it asks about the actual delete.
"""

from __future__ import annotations

from typing import Any

from amplifier_core.models import ToolResult
from context_intelligence.client import AsyncCIClient, CIClientError
from context_intelligence.tool_resolver import (
    ToolConfigResolver,
    _connectable_pool,
    _origin_dict,
    resolve_query_connection,
)


class SessionSummaryTool:
    """Fetch the preview facts for a session from the context-intelligence server.

    Implements the Amplifier Tool protocol (name, description, input_schema,
    execute). Configuration and provenance are resolved via
    resolve_query_connection() at execute() time, over the connectable pool
    (tool sources union the hook's upload destinations).
    """

    def __init__(self, coordinator: Any, resolver: ToolConfigResolver | None = None) -> None:
        self._coordinator = coordinator
        self._tool_resolver = resolver or ToolConfigResolver({}, coordinator)
        self._hook_resolver: Any | None = None

    @property
    def name(self) -> str:
        return "session_summary"

    @property
    def description(self) -> str:
        return (
            "Fetch the preview facts for one session from the context-intelligence "
            "server: who created it, how many nodes/edges/blobs it has, when it "
            "started and last changed, and whether it is safe to delete right now. "
            "This makes NO changes -- it is a read, always call it BEFORE "
            "delete_session so the user can see what would be removed. Every result "
            "names the `source` (name/url/origin) that answered -- ALWAYS state it "
            "in your answer."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": (
                        "The id of the session to preview. Required unless list_sources=true."
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Optional name of a specific connectable endpoint (server) to "
                        "ask -- either a configured source OR a hook upload destination "
                        "(the full connectable set; call with list_sources=true to see "
                        "the names). Omitting `source` uses the default endpoint: the "
                        "single configured source, or -- if no sources are configured -- "
                        "the first destination in config order. The only case where "
                        "omitting it errors is when 2+ SOURCES are configured (then you "
                        "must pass source=<name>, and the error lists the valid names)."
                    ),
                },
                "list_sources": {
                    "type": "boolean",
                    "description": (
                        "When true, do NOT look up a session. Return the connectable "
                        "set -- every server this tool can reach, with name, url, and "
                        "origin (source/destination). Use this to discover valid "
                        "`source` values, or to tell the user which servers a session "
                        "could be on, before selecting one."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        from context_intelligence.tool_resolver import SourceSelectionError

        # Late-mount upgrade: retry hook capability lookup on every call while
        # _hook_resolver is None (hook may mount after the tool).
        if self._hook_resolver is None:
            self._hook_resolver = self._coordinator.get_capability(
                "context_intelligence.hook_config_resolver"
            )

        if input.get("list_sources"):
            pool = _connectable_pool(self._tool_resolver, self._hook_resolver)
            return ToolResult(
                success=True,
                output={
                    "connectable_set": [
                        {"name": e.name, "url": e.url, "origin": e.kind} for e in pool.values()
                    ]
                },
            )

        source_name = input.get("source")
        try:
            conn = resolve_query_connection(
                self._hook_resolver, self._tool_resolver, source_name=source_name
            )
        except SourceSelectionError as exc:
            return ToolResult(
                success=False,
                error={
                    "message": str(exc),
                    "type": exc.error_type,  # "unknown_source" | "ambiguous_source_selection"
                    "valid_sources": exc.valid_names,
                },
            )
        except ValueError as exc:
            # The selected source itself is misconfigured -- names only it.
            return ToolResult(
                success=False,
                error={"message": str(exc), "type": "source_misconfigured"},
            )

        if not conn.url:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence server URL not configured",
                    "type": "configuration_error",
                },
            )

        if "session_id" not in input:
            # Endpoint already resolved above -> attach provenance: `source` is
            # present on failures that occur AFTER an endpoint is chosen.
            return ToolResult(
                success=False,
                error={
                    "message": "session_id is required unless list_sources=true",
                    "type": "validation_error",
                    "source": _origin_dict(conn.origin),
                },
            )
        session_id: str = input["session_id"]

        async_client = AsyncCIClient(
            server_url=conn.url,
            api_key=conn.api_key or "",
            auth_strategy=conn.auth_strategy,
            timeout=self._tool_resolver.request_timeout,
        )
        try:
            summary = await async_client.session_summary(session_id)
        except CIClientError as exc:
            # success=False + output unset is safe: ToolResult.model_post_init
            # back-fills output from error["message"] when output is None. Do NOT
            # also set output= here or that back-fill is suppressed.
            origin_name = conn.origin.name if conn.origin and conn.origin.name else conn.url
            message = f"session lookup failed against {origin_name}: {exc}"
            if exc.status_code == 404:
                message = f"unknown session {session_id!r} on {origin_name}"
            elif exc.status_code == 409:
                message = (
                    f"session {session_id!r} on {origin_name} is still receiving data, "
                    "or the id is ambiguous across workspaces"
                )
            return ToolResult(
                success=False,
                error={
                    "message": message,
                    "type": exc.error_type,  # connection_error|timeout|http_status|decode_error
                    "source": _origin_dict(conn.origin),
                    **({"status_code": exc.status_code} if exc.status_code is not None else {}),
                },
            )
        return ToolResult(
            success=True,
            output={"source": _origin_dict(conn.origin), "summary": summary},
        )
