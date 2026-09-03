"""Unit tests for hook-server-data-ops-lockdown.

Verifies the module's actual runtime behaviour:
  - Module contract: __amplifier_module_type__ == "hook", mount() is a
    coroutine, mount() registers a `tool:pre` handler and returns a cleanup
    callable.
  - The handler denies exactly the four lockdown tools (write_file,
    edit_file, apply_patch, graph_query) with a HookResult(action="deny",
    reason=...).
  - The handler denies a `delegate` call whose target agent is not exactly
    ALLOWED_DELEGATE_AGENT ("context-intelligence:graph-analyst") -- this is
    the delegation-bypass fix: a behavioral DTU test proved server-data-ops
    running as the ROOT agent (no parent to enforce its `agents:`
    frontmatter allowlist) could delegate to foundation:file-ops and have it
    write a file to disk unchecked.
  - The handler allows every other tool call (session_summary,
    delete_session, whoami, read_file, load_skill, todo), returning
    HookResult(action="continue").

Deliberately NOT tested here: that the agent .md frontmatter contains a given
string (declares the hook, excludes it from delegated sessions, or pins the
delegation allowlist). Those are config assertions that only prove "the YAML
says what we typed" -- they give false confidence and fail only if someone
edits the same line the test reads. The real guarantees they gestured at --
the hook does not leak into graph-analyst's session, and server-data-ops
cannot hand a file-write to another agent -- are behavioural properties,
proven in the DTU security-validation profile, not by grepping a file.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest
from amplifier_core.models import HookResult

from amplifier_module_hook_server_data_ops_lockdown import (
    ALLOWED_DELEGATE_AGENT,
    DELEGATE_DENY_REASON,
    DENIED_TOOLS,
    DENY_REASON,
    _deny_lockdown_tools,
    mount,
)


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
            "read_file",
            "load_skill",
            "todo",
        ],
    )
    async def test_allows_every_other_tool(self, tool_name: str) -> None:
        """`delegate` is deliberately excluded from this list -- it has its
        own target-checked behaviour, covered by TestDelegateTargetLockdown
        below, and an empty tool_input (as used here) would now be denied
        (fail closed on a missing `agent` field)."""
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


class TestDelegateTargetLockdown:
    """Direct handler tests for the delegation-bypass fix.

    A behavioral DTU test proved server-data-ops, running as the ROOT agent
    (no parent session to enforce its `agents:` frontmatter allowlist), could
    call `delegate(agent="foundation:file-ops")` and have it write a file to
    disk unchecked. These tests prove the handler itself now closes that hole,
    independent of any frontmatter allowlist.
    """

    @pytest.mark.asyncio
    async def test_delegate_to_allowed_agent_is_allowed(self) -> None:
        result = await _deny_lockdown_tools(
            "tool:pre",
            {
                "tool_name": "delegate",
                "tool_input": {"agent": ALLOWED_DELEGATE_AGENT, "instruction": "search"},
            },
        )

        assert isinstance(result, HookResult)
        assert result.action == "continue"
        assert result.reason is None

    @pytest.mark.asyncio
    async def test_delegate_to_allowed_agent_is_the_namespaced_graph_analyst(self) -> None:
        """Pin the exact allowed string so a future edit that changes it is
        caught here, not just discovered behaviorally."""
        assert ALLOWED_DELEGATE_AGENT == "context-intelligence:graph-analyst"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "target_agent",
        [
            "foundation:file-ops",
            "self",
            "graph-analyst",  # bare (unnamespaced) form -- must NOT match
            "context-intelligence:server-data-ops",
            "context-intelligence:session-navigator",
        ],
    )
    async def test_delegate_to_any_other_agent_is_denied(self, target_agent: str) -> None:
        result = await _deny_lockdown_tools(
            "tool:pre",
            {
                "tool_name": "delegate",
                "tool_input": {"agent": target_agent, "instruction": "do something"},
            },
        )

        assert isinstance(result, HookResult)
        assert result.action == "deny"
        assert result.reason == DELEGATE_DENY_REASON

    @pytest.mark.asyncio
    async def test_delegate_with_missing_agent_field_is_denied(self) -> None:
        """Fail closed: e.g. a resume-by-session_id call that omits `agent`
        entirely must be denied, not allowed through."""
        result = await _deny_lockdown_tools(
            "tool:pre",
            {
                "tool_name": "delegate",
                "tool_input": {"session_id": "abc123", "instruction": "continue"},
            },
        )

        assert isinstance(result, HookResult)
        assert result.action == "deny"
        assert result.reason == DELEGATE_DENY_REASON

    @pytest.mark.asyncio
    async def test_delegate_with_empty_agent_field_is_denied(self) -> None:
        """Fail closed: an explicit empty string is not evidence it targets
        the allowed agent."""
        result = await _deny_lockdown_tools(
            "tool:pre",
            {"tool_name": "delegate", "tool_input": {"agent": "", "instruction": "do something"}},
        )

        assert isinstance(result, HookResult)
        assert result.action == "deny"
        assert result.reason == DELEGATE_DENY_REASON

    @pytest.mark.asyncio
    async def test_delegate_with_missing_tool_input_is_denied(self) -> None:
        """Fail closed: no tool_input key at all (malformed/edge-case event
        payload) must not be treated as an allowed delegate."""
        result = await _deny_lockdown_tools("tool:pre", {"tool_name": "delegate"})

        assert isinstance(result, HookResult)
        assert result.action == "deny"
        assert result.reason == DELEGATE_DENY_REASON

    @pytest.mark.asyncio
    async def test_denied_tools_still_deny_even_though_delegate_check_exists(self) -> None:
        """Regression guard: adding the delegate-target branch must not
        change the outright deny behaviour for the original four tools."""
        for tool_name in DENIED_TOOLS:
            result = await _deny_lockdown_tools(
                "tool:pre", {"tool_name": tool_name, "tool_input": {}}
            )
            assert result.action == "deny"
            assert result.reason == DENY_REASON
