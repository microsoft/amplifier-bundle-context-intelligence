"""Tests for WhoamiTool.

Constructor: WhoamiTool(coordinator, resolver=None). Patch path is
context_intelligence.whoami_tool.
"""

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


def _make_hook_resolver(
    server_url: str | None = "http://localhost:8080",
    workspace: str = "test-workspace",
    api_key: str = "test-api-key",
) -> MagicMock:
    """Create a hook resolver mock (returned by get_capability)."""
    resolver = MagicMock()
    resolver.workspace = workspace
    if server_url:
        resolver.destinations = {
            "default": SimpleNamespace(name="default", url=server_url, api_key=api_key)
        }
    else:
        resolver.destinations = {}
    return resolver


def _make_hook_resolver_with_dests(destinations: dict) -> MagicMock:
    """Hook resolver mock with a specific destinations dict."""
    resolver = MagicMock()
    resolver.workspace = "test-workspace"
    resolver.destinations = destinations
    return resolver


def _make_mock_async_ci_client(return_value: Any = None):
    """Return (mock_instance, mock_cls) for patching AsyncCIClient."""
    mock_instance = AsyncMock()
    mock_instance.whoami = AsyncMock(
        return_value=return_value if return_value is not None else {"contributor_id": "alice"}
    )
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_instance, mock_cls


def _make_tool_resolver(config: dict, coordinator: Any = None) -> Any:
    """Build a real ToolConfigResolver from a config dict (for injection)."""
    from context_intelligence.tool_resolver import ToolConfigResolver

    coord = coordinator or MagicMock()
    coord.config = {}
    return ToolConfigResolver(config, coord)


# ---------------------------------------------------------------------------
# TestWhoamiToolProtocol
# ---------------------------------------------------------------------------


