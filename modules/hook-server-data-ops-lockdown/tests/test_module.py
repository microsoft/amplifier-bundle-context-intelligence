"""Unit tests for hook-server-data-ops-lockdown.

Verifies:
  - Module contract: __amplifier_module_type__ == "hook", mount() is a
    coroutine, mount() registers a `tool:pre` handler and returns a cleanup
    callable.
  - The handler denies exactly the four lockdown tools (write_file,
    edit_file, apply_patch, graph_query) with a HookResult(action="deny",
    reason=...).
  - The handler allows every other tool call server-data-ops actually
    makes (session_summary, delete_session, whoami, delegate) plus a
    read-only sentinel (read_file), returning HookResult(action="continue").
  - Session-scope composition: agents/server-data-ops.md declares the
    companion settings.exclude_hooks fix (see TestSessionScopeComposition
    below and this module's own docstring for why the handler itself
    cannot do this -- the tool:pre payload carries no session/agent
    identity to check against).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from amplifier_core.models import HookResult

from amplifier_module_hook_server_data_ops_lockdown import (
    DENIED_TOOLS,
    DENY_REASON,
    _deny_lockdown_tools,
    mount,
)

# modules/hook-server-data-ops-lockdown/tests/test_module.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_DATA_OPS_AGENT = REPO_ROOT / "agents" / "server-data-ops.md"


def _make_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock(name="unregister"))
    return coordinator


class TestModuleContract:
    def test_module_type_is_hook(self) -> None:
        from amplifier_module_hook_server_data_ops_lockdown import (
            __amplifier_module_type__,
        )

        assert __amplifier_module_type__ == "hook"

    def test_mount_is_coroutine(self) -> None:
        assert inspect.iscoroutinefunction(mount)

    def test_denied_tools_are_exactly_the_four_named(self) -> None:
        assert DENIED_TOOLS == frozenset({"write_file", "edit_file", "apply_patch", "graph_query"})

    @pytest.mark.asyncio
    async def test_mount_registers_tool_pre_handler(self) -> None:
        coordinator = _make_coordinator()

        await mount(coordinator, {})

        coordinator.hooks.register.assert_called_once()
        args, kwargs = coordinator.hooks.register.call_args
        assert args[0] == "tool:pre"
        assert args[1] is _deny_lockdown_tools
        assert kwargs.get("priority") == 10

    @pytest.mark.asyncio
    async def test_mount_returns_cleanup_that_unregisters(self) -> None:
        coordinator = _make_coordinator()
        unregister_fn = MagicMock(name="unregister")
        coordinator.hooks.register.return_value = unregister_fn

        cleanup = await mount(coordinator, {})
        assert callable(cleanup)

        cleanup()
        unregister_fn.assert_called_once()


class TestDenyLockdownTools:
    """Direct handler tests -- mirrors HOOK_CONTRACT.md's own test pattern:
    `await handler("tool:pre", {"tool_name": ..., "tool_input": ...})`.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["write_file", "edit_file", "apply_patch", "graph_query"])
    async def test_denies_each_lockdown_tool(self, tool_name: str) -> None:
        result = await _deny_lockdown_tools("tool:pre", {"tool_name": tool_name, "tool_input": {}})

        assert isinstance(result, HookResult)
        assert result.action == "deny"
        assert result.reason == DENY_REASON

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name",
        [
            "session_summary",
            "delete_session",
            "whoami",
            "delegate",
            "read_file",
            "load_skill",
            "todo",
        ],
    )
    async def test_allows_every_other_tool(self, tool_name: str) -> None:
        result = await _deny_lockdown_tools("tool:pre", {"tool_name": tool_name, "tool_input": {}})

        assert isinstance(result, HookResult)
        assert result.action == "continue"
        assert result.reason is None

    @pytest.mark.asyncio
    async def test_missing_tool_name_is_allowed(self) -> None:
        """Defensive: a data dict with no tool_name key must never be denied
        (missing information is not evidence of a lockdown-tool call)."""
        result = await _deny_lockdown_tools("tool:pre", {})

        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_deny_reason_is_plain_and_explains_delegation(self) -> None:
        result: Any = await _deny_lockdown_tools(
            "tool:pre", {"tool_name": "graph_query", "tool_input": {}}
        )

        assert "delete agent" in result.reason
        assert "graph-analyst" in result.reason


class TestSessionScopeComposition:
    """Proves the session-scoping fix for the subtree leak this module's own
    docstring documents (a DTU eval caught it: graph-analyst's legitimate
    graph_query calls were denied whenever server-data-ops delegated search
    to it, because this hook -- mounted on server-data-ops's own session --
    was ALSO inherited by every session server-data-ops spawned).

    The handler under test (`_deny_lockdown_tools`) has zero session/agent
    awareness -- it only ever inspects `tool_name` -- and the `tool:pre`
    event's documented payload (core:docs/contracts/ORCHESTRATOR_CONTRACT.md,
    HOOK_CONTRACT.md) carries no session or agent identity to check
    against, so the handler structurally cannot distinguish
    "server-data-ops's own call" from "a descendant session's call". These
    tests cannot exercise a real delegate spawn (that requires the
    app-layer session_spawner.py, only exercised in a DTU); instead they
    verify the actual fix -- agents/server-data-ops.md's own tool-delegate
    config excluding this hook module from inheritance -- is in place.
    """

    @staticmethod
    def _server_data_ops_tools() -> dict[str, dict[str, Any]]:
        text = SERVER_DATA_OPS_AGENT.read_text(encoding="utf-8")
        _, frontmatter, _ = text.split("---", 2)
        config = yaml.safe_load(frontmatter)
        return {t["module"]: t for t in config.get("tools", [])}

    @staticmethod
    def _server_data_ops_hooks() -> dict[str, dict[str, Any]]:
        text = SERVER_DATA_OPS_AGENT.read_text(encoding="utf-8")
        _, frontmatter, _ = text.split("---", 2)
        config = yaml.safe_load(frontmatter)
        return {h["module"]: h for h in config.get("hooks", [])}

    def test_agent_declares_this_hook(self) -> None:
        """Sanity check: server-data-ops still mounts this hook module for
        its own session (the hook is meaningless if this ever drops)."""
        hooks = self._server_data_ops_hooks()

        assert "hook-server-data-ops-lockdown" in hooks

    def test_agent_excludes_this_hook_from_delegated_sessions(self) -> None:
        """The actual fix: tool-delegate's settings.exclude_hooks must name
        this hook's own module id, so a spawned child (graph-analyst,
        session-navigator, ...) never inherits it."""
        tools = self._server_data_ops_tools()

        assert "tool-delegate" in tools, "server-data-ops must declare tool-delegate in tools:"
        settings = tools["tool-delegate"].get("config", {}).get("settings", {})
        excluded_hooks = settings.get("exclude_hooks", [])

        assert "hook-server-data-ops-lockdown" in excluded_hooks, (
            "settings.exclude_hooks must list this hook's own module id, or the "
            "subtree leak documented in this module's docstring reopens"
        )

    def test_agent_does_not_mount_graph_query_tool(self) -> None:
        """Corroborates this module's docstring claim (verified against
        history at commit a897c2d): server-data-ops's own tools: list never
        declares tool-context-intelligence-query, so it never has graph_query
        available to call directly -- all searching goes through graph-analyst
        via delegation instead."""
        tools = self._server_data_ops_tools()

        assert "tool-context-intelligence-query" not in tools
