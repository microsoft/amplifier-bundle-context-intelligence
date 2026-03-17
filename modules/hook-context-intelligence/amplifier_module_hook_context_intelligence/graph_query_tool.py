"""GraphQueryTool — agent-facing tool for executing Cypher queries against the context-intelligence server.

Implements the Amplifier Tool protocol so it can be registered via
``coordinator.mount("tools", tool, name=tool.name)``.  The ``execute()``
method is the primary entry-point; ``_graph_query()`` is an internal helper
that performs the actual HTTP call and returns raw results.
"""

from __future__ import annotations

from typing import Any

import httpx

from amplifier_core.models import ToolResult


class GraphQueryTool:
    """Agent-facing tool for executing Cypher queries against the context-intelligence server.

    Implements the Amplifier Tool protocol (name, description, execute) so it
    can be mounted directly via ``coordinator.mount()``.  Automatically injects
    the configured workspace into every query request, scoping results to the
    correct session namespace.
    """

    def __init__(self, server_url: str, workspace: str) -> None:
        self._server_url = server_url.rstrip("/")
        self._workspace = workspace

    # ------------------------------------------------------------------
    # Amplifier Tool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Tool name for invocation."""
        return "graph_query"

    @property
    def description(self) -> str:
        """Human-readable tool description."""
        return (
            "Execute a Cypher query against the context-intelligence property graph. "
            "Use this to explore session history, relationships between entities, "
            "and metadata stored in the graph. The workspace is automatically injected "
            "to scope results to the current session namespace."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the JSON Schema describing the tool's input parameters."""
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
        """Execute tool with given input.

        Extracts ``query``, ``params``, and ``workspace`` from *input*,
        delegates to ``_graph_query()``, and wraps the result in a
        :class:`ToolResult`.

        Args:
            input: Dict with keys ``query`` (required), ``params`` (optional),
                ``workspace`` (optional — omit to use instance workspace,
                pass ``"*"`` for cross-workspace queries).

        Returns:
            :class:`ToolResult` with ``success=True`` and ``output`` set to
            the parsed JSON response on success, or ``success=False`` and
            ``error`` set on failure.
        """
        query: str = input["query"]
        params: dict[str, Any] | None = input.get("params")
        workspace: str | None = input.get("workspace")

        raw = await self._graph_query(query, params=params, workspace=workspace)

        if isinstance(raw, dict) and "error" in raw:
            # _graph_query returns {"error": "...", "type": "..."} on failure; lift into ToolResult
            return ToolResult(
                success=False,
                error={"message": raw["error"], "type": raw.get("type", "query_error")},
            )

        return ToolResult(success=True, output=raw)

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    async def _graph_query(
        self,
        query: str,
        params: dict | None = None,
        workspace: str | None = None,
    ) -> list[dict] | dict:
        """Execute a Cypher query against the context-intelligence server.

        POSTs to {server_url}/cypher with JSON body containing query, params,
        and workspace. The workspace is automatically injected from the instance
        configuration, but can be overridden per-call for cross-workspace queries.

        Args:
            query: Cypher query string to execute.
            params: Optional query parameters dict. Defaults to empty dict if None.
            workspace: Optional workspace override. When provided, uses this value
                instead of the instance workspace. Pass ``"*"`` for cross-workspace
                (wildcard) queries.

        Returns:
            Parsed JSON response (typically a list of result dicts) on success,
            or an error dict on failure.
        """
        effective_workspace = workspace if workspace is not None else self._workspace
        body = {
            "query": query,
            "params": params if params is not None else {},
            "workspace": effective_workspace,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{self._server_url}/cypher", json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            return {
                "error": f"Server returned {exc.response.status_code}: {exc.response.text}",
                "type": "http_error",
            }
        except httpx.TransportError as exc:
            return {"error": f"Server unavailable: {exc}", "type": "connection_error"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Graph query failed: {exc}", "type": "query_error"}
