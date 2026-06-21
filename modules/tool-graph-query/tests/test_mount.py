"""Tests for tool-graph-query module mount contract."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock


class TestModuleContract:
    """Module-level contract for a tool module."""

    def test_module_type_is_tool(self) -> None:
        from amplifier_module_tool_graph_query import __amplifier_module_type__

        assert __amplifier_module_type__ == "tool"

    def test_mount_is_coroutine(self) -> None:
        from amplifier_module_tool_graph_query import mount

        assert inspect.iscoroutinefunction(mount)

    def test_mount_signature_has_coordinator_and_config(self) -> None:
        from amplifier_module_tool_graph_query import mount

        sig = inspect.signature(mount)
        params = list(sig.parameters.keys())
        assert params[0] == "coordinator"
        assert params[1] == "config"


class TestMountBehavior:
    """mount() registers a Tool-protocol-compliant object via coordinator.mount()."""

    async def test_mount_calls_coordinator_mount_with_tools_category(self) -> None:
        from amplifier_module_tool_graph_query import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        coordinator.mount.assert_called_once()
        assert coordinator.mount.call_args.args[0] == "tools"

    async def test_mounted_tool_has_name_graph_query(self) -> None:
        from amplifier_module_tool_graph_query import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        assert coordinator.mount.call_args.kwargs["name"] == "graph_query"

    async def test_mounted_tool_is_protocol_compliant(self) -> None:
        from amplifier_module_tool_graph_query import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, config={})
        tool = coordinator.mount.call_args.args[1]
        assert hasattr(tool, "name")
        assert hasattr(tool, "description")
        assert hasattr(tool, "input_schema")
        assert hasattr(tool, "execute")
        assert isinstance(tool.input_schema, dict)
        assert inspect.iscoroutinefunction(tool.execute)

    async def test_mount_returns_metadata_dict(self) -> None:
        from amplifier_module_tool_graph_query import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        result = await mount(coordinator, config={})
        assert isinstance(result, dict)
        assert result["tool"] == "graph_query"
        assert result["status"] == "mounted"

    async def test_config_dict_passed_to_tool_constructor(self) -> None:
        """Config dict is forwarded to the tool so it can resolve server_url and workspace."""
        from amplifier_module_tool_graph_query import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(
            coordinator,
            config={"context_intelligence_server_url": "http://test", "workspace": "ws1"},
        )
        tool = coordinator.mount.call_args.args[1]
        assert tool._config["context_intelligence_server_url"] == "http://test"
        assert tool._config["workspace"] == "ws1"


class TestOnSessionReadyWiring:
    """on_session_ready is exposed at module level and mount() registers the tool capability."""

    def test_module_exposes_on_session_ready(self) -> None:
        import amplifier_module_tool_graph_query as mod

        fn = getattr(mod, "on_session_ready", None)
        assert fn is not None
        assert inspect.iscoroutinefunction(fn)
        sig = inspect.signature(fn)
        first_param = list(sig.parameters.keys())[0]
        assert first_param == "coordinator"

    async def test_mount_registers_graph_query_tool_capability(self) -> None:
        from amplifier_module_tool_graph_query import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        coordinator.register_capability = MagicMock()
        await mount(coordinator, config={})
        names = [c.args[0] for c in coordinator.register_capability.call_args_list]
        assert "context_intelligence._graph_query_tool" in names


class TestSkillSyncEnabledConfig:
    """The skill_sync_enabled knob is forwarded to the tool and resolves."""

    async def test_config_skill_sync_enabled_false_forwarded_and_resolves(
        self, monkeypatch
    ) -> None:
        from amplifier_module_tool_graph_query import mount

        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SKILL_SYNC_ENABLED", raising=False)
        coordinator = MagicMock()
        coordinator.config = {}  # real dict so the resolver coordinator-level read is clean
        coordinator.mount = AsyncMock()

        await mount(coordinator, config={"skill_sync_enabled": False})

        tool = coordinator.mount.call_args.args[1]
        assert tool._config["skill_sync_enabled"] is False
        assert tool.skill_sync_enabled is False

    async def test_default_skill_sync_enabled_is_true(self, monkeypatch) -> None:
        from amplifier_module_tool_graph_query import mount

        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SKILL_SYNC_ENABLED", raising=False)
        coordinator = MagicMock()
        coordinator.config = {}
        coordinator.mount = AsyncMock()

        await mount(coordinator, config={})

        tool = coordinator.mount.call_args.args[1]
        assert tool.skill_sync_enabled is True
