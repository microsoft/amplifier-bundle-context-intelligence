"""Tests for GraphQueryTool — migrated to AsyncCIClient (Task 10)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_coordinator(resolver: Any = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=resolver)
    return coordinator


def _make_resolver(
    server_url: str | None = "http://localhost:8080",
    workspace: str = "test-workspace",
    api_key: str | None = "test-api-key",
) -> MagicMock:
    resolver = MagicMock()
    resolver.context_intelligence_server_url = server_url
    resolver.workspace = workspace
    resolver.context_intelligence_api_key = api_key
    return resolver


def _make_mock_async_ci_client(return_value: Any = None):
    """Return (mock_instance, mock_cls) for patching AsyncCIClient."""
    mock_instance = AsyncMock()
    mock_instance.cypher = AsyncMock(return_value=return_value if return_value is not None else [])
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_instance, mock_cls


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

    def test_input_schema_returns_object_type(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        tool = GraphQueryTool(coordinator=_make_coordinator())
        assert tool.input_schema["type"] == "object"

    def test_input_schema_has_query_as_required(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        tool = GraphQueryTool(coordinator=_make_coordinator())
        assert "query" in tool.input_schema["required"]

    def test_input_schema_has_optional_params_and_workspace(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        tool = GraphQueryTool(coordinator=_make_coordinator())
        props = tool.input_schema["properties"]
        assert "params" in props
        assert "workspace" in props
        # Neither should be required
        assert "params" not in tool.input_schema["required"]
        assert "workspace" not in tool.input_schema["required"]

    async def test_execute_returns_tool_result(self) -> None:
        from amplifier_core.models import ToolResult

        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
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
        assert result.error is not None
        assert result.error["type"] == "configuration_error"

    async def test_server_url_none_returns_configuration_error(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(server_url=None)
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)
        result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"

    async def test_resolver_cached_after_first_lookup(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})
            await tool.execute({"query": "MATCH (n) RETURN n LIMIT 2"})

        # get_capability should only be called once (on first execute)
        coordinator.get_capability.assert_called_once_with("context_intelligence.config_resolver")

    async def test_configured_resolver_succeeds(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True


# ---------------------------------------------------------------------------
# TestGraphQuery
# ---------------------------------------------------------------------------


class TestGraphQuery:
    """AsyncCIClient construction and delegation tests."""

    async def test_client_constructed_with_server_url_and_api_key(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(server_url="http://ci-server:9000", api_key="my-key")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n"})

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("server_url") == "http://ci-server:9000"
        assert call_kwargs.get("api_key") == "my-key"

    async def test_workspace_injected_into_cypher_call(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(workspace="my-workspace")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n"})

        cypher_args = mock_instance.cypher.call_args
        assert cypher_args is not None
        # workspace is the 2nd positional arg: cypher(query, workspace)
        all_args = list(cypher_args.args) + list(cypher_args.kwargs.values())
        assert "my-workspace" in all_args

    async def test_result_forwarded_from_cypher_call(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        expected = [{"n": {"id": "session-1"}}]
        mock_instance, mock_cls = _make_mock_async_ci_client(return_value=expected)
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n:Session) RETURN n LIMIT 10"})

        assert result.success is True
        assert result.output == expected


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

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n", "workspace": "override-workspace"})

        cypher_args = mock_instance.cypher.call_args
        all_args = list(cypher_args.args) + list(cypher_args.kwargs.values())
        assert "override-workspace" in all_args

    async def test_wildcard_workspace_override(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(workspace="default-workspace")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n", "workspace": "*"})

        cypher_args = mock_instance.cypher.call_args
        all_args = list(cypher_args.args) + list(cypher_args.kwargs.values())
        assert "*" in all_args

    async def test_default_workspace_from_resolver(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(workspace="resolver-workspace")
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            # No workspace key in input — should fall back to resolver's workspace
            await tool.execute({"query": "MATCH (n) RETURN n"})

        cypher_args = mock_instance.cypher.call_args
        all_args = list(cypher_args.args) + list(cypher_args.kwargs.values())
        assert "resolver-workspace" in all_args


# ---------------------------------------------------------------------------
# TestGraphQueryErrors
# ---------------------------------------------------------------------------


class TestGraphQueryErrors:
    """Error path tests — AsyncCIClient.cypher() returns [] on HTTP failure (graceful degradation)."""

    async def test_server_error_returns_success_with_empty_result(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        # AsyncCIClient.cypher() returns [] on HTTP error (graceful degradation)
        mock_instance, mock_cls = _make_mock_async_ci_client(return_value=[])
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        assert result.output == []

    async def test_none_api_key_passed_as_empty_string(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(api_key=None)
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        # Should succeed and pass empty string as api_key
        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("api_key") == ""
