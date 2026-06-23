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

from context_intelligence.client import AsyncCIClient
from context_intelligence.tool_resolver import ToolConfigResolver, resolve_query_endpoint

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
            },
            "required": ["query"],
        }

    def _resolve_server_config(self, coordinator: Any) -> tuple[str | None, str | None, str]:
        """Resolve (server_url, api_key, workspace) using the three-tier fallback chain.

        Late-mount upgrade: retries hook capability lookup on every call while
        _hook_resolver is None (hook may mount after the tool).
        """
        if self._hook_resolver is None:
            self._hook_resolver = coordinator.get_capability(
                "context_intelligence.hook_config_resolver"
            )
        url, api_key = resolve_query_endpoint(self._hook_resolver, self._tool_resolver)
        workspace = (
            self._hook_resolver.workspace
            if self._hook_resolver is not None
            else self._tool_resolver.workspace
        )
        return url, api_key, workspace

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        server_url, api_key, workspace = self._resolve_server_config(self._coordinator)

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

        async_client = AsyncCIClient(server_url=server_url, api_key=api_key or "")
        result = await async_client.cypher(query, effective_workspace, params=params)
        return ToolResult(success=True, output=result)
