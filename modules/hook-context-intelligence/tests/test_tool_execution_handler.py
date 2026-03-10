"""Tests for ToolExecutionHandler — tool:pre, tool:post, tool:error, delegate:* events."""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.handlers.step import StepHandler
from amplifier_module_hook_context_intelligence.handlers.tool_execution import (
    RESULT_PREVIEW_MAX_LEN,
    ToolExecutionHandler,
)
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id

# ── Constants ────────────────────────────────────────────────────────────

SESSION_TIMESTAMP = "2026-03-06T00:00:00Z"
PROMPT_TIMESTAMP = "2026-03-06T01:00:00Z"
EXEC_TIMESTAMP = "2026-03-06T02:00:00Z"
STEP1_TIMESTAMP = "2026-03-06T03:00:00Z"
TOOL1_TIMESTAMP = "2026-03-06T03:10:00Z"
TOOL2_TIMESTAMP = "2026-03-06T03:20:00Z"
TOOL3_TIMESTAMP = "2026-03-06T03:30:00Z"
TOOL_POST_TIMESTAMP = "2026-03-06T03:40:00Z"
TOOL_ERROR_TIMESTAMP = "2026-03-06T03:50:00Z"
DELEGATE_SPAWNED_TIMESTAMP = "2026-03-06T04:00:00Z"
DELEGATE_COMPLETED_TIMESTAMP = "2026-03-06T04:10:00Z"

EXPECTED_TE1_ID = make_node_id("s1", "tool:pre", TOOL1_TIMESTAMP)
EXPECTED_TE2_ID = make_node_id("s1", "tool:pre", TOOL2_TIMESTAMP)
EXPECTED_TE3_ID = make_node_id("s1", "tool:pre", TOOL3_TIMESTAMP)


# ── Helpers ──────────────────────────────────────────────────────────────


async def _seed_through_step(services: HookStateService, session_id: str = "s1") -> str:
    """Create Session + prompt:submit + execution:start + provider:request so we have a current step."""
    session_handler = SessionHandler(services)
    await session_handler(
        "session:start",
        {"session_id": session_id, "timestamp": SESSION_TIMESTAMP},
    )
    run_handler = OrchestratorRunHandler(services)
    await run_handler(
        "prompt:submit",
        {"session_id": session_id, "timestamp": PROMPT_TIMESTAMP, "prompt": "Hello"},
    )
    await run_handler(
        "execution:start",
        {"session_id": session_id, "timestamp": EXEC_TIMESTAMP},
    )
    step_handler = StepHandler(services)
    await step_handler(
        "provider:request",
        {
            "session_id": session_id,
            "timestamp": STEP1_TIMESTAMP,
            "iteration": 1,
            "provider": "anthropic",
        },
    )
    return services.get_cursors(session_id).current_step_id  # type: ignore[return-value]


async def _seed_one_tool(
    services: HookStateService,
    session_id: str = "s1",
    tool_call_id: str = "call_001",
    tool_name: str = "read_file",
    parallel_group_id: str = "pg1",
    timestamp: str = TOOL1_TIMESTAMP,
) -> str:
    """Seed session through step and create one ToolExecution via tool:pre.

    Returns the ToolExecution node ID.
    """
    await _seed_through_step(services, session_id)
    handler = ToolExecutionHandler(services)
    await handler(
        "tool:pre",
        {
            "session_id": session_id,
            "timestamp": timestamp,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "parallel_group_id": parallel_group_id,
        },
    )
    return make_node_id(session_id, "tool:pre", timestamp)


# ── TestToolPreHappyPath (6 tests) ──────────────────────────────────────


