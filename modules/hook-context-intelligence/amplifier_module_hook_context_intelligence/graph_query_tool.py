"""GraphQueryTool — agent-facing tool for executing Cypher queries against the context-intelligence server.

Agents use graph_query() to run Cypher queries against the property graph,
with automatic workspace injection so queries are scoped to the correct session namespace.
"""

from __future__ import annotations

import httpx


class GraphQueryTool:
    """Agent-facing tool for executing Cypher queries against the context-intelligence server.

    Automatically injects the configured workspace into every query request,
    scoping results to the correct session namespace.
    """

    def __init__(self, server_url: str, workspace: str) -> None:
        self._server_url = server_url.rstrip("/")
        self._workspace = workspace

    async def graph_query(
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
            return {"error": f"Server returned {exc.response.status_code}: {exc.response.text}"}
        except httpx.TransportError as exc:
            return {"error": f"Server unavailable: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Graph query failed: {exc}"}