class TestWhoamiToolProtocol:
    """Tool protocol surface tests."""

    def test_name_is_whoami(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        tool = WhoamiTool(_make_coordinator())
        assert tool.name == "whoami"

    def test_description_mentions_contributor_id(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        tool = WhoamiTool(_make_coordinator())
        assert "contributor_id" in tool.description

    def test_input_schema_returns_object_type(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        tool = WhoamiTool(_make_coordinator())
        assert tool.input_schema["type"] == "object"

    def test_input_schema_has_optional_source_and_list_sources(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        tool = WhoamiTool(_make_coordinator())
        props = tool.input_schema["properties"]
        assert "source" in props
        assert "list_sources" in props
        assert "source" not in tool.input_schema["required"]
        assert "list_sources" not in tool.input_schema["required"]
        # whoami takes no session_id -- there is no session to look up.
        assert "session_id" not in props

    async def test_execute_returns_tool_result(self) -> None:
        from amplifier_core.models import ToolResult

        from context_intelligence.whoami_tool import WhoamiTool

        hook_resolver = _make_hook_resolver()
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = WhoamiTool(coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({})

        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# TestListSources
# ---------------------------------------------------------------------------


class TestListSources:
    async def test_list_sources_does_not_call_client(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        resolver = _make_tool_resolver(
            {"sources": {"only": {"url": "http://only.example.com", "api_key": "k"}}}
        )
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = WhoamiTool(coordinator, resolver)

        mock_cls = MagicMock()
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"list_sources": True})

        assert result.success is True
        assert result.output is not None
        names = {e["name"] for e in result.output["connectable_set"]}
        assert names == {"only"}
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# TestWhoamiConstruction -- AsyncCIClient construction and delegation
# ---------------------------------------------------------------------------


class TestWhoamiConstruction:
    """AsyncCIClient construction and delegation tests (mirrors SessionSummaryTool)."""

    async def test_client_constructed_with_server_url_and_api_key(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000", api_key="my-key")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = WhoamiTool(coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            await tool.execute({})

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("server_url") == "http://ci-server:9000"
        assert call_kwargs.get("api_key") == "my-key"

    async def test_whoami_called_with_no_arguments(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        hook_resolver = _make_hook_resolver()
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = WhoamiTool(coordinator)

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            await tool.execute({})

        mock_instance.whoami.assert_called_once_with()

    async def test_result_forwarded_and_source_stamped(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = WhoamiTool(coordinator)

        _, mock_cls = _make_mock_async_ci_client(return_value={"contributor_id": "alice"})
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({})

        assert result.success is True
        assert result.output is not None
        assert result.output["contributor_id"] == "alice"
        assert result.output["source"] is not None
        assert result.output["source"]["url"] == "http://ci-server:9000"

    async def test_null_contributor_id_when_auth_disabled(self) -> None:
        """Server returns contributor_id: null when auth is disabled -- passed through as-is."""
        from context_intelligence.whoami_tool import WhoamiTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = WhoamiTool(coordinator)

        _, mock_cls = _make_mock_async_ci_client(return_value={"contributor_id": None})
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({})

        assert result.success is True
        assert result.output is not None
        assert result.output["contributor_id"] is None


# ---------------------------------------------------------------------------
# TestWhoamiConfigFallback
# ---------------------------------------------------------------------------


class TestWhoamiConfigFallback:
    async def test_capability_not_found_returns_configuration_error(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        coordinator = _make_coordinator(resolver=None)
        tool = WhoamiTool(coordinator)
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            result = await tool.execute({})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"


# ---------------------------------------------------------------------------
# TestWhoamiSourceSelection -- pool/selection + fail-loud ambiguity
# ---------------------------------------------------------------------------


class TestWhoamiSourceSelection:
    """execute() with an explicit `source` -- matching / not matching / omitted-with-2+."""

    def _two_source_config(self) -> dict:
        return {
            "sources": {
                "alpha": {"url": "http://alpha.example.com", "api_key": "alpha-key"},
                "beta": {"url": "http://beta.example.com", "api_key": "beta-key"},
            }
        }

    async def test_source_matching_name_selects_that_source(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        resolver = _make_tool_resolver(self._two_source_config())
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = WhoamiTool(coordinator, resolver)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"source": "beta"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://beta.example.com"
        assert call_kwargs["api_key"] == "beta-key"

    async def test_source_not_matching_returns_unknown_source_error(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        resolver = _make_tool_resolver(self._two_source_config())
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = WhoamiTool(coordinator, resolver)

        result = await tool.execute({"source": "gamma"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "unknown_source"
        assert result.error["valid_sources"] == ["alpha", "beta"]

    async def test_source_omitted_with_two_configured_returns_ambiguous_error(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        resolver = _make_tool_resolver(self._two_source_config())
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = WhoamiTool(coordinator, resolver)

        result = await tool.execute({})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "ambiguous_source_selection"
        assert result.error["valid_sources"] == ["alpha", "beta"]

    async def test_source_omitted_with_one_configured_still_succeeds(self) -> None:
        """Safe to omit source with exactly one configured (backward compatible)."""
        from context_intelligence.whoami_tool import WhoamiTool

        config = {
            "sources": {
                "default": {"url": "http://only.example.com", "api_key": "only-key"},
            }
        }
        resolver = _make_tool_resolver(config)
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = WhoamiTool(coordinator, resolver)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://only.example.com"

    async def test_selected_source_misconfigured_returns_source_misconfigured_error(self) -> None:
        from context_intelligence.whoami_tool import WhoamiTool

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "gk"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        resolver = _make_tool_resolver(config)
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = WhoamiTool(coordinator, resolver)

        result = await tool.execute({"source": "bad"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "source_misconfigured"
        assert "bad" in result.error["message"]


# ---------------------------------------------------------------------------
# TestWhoamiServerErrors -- transport/HTTP failures surfaced as clear tool errors
# ---------------------------------------------------------------------------


class TestWhoamiServerErrors:
    async def test_http_error_surfaces_as_clear_tool_error(self) -> None:
        from context_intelligence.client import CIClientError
        from context_intelligence.whoami_tool import WhoamiTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = WhoamiTool(coordinator)

        mock_instance = AsyncMock()
        mock_instance.whoami = AsyncMock(
            side_effect=CIClientError(
                "HTTP 500 from http://ci-server:9000/whoami",
                error_type="http_status",
                url="http://ci-server:9000/whoami",
                status_code=500,
            )
        )
        mock_cls = MagicMock(return_value=mock_instance)
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_status"
        assert result.error["status_code"] == 500
        assert result.error["source"] is not None

    async def test_connection_error_surfaces_as_clear_tool_error(self) -> None:
        from context_intelligence.client import CIClientError
        from context_intelligence.whoami_tool import WhoamiTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = WhoamiTool(coordinator)

        mock_instance = AsyncMock()
        mock_instance.whoami = AsyncMock(
            side_effect=CIClientError(
                "connection error to http://ci-server:9000/whoami: refused",
                error_type="connection_error",
                url="http://ci-server:9000/whoami",
            )
        )
        mock_cls = MagicMock(return_value=mock_instance)
        with patch(
            "context_intelligence.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "connection_error"
        assert result.error["source"] is not None
