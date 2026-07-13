"""Module-level contract tests for tool-context-intelligence-query.

Tests for the merged two-tool module: mount registers both tools from one call,
the ToolConfigResolver is shared (one instance, identical resolution), the lazy
hook lookup stays lazy (not cached at mount time), and malformed/empty destination
inputs fail loud or fall through correctly.
"""

from __future__ import annotations

import asyncio
import inspect
import os
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


def _make_dest(url: str, api_key: str) -> SimpleNamespace:
    return SimpleNamespace(name="default", url=url, api_key=api_key)


def _make_hook_resolver(url: str | None = None, api_key: str | None = None) -> MagicMock:
    """Minimal hook resolver mock with a destinations dict."""
    resolver = MagicMock()
    resolver.workspace = "test-workspace"
    if url:
        resolver.destinations = {"default": _make_dest(url, api_key or "")}
    else:
        resolver.destinations = {}
    return resolver


def _graph_tool(coordinator: Any, resolver: Any = None) -> Any:
    from context_intelligence.tool_resolver import ToolConfigResolver

    from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

    if resolver is None:
        resolver = ToolConfigResolver({}, coordinator)
    return GraphQueryTool(coordinator, resolver)


def _blob_tool(coordinator: Any, resolver: Any = None) -> Any:
    from context_intelligence.tool_resolver import ToolConfigResolver

    from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

    if resolver is None:
        resolver = ToolConfigResolver({}, coordinator)
    return BlobReadTool(coordinator, resolver)


# ---------------------------------------------------------------------------
# TestModuleContract
# ---------------------------------------------------------------------------


