"""GraphQueryTool — agent-facing tool for executing Cypher queries.

Implements the Amplifier Tool protocol.  Resolves configuration lazily
via the ``context_intelligence.config_resolver`` coordinator capability
registered by the hook-context-intelligence module.
"""

from __future__ import annotations

from typing import Any

from context_intelligence.client import AsyncCIClient

from amplifier_core.models import ToolResult


class GraphQueryTool:
    """Execute Cypher queries against the context-intelligence server.

    Implements the Amplifier Tool protocol (name, description, input_schema,
    execute).  Configuration is resolved lazily at execute() time via the
    coordinator's ``context_intelligence.config_resolver`` capability.
    """

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
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

        if self._resolver is None:
            # Analytics-only mode: hook-context-intelligence is not mounted.
            # Fall back to StandaloneConfigResolver which reads from env vars
            # and ~/.amplifier/settings.yaml — the same sources ConfigResolver
            # uses, but without needing the hook's coordinator capability.
            from context_intelligence.standalone_resolver import StandaloneConfigResolver

            self._resolver = StandaloneConfigResolver()

        server_url = self._resolver.context_intelligence_server_url
        if not server_url:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence server URL not configured",
                    "type": "configuration_error",
                },
            )

        workspace = self._resolver.workspace
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

        api_key = self._resolver.context_intelligence_api_key
        async_client = AsyncCIClient(server_url=server_url, api_key=api_key or "")
        result = await async_client.cypher(query, effective_workspace, params=params)
        return ToolResult(success=True, output=result)
