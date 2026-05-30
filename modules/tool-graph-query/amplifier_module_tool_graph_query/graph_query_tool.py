"""GraphQueryTool — agent-facing tool for executing Cypher queries.

Implements the Amplifier Tool protocol.  Configuration is resolved lazily at
execute() time, preferring the ``context_intelligence.config_resolver``
coordinator capability registered by hook-context-intelligence.  When the hook
is not mounted (analytics-only mode) the tool falls back to the ``config`` dict
passed via mount() — the standard Amplifier tool configuration mechanism.
"""

from __future__ import annotations

from typing import Any

from context_intelligence.client import AsyncCIClient

from amplifier_core.models import ToolResult


class GraphQueryTool:
    """Execute Cypher queries against the context-intelligence server.

    Implements the Amplifier Tool protocol (name, description, input_schema,
    execute).  Configuration priority at execute() time:

    1. ``context_intelligence.config_resolver`` coordinator capability
       (registered by hook-context-intelligence when the full behavior is used).
    2. ``config`` dict passed to mount() — used when the analytics-only behavior
       is composed without the hook.
    """

    def __init__(self, coordinator: Any, config: dict[str, Any] | None = None) -> None:
        self._coordinator = coordinator
        self._config: dict[str, Any] = config or {}
        self._resolver: Any | None = None

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
                        'named parameters (e.g. {"id": "abc-123"}). Defaults to empty dict.'
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

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        if self._resolver is None:
            self._resolver = self._coordinator.get_capability(
                "context_intelligence.config_resolver"
            )

        # Resolve server_url, api_key, workspace — from hook capability when
        # available, otherwise directly from the tool's mount() config dict.
        if self._resolver is not None:
            server_url = self._resolver.context_intelligence_server_url
            api_key = self._resolver.context_intelligence_api_key
            workspace = self._resolver.workspace
        else:
            server_url = self._config.get("context_intelligence_server_url") or None
            api_key = self._config.get("context_intelligence_api_key") or None
            workspace = self._config.get("workspace") or "default"

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