class TestToolPreHappyPath:
    async def test_creates_tool_execution_node(self, services: HookStateService) -> None:
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None

    async def test_correct_labels(self, services: HookStateService) -> None:
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        assert node["labels"] == {"ToolExecution"}

    async def test_node_properties(self, services: HookStateService) -> None:
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        props = node["properties"]
        assert props["tool_call_id"] == "call_001"
        assert props["tool_name"] == "read_file"
        assert props["parallel_group_id"] == "pg1"
        assert props["started_at"] == TOOL1_TIMESTAMP
        assert props["status"] == "executing"
        assert props["session_id"] == "s1"

    async def test_triggered_edge_from_step(self, services: HookStateService) -> None:
        step_id = await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        edge = await services.graph.get_edge(step_id, EXPECTED_TE1_ID, "TRIGGERED")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == TOOL1_TIMESTAMP
        # step_counter is 1 after provider:request
        assert edge["properties"]["seq"] == 1

    async def test_tool_call_map_populated(self, services: HookStateService) -> None:
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        cursors = services.get_cursors("s1")
        assert cursors.tool_call_map["call_001"] == EXPECTED_TE1_ID

    async def test_returns_hook_result_continue(self, services: HookStateService) -> None:
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        result = await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        assert result.action == "continue"


# ── TestToolPreParallelWith (3 tests) ───────────────────────────────────


