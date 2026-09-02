"""WhoamiTool -- agent-facing tool that resolves the acting user's identity.

Implements the Amplifier Tool protocol. Configuration and provenance are
resolved via ``resolve_query_connection`` (same as SessionSummaryTool and
DeleteSessionTool -- parity guaranteed by the shared helper), a SINGLE-HIT
selection over the connectable pool (tool ``sources`` union hook
``destinations``). See ``resolve_query_connection``'s docstring in
context_intelligence/tool_resolver.py for the authoritative selection rule
(in brief: explicit ``source=<name>`` reaches any pool entry; with no name,
1 source -> it, 2+ sources -> fail loud, 0 sources -> the FIRST destination
in config order for any N, else env).

Every result (success or failure) carries a ``source`` field naming the
endpoint that answered / was attempted. Callers can also pass
``list_sources: true`` to discover the connectable set without calling the
server.

The ``ToolConfigResolver`` is injected at construction time by ``mount()``
(one shared instance across all three server-data-ops tools -- single config
namespace).

This tool never talks to the server directly -- the only path to the server
is through ``AsyncCIClient`` (the shared library). This is a READ (no
changes made): it resolves who the server thinks is making the request,
so an agent (e.g. the delete workflow) can compare it against a session's
``created_by`` for ownership warnings.
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


class WhoamiTool:
    """Resolve the acting user's identity from the context-intelligence server.

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
        return "whoami"

    @property
    def description(self) -> str:
        return (
            "Resolve the authenticated caller's identity for the chosen "
            "context-intelligence server -- returns the acting user's "
            "`contributor_id` (their github id), or null when auth is "
            "disabled server-side. Use this to answer 'who am I' and to "
            "compare against a session's `created_by` for ownership "
            "warnings before a delete. This makes NO changes -- it is a "
            "read. Every result names the `source` (name/url/origin) that "
            "answered -- ALWAYS state it in your answer."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
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
                        "When true, do NOT resolve an identity. Return the connectable "
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

        async_client = AsyncCIClient(
            server_url=conn.url,
            api_key=conn.api_key or "",
            auth_strategy=conn.auth_strategy,
            timeout=self._tool_resolver.request_timeout,
        )
        try:
            identity = await async_client.whoami()
        except CIClientError as exc:
            # success=False + output unset is safe: ToolResult.model_post_init
            # back-fills output from error["message"] when output is None. Do NOT
            # also set output= here or that back-fill is suppressed.
            origin_name = conn.origin.name if conn.origin and conn.origin.name else conn.url
            message = f"whoami lookup failed against {origin_name}: {exc}"
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
            output={
                "contributor_id": identity.get("contributor_id"),
                "source": _origin_dict(conn.origin),
            },
        )
