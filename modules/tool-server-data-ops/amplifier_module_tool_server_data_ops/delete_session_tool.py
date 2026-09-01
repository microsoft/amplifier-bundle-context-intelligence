"""DeleteSessionTool -- agent-facing tool that permanently deletes a session.

Implements the Amplifier Tool protocol. Configuration and provenance are
resolved via ``resolve_query_connection`` (same as SessionSummaryTool -- parity
guaranteed by the shared helper), a SINGLE-HIT selection over the connectable
pool (tool ``sources`` union hook ``destinations``). See
``resolve_query_connection``'s docstring in context_intelligence/tool_resolver.py
for the authoritative selection rule (in brief: explicit ``source=<name>``
reaches any pool entry; with no name, 1 source -> it, 2+ sources -> fail loud,
0 sources -> the FIRST destination in config order for any N, else env).

Every result (success or failure) carries a ``source`` field naming the
endpoint that answered / was attempted. Callers can also pass
``list_sources: true`` to discover the connectable set without deleting
anything.

The ``ToolConfigResolver`` is injected at construction time by ``mount()``
(one shared instance for both server-data-ops tools -- single config namespace).

This tool never talks to the server directly -- the only path to the server
is through ``AsyncCIClient`` (the shared library). This is a REAL, PERMANENT
CHANGE: there is no workspace input and no "preview only" flag on the server
call itself -- the delete always runs against the whole session graph. The
agent using this tool is responsible for showing the user a preview
(session_summary) and getting explicit confirmation FIRST.
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


class DeleteSessionTool:
    """Permanently delete one session's whole graph from the context-intelligence server.

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
        return "delete_session"

    @property
    def description(self) -> str:
        return (
            "Permanently delete one session's whole graph (nodes, relationships, "
            "and blobs) from the context-intelligence server. This is a REAL, "
            "PERMANENT change -- there is no undo and no preview flag here. "
            "ALWAYS call session_summary first to show the user what would be "
            "removed, and get their explicit confirmation before calling this. "
            "If the session is still receiving data, the server refuses with a "
            "clear error rather than deleting a live session. Every result names "
            "the `source` (name/url/origin) the delete was sent to -- ALWAYS "
            "state it in your answer."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": (
                        "The id of the session to permanently delete. Required "
                        "unless list_sources=true."
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Optional name of a specific connectable endpoint (server) to "
                        "delete from -- either a configured source OR a hook upload "
                        "destination (the full connectable set; call with "
                        "list_sources=true to see the names). Omitting `source` uses "
                        "the default endpoint: the single configured source, or -- if "
                        "no sources are configured -- the first destination in config "
                        "order. The only case where omitting it errors is when 2+ "
                        "SOURCES are configured (then you must pass source=<name>, and "
                        "the error lists the valid names)."
                    ),
                },
                "list_sources": {
                    "type": "boolean",
                    "description": (
                        "When true, do NOT delete anything. Return the connectable "
                        "set -- every server this tool can reach, with name, url, and "
                        "origin (source/destination). Use this to discover valid "
                        "`source` values before selecting one."
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
            result = await async_client.delete_session(session_id)
        except CIClientError as exc:
            # success=False + output unset is safe: ToolResult.model_post_init
            # back-fills output from error["message"] when output is None. Do NOT
            # also set output= here or that back-fill is suppressed.
            origin_name = conn.origin.name if conn.origin and conn.origin.name else conn.url
            message = f"delete failed against {origin_name}: {exc}"
            if exc.status_code == 404:
                message = f"unknown session {session_id!r} on {origin_name}"
            elif exc.status_code == 409:
                message = (
                    f"session {session_id!r} on {origin_name} is still receiving data "
                    "and cannot be deleted yet, or the id is ambiguous across workspaces"
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
            output={"source": _origin_dict(conn.origin), "result": result},
        )
