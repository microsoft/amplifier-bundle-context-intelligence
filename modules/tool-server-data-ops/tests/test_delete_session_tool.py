"""Tests for DeleteSessionTool.

Constructor: DeleteSessionTool(coordinator, resolver=None). Patch path is
amplifier_module_tool_server_data_ops.delete_session_tool.
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
            "default": SimpleNamespace(name="default", url=server_url, api_key=api_key or ""),
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
    mock_instance.delete_session = AsyncMock(
        return_value=return_value if return_value is not None else {"nodes_deleted": 0}
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
# TestDeleteSessionToolProtocol
# ---------------------------------------------------------------------------


class TestDeleteSessionToolProtocol:
    """Tool protocol surface tests."""

    def test_name_is_delete_session(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        tool = DeleteSessionTool(_make_coordinator())
        assert tool.name == "delete_session"

    def test_description_mentions_permanent(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        tool = DeleteSessionTool(_make_coordinator())
        assert "permanent" in tool.description.lower()

    def test_input_schema_returns_object_type(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        tool = DeleteSessionTool(_make_coordinator())
        assert tool.input_schema["type"] == "object"

    def test_input_schema_session_id_not_required_but_enforced_at_execute(self) -> None:
        """`session_id` is NOT in the JSON-schema `required` list -- list_sources=true
        calls legitimately omit it. execute() enforces the rule itself."""
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        tool = DeleteSessionTool(_make_coordinator())
        assert "session_id" not in tool.input_schema["required"]
        assert "session_id" in tool.input_schema["properties"]

    def test_input_schema_has_optional_source_and_list_sources(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        tool = DeleteSessionTool(_make_coordinator())
        props = tool.input_schema["properties"]
        assert "source" in props
        assert "list_sources" in props
        assert "source" not in tool.input_schema["required"]
        assert "list_sources" not in tool.input_schema["required"]

    async def test_execute_returns_tool_result(self) -> None:
        from amplifier_core.models import ToolResult

        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        hook_resolver = _make_hook_resolver()
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = DeleteSessionTool(coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"session_id": "abc"})

        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# TestListSources
# ---------------------------------------------------------------------------


class TestListSources:
    async def test_list_sources_does_not_call_client(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        resolver = _make_tool_resolver(
            {"sources": {"only": {"url": "http://only.example.com", "api_key": "k"}}}
        )
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = DeleteSessionTool(coordinator, resolver)

        mock_cls = MagicMock()
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"list_sources": True})

        assert result.success is True
        assert result.output is not None
        names = {e["name"] for e in result.output["connectable_set"]}
        assert names == {"only"}
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# TestDeleteSessionConstruction -- AsyncCIClient construction and delegation
# ---------------------------------------------------------------------------


class TestDeleteSessionConstruction:
    """AsyncCIClient construction and delegation tests (mirrors GraphQueryTool)."""

    async def test_client_constructed_with_server_url_and_api_key(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000", api_key="my-key")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = DeleteSessionTool(coordinator)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            await tool.execute({"session_id": "abc"})

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs.get("server_url") == "http://ci-server:9000"
        assert call_kwargs.get("api_key") == "my-key"

    async def test_session_id_forwarded_to_client_delete_session(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        hook_resolver = _make_hook_resolver()
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = DeleteSessionTool(coordinator)

        mock_instance, mock_cls = _make_mock_async_ci_client()
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            await tool.execute({"session_id": "the-session-id"})

        mock_instance.delete_session.assert_called_once_with("the-session-id")

    async def test_result_forwarded_and_source_stamped(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = DeleteSessionTool(coordinator)

        expected = {
            "root_id": "abc",
            "nodes_deleted": 42,
            "relationships_deleted": 10,
            "blobs_deleted": 2,
        }
        _, mock_cls = _make_mock_async_ci_client(return_value=expected)
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"session_id": "abc"})

        assert result.success is True
        assert result.output is not None
        assert result.output["result"] == expected
        assert result.output["source"] is not None
        assert result.output["source"]["url"] == "http://ci-server:9000"


# ---------------------------------------------------------------------------
# TestDeleteSessionConfigFallback
# ---------------------------------------------------------------------------


class TestDeleteSessionConfigFallback:
    async def test_capability_not_found_returns_configuration_error(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        coordinator = _make_coordinator(resolver=None)
        tool = DeleteSessionTool(coordinator)
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            result = await tool.execute({"session_id": "abc"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"

    async def test_missing_session_id_validation_error_carries_source(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        resolver = _make_tool_resolver(
            {"sources": {"only": {"url": "http://only.example.com", "api_key": "k"}}}
        )
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = DeleteSessionTool(coordinator, resolver)

        result = await tool.execute({})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "validation_error"
        assert result.error["source"] == {
            "name": "only",
            "url": "http://only.example.com",
            "origin": "source",
        }


# ---------------------------------------------------------------------------
# TestDeleteSessionSourceSelection -- pool/selection + fail-loud ambiguity
# ---------------------------------------------------------------------------


class TestDeleteSessionSourceSelection:
    """execute() with an explicit `source` -- matching / not matching / omitted-with-2+."""

    def _two_source_config(self) -> dict:
        return {
            "sources": {
                "alpha": {"url": "http://alpha.example.com", "api_key": "alpha-key"},
                "beta": {"url": "http://beta.example.com", "api_key": "beta-key"},
            }
        }

    async def test_source_matching_name_selects_that_source(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        resolver = _make_tool_resolver(self._two_source_config())
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = DeleteSessionTool(coordinator, resolver)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"session_id": "abc", "source": "beta"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://beta.example.com"
        assert call_kwargs["api_key"] == "beta-key"

    async def test_source_not_matching_returns_unknown_source_error(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        resolver = _make_tool_resolver(self._two_source_config())
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = DeleteSessionTool(coordinator, resolver)

        result = await tool.execute({"session_id": "abc", "source": "gamma"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "unknown_source"
        assert result.error["valid_sources"] == ["alpha", "beta"]

    async def test_source_omitted_with_two_configured_returns_ambiguous_error(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        resolver = _make_tool_resolver(self._two_source_config())
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = DeleteSessionTool(coordinator, resolver)

        result = await tool.execute({"session_id": "abc"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "ambiguous_source_selection"
        assert result.error["valid_sources"] == ["alpha", "beta"]

    async def test_source_omitted_with_one_configured_still_succeeds(self) -> None:
        """Safe to omit source with exactly one configured (backward compatible)."""
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        config = {
            "sources": {
                "default": {"url": "http://only.example.com", "api_key": "only-key"},
            }
        }
        resolver = _make_tool_resolver(config)
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = DeleteSessionTool(coordinator, resolver)

        _, mock_cls = _make_mock_async_ci_client()
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"session_id": "abc"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://only.example.com"

    async def test_selected_source_misconfigured_returns_source_misconfigured_error(self) -> None:
        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "gk"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        resolver = _make_tool_resolver(config)
        coordinator = _make_coordinator(resolver=_make_hook_resolver_with_dests({}))
        tool = DeleteSessionTool(coordinator, resolver)

        result = await tool.execute({"session_id": "abc", "source": "bad"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "source_misconfigured"
        assert "bad" in result.error["message"]


# ---------------------------------------------------------------------------
# TestDeleteSessionServerErrors -- 404/409 surfaced as clear tool errors
# ---------------------------------------------------------------------------


class TestDeleteSessionServerErrors:
    async def test_404_surfaces_as_clear_tool_error(self) -> None:
        from context_intelligence.client import CIClientError

        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = DeleteSessionTool(coordinator)

        mock_instance = AsyncMock()
        mock_instance.delete_session = AsyncMock(
            side_effect=CIClientError(
                "HTTP 404 from http://ci-server:9000/sessions/missing",
                error_type="http_status",
                url="http://ci-server:9000/sessions/missing",
                status_code=404,
            )
        )
        mock_cls = MagicMock(return_value=mock_instance)
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"session_id": "missing"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_status"
        assert result.error["status_code"] == 404
        assert "missing" in result.error["message"]
        assert result.error["source"] is not None

    async def test_409_surfaces_as_clear_tool_error(self) -> None:
        """A 409 (still receiving data / ambiguous id) must never be silently
        treated as a completed delete -- it surfaces as a clear tool error."""
        from context_intelligence.client import CIClientError

        from amplifier_module_tool_server_data_ops.delete_session_tool import DeleteSessionTool

        hook_resolver = _make_hook_resolver(server_url="http://ci-server:9000")
        coordinator = _make_coordinator(resolver=hook_resolver)
        tool = DeleteSessionTool(coordinator)

        mock_instance = AsyncMock()
        mock_instance.delete_session = AsyncMock(
            side_effect=CIClientError(
                "HTTP 409 from http://ci-server:9000/sessions/live",
                error_type="http_status",
                url="http://ci-server:9000/sessions/live",
                status_code=409,
            )
        )
        mock_cls = MagicMock(return_value=mock_instance)
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"session_id": "live"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_status"
        assert result.error["status_code"] == 409
        assert "still receiving data" in result.error["message"]
