"""Tests for GraphQueryTool class.

Tests covering:
- TestGraphQueryToolProtocol: name property, description property, get_schema(), execute() returns ToolResult
- TestGraphQuery: correct URL with trailing slash stripped, workspace injected as top-level
  field, user params forwarded, returns parsed JSON list, no params sends empty dict
- TestGraphQueryErrors: HTTP 500 returns ToolResult(success=False), connection error
  returns ToolResult(success=False)
- TestGraphQueryWorkspaceOverride: per-call workspace= overrides instance workspace, wildcard
  workspace is forwarded as-is
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from amplifier_core.models import ToolResult

from amplifier_module_hook_context_intelligence.graph_query_tool import GraphQueryTool


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_client(json_return: list | None = None) -> tuple[AsyncMock, MagicMock]:
    """Return (mock_client, mock_cls) wired for patching httpx.AsyncClient.

    Args:
        json_return: Value returned by mock_response.json(). Defaults to []。

    Returns:
        Tuple of (mock_client, mock_cls) where mock_cls is the replacement for
        httpx.AsyncClient and mock_client is the inner async context manager value.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = json_return if json_return is not None else []

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

    return mock_client, mock_cls


# ---------------------------------------------------------------------------
# TestGraphQueryToolProtocol
# ---------------------------------------------------------------------------


class TestGraphQueryToolProtocol:
    """GraphQueryTool implements the Amplifier Tool protocol."""

    def test_has_name_property_returning_graph_query(self) -> None:
        """GraphQueryTool.name returns 'graph_query'."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        assert tool.name == "graph_query"

    def test_has_description_property_returning_non_empty_string(self) -> None:
        """GraphQueryTool.description returns a non-empty string."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0

    def test_get_schema_returns_dict(self) -> None:
        """get_schema() returns a dict."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        schema = tool.get_schema()
        assert isinstance(schema, dict)

    def test_get_schema_has_properties_key(self) -> None:
        """get_schema() returns a dict with 'properties' key."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        schema = tool.get_schema()
        assert "properties" in schema

    def test_get_schema_has_required_key(self) -> None:
        """get_schema() returns a dict with 'required' key."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        schema = tool.get_schema()
        assert "required" in schema

    def test_get_schema_has_query_in_required(self) -> None:
        """get_schema() has 'query' in required list."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        schema = tool.get_schema()
        assert "query" in schema["required"]

    def test_get_schema_query_has_description(self) -> None:
        """get_schema() 'query' property has a description."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        schema = tool.get_schema()
        assert "query" in schema["properties"]
        assert "description" in schema["properties"]["query"]

    def test_get_schema_has_params_property(self) -> None:
        """get_schema() has optional 'params' property."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        schema = tool.get_schema()
        assert "params" in schema["properties"]

    def test_get_schema_has_workspace_property(self) -> None:
        """get_schema() has optional 'workspace' property."""
        tool = GraphQueryTool("http://localhost:8080", "ws")
        schema = tool.get_schema()
        assert "workspace" in schema["properties"]

    async def test_execute_returns_tool_result_on_success(self) -> None:
        """execute() returns a ToolResult instance on success."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert isinstance(result, ToolResult)

    async def test_execute_returns_success_true_on_success(self) -> None:
        """execute() returns ToolResult(success=True) on successful query."""
        expected = [{"n": {"id": "node-1"}}]
        mock_client, mock_cls = _make_mock_client(json_return=expected)
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        assert result.output == expected

    async def test_execute_returns_tool_result_on_http_error(self) -> None:
        """execute() returns ToolResult(success=False, error={...}) on HTTP error."""
        mock_request = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        error = httpx.HTTPStatusError(
            "500 Internal Server Error", request=mock_request, response=mock_resp
        )
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = error

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.error is not None
        assert "message" in result.error
        assert result.error.get("type") == "http_error"

    async def test_execute_returns_tool_result_on_connection_error(self) -> None:
        """execute() returns ToolResult(success=False, error={...}) on connection error."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.error is not None
        assert "message" in result.error
        assert result.error.get("type") == "connection_error"


