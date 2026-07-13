"""GraphQueryTool — agent-facing tool for executing Cypher queries.

Implements the Amplifier Tool protocol.  Configuration is resolved via the
three-tier fallback chain in ``resolve_query_endpoint``:

  1. Explicit read-config (``sources:`` in mount config, if set).
  2. Upload destinations from ``context_intelligence.hook_config_resolver``
     capability (fixes the destinations-only config bug).
  3. ``AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL`` env var (canonical last-resort).

The hook resolver is fetched lazily at first ``execute()`` call so that late
mount order is handled correctly (tools mount before hooks).

The ``ToolConfigResolver`` is injected at construction time by ``mount()``
(one shared instance for both CI read tools — single config namespace).
"""

from __future__ import annotations

from typing import Any

from context_intelligence.client import AsyncCIClient, CIClientError
from context_intelligence.tool_resolver import (
    ToolConfigResolver,
    resolve_query_auth_strategy,
    resolve_query_endpoint,
)

from amplifier_core.models import ToolResult


class GraphQueryTool:
    """Execute Cypher queries against the context-intelligence server.

    Implements the Amplifier Tool protocol (name, description, input_schema,
    execute).  Configuration is resolved via resolve_query_endpoint() at
    execute() time, consulting the hook's upload destinations as a fallback.
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
            "to scope results to the current session namespace."
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
                        'Example: "MATCH (n:Session) RETURN n LIMIT 10"'
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
                        "Optional name of a specific configured read source to query (see "
                        "overrides.tool-context-intelligence-query.config.sources). Required when "
                        "2 or more sources are configured and you have not already been told which "
                        "one to use -- omitting it in that case raises an error listing the valid "
                        "names. Safe to omit when 0 or 1 source is configured."
                    ),
                },
            },
            "required": ["query"],
        }

    @property
    def skill_sync_enabled(self) -> bool:
        """Pass-through to the resolver's skill_sync_enabled knob.

        Consumed by skill_sync.on_session_ready via the coordinator capability;
        returning False (the resolver default) makes the sync path a complete
        no-op (zero GET /version, zero skill fetch, no reload handler).
        """
        return self._tool_resolver.skill_sync_enabled

    def _resolve_server_config(
        self,
        coordinator: Any,
        source_name: str | None = None,
        *,
        allow_implicit_default: bool = False,
    ) -> tuple[str | None, str | None, str, Any]:
        """Resolve (server_url, api_key, workspace, auth_strategy) using the three-tier fallback chain.

        source_name / allow_implicit_default: forwarded to resolve_query_endpoint() /
        resolve_query_auth_strategy() unchanged -- see their docstrings and
        docs/designs/workstream-1-multi-source-query-tools.md sec 2.2 for the selection
        contract (criteria 1-3). Raises SourceSelectionError / ValueError on ambiguous
        or misconfigured selection; callers (execute(), skill_sync) are responsible for
        catching these and degrading appropriately for their own context.

        Late-mount upgrade: retries hook capability lookup on every call while
        _hook_resolver is None (hook may mount after the tool).
        """
        if self._hook_resolver is None:
            self._hook_resolver = coordinator.get_capability(
                "context_intelligence.hook_config_resolver"
            )
        url, api_key = resolve_query_endpoint(
            self._hook_resolver,
            self._tool_resolver,
            source_name=source_name,
            allow_implicit_default=allow_implicit_default,
        )
        auth_strategy = resolve_query_auth_strategy(
            self._hook_resolver,
            self._tool_resolver,
            api_key=api_key or "",
            source_name=source_name,
            allow_implicit_default=allow_implicit_default,
        )
        workspace = (
            self._hook_resolver.workspace
            if self._hook_resolver is not None
            else self._tool_resolver.workspace
        )
        return url, api_key, workspace, auth_strategy

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        from context_intelligence.tool_resolver import SourceSelectionError  # noqa: PLC0415

        source_name = input.get("source")
        try:
            server_url, api_key, workspace, auth_strategy = self._resolve_server_config(
                self._coordinator, source_name
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

        if not server_url:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence server URL not configured",
                    "type": "configuration_error",
                },
            )

        query: str = input["query"]
        ws_override = input.get("workspace")
        effective_workspace = ws_override if ws_override is not None else workspace

        raw_params = input.get("params")
        if raw_params is None:
            params: dict = {}
        elif not isinstance(raw_params, dict):
            return ToolResult(
                success=False,
                error={
                    "message": (f"params must be a dict, got {type(raw_params).__name__!r}"),
                    "type": "validation_error",
                },
            )
        else:
            params = raw_params

        async_client = AsyncCIClient(
            server_url=server_url,
            api_key=api_key or "",
            auth_strategy=auth_strategy,
            timeout=self._tool_resolver.request_timeout,
        )
        try:
            result = await async_client.cypher(query, effective_workspace, params=params)
        except CIClientError as exc:
            # success=False + output unset is safe: ToolResult.model_post_init
            # back-fills output from error["message"] when output is None. Do NOT
            # also set output= here or that back-fill is suppressed (matches the
            # SourceSelectionError/ValueError handlers above).
            return ToolResult(
                success=False,
                error={
                    "message": f"query failed against {server_url}: {exc}",
                    "type": exc.error_type,  # connection_error|timeout|http_status|decode_error
                    **({"status_code": exc.status_code} if exc.status_code is not None else {}),
                },
            )
        return ToolResult(success=True, output=result)
