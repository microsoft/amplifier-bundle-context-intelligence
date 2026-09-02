"""Module-level contract tests for tool-server-data-ops.

Tests for the merged three-tool module: mount registers all three tools from
one call, the ToolConfigResolver is shared (one instance, identical
resolution), and the lazy hook lookup stays lazy (not cached at mount time).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(hook_resolver: Any = None) -> MagicMock:
    """Coordinator whose get_capability returns hook_resolver."""
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability = MagicMock(return_value=hook_resolver)
    coordinator.mount = AsyncMock()
    return coordinator


def _make_dest(url: str, api_key: str = "") -> SimpleNamespace:
    return SimpleNamespace(name="default", url=url, api_key=api_key)


def _make_hook_resolver(url: str | None = None, api_key: str = "") -> MagicMock:
    """Minimal hook resolver mock with a destinations dict."""
    resolver = MagicMock()
    resolver.workspace = "test-workspace"
    if url:
        resolver.destinations = {"default": _make_dest(url, api_key or "")}
    else:
        resolver.destinations = {}
    return resolver


# ---------------------------------------------------------------------------
# TestModuleContract
# ---------------------------------------------------------------------------


class TestModuleContract:
    """Module-level contract (type marker + mount signature)."""

    def test_module_type_is_tool(self) -> None:
        from amplifier_module_tool_server_data_ops import __amplifier_module_type__

        assert __amplifier_module_type__ == "tool"

    def test_mount_is_coroutine(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        assert inspect.iscoroutinefunction(mount)

    def test_mount_signature_has_coordinator_and_config(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        sig = inspect.signature(mount)
        params = list(sig.parameters.keys())
        assert params[0] == "coordinator"
        assert params[1] == "config"


# ---------------------------------------------------------------------------
# TestMountRegistersExactlyThreeTools
# ---------------------------------------------------------------------------


class TestMountRegistersExactlyThreeTools:
    """mount() must register exactly three tools with distinct names."""

    async def test_mount_registers_exactly_three_tools(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        assert coordinator.mount.call_count == 3

    async def test_all_tool_calls_use_tools_category(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        for call in coordinator.mount.call_args_list:
            assert call.args[0] == "tools"

    async def test_tool_names_are_session_summary_delete_session_and_whoami(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        registered_names = {call.kwargs["name"] for call in coordinator.mount.call_args_list}
        assert registered_names == {"session_summary", "delete_session", "whoami"}

    async def test_mounted_tools_are_protocol_compliant(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        for call in coordinator.mount.call_args_list:
            tool = call.args[1]
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert hasattr(tool, "input_schema")
            assert hasattr(tool, "execute")
            assert isinstance(tool.input_schema, dict)
            assert inspect.iscoroutinefunction(tool.execute)

    async def test_mount_returns_none(self) -> None:
        """mount() returns None -- the kernel ignores non-callable returns."""
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator()
        result = await mount(coordinator, config={})
        assert result is None

    async def test_mount_makes_no_register_capability_call(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        coordinator.register_capability.assert_not_called()


# ---------------------------------------------------------------------------
# TestSharedResolverInvariant
# ---------------------------------------------------------------------------


class TestSharedResolverInvariant:
    """The ToolConfigResolver is shared: one instance, identical resolution."""

    async def test_all_three_tools_have_same_resolver_instance(self) -> None:
        """summary/delete/whoami._tool_resolver are all the SAME object from mount()."""
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        summary = tools["session_summary"]
        delete = tools["delete_session"]
        whoami = tools["whoami"]
        assert summary._tool_resolver is delete._tool_resolver
        assert summary._tool_resolver is whoami._tool_resolver

    async def test_shared_resolver_consistency_same_url_and_api_key(self) -> None:
        """All three tools resolve to the SAME (url, api_key) from sources.

        This is the load-bearing correctness invariant: with a shared resolver,
        divergent read-endpoint config is structurally impossible.
        """
        from context_intelligence.tool_resolver import resolve_query_connection

        from amplifier_module_tool_server_data_ops import mount

        config = {
            "sources": {
                "primary": {"url": "http://data-ops.example.com", "api_key": "shared-key"},
            }
        }
        coordinator = _make_coordinator()
        await mount(coordinator, config=config)

        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        summary = tools["session_summary"]
        delete = tools["delete_session"]
        whoami = tools["whoami"]

        # Resolve using the shared resolver (no hook resolver needed for tier-1 hit)
        summary_conn = resolve_query_connection(None, summary._tool_resolver)
        delete_conn = resolve_query_connection(None, delete._tool_resolver)
        whoami_conn = resolve_query_connection(None, whoami._tool_resolver)

        assert (
            summary_conn.url == delete_conn.url == whoami_conn.url == "http://data-ops.example.com"
        )
        assert summary_conn.api_key == delete_conn.api_key == whoami_conn.api_key == "shared-key"


# ---------------------------------------------------------------------------
# TestLateMountTimingInvariant
# ---------------------------------------------------------------------------


class TestLateMountTimingInvariant:
    """The lazy hook-resolver lookup must NOT be cached at mount() time.

    Catches any regression where the hook capability is fetched eagerly in
    mount() rather than lazily in execute() (which would break when the hook
    mounts later).
    """

    async def test_late_mount_session_summary_resolves_destination_after_hook_registers(
        self,
    ) -> None:
        """Mount with NO hook -> register hook AFTER -> execute() sees the hook's destination."""
        from amplifier_module_tool_server_data_ops import mount

        # Step 1: mount with no hook registered
        coordinator = _make_coordinator(hook_resolver=None)
        await mount(coordinator, config={})
        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        summary = tools["session_summary"]

        # Confirm hook resolver is None after mount (lazy, not fetched yet)
        assert summary._hook_resolver is None

        # Step 2: register the hook resolver AFTER mount
        hook_resolver = _make_hook_resolver(url="http://late-hook.example.com", api_key="late-key")
        coordinator.get_capability.return_value = hook_resolver

        # Step 3: execute() must now see the late-registered hook destination
        mock_client = MagicMock()
        mock_client.session_summary = AsyncMock(return_value={"deletable": True})
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_server_data_ops.session_summary_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await summary.execute({"session_id": "abc"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://late-hook.example.com"
        assert call_kwargs["api_key"] == "late-key"

    async def test_late_mount_delete_session_resolves_destination_after_hook_registers(
        self,
    ) -> None:
        """DeleteSessionTool: mount with no hook -> register hook -> execute sees destination."""
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator(hook_resolver=None)
        await mount(coordinator, config={})
        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        delete = tools["delete_session"]

        assert delete._hook_resolver is None

        hook_resolver = _make_hook_resolver(url="http://late-hook.example.com", api_key="late-key")
        coordinator.get_capability.return_value = hook_resolver

        mock_client = MagicMock()
        mock_client.delete_session = AsyncMock(return_value={"nodes_deleted": 1})
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_server_data_ops.delete_session_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await delete.execute({"session_id": "abc"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://late-hook.example.com"
        assert call_kwargs["api_key"] == "late-key"

    async def test_late_mount_whoami_resolves_destination_after_hook_registers(
        self,
    ) -> None:
        """WhoamiTool: mount with no hook -> register hook -> execute sees destination."""
        from amplifier_module_tool_server_data_ops import mount

        coordinator = _make_coordinator(hook_resolver=None)
        await mount(coordinator, config={})
        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        whoami = tools["whoami"]

        assert whoami._hook_resolver is None

        hook_resolver = _make_hook_resolver(url="http://late-hook.example.com", api_key="late-key")
        coordinator.get_capability.return_value = hook_resolver

        mock_client = MagicMock()
        mock_client.whoami = AsyncMock(return_value={"contributor_id": "alice"})
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_server_data_ops.whoami_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await whoami.execute({})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://late-hook.example.com"
        assert call_kwargs["api_key"] == "late-key"


# ---------------------------------------------------------------------------
# TestMountWithMisconfiguredSource
# ---------------------------------------------------------------------------


class TestMountWithMisconfiguredSource:
    """mount() with one bad + one good source entry does not raise."""

    async def test_mount_does_not_raise_with_one_bad_source(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "gk"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        coordinator = _make_coordinator()
        # Must not raise.
        result = await mount(coordinator, config=config)
        assert result is None

    async def test_mount_registers_all_tools_with_one_bad_source(self) -> None:
        from amplifier_module_tool_server_data_ops import mount

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "gk"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        coordinator = _make_coordinator()
        await mount(coordinator, config=config)

        assert coordinator.mount.call_count == 3
        registered_names = {call.kwargs["name"] for call in coordinator.mount.call_args_list}
        assert registered_names == {"session_summary", "delete_session", "whoami"}

    async def test_mount_logs_warning_with_one_bad_source(self, caplog: Any) -> None:
        import logging

        from amplifier_module_tool_server_data_ops import mount

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "gk"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        coordinator = _make_coordinator()
        with caplog.at_level(logging.WARNING, logger="context_intelligence.tool_resolver"):
            await mount(coordinator, config=config)

        assert any("misconfigured" in r.message for r in caplog.records)
        assert any("bad" in r.message for r in caplog.records)
