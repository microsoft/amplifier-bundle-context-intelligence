"""GraphQueryTool — agent-facing tool for executing Cypher queries.

Implements the Amplifier Tool protocol. Configuration and provenance are
resolved via ``resolve_query_connection`` — a SINGLE-HIT selection over the
connectable pool (tool ``sources`` ∪ hook ``destinations``):

  1. Explicit ``source=<name>`` — resolves against the WHOLE pool (can name a
     tool source OR a hook upload destination).
  2. No name — default semantics (PR #67, unchanged). See
     ``resolve_query_connection``'s docstring in context_intelligence/tool_resolver.py
     for the authoritative rule; in brief: 1 source -> use it; 2+ sources ->
     fail loud (the ONLY default-path fail-loud); 0 sources + N destinations ->
     use the FIRST destination in config order (destinations are the established
     read-fallback pool; no error for any N); 0 of either -> env (tier 3).

Every result (success or failure) carries a ``source`` field naming the
endpoint that answered / was attempted (docs/multi-source-build-spec-v5.md §5) —
so which endpoint served a default-path pick is always visible to the user.
Callers can also pass ``list_sources: true`` to discover the connectable set
without running a query (§4.3).

The hook resolver is fetched lazily at first ``execute()`` call so that late
mount order is handled correctly (tools mount before hooks).

The ``ToolConfigResolver`` is injected at construction time by ``mount()``
(one shared instance for both CI read tools — single config namespace).

READ-SIDE / FAN-IN ONLY: this tool never touches the hook's fan-out (write
path, logging handler, destination dispatcher). It only reads
``HookConfigResolver.destinations`` to know what endpoints exist to connect to.
"""

from __future__ import annotations

from typing import Any

from context_intelligence.client import AsyncCIClient, CIClientError
from context_intelligence.tool_resolver import (
    ToolConfigResolver,
    _connectable_pool,
    _origin_dict,
    resolve_query_connection,
)

from amplifier_core.models import ToolResult


class GraphQueryTool:
    """Execute Cypher queries against the context-intelligence property graph.

    Implements the Amplifier Tool protocol (name, description, input_schema,
    execute). Configuration and provenance are resolved via
    resolve_query_connection() at execute() time, over the connectable pool
    (tool sources ∪ the hook's upload destinations).
    """

    def __init__(self, coordinator: Any, resolver: ToolConfigResolver | None = None) -> None:
        self._coordinator = coordinator
        self._tool_resolver = resolver or ToolConfigResolver({}, coordinator)
        self._hook_resolver: Any | None = None

    @property
    def name(self) -> str:
        return "graph_query"

    @property
    def description(self) -> str:
        return (
            "Execute a Cypher query against the context-intelligence property graph. "
            "Use this to explore session history, relationships between entities, "
            "and metadata stored in the graph. The workspace is automatically injected "
            "to scope results to the current session namespace. Every result names the "
            "`source` (name/url/origin) that answered -- ALWAYS state it in your answer."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Cypher query string to execute against the context-intelligence graph. "
                        'Example: "MATCH (n:Session) RETURN n LIMIT 10". Required unless '
                        "list_sources=true."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Optional query parameters dict passed to the Cypher query as "
                        'named parameters (e.g. {"id": "abc-123"}). Defaults to empty dict. '
                        "Note: the effective workspace is bound into the query as the "
                        '"$workspace" parameter. A specific (non-"*") workspace overrides '
                        'any "workspace" key you supply here; when querying across all '
                        'workspaces ("*"), no override occurs and your "workspace" key (if '
                        "any) is passed through unchanged."
                    ),
                },
                "workspace": {
                    "type": "string",
                    "description": (
                        "Optional workspace override. Omit to use the configured workspace value. "
                        'Pass "*" to query across all workspaces.'
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Optional name of a specific connectable endpoint to query -- either a "
                        "configured read source OR a hook upload destination (the full "
                        "connectable set; call with list_sources=true to see the names). "
                        "Omitting `source` uses the default endpoint: the single configured "
                        "source, or -- if no sources are configured -- the first destination "
                        "in config order (destinations are the read fallback pool). The only "
                        "case where omitting it errors is when 2+ SOURCES are configured (then "
                        "you must pass source=<name>, and the error lists the valid names); "
                        "any number of destinations is fine to leave implicit. Pass "
                        "source=<name> to target a specific source or destination."
                    ),
                },
                "list_sources": {
                    "type": "boolean",
                    "description": (
                        "When true, do NOT run a query. Return the connectable set -- every "
                        "source and destination this tool can reach, with name, url, and origin "
                        "(source/destination). Use this to discover valid `source` values before "
                        "selecting one."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        from context_intelligence.tool_resolver import SourceSelectionError  # noqa: PLC0415

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
            # The selected source itself is misconfigured (criterion 4) -- names only it.
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

        if "query" not in input:
            # Endpoint already resolved above -> attach provenance (spec §5.1):
            # `source` is present on failures that occur AFTER an endpoint is chosen.
            return ToolResult(
                success=False,
                error={
                    "message": "query is required unless list_sources=true",
                    "type": "validation_error",
                    "source": _origin_dict(conn.origin),
                },
            )
        query: str = input["query"]

        workspace = (
            self._hook_resolver.workspace
            if self._hook_resolver is not None
            else self._tool_resolver.workspace
        )
        ws_override = input.get("workspace")
        effective_workspace = ws_override if ws_override is not None else workspace

        raw_params = input.get("params")
        if raw_params is None:
            params: dict = {}
        elif not isinstance(raw_params, dict):
            # Endpoint already resolved above -> attach provenance (spec §5.1).
            return ToolResult(
                success=False,
                error={
                    "message": (f"params must be a dict, got {type(raw_params).__name__!r}"),
                    "type": "validation_error",
                    "source": _origin_dict(conn.origin),
                },
            )
        else:
            params = raw_params

        async_client = AsyncCIClient(
            server_url=conn.url,
            api_key=conn.api_key or "",
            auth_strategy=conn.auth_strategy,
            timeout=self._tool_resolver.request_timeout,
        )
        try:
            result = await async_client.cypher(query, effective_workspace, params=params)
        except CIClientError as exc:
            # success=False + output unset is safe: ToolResult.model_post_init
            # back-fills output from error["message"] when output is None. Do NOT
            # also set output= here or that back-fill is suppressed (matches the
            # SourceSelectionError/ValueError handlers above).
            origin_name = conn.origin.name if conn.origin and conn.origin.name else conn.url
            return ToolResult(
                success=False,
                error={
                    "message": f"query failed against {origin_name}: {exc}",
                    "type": exc.error_type,  # connection_error|timeout|http_status|decode_error
                    "source": _origin_dict(conn.origin),
                    **({"status_code": exc.status_code} if exc.status_code is not None else {}),
                },
            )
        return ToolResult(
            success=True,
            output={"source": _origin_dict(conn.origin), "rows": result},
        )
