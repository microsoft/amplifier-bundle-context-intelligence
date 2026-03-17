"""GraphQueryTool — agent-facing tool for executing Cypher queries.

Implements the Amplifier Tool protocol.  Resolves configuration lazily
via the ``context_intelligence.config_resolver`` coordinator capability
registered by the hook-context-intelligence module.
"""

from __future__ import annotations

from typing import Any

import httpx

from amplifier_core.models import ToolResult


class GraphQueryTool:
    """Execute Cypher queries against the context-intelligence server.

    Implements the Amplifier Tool protocol (name, description, get_schema,
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

    def get_schema(self) -> dict[str, Any]:
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
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence hook not configured",
                    "type": "configuration_error",
                },
            )

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
        server_url = server_url.rstrip("/")

        query: str = input["query"]
        params: dict[str, Any] | None = input.get("params")
        ws_override = input.get("workspace")
        effective_workspace = ws_override if ws_override is not None else workspace

        body = {
            "query": query,
            "params": params if params is not None else {},
            "workspace": effective_workspace,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{server_url}/cypher", json=body)
                resp.raise_for_status()
                return ToolResult(success=True, output=resp.json())
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                error={
                    "message": f"Server returned {exc.response.status_code}: {exc.response.text}",
                    "type": "http_error",
                },
            )
        except httpx.TransportError as exc:
            return ToolResult(
                success=False,
                error={
                    "message": f"Server unavailable: {exc}",
                    "type": "connection_error",
                },
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error={
                    "message": f"Graph query failed: {exc}",
                    "type": "query_error",
                },
            )