class TestToolPreParallelWith:
    async def test_solo_tool_no_parallel_with(self, services: HookStateService) -> None:
        """A single tool in a group should have no PARALLEL_WITH edges."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        # No PARALLEL_WITH edge should exist in either direction
        edge_fwd = await services.graph.get_edge(EXPECTED_TE1_ID, EXPECTED_TE1_ID, "PARALLEL_WITH")
        assert edge_fwd is None

    async def test_two_tools_same_group_get_parallel_with(self, services: HookStateService) -> None:
        """Two tools in the same parallel_group_id get a PARALLEL_WITH edge (new→existing)."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        # First tool
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        # Second tool in same group
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL2_TIMESTAMP,
                "tool_call_id": "call_002",
                "tool_name": "grep",
                "parallel_group_id": "pg1",
            },
        )
        # new→existing: TE2→TE1
        edge = await services.graph.get_edge(EXPECTED_TE2_ID, EXPECTED_TE1_ID, "PARALLEL_WITH")
        assert edge is not None

    async def test_different_groups_no_parallel_with(self, services: HookStateService) -> None:
        """Tools in different parallel_group_ids should NOT get PARALLEL_WITH edges."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        # Tool in group A
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pgA",
            },
        )
        # Tool in group B
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL2_TIMESTAMP,
                "tool_call_id": "call_002",
                "tool_name": "grep",
                "parallel_group_id": "pgB",
            },
        )
        # No PARALLEL_WITH between them
        edge_fwd = await services.graph.get_edge(EXPECTED_TE2_ID, EXPECTED_TE1_ID, "PARALLEL_WITH")
        edge_rev = await services.graph.get_edge(EXPECTED_TE1_ID, EXPECTED_TE2_ID, "PARALLEL_WITH")
        assert edge_fwd is None
        assert edge_rev is None


# ── TestToolPreErrorPaths (2 tests) ─────────────────────────────────────


class TestToolPreErrorPaths:
    async def test_graceful_when_no_current_step_id(self, services: HookStateService) -> None:
        """tool:pre without a current_step_id should not crash."""
        # Create session but no provider:request, so current_step_id is None
        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": "s1", "timestamp": SESSION_TIMESTAMP},
        )
        handler = ToolExecutionHandler(services)
        result = await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        assert result.action == "continue"

    async def test_graceful_when_missing_session_id(self, services: HookStateService) -> None:
        """tool:pre without session_id should not crash."""
        handler = ToolExecutionHandler(services)
        result = await handler(
            "tool:pre",
            {
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
            },
        )
        assert result.action == "continue"


# ── TestToolPost ────────────────────────────────────────────────────────


class TestToolPost:
    async def test_enriches_with_status_ended_at_result_preview(
        self, services: HookStateService
    ) -> None:
        te_id = await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:post",
            {
                "session_id": "s1",
                "timestamp": TOOL_POST_TIMESTAMP,
                "tool_call_id": "call_001",
                "result": "file content here",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        props = node["properties"]
        assert props["status"] == "complete"
        assert props["ended_at"] == TOOL_POST_TIMESTAMP
        assert props["result_preview"] == "file content here"

    async def test_result_preview_truncated_to_500(self, services: HookStateService) -> None:
        te_id = await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        long_result = "x" * 1000
        await handler(
            "tool:post",
            {
                "session_id": "s1",
                "timestamp": TOOL_POST_TIMESTAMP,
                "tool_call_id": "call_001",
                "result": long_result,
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert len(node["properties"]["result_preview"]) == RESULT_PREVIEW_MAX_LEN

    async def test_missing_tool_call_id_mapping_returns_continue(
        self, services: HookStateService
    ) -> None:
        """tool:post with an unmapped tool_call_id should return continue gracefully."""
        await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        result = await handler(
            "tool:post",
            {
                "session_id": "s1",
                "timestamp": TOOL_POST_TIMESTAMP,
                "tool_call_id": "unknown_call_999",
                "result": "some result",
            },
        )
        assert result.action == "continue"

    async def test_missing_session_id_returns_continue(self, services: HookStateService) -> None:
        """tool:post without session_id should return continue gracefully."""
        handler = ToolExecutionHandler(services)
        result = await handler(
            "tool:post",
            {
                "timestamp": TOOL_POST_TIMESTAMP,
                "tool_call_id": "call_001",
                "result": "some result",
            },
        )
        assert result.action == "continue"


# ── TestToolError ───────────────────────────────────────────────────────


class TestToolError:
    async def test_sets_status_error_with_message_and_ended_at(
        self, services: HookStateService
    ) -> None:
        te_id = await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:error",
            {
                "session_id": "s1",
                "timestamp": TOOL_ERROR_TIMESTAMP,
                "tool_call_id": "call_001",
                "error": "File not found",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        props = node["properties"]
        assert props["status"] == "error"
        assert props["ended_at"] == TOOL_ERROR_TIMESTAMP
        assert props["error"] == "File not found"

    async def test_missing_tool_call_id_mapping_returns_continue(
        self, services: HookStateService
    ) -> None:
        """tool:error with an unmapped tool_call_id should return continue gracefully."""
        await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        result = await handler(
            "tool:error",
            {
                "session_id": "s1",
                "timestamp": TOOL_ERROR_TIMESTAMP,
                "tool_call_id": "unknown_call_999",
                "error": "Something broke",
            },
        )
        assert result.action == "continue"


# ── TestDelegateEvents ──────────────────────────────────────────────────


class TestDelegateEvents:
    async def test_agent_spawned_adds_delegation_label_and_spawned_edge(
        self, services: HookStateService
    ) -> None:
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "delegate",
                "parallel_group_id": "pg1",
            },
        )
        await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_001",
                "child_session_id": "child-s1",
                "child_agent": "foundation:explorer",
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        assert "Delegation" in node["labels"]
        assert node["properties"]["child_session_id"] == "child-s1"
        assert node["properties"]["child_agent"] == "foundation:explorer"

        # SPAWNED edge to child session node
        edge = await services.graph.get_edge(EXPECTED_TE1_ID, "child-s1", "SPAWNED")
        assert edge is not None

    async def test_agent_completed_enriches_node(self, services: HookStateService) -> None:
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "delegate",
                "parallel_group_id": "pg1",
            },
        )
        await handler(
            "delegate:agent_completed",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_COMPLETED_TIMESTAMP,
                "tool_call_id": "call_001",
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        assert node["properties"]["delegate_completed_at"] == DELEGATE_COMPLETED_TIMESTAMP

    async def test_context_inherited_is_noop(self, services: HookStateService) -> None:
        handler = ToolExecutionHandler(services)
        result = await handler(
            "delegate:context_inherited",
            {"session_id": "s1", "timestamp": DELEGATE_SPAWNED_TIMESTAMP},
        )
        assert result.action == "continue"

    async def test_session_resumed_is_noop(self, services: HookStateService) -> None:
        handler = ToolExecutionHandler(services)
        result = await handler(
            "delegate:session_resumed",
            {"session_id": "s1", "timestamp": DELEGATE_SPAWNED_TIMESTAMP},
        )
        assert result.action == "continue"
