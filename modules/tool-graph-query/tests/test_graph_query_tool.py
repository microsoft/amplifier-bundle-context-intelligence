"""Tests for GraphQueryTool — migrated to AsyncCIClient (Task 10)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_coordinator(resolver: Any = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = {}
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
    # destinations must be a real dict so _first_destination() can iterate it safely
    if server_url:
        resolver.destinations = {
            "default": SimpleNamespace(name="default", url=server_url, api_key=api_key or ""),
        }
    else:
        resolver.destinations = {}
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
        # Clear all CI env vars so tier-3 fallback does not accidentally succeed
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"

    async def test_server_url_none_returns_configuration_error(self) -> None:
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver(server_url=None)
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)
        # Clear all CI env vars so tier-3 fallback does not accidentally succeed
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
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
        coordinator.get_capability.assert_called_once_with(
            "context_intelligence.hook_config_resolver"
        )

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


# ---------------------------------------------------------------------------
# TestGraphQueryParamsForwarding — regression for params wiring bug
# ---------------------------------------------------------------------------


class TestGraphQueryParamsForwarding:
    """Regression: user-supplied params reach AsyncCIClient.cypher()."""

    async def test_params_are_forwarded_to_async_client_cypher(self) -> None:
        """params={...} from tool input must be forwarded as a kwarg to cypher()."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute(
                {
                    "query": "MATCH (s:Session {id: $session_id}) RETURN s",
                    "params": {"session_id": "abc"},
                }
            )

        assert result.success is True
        cypher_call = mock_instance.cypher.call_args
        assert cypher_call is not None
        # params must arrive as the 'params' keyword argument
        assert cypher_call.kwargs.get("params") == {"session_id": "abc"}

    async def test_none_params_sends_empty_dict_to_cypher(self) -> None:
        """Omitting params from tool input must default to {} at cypher()."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            await tool.execute({"query": "MATCH (n) RETURN n"})

        cypher_call = mock_instance.cypher.call_args
        assert cypher_call is not None
        assert cypher_call.kwargs.get("params") == {}

    async def test_non_dict_params_returns_validation_error(self) -> None:
        """Passing params as a non-dict must return a validation_error ToolResult."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        resolver = _make_resolver()
        coordinator = _make_coordinator(resolver=resolver)
        tool = GraphQueryTool(coordinator=coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n", "params": "not-a-dict"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "validation_error"


# ---------------------------------------------------------------------------
# Helpers for config-fallback tests (§7 matrix)
# ---------------------------------------------------------------------------


def _make_hook_resolver(destinations: dict) -> MagicMock:
    """Hook resolver mock with specific destinations dict."""
    resolver = MagicMock()
    resolver.workspace = "test-workspace"
    resolver.destinations = destinations
    return resolver


def _make_dest(url: str, api_key: str) -> SimpleNamespace:
    """Quick Destination-like SimpleNamespace for test doubles."""
    name = "default"
    return SimpleNamespace(name=name, url=url, api_key=api_key)


# ---------------------------------------------------------------------------
# TestConfigFallback — §7 test matrix cases #1–#7/#9–#10
# ---------------------------------------------------------------------------


class TestConfigFallback:
    """Config-resolution three-tier fallback (spec §7).

    Tests the explicit-read-config-first, then upload-destination, then env
    precedence order — the core bug fix and its coherent read-config model.
    """

    # --- Case #1 / #4: read_destinations wins over hook destination ---

    async def test_case1_read_config_wins_over_destination(self) -> None:
        """Case #1: explicit read_destinations wins over upload destination (tier 1 > tier 2)."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={"default": _make_dest("http://upload.example.com", "upload-key")}
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        config = {
            "read_destinations": {
                "default": {"url": "http://read.example.com", "api_key": "read-key"},
            }
        }
        tool = GraphQueryTool(coordinator=coordinator, config=config)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://read.example.com"
        assert call_kwargs["api_key"] == "read-key"

    # --- Case #2: CORE BUG FIX — destinations-only config succeeds ---

    async def test_case2_destinations_only_falls_through_to_tier2(self) -> None:
        """Case #2: no read_destinations + no legacy scalar → falls through to hook destinations."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={"default": _make_dest("http://dest.example.com", "dest-key")}
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        # Tool config is empty — no read_destinations, no legacy scalars
        tool = GraphQueryTool(coordinator=coordinator, config={})

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://dest.example.com"
        assert call_kwargs["api_key"] == "dest-key"

    # --- Case #3: per-field independence ---

    async def test_case3_read_config_url_empty_falls_to_destination_for_url(self) -> None:
        """Case #3: read_destinations url="" → url falls to destination; api_key stays at tier 1."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={"default": _make_dest("http://dest.example.com", "dest-key")}
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        config = {
            "read_destinations": {
                "default": {"url": "", "api_key": "read-key"},  # url is empty
            }
        }
        tool = GraphQueryTool(coordinator=coordinator, config=config)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # url falls through from tier 1 (empty) to tier 2 (destination)
        assert call_kwargs["server_url"] == "http://dest.example.com"
        # api_key stays at tier 1
        assert call_kwargs["api_key"] == "read-key"

    # --- Case #4: explicit-first precedence assertion ---

    async def test_case4_explicit_read_config_wins_both_fields(self) -> None:
        """Case #4: BOTH url and api_key come from tier 1 when read_destinations is set."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={"default": _make_dest("http://upload.example.com", "upload-key")}
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        config = {
            "read_destinations": {
                "default": {"url": "http://read.example.com", "api_key": "read-key"},
            }
        }
        tool = GraphQueryTool(coordinator=coordinator, config=config)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # Both fields must come from tier 1 (explicit read config)
        assert call_kwargs["server_url"] == "http://read.example.com"
        assert call_kwargs["api_key"] == "read-key"

    # --- Case #5: env hit ---

    async def test_case5_env_hit_when_no_config_or_destinations(self) -> None:
        """Case #5: canonical env vars work as tier-3 fallback (below hook destination)."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(destinations={})  # no destinations
        coordinator = _make_coordinator(resolver=dest_resolver)
        tool = GraphQueryTool(coordinator=coordinator, config={})

        env_patch = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env.example.com",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-key",
        }
        _, mock_cls = _make_mock_async_ci_client()
        with (
            patch.dict(os.environ, env_patch),
            patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls),
        ):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://env.example.com"
        assert call_kwargs["api_key"] == "env-key"

    # --- Regression: env is below hook destination (tier 2 beats tier 3) ---

    async def test_case5b_env_does_not_override_hook_destination(self) -> None:
        """Regression: canonical env vars set + hook destination present → destination wins.

        Locks in that env (tier 3) never outranks the hook destination (tier 2).
        """
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={"default": _make_dest("http://dest.example.com", "dest-key")}
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        tool = GraphQueryTool(coordinator=coordinator, config={})

        # Canonical env set — must NOT override the hook destination
        env_patch = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env-override.example.com",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-override-key",
        }
        _, mock_cls = _make_mock_async_ci_client()
        with (
            patch.dict(os.environ, env_patch),
            patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls),
        ):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # Hook destination (tier 2) wins over env (tier 3)
        assert call_kwargs["server_url"] == "http://dest.example.com"
        assert call_kwargs["api_key"] == "dest-key"

    # --- Case #6: all miss → configuration_error ---

    async def test_case6_all_miss_returns_configuration_error(self) -> None:
        """Case #6: no read_destinations, no destinations, no env → configuration_error."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(destinations={})
        coordinator = _make_coordinator(resolver=dest_resolver)
        tool = GraphQueryTool(coordinator=coordinator, config={})

        # Exclude ALL CI env vars (including canonical SERVER_URL / API_KEY)
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")
        }
        _, mock_cls = _make_mock_async_ci_client()
        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls),
        ):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"

    # --- Case #7: multi-entry ordering determinism ---

    async def test_case7_multi_entry_ordering_first_read_entry_wins(self) -> None:
        """Case #7: first entry in read_destinations (insertion order) wins."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={
                "d1": SimpleNamespace(name="d1", url="http://d1.example.com", api_key="d1-key"),
                "d2": SimpleNamespace(name="d2", url="http://d2.example.com", api_key="d2-key"),
            }
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        config = {
            "read_destinations": {
                "alpha": {"url": "http://alpha.example.com", "api_key": "alpha-key"},
                "beta": {"url": "http://beta.example.com", "api_key": "beta-key"},
            }
        }
        tool = GraphQueryTool(coordinator=coordinator, config=config)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # First read_destinations entry ("alpha") wins
        assert call_kwargs["server_url"] == "http://alpha.example.com"
        assert call_kwargs["api_key"] == "alpha-key"

    async def test_case7_second_execute_same_result_deterministic(self) -> None:
        """Case #7: repeated executes give the same endpoint (deterministic order)."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={
                "d1": SimpleNamespace(name="d1", url="http://d1.example.com", api_key="d1")
            }
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        config = {
            "read_destinations": {
                "alpha": {"url": "http://alpha.example.com", "api_key": "alpha-key"},
            }
        }
        tool = GraphQueryTool(coordinator=coordinator, config=config)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            r1 = await tool.execute({"query": "MATCH (n) RETURN n"})
            r2 = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert r1.success is True
        assert r2.success is True
        # Both executes used the same endpoint
        calls = mock_cls.call_args_list
        assert (
            calls[0].kwargs["server_url"]
            == calls[1].kwargs["server_url"]
            == "http://alpha.example.com"
        )

    # --- Case #9: legacy top-level scalar synthesizes default ---

    async def test_case9_legacy_scalars_synthesize_read_default_wins_tier1(self) -> None:
        """Case #9: legacy context_intelligence_server_url+api_key synthesize read_destinations."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={"default": _make_dest("http://upload.example.com", "upload-key")}
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        # Legacy scalars in tool config — no read_destinations key
        config = {
            "context_intelligence_server_url": "http://legacy.example.com",
            "context_intelligence_api_key": "legacy-key",
        }
        tool = GraphQueryTool(coordinator=coordinator, config=config)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # Synthesized default ("http://legacy.example.com") wins at tier 1
        assert call_kwargs["server_url"] == "http://legacy.example.com"
        assert call_kwargs["api_key"] == "legacy-key"

    # --- Case #10: legacy url-only → no synthesis, falls through ---

    async def test_case10_legacy_url_only_no_synthesis_falls_through_to_destination(self) -> None:
        """Case #10: legacy url-only (no api_key) → read_destinations={}, falls to tier 2."""
        from amplifier_module_tool_graph_query.graph_query_tool import GraphQueryTool

        dest_resolver = _make_hook_resolver(
            destinations={"default": _make_dest("http://upload.example.com", "upload-key")}
        )
        coordinator = _make_coordinator(resolver=dest_resolver)
        # Only server_url, no api_key → both-fields-required not met → no synthesis
        config = {
            "context_intelligence_server_url": "http://legacy.example.com",
            # no context_intelligence_api_key
        }
        tool = GraphQueryTool(coordinator=coordinator, config=config)

        _, mock_cls = _make_mock_async_ci_client()
        with patch("amplifier_module_tool_graph_query.graph_query_tool.AsyncCIClient", mock_cls):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # No synthesis → read_destinations={} → falls through to tier 2
        assert call_kwargs["server_url"] == "http://upload.example.com"
        assert call_kwargs["api_key"] == "upload-key"
