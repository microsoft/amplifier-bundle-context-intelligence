"""Tests for GraphQueryTool — full implementation (Task 3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_coordinator(resolver: Any = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=resolver)
    return coordinator


def _make_resolver(server_url: str | None = "http://localhost:8080", workspace: str = "test-workspace") -> MagicMock:
    resolver = MagicMock()
    resolver.context_intelligence_server_url = server_url
    resolver.workspace = workspace
    return resolver


def _make_mock_client(json_return=None):
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
    """Tool protocol surface tests."""

    def test_name_is_graph_query(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool
        tool = GraphQueryTool(coordinator=_make_coordinator())
        assert tool.name == "graph_query"

    def test_description_mentions_cypher(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool
        tool = GraphQueryTool(coordinator=_make_coordinator())
        assert "Cypher" in tool.description

    def test_description_mentions_context_intelligence(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool
        tool = GraphQueryTool(coordinator=_make_coordinator())
        assert "context-intelligence" in tool.description

    def test_get_schema_returns_object_type(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool
        tool = GraphQueryTool(coordinator=_make_coordinator())
        schema = tool.get_schema()
        assert schema["type"] == "object"

    def test_get_schema_has_query_as_required(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool
        tool = GraphQueryTool(coordinator=_make_coordinator())
        schema = tool.get_schema()
        assert "query" in schema["required"]

    def test_get_schema_has_optional_params_and_workspace(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool
        tool = GraphQueryTool(coordinator=_make_coordinator())
        schema = tool.get_schema()
        props = schema["properties"]
        assert "params" in props
        assert "workspace" in props
        # They should not be in 'required'
        assert "params" not in schema["required"]
        assert "workspace" not in schema["required"]

    async def test_execute_returns_tool_result(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool
        from amplifier_core.models import ToolResult

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        _, mock_cls = _make_mock_client(json_return=[])
        with patch("httpx.AsyncClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})

        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# TestLazyCapabilityResolution
# ---------------------------------------------------------------------------

class TestLazyCapabilityResolution:
    """Lazy resolver lookup and caching behaviour."""

    async def test_capability_not_found_returns_configuration_error(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        coordinator = _make_coordinator(resolver=None)
        tool = GraphQueryTool(coordinator=coordinator)
        result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error["type"] == "configuration_error"

    async def test_server_url_none_returns_configuration_error(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(server_url=None)
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)
        result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error["type"] == "configuration_error"

    async def test_configured_resolver_posts_to_server(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        mock_client.post.assert_called_once()

    async def test_connection_error_returns_connection_error_type(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_cls = MagicMock()
        mock_cls.return_value.__aenter__ = AsyncMock(
            side_effect=httpx.TransportError("connection refused")
        )
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error["type"] == "connection_error"

    async def test_resolver_cached_after_first_lookup(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})
            await tool.execute({"query": "MATCH (n) RETURN n LIMIT 2"})

        # get_capability should only be called once (on first execute)
        coordinator.get_capability.assert_called_once_with(
            "context_intelligence.config_resolver"
        )


# ---------------------------------------------------------------------------
# TestGraphQuery
# ---------------------------------------------------------------------------

class TestGraphQuery:
    """HTTP request construction tests."""

    async def test_trailing_slash_stripped_from_server_url(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(server_url="http://localhost:8080/")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n"})

        call_url = mock_client.post.call_args.args[0]
        assert call_url == "http://localhost:8080/cypher"

    async def test_workspace_injected_as_top_level_field(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(workspace="my-project")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n"})

        body = mock_client.post.call_args.kwargs["json"]
        assert body["workspace"] == "my-project"

    async def test_user_params_forwarded_in_body(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            await tool.execute({"query": "MATCH (n) WHERE n.id = $id RETURN n", "params": {"id": "abc-123"}})

        body = mock_client.post.call_args.kwargs["json"]
        assert body["params"] == {"id": "abc-123"}

    async def test_no_params_sends_empty_dict(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n"})

        body = mock_client.post.call_args.kwargs["json"]
        assert body["params"] == {}

    async def test_returns_parsed_json_in_output(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        expected = [{"n": {"id": "session-1"}}]
        mock_client, mock_cls = _make_mock_client(json_return=expected)
        with patch("httpx.AsyncClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n:Session) RETURN n LIMIT 10"})

        assert result.success is True
        assert result.output == expected


# ---------------------------------------------------------------------------
# TestGraphQueryErrors
# ---------------------------------------------------------------------------

class TestGraphQueryErrors:
    """Error path tests."""

    async def test_http_500_returns_http_error(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        http_error = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=http_error)
        mock_cls = MagicMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error["type"] == "http_error"
        assert "500" in result.error["message"]

    async def test_transport_error_returns_connection_error(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.TransportError("connection refused")
        )
        mock_cls = MagicMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error["type"] == "connection_error"


# ---------------------------------------------------------------------------
# TestGraphQueryWorkspaceOverride
# ---------------------------------------------------------------------------

class TestGraphQueryWorkspaceOverride:
    """Per-call workspace override behaviour."""

    async def test_per_call_workspace_override(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(workspace="default-workspace")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n", "workspace": "override-workspace"})

        body = mock_client.post.call_args.kwargs["json"]
        assert body["workspace"] == "override-workspace"

    async def test_wildcard_workspace_override(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(workspace="default-workspace")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n", "workspace": "*"})

        body = mock_client.post.call_args.kwargs["json"]
        assert body["workspace"] == "*"

    async def test_default_workspace_from_resolver(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(workspace="resolver-workspace")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_client, mock_cls = _make_mock_client()
        with patch("httpx.AsyncClient", mock_cls):
            # No workspace key in input — should fall back to resolver's workspace
            await tool.execute({"query": "MATCH (n) RETURN n"})

        body = mock_client.post.call_args.kwargs["json"]
        assert body["workspace"] == "resolver-workspace"
