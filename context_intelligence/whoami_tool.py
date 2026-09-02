"""WhoamiTool -- agent-facing tool that resolves the acting user's identity.

Lives in the shared ``context_intelligence`` library, not in either tool
module's own package, because it is mounted from two independent modules
(``tool-context-intelligence-query`` and ``tool-server-data-ops``) -- both
import it from here so there is exactly one implementation. Each mounting
module builds its own ``ToolConfigResolver`` and injects it at construction
time; this class never constructs its own resolver.

Implements the Amplifier Tool protocol. Endpoint selection goes through
``resolve_query_connection`` (see context_intelligence/tool_resolver.py for
the authoritative selection rule); every result carries the resolved
``source``.

Read-only: resolves who the server thinks is making the request, so a
caller can compare it against a session's ``created_by`` for ownership
warnings.
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
