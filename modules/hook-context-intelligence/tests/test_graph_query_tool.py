"""Tests for GraphQueryTool class.

7 tests covering:
- TestGraphQuery: correct URL with trailing slash stripped, workspace injected as top-level
  field, user params forwarded, returns parsed JSON list, no params sends empty dict
- TestGraphQueryErrors: HTTP 500 returns error dict with '500' in message, connection error
  returns error dict
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from amplifier_module_hook_context_intelligence.graph_query_tool import GraphQueryTool


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_client(json_return: list | None = None) -> tuple[AsyncMock, MagicMock]:
    """Return (mock_client, mock_cls) wired for patching httpx.AsyncClient.

    Args:
        json_return: Value returned by mock_response.json(). Defaults to [].

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
# TestGraphQuery
# ---------------------------------------------------------------------------


class TestGraphQuery:
    """graph_query() POSTs Cypher queries to /cypher with workspace injection."""

    async def test_correct_url_trailing_slash_stripped(self) -> None:
        """graph_query() POSTs to {server_url}/cypher, stripping trailing slash from server_url."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080/", "my-workspace")
            await tool.graph_query("MATCH (n) RETURN n")

            call_args = mock_client.post.call_args
            assert call_args[0][0] == "http://localhost:8080/cypher"

    async def test_workspace_injected_as_top_level_field(self) -> None:
        """graph_query() sends workspace as a top-level field in the POST body."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "test-workspace")
            await tool.graph_query("MATCH (n) RETURN n")

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["workspace"] == "test-workspace"

    async def test_user_params_forwarded(self) -> None:
        """graph_query() forwards user-provided params in the POST body."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            await tool.graph_query("MATCH (n) WHERE n.id = $id RETURN n", {"id": "abc-123"})

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["params"] == {"id": "abc-123"}
            assert body["query"] == "MATCH (n) WHERE n.id = $id RETURN n"

    async def test_returns_parsed_json_list(self) -> None:
        """graph_query() returns the parsed JSON response from the server."""
        expected = [{"n": {"id": "node-1", "type": "Session"}}]
        mock_client, mock_cls = _make_mock_client(json_return=expected)
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.graph_query("MATCH (n) RETURN n")

            assert result == expected

    async def test_no_params_sends_empty_dict(self) -> None:
        """graph_query() sends empty dict for params when none are provided."""
        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            tool = GraphQueryTool("http://localhost:8080", "ws")
            await tool.graph_query("MATCH (n) RETURN n")

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["params"] == {}


# ---------------------------------------------------------------------------
# TestGraphQueryErrors
# ---------------------------------------------------------------------------


class TestGraphQueryErrors:
    """graph_query() returns error dicts on HTTP errors and transport failures."""

    async def test_http_500_returns_error_dict_with_status_code(self) -> None:
        """graph_query() returns {'error': '...500...'} on HTTP 500 response."""
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
            result = await tool.graph_query("MATCH (n) RETURN n")

            assert isinstance(result, dict)
            assert "error" in result
            assert "500" in result["error"]

    async def test_connection_error_returns_error_dict(self) -> None:
        """graph_query() returns {'error': 'Server unavailable: ...'} on transport error."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = GraphQueryTool("http://localhost:8080", "ws")
            result = await tool.graph_query("MATCH (n) RETURN n")

            assert isinstance(result, dict)
            assert "error" in result
            assert "unavailable" in result["error"].lower()