class TestModuleContract:
    """Module-level contract (type marker + mount signature)."""

    def test_module_type_is_tool(self) -> None:
        from amplifier_module_tool_context_intelligence_query import __amplifier_module_type__

        assert __amplifier_module_type__ == "tool"

    def test_mount_is_coroutine(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        assert inspect.iscoroutinefunction(mount)

    def test_mount_signature_has_coordinator_and_config(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        sig = inspect.signature(mount)
        params = list(sig.parameters.keys())
        assert params[0] == "coordinator"
        assert params[1] == "config"


# ---------------------------------------------------------------------------
# TestMountRegistersExactlyTwoTools
# ---------------------------------------------------------------------------


class TestMountRegistersExactlyTwoTools:
    """mount() must register exactly two tools with distinct names."""

    async def test_mount_registers_exactly_two_tools(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        assert coordinator.mount.call_count == 2

    async def test_both_tool_calls_use_tools_category(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        for call in coordinator.mount.call_args_list:
            assert call.args[0] == "tools"

    async def test_tool_names_are_graph_query_and_blob_read(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        registered_names = {call.kwargs["name"] for call in coordinator.mount.call_args_list}
        assert registered_names == {"graph_query", "blob_read"}

    async def test_mounted_tools_are_protocol_compliant(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

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
        """mount() returns None — the kernel ignores non-callable returns."""
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        result = await mount(coordinator, config={})
        assert result is None


# ---------------------------------------------------------------------------
# TestSeam1SkillSyncLifecycleCutover
#
# Seam 1 (docs/skill-sync-removal-plan.md §4.5): kernel <-> module lifecycle
# via on_session_ready + the orphaned _GRAPH_QUERY_TOOL_CAPABILITY registration.
# ELIMINATED seam — this is a cutover check (false-before/true-after), not a
# standing test: it proves the seam is GONE after skill_sync's removal, then
# retires. The standing guard against reintroduction is the residue grep
# (plan §7 item 1), not this test living on forever.
# ---------------------------------------------------------------------------


class TestSeam1SkillSyncLifecycleCutover:
    """mount() exposes no on_session_ready and makes no register_capability call.

    Both halves flip with the skill_sync removal: before, the module imported
    on_session_ready and mount() registered `_GRAPH_QUERY_TOOL_CAPABILITY` for
    it to consume; after, neither exists. This test cannot pass against the
    pre-change code.
    """

    async def test_mount_registers_both_tools_by_name(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        registered_names = {call.kwargs["name"] for call in coordinator.mount.call_args_list}
        assert registered_names == {"graph_query", "blob_read"}

    async def test_module_has_no_on_session_ready(self) -> None:
        import amplifier_module_tool_context_intelligence_query as module

        assert getattr(module, "on_session_ready", None) is None

    async def test_mount_makes_no_register_capability_call(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        # skill_sync's only consumer of this capability is gone; mount() must
        # not register it (or any capability) any more.
        coordinator.register_capability.assert_not_called()


# ---------------------------------------------------------------------------
# TestSeam2SkillSyncEnabledCutover
#
# Seam 2 (docs/skill-sync-removal-plan.md §4.5): the skill_sync_enabled config
# path through ToolConfigResolver / GraphQueryTool. ELIMINATED seam, SILENT by
# accepted decision (plan §11) — cutover check only, no standing test. The
# residue grep is the standing guard.
# ---------------------------------------------------------------------------


class TestSeam2SkillSyncEnabledCutover:
    """skill_sync_enabled is gone from both the resolver and the tool.

    Both attributes were `True`-hasattr before this removal (the draft's
    "mount doesn't raise" assertion was a proven false green — mount() never
    read skill_sync_enabled, only on_session_ready did). Asserting hasattr is
    False is the one check that actually flips.
    """

    def test_resolver_has_no_skill_sync_enabled_attribute(self) -> None:
        from context_intelligence.tool_resolver import ToolConfigResolver

        coordinator = _make_coordinator()
        resolver = ToolConfigResolver({}, coordinator)

        assert hasattr(resolver, "skill_sync_enabled") is False

    def test_graph_query_tool_has_no_skill_sync_enabled_attribute(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool
        from context_intelligence.tool_resolver import ToolConfigResolver

        coordinator = _make_coordinator()
        resolver = ToolConfigResolver({}, coordinator)
        tool = GraphQueryTool(coordinator, resolver)

        assert hasattr(tool, "skill_sync_enabled") is False

    async def test_stale_skill_sync_enabled_key_is_inert_at_mount(self) -> None:
        """A stale skill_sync_enabled key in config must not raise on mount.

        The key is functionally inert (the behavior it toggled is gone) but
        silently ignored per the accepted, recorded decision in plan §11 —
        not a bug, an explicit choice not to add deprecation-warning machinery
        for a feature with no known live consumers.
        """
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        config = {
            "skill_sync_enabled": True,
            "sources": {
                "primary": {"url": "http://read.example.com", "api_key": "shared-key"},
            },
        }

        # Must complete without raising — that's the whole assertion.
        await mount(coordinator, config=config)


# ---------------------------------------------------------------------------
# TestSharedResolverInvariant
# ---------------------------------------------------------------------------


class TestSharedResolverInvariant:
    """The ToolConfigResolver is shared: one instance, identical resolution."""

    async def test_both_tools_have_same_resolver_instance(self) -> None:
        """gq._tool_resolver is br._tool_resolver: same object from mount()."""
        from amplifier_module_tool_context_intelligence_query import mount

        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        gq = tools["graph_query"]
        br = tools["blob_read"]
        assert gq._tool_resolver is br._tool_resolver

    async def test_shared_resolver_consistency_same_url_and_api_key(self) -> None:
        """Both tools resolve to the SAME (url, api_key) from sources.

        This is the load-bearing correctness invariant: with a shared resolver,
        divergent read-endpoint config is structurally impossible.
        """
        from amplifier_module_tool_context_intelligence_query import mount
        from context_intelligence.tool_resolver import resolve_query_connection

        config = {
            "sources": {
                "primary": {"url": "http://read.example.com", "api_key": "shared-key"},
            }
        }
        coordinator = _make_coordinator()
        await mount(coordinator, config=config)

        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        gq = tools["graph_query"]
        br = tools["blob_read"]

        # Resolve using the shared resolver (no hook resolver needed for tier-1 hit)
        gq_conn = resolve_query_connection(None, gq._tool_resolver)
        br_conn = resolve_query_connection(None, br._tool_resolver)

        assert gq_conn.url == br_conn.url == "http://read.example.com"
        assert gq_conn.api_key == br_conn.api_key == "shared-key"

    async def test_concurrent_resolution_is_consistent(self) -> None:
        """Execute both tools 'concurrently'; both resolve to the same endpoint.

        ToolConfigResolver.sources is synchronous (no await between
        cache-check and cache-set), so under asyncio there is no interleaving.
        This test confirms identical results when both tools run concurrently.
        """
        from unittest.mock import AsyncMock as AM

        from amplifier_module_tool_context_intelligence_query import mount

        config = {
            "sources": {
                "primary": {"url": "http://shared.example.com", "api_key": "shared-key"},
            }
        }
        hook_resolver = _make_hook_resolver(url="http://hook.example.com", api_key="hook-key")
        coordinator = _make_coordinator(hook_resolver=hook_resolver)
        await mount(coordinator, config=config)

        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        gq = tools["graph_query"]
        br = tools["blob_read"]

        # Mock the AsyncCIClient for both tools
        gq_client = MagicMock()
        gq_client.cypher = AM(return_value=[])
        br_client = MagicMock()
        br_client.fetch_blob = AM(return_value=None)  # None → http_error, but url is resolved

        gq_results = []
        br_results = []

        with (
            patch(
                "amplifier_module_tool_context_intelligence_query.graph_query_tool.AsyncCIClient",
                return_value=gq_client,
            ) as gq_cls,
            patch(
                "amplifier_module_tool_context_intelligence_query.blob_read_tool.AsyncCIClient",
                return_value=br_client,
            ) as br_cls,
        ):
            gq_task = asyncio.create_task(gq.execute({"query": "MATCH (n) RETURN n"}))
            br_task = asyncio.create_task(br.execute({"uri": "ci-blob://s/k"}))
            gq_result, br_result = await asyncio.gather(gq_task, br_task)
            gq_results.append(gq_cls.call_args)
            br_results.append(br_cls.call_args)

        # Both tools must resolve to the shared read-destinations URL (tier 1)
        assert gq_results[0] is not None
        assert br_results[0] is not None
        # AsyncCIClient is always called with keyword args (server_url=, api_key=)
        gq_url = gq_results[0].kwargs.get("server_url")
        br_url = br_results[0].kwargs.get("server_url")
        # Read-destinations (tier 1) wins over hook destination (tier 2)
        assert gq_url == "http://shared.example.com"
        assert br_url == "http://shared.example.com"


# ---------------------------------------------------------------------------
# TestLateMount_TimingInvariant
# ---------------------------------------------------------------------------


class TestLateMountTimingInvariant:
    """The lazy hook-resolver lookup must NOT be cached at mount() time.

    Catches any regression where the hook capability is fetched eagerly in mount()
    rather than lazily in execute() (which would break when the hook mounts later).
    """

    async def test_late_mount_graph_query_resolves_destination_after_hook_registers(
        self,
    ) -> None:
        """Mount with NO hook → register hook AFTER → execute() sees the hook's destination."""
        from amplifier_module_tool_context_intelligence_query import mount

        # Step 1: mount with no hook registered
        coordinator = _make_coordinator(hook_resolver=None)
        await mount(coordinator, config={})
        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        gq = tools["graph_query"]

        # Confirm hook resolver is None after mount (lazy, not fetched yet)
        assert gq._hook_resolver is None

        # Step 2: register the hook resolver AFTER mount
        hook_resolver = _make_hook_resolver(url="http://late-hook.example.com", api_key="late-key")
        coordinator.get_capability.return_value = hook_resolver

        # Step 3: execute() must now see the late-registered hook destination
        mock_client = MagicMock()
        mock_client.cypher = AsyncMock(return_value=[])
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_context_intelligence_query.graph_query_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await gq.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # Tier 2 (hook destination) resolved because no sources key in config
        assert call_kwargs["server_url"] == "http://late-hook.example.com"
        assert call_kwargs["api_key"] == "late-key"

    async def test_late_mount_blob_read_resolves_destination_after_hook_registers(
        self,
    ) -> None:
        """BlobReadTool: mount with no hook → register hook → execute sees destination."""
        import pathlib
        import shutil

        from amplifier_module_tool_context_intelligence_query import mount

        # Cleanup blob dir
        blob_dir = pathlib.Path("/tmp/ci-blobs")
        if blob_dir.exists():
            shutil.rmtree(blob_dir)

        # Step 1: mount with no hook
        coordinator = _make_coordinator(hook_resolver=None)
        await mount(coordinator, config={})
        tools = {call.kwargs["name"]: call.args[1] for call in coordinator.mount.call_args_list}
        br = tools["blob_read"]

        assert br._hook_resolver is None

        # Step 2: register hook AFTER mount
        hook_resolver = _make_hook_resolver(url="http://late-hook.example.com", api_key="late-key")
        coordinator.get_capability.return_value = hook_resolver

        # Step 3: execute resolves from the hook destination
        mock_client = MagicMock()
        mock_client.fetch_blob = AsyncMock(return_value={"ok": True})
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_context_intelligence_query.blob_read_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await br.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://late-hook.example.com"
        assert call_kwargs["api_key"] == "late-key"


# ---------------------------------------------------------------------------
# TestMalformedDestinationInputs
# ---------------------------------------------------------------------------


class TestMalformedDestinationInputs:
    """Malformed / empty destination inputs must fail loud or fall through correctly."""

    async def test_empty_sources_list_falls_through(self) -> None:
        """sources: [] (a list, not dict) — ToolConfigResolver.sources returns {}
        for a non-dict value → falls to tier 2 (hook destination)."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool
        from context_intelligence.tool_resolver import ToolConfigResolver

        coord = MagicMock()
        coord.config = {}
        # sources as a list (malformed — ToolConfigResolver.sources guards isinstance(raw, dict))
        config = {"sources": []}
        resolver = ToolConfigResolver(config, coord)

        hook_resolver = _make_hook_resolver(
            url="http://fallback.example.com", api_key="fallback-key"
        )
        coordinator = _make_coordinator(hook_resolver=hook_resolver)
        tool = GraphQueryTool(coordinator, resolver)

        mock_client = MagicMock()
        mock_client.cypher = AsyncMock(return_value=[])
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_context_intelligence_query.graph_query_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # Falls through to tier 2 (hook destination)
        assert call_kwargs["server_url"] == "http://fallback.example.com"

    async def test_empty_sources_dict_falls_through(self) -> None:
        """sources: {} (empty dict) → first entry is None → falls to tier 2."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool
        from context_intelligence.tool_resolver import ToolConfigResolver

        coord = MagicMock()
        coord.config = {}
        config = {"sources": {}}
        resolver = ToolConfigResolver(config, coord)

        hook_resolver = _make_hook_resolver(
            url="http://fallback.example.com", api_key="fallback-key"
        )
        coordinator = _make_coordinator(hook_resolver=hook_resolver)
        tool = GraphQueryTool(coordinator, resolver)

        mock_client = MagicMock()
        mock_client.cypher = AsyncMock(return_value=[])
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_context_intelligence_query.graph_query_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://fallback.example.com"

    async def test_entry_with_empty_url_is_now_source_misconfigured(self) -> None:
        """BEHAVIOR CHANGE (criterion 4, workstream-1-multi-source-query-tools.md §2.3):
        an entry with url: '' is "missing url" per _collect_source_problems (both url
        and api_key are documented-required for a `sources` entry). Previously this
        validation only ran at mount() time, so a unit test bypassing mount() could
        exercise pure per-field fallback on an internally-incomplete source.
        Now resolve_query_connection() validates the selected source on every call, so
        this configuration is caught immediately as source_misconfigured."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool
        from context_intelligence.tool_resolver import ToolConfigResolver

        coord = MagicMock()
        coord.config = {}
        config = {
            "sources": {
                "primary": {"url": "", "api_key": "read-key"},
            }
        }
        resolver = ToolConfigResolver(config, coord)

        hook_resolver = _make_hook_resolver(url="http://fallback.example.com", api_key="hook-key")
        coordinator = _make_coordinator(hook_resolver=hook_resolver)
        tool = GraphQueryTool(coordinator, resolver)

        result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "source_misconfigured"
        assert "primary" in result.error["message"]

    async def test_all_tiers_miss_returns_loud_configuration_error(self) -> None:
        """No config, no destinations, no env → configuration_error (loud, not silent empty)."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool
        from context_intelligence.tool_resolver import ToolConfigResolver

        coord = MagicMock()
        coord.config = {}
        resolver = ToolConfigResolver({}, coord)

        hook_resolver = _make_hook_resolver(url=None)  # empty destinations
        coordinator = _make_coordinator(hook_resolver=hook_resolver)
        tool = GraphQueryTool(coordinator, resolver)

        clean_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")
        }
        mock_client = MagicMock()
        mock_cls = MagicMock(return_value=mock_client)
        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch(
                "amplifier_module_tool_context_intelligence_query.graph_query_tool.AsyncCIClient",
                mock_cls,
            ),
        ):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"
        # Must not have called the client (fails before reaching that point)
        mock_cls.assert_not_called()

    async def test_none_sources_treated_as_absent(self) -> None:
        """If sources is explicitly None/null → absent-key semantics (legacy synthesis).

        The ToolConfigResolver treats None as a non-dict → falls to legacy synthesis.
        With no legacy scalars either, result is {} → falls through.
        """
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool
        from context_intelligence.tool_resolver import ToolConfigResolver

        coord = MagicMock()
        coord.config = {}
        # sources: null  — the sentinel detection in ToolConfigResolver checks
        # 'raw is not _sentinel' → key IS present (None). isinstance(None, dict) is False → {}
        config = {"sources": None}
        resolver = ToolConfigResolver(config, coord)

        hook_resolver = _make_hook_resolver(url="http://hook.example.com", api_key="hook-key")
        coordinator = _make_coordinator(hook_resolver=hook_resolver)
        tool = GraphQueryTool(coordinator, resolver)

        mock_client = MagicMock()
        mock_client.cypher = AsyncMock(return_value=[])
        mock_cls = MagicMock(return_value=mock_client)
        with patch(
            "amplifier_module_tool_context_intelligence_query.graph_query_tool.AsyncCIClient",
            mock_cls,
        ):
            result = await tool.execute({"query": "MATCH (n) RETURN n"})

        # Nsources → {} → falls to tier 2 (hook destination)
        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://hook.example.com"


# ---------------------------------------------------------------------------
# TestMountWithMisconfiguredSource — criterion 4 (workstream-1-multi-source-query-tools.md §6)
# ---------------------------------------------------------------------------


class TestMountWithMisconfiguredSource:
    """mount() with one bad + one good source entry no longer raises (criterion 4)."""

    async def test_mount_does_not_raise_with_one_bad_source(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "good-key"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        coordinator = _make_coordinator()
        # Must not raise.
        result = await mount(coordinator, config=config)
        assert result is None

    async def test_mount_registers_both_tools_with_one_bad_source(self) -> None:
        from amplifier_module_tool_context_intelligence_query import mount

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "good-key"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        coordinator = _make_coordinator()
        await mount(coordinator, config=config)

        assert coordinator.mount.call_count == 2
        registered_names = {call.kwargs["name"] for call in coordinator.mount.call_args_list}
        assert registered_names == {"graph_query", "blob_read"}

    async def test_mount_logs_warning_with_one_bad_source(self, caplog: Any) -> None:
        import logging

        from amplifier_module_tool_context_intelligence_query import mount

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "good-key"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        coordinator = _make_coordinator()
        with caplog.at_level(logging.WARNING, logger="context_intelligence.tool_resolver"):
            await mount(coordinator, config=config)

        assert any("misconfigured" in r.message for r in caplog.records)
        assert any("bad" in r.message for r in caplog.records)
