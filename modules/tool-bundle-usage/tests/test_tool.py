"""Tests for amplifier-module-tool-bundle-usage.

Verifies that the tool follows the established lazy-resolver pattern:
- mount() stores the coordinator reference, does NOT call get_capability()
- execute() resolves the config capability on first call (lazy)
- Constructs AsyncCIClient from resolver values
- Delegates to run_bundle_analysis() with the constructed client
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_coordinator(resolver_value):
    """Build a fake coordinator whose get_capability returns the given resolver."""
    coord = MagicMock()
    coord.get_capability = MagicMock(return_value=resolver_value)
    coord.mount = AsyncMock()
    return coord


def make_resolver(server_url="http://srv:7474", api_key="key", workspace="ws"):
    return SimpleNamespace(
        context_intelligence_server_url=server_url,
        context_intelligence_api_key=api_key,
        workspace=workspace,
    )


class TestMount:
    @pytest.mark.asyncio
    async def test_mount_does_not_call_get_capability(self):
        """Hooks register AFTER tools — mount must NOT resolve the capability."""
        from amplifier_module_tool_bundle_usage import mount

        coord = make_coordinator(resolver_value=None)
        await mount(coord, {})
        coord.get_capability.assert_not_called()

    @pytest.mark.asyncio
    async def test_mount_registers_tool(self):
        from amplifier_module_tool_bundle_usage import mount

        coord = make_coordinator(resolver_value=None)
        await mount(coord, {})
        coord.mount.assert_called_once()
        # mount("tools", tool, name="bundle_usage")
        args, kwargs = coord.mount.call_args
        assert args[0] == "tools"
        assert kwargs.get("name") == "bundle_usage" or (
            len(args) >= 3 and args[2] == "bundle_usage"
        )


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_resolves_capability_lazily(self):
        from amplifier_module_tool_bundle_usage.bundle_usage_tool import BundleUsageTool

        coord = make_coordinator(resolver_value=make_resolver())
        tool = BundleUsageTool(coordinator=coord)
        assert coord.get_capability.call_count == 0  # not yet

        with patch(
            "amplifier_module_tool_bundle_usage.bundle_usage_tool.run_bundle_analysis",
            new=AsyncMock(return_value={"signals": {}, "inventory": {}, "gap": {}}),
        ):
            await tool.execute({"workspace": "ws"})

        assert coord.get_capability.call_count == 1
        coord.get_capability.assert_called_with("context_intelligence.config_resolver")

    @pytest.mark.asyncio
    async def test_execute_constructs_async_client_from_resolver(self):
        from amplifier_module_tool_bundle_usage.bundle_usage_tool import BundleUsageTool

        coord = make_coordinator(
            resolver_value=make_resolver(
                server_url="http://example:7474", api_key="secret", workspace="my-ws"
            )
        )
        tool = BundleUsageTool(coordinator=coord)

        captured = {}

        async def fake_run(*, client, workspace, **kwargs):
            captured["client"] = client
            captured["workspace"] = workspace
            captured.update(kwargs)
            return {"signals": {}, "inventory": {}, "gap": {}}

        with patch(
            "amplifier_module_tool_bundle_usage.bundle_usage_tool.run_bundle_analysis",
            new=fake_run,
        ):
            await tool.execute({})

        # Client constructed from resolver values
        from context_intelligence.client import AsyncCIClient

        assert isinstance(captured["client"], AsyncCIClient)
        assert captured["workspace"] == "my-ws"

    @pytest.mark.asyncio
    async def test_execute_propagates_session_id(self):
        from amplifier_module_tool_bundle_usage.bundle_usage_tool import BundleUsageTool

        coord = make_coordinator(resolver_value=make_resolver())
        tool = BundleUsageTool(coordinator=coord)

        captured = {}

        async def fake_run(*, client, workspace, session_id=None, **kwargs):
            captured["session_id"] = session_id
            return {"signals": {}, "inventory": {}, "gap": {}}

        with patch(
            "amplifier_module_tool_bundle_usage.bundle_usage_tool.run_bundle_analysis",
            new=fake_run,
        ):
            await tool.execute({"session_id": "abc-123"})

        assert captured["session_id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_execute_returns_configuration_error_when_no_resolver(self):
        from amplifier_core.models import ToolResult
        from amplifier_module_tool_bundle_usage.bundle_usage_tool import BundleUsageTool

        coord = make_coordinator(resolver_value=None)
        tool = BundleUsageTool(coordinator=coord)

        result = await tool.execute({})
        assert isinstance(result, ToolResult)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_execute_returns_tool_result_success(self):
        from amplifier_core.models import ToolResult
        from amplifier_module_tool_bundle_usage.bundle_usage_tool import BundleUsageTool

        coord = make_coordinator(resolver_value=make_resolver())
        tool = BundleUsageTool(coordinator=coord)

        with patch(
            "amplifier_module_tool_bundle_usage.bundle_usage_tool.run_bundle_analysis",
            new=AsyncMock(return_value={"signals": {}, "inventory": {}, "gap": {}}),
        ):
            result = await tool.execute({})

        assert isinstance(result, ToolResult)
        assert result.success is True