# ---------------------------------------------------------------------------
# TestGraphQuery
# ---------------------------------------------------------------------------


class TestGraphQuery:
    """execute() POSTs Cypher queries to /cypher with workspace injection."""

    async def test_correct_url_trailing_slash_stripped(self) -> None:
        """execute() POSTs to {server_url}/cypher, stripping trailing slash from server_url."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080/", "my-workspace")
            await tool.execute({"query": "MATCH (n) RETURN n"})

            call_args = mock_client.post.call_args
            assert call_args[0][0] == "http://localhost:8080/cypher"

    async def test_workspace_injected_as_top_level_field(self) -> None:
        """execute() sends workspace as a top-level field in the POST body."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "test-workspace")
            await tool.execute({"query": "MATCH (n) RETURN n"})

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["workspace"] == "test-workspace"

    async def test_user_params_forwarded(self) -> None:
        """execute() forwards user-provided params in the POST body."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            await tool.execute(
                {"query": "MATCH (n) WHERE n.id = $id RETURN n", "params": {"id": "abc-123"}}
            )

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["params"] == {"id": "abc-123"}
            assert body["query"] == "MATCH (n) WHERE n.id = $id RETURN n"

    async def test_returns_parsed_json_list_in_output(self) -> None:
        """execute() returns the parsed JSON response in ToolResult.output."""
        expected = [{"n": {"id": "node-1", "type": "Session"}}]
        mock_client, mock_cls = _make_mock_client(json_return=expected)
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

            assert result.output == expected

    async def test_no_params_sends_empty_dict(self) -> None:
        """execute() sends empty dict for params when none are provided."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            await tool.execute({"query": "MATCH (n) RETURN n"})

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["params"] == {}


# ---------------------------------------------------------------------------
# TestGraphQueryErrors
# ---------------------------------------------------------------------------


class TestGraphQueryErrors:
    """execute() returns ToolResult(success=False) on HTTP errors and transport failures."""

    async def test_http_500_returns_failure_tool_result_with_status_code(self) -> None:
        """execute() returns ToolResult(success=False, error={...}) on HTTP 500 response."""
        mock_request = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        error = httpx.HTTPStatusError(
            "500 Internal Server Error", request=mock_request, response=mock_resp
        )
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = error

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

            assert result.success is False
            assert result.error is not None
            assert "500" in result.error.get("message", "")
            assert result.error.get("type") == "http_error"

    async def test_connection_error_returns_failure_tool_result(self) -> None:
        """execute() returns ToolResult(success=False, error={...}) on transport error."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

            assert result.success is False
            assert result.error is not None
            assert "unavailable" in result.error.get("message", "").lower()
            assert result.error.get("type") == "connection_error"


# ---------------------------------------------------------------------------
# TestGraphQueryWorkspaceOverride
# ---------------------------------------------------------------------------


class TestGraphQueryWorkspaceOverride:
    """execute() accepts a per-call workspace in input that overrides the instance workspace."""

    async def test_per_call_workspace_overrides_instance_workspace(self) -> None:
        """execute({'workspace': ...}) sends the per-call workspace, not the instance one."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "default-workspace")
            await tool.execute(
                {
                    "query": "MATCH (s:Session {workspace: $workspace}) RETURN s.node_id",
                    "workspace": "project-alpha",
                }
            )

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["workspace"] == "project-alpha"

    async def test_wildcard_workspace_forwarded_as_is(self) -> None:
        """execute({'workspace': '*'}) forwards the wildcard string verbatim."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "default-workspace")
            await tool.execute(
                {
                    "query": "MATCH (s:Session) RETURN s.workspace, s.node_id",
                    "workspace": "*",
                }
            )

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["workspace"] == "*"

    async def test_no_workspace_in_input_uses_instance_workspace(self) -> None:
        """execute() without workspace in input uses the configured instance workspace."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "configured-workspace")
            await tool.execute({"query": "MATCH (n) RETURN n"})

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["workspace"] == "configured-workspace"
