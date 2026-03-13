"""Tests for ToolExecutionHandler — tool:pre, tool:post, tool:error, delegate:* events."""

from __future__ import annotations

import json

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

EXPECTED_TE1_ID = make_node_id("s1", "tool:pre", TOOL1_TIMESTAMP, disambiguator="call_001")
EXPECTED_TE2_ID = make_node_id("s1", "tool:pre", TOOL2_TIMESTAMP, disambiguator="call_002")
EXPECTED_TE3_ID = make_node_id("s1", "tool:pre", TOOL3_TIMESTAMP, disambiguator="call_003")


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
    return make_node_id(session_id, "tool:pre", timestamp, disambiguator=tool_call_id)


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

    async def test_parallel_with_edge_has_occurred_at(self, services: HookStateService) -> None:
        """PARALLEL_WITH edge should carry occurred_at matching the newer tool's timestamp."""
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
        # Second tool in same group — this creates the PARALLEL_WITH edge
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
        # PARALLEL_WITH edge should include occurred_at = TOOL2_TIMESTAMP
        edge = await services.graph.get_edge(EXPECTED_TE2_ID, EXPECTED_TE1_ID, "PARALLEL_WITH")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == TOOL2_TIMESTAMP


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
    """Tests for delegate:* no-op events (context_inherited, session_resumed)."""

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


# ── TestDelegateAgentSpawned (6 tests) ───────────────────────────────────


class TestDelegateAgentSpawned:
    """Granular tests for delegate:agent_spawned handler behaviour."""

    async def test_adds_delegation_label_to_tool_execution(
        self, services: HookStateService
    ) -> None:
        """delegate:agent_spawned adds 'Delegation' label alongside 'ToolExecution'."""
        te_id = await _seed_one_tool(services, tool_call_id="call_d1", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_d1",
                "child_session_id": "child-abc",
                "child_agent": "foundation:explorer",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert "ToolExecution" in node["labels"]
        assert "Delegation" in node["labels"]

    async def test_stores_child_session_id_and_child_agent(
        self, services: HookStateService
    ) -> None:
        """child_session_id and child_agent stored as TE node properties."""
        te_id = await _seed_one_tool(services, tool_call_id="call_d2", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_d2",
                "child_session_id": "child-xyz",
                "child_agent": "foundation:bug-hunter",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert node["properties"]["child_session_id"] == "child-xyz"
        assert node["properties"]["child_agent"] == "foundation:bug-hunter"

    async def test_creates_spawned_edge_to_child_session(self, services: HookStateService) -> None:
        """SPAWNED edge created from TE node to child session node."""
        te_id = await _seed_one_tool(services, tool_call_id="call_d3", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_d3",
                "child_session_id": "child-sess-99",
                "child_agent": "self",
            },
        )
        edge = await services.graph.get_edge(te_id, "child-sess-99", "SPAWNED")
        assert edge is not None

    async def test_empty_child_session_id_skips_spawned_edge(
        self, services: HookStateService
    ) -> None:
        """Empty child_session_id adds Delegation label but skips SPAWNED edge creation."""
        te_id = await _seed_one_tool(services, tool_call_id="call_d5", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_d5",
                "child_session_id": "",
                "child_agent": "foundation:explorer",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        # Delegation label is still added
        assert "Delegation" in node["labels"]
        # But no SPAWNED edge is created (empty child_session_id)
        edge = await services.graph.get_edge(te_id, "", "SPAWNED")
        assert edge is None

    async def test_missing_tool_call_id_gracefully_skips(self, services: HookStateService) -> None:
        """G3 gap: missing tool_call_id means no TE lookup; handler skips without adding Delegation label."""
        te_id = await _seed_one_tool(services, tool_call_id="call_d4", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        # Send agent_spawned with an unmapped tool_call_id (simulates G3 gap)
        result = await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "unmapped_call_999",
                "child_session_id": "child-orphan",
                "child_agent": "foundation:explorer",
            },
        )
        assert result.action == "continue"
        # The original TE should NOT have the Delegation label
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert "Delegation" not in node["labels"]

    async def test_missing_session_id_returns_continue(self, services: HookStateService) -> None:
        """delegate:agent_spawned without session_id returns continue gracefully."""
        handler = ToolExecutionHandler(services)
        result = await handler(
            "delegate:agent_spawned",
            {
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_001",
                "child_session_id": "child-no-parent",
                "child_agent": "foundation:explorer",
            },
        )
        assert result.action == "continue"

    async def test_spawned_edge_has_occurred_at(self, services: HookStateService) -> None:
        """SPAWNED edge should carry occurred_at from the delegate:agent_spawned timestamp."""
        te_id = await _seed_one_tool(services, tool_call_id="call_d9", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_d9",
                "child_session_id": "child-oat-99",
                "child_agent": "foundation:explorer",
            },
        )
        edge = await services.graph.get_edge(te_id, "child-oat-99", "SPAWNED")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == DELEGATE_SPAWNED_TIMESTAMP


# ── TestDelegateAgentCompleted (2 tests) ─────────────────────────────────


class TestDelegateAgentCompleted:
    """Granular tests for delegate:agent_completed handler behaviour."""

    async def test_enriches_with_delegate_completed_at(self, services: HookStateService) -> None:
        """delegate:agent_completed sets delegate_completed_at timestamp on TE node."""
        te_id = await _seed_one_tool(services, tool_call_id="call_c1", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_completed",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_COMPLETED_TIMESTAMP,
                "tool_call_id": "call_c1",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert node["properties"]["delegate_completed_at"] == DELEGATE_COMPLETED_TIMESTAMP

    async def test_missing_tool_call_id_returns_continue(self, services: HookStateService) -> None:
        """delegate:agent_completed with unmapped tool_call_id returns continue gracefully."""
        await _seed_one_tool(services, tool_call_id="call_c2", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        result = await handler(
            "delegate:agent_completed",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_COMPLETED_TIMESTAMP,
                "tool_call_id": "unmapped_call_888",
            },
        )
        assert result.action == "continue"


# ── TestToolPreDataProperty ────────────────────────────────────────────────


class TestToolPreDataProperty:
    """tool:pre node should store a 'data' property with the full event payload."""

    async def test_tool_pre_node_has_data_property(self, services: HookStateService) -> None:
        """tool:pre node must include 'data' as json.dumps of the full event payload."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": TOOL1_TIMESTAMP,
            "tool_call_id": "call_001",
            "tool_name": "read_file",
            "parallel_group_id": "pg1",
        }
        await handler("tool:pre", event_data)
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        props = node["properties"]
        assert "data" in props
        decoded = json.loads(props["data"])
        assert decoded["tool_name"] == "read_file"
        assert decoded["tool_call_id"] == "call_001"


# ── TestToolPostDataProperty ───────────────────────────────────────────────


class TestToolPostDataProperty:
    """tool:post must enrich TE node with 'data_tool_post' containing the full event payload."""

    async def test_tool_post_enriches_with_data_tool_post(self, services: HookStateService) -> None:
        """tool:post sets data_tool_post = json.dumps(data) on the TE node."""
        te_id = await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": TOOL_POST_TIMESTAMP,
            "tool_call_id": "call_001",
            "result": "file content here",
        }
        await handler("tool:post", event_data)
        node = await services.graph.get_node(te_id)
        assert node is not None
        props = node["properties"]
        assert "data_tool_post" in props
        decoded = json.loads(props["data_tool_post"])
        assert decoded["result"] == "file content here"


# ── TestToolErrorDataProperty ──────────────────────────────────────────────


class TestToolErrorDataProperty:
    """tool:error must enrich TE node with 'data_tool_error' containing the full event payload."""

    async def test_tool_error_enriches_with_data_tool_error(
        self, services: HookStateService
    ) -> None:
        """tool:error sets data_tool_error = json.dumps(data) on the TE node."""
        te_id = await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": TOOL_ERROR_TIMESTAMP,
            "tool_call_id": "call_001",
            "error": "File not found",
        }
        await handler("tool:error", event_data)
        node = await services.graph.get_node(te_id)
        assert node is not None
        props = node["properties"]
        assert "data_tool_error" in props
        decoded = json.loads(props["data_tool_error"])
        assert decoded["error"] == "File not found"


# ── TestDelegateAgentSpawnedDataProperty ──────────────────────────────────


class TestDelegateAgentSpawnedDataProperty:
    """delegate:agent_spawned must enrich TE node with 'data_delegate_agent_spawned'."""

    async def test_delegate_agent_spawned_enriches_with_data(
        self, services: HookStateService
    ) -> None:
        """delegate:agent_spawned sets data_delegate_agent_spawned = json.dumps(data) on TE node."""
        te_id = await _seed_one_tool(services, tool_call_id="call_d1", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
            "tool_call_id": "call_d1",
            "child_session_id": "child-abc",
            "child_agent": "foundation:explorer",
        }
        await handler("delegate:agent_spawned", event_data)
        node = await services.graph.get_node(te_id)
        assert node is not None
        props = node["properties"]
        assert "data_delegate_agent_spawned" in props
        decoded = json.loads(props["data_delegate_agent_spawned"])
        assert decoded["child_agent"] == "foundation:explorer"


# ── TestDelegateAgentCompletedDataProperty ────────────────────────────────


class TestDelegateAgentCompletedDataProperty:
    """delegate:agent_completed must enrich TE node with 'data_delegate_agent_completed'."""

    async def test_delegate_agent_completed_enriches_with_data(
        self, services: HookStateService
    ) -> None:
        """delegate:agent_completed sets data_delegate_agent_completed = json.dumps(data)."""
        te_id = await _seed_one_tool(services, tool_call_id="call_c1", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": DELEGATE_COMPLETED_TIMESTAMP,
            "tool_call_id": "call_c1",
        }
        await handler("delegate:agent_completed", event_data)
        node = await services.graph.get_node(te_id)
        assert node is not None
        props = node["properties"]
        assert "data_delegate_agent_completed" in props
        decoded = json.loads(props["data_delegate_agent_completed"])
        assert decoded["tool_call_id"] == "call_c1"


# — TestParallelToolsSameTimestamp (collision fix)————————————————————————————


class TestParallelToolsSameTimestamp:
    """Two parallel tool:pre events with the SAME timestamp but different tool_call_ids
    must produce TWO distinct ToolExecution nodes — not one merged node.
    This is the critical test that validates the collision fix (Root B).
    """

    SAME_TIMESTAMP = "2026-03-06T03:10:00Z"

    async def test_two_tools_same_timestamp_produce_distinct_nodes(
        self, services: HookStateService
    ) -> None:
        """Two tool:pre with identical timestamp but different tool_call_id → 2 nodes."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_AAAA",
                "tool_name": "bash",
                "parallel_group_id": "pg_same",
            },
        )
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_BBBB",
                "tool_name": "read_file",
                "parallel_group_id": "pg_same",
            },
        )

        # Both nodes must exist and be distinct
        te_a = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_AAAA")
        te_b = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_BBBB")

        node_a = await services.graph.get_node(te_a)
        node_b = await services.graph.get_node(te_b)
        assert node_a is not None, f"Node A not found: {te_a}"
        assert node_b is not None, f"Node B not found: {te_b}"
        assert te_a != te_b
        assert node_a["properties"]["tool_name"] == "bash"
        assert node_b["properties"]["tool_name"] == "read_file"

    async def test_parallel_with_edge_not_self_loop(self, services: HookStateService) -> None:
        """PARALLEL_WITH edge connects two DIFFERENT nodes (not a self-loop)."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_CCCC",
                "tool_name": "bash",
                "parallel_group_id": "pg_same",
            },
        )
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_DDDD",
                "tool_name": "read_file",
                "parallel_group_id": "pg_same",
            },
        )

        te_c = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_CCCC")
        te_d = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_DDDD")

        # PARALLEL_WITH edge should exist between them
        edge = await services.graph.get_edge(te_d, te_c, "PARALLEL_WITH")
        assert edge is not None, "PARALLEL_WITH edge missing"

        # Self-loop must NOT exist
        self_loop_c = await services.graph.get_edge(te_c, te_c, "PARALLEL_WITH")
        self_loop_d = await services.graph.get_edge(te_d, te_d, "PARALLEL_WITH")
        assert self_loop_c is None, "Self-loop on node C"
        assert self_loop_d is None, "Self-loop on node D"

    async def test_tool_call_map_uses_disambiguated_ids(self, services: HookStateService) -> None:
        """tool_call_map entries use the disambiguated node IDs."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_EEEE",
                "tool_name": "bash",
                "parallel_group_id": "pg_same",
            },
        )

        cursors = services.get_cursors("s1")
        expected_id = make_node_id(
            "s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_EEEE"
        )
        assert cursors.tool_call_map["toolu_EEEE"] == expected_id

    async def test_missing_tool_call_id_falls_back_to_old_format(
        self, services: HookStateService
    ) -> None:
        """When tool_call_id is empty, make_node_id uses the old format (no disambiguator)."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "",
                "tool_name": "bash",
                "parallel_group_id": "",
            },
        )

        old_format_id = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP)
        node = await services.graph.get_node(old_format_id)
        assert node is not None, "Fallback to old format should work when tool_call_id is empty"


# ── TestToolPreInputPreview ─────────────────────────────────────────────────


class TestToolPreInputPreview:
    """tool:pre node should store a 'tool_input_preview' property when tool_input is present."""

    async def test_tool_input_preview_stored_when_present(self, services: HookStateService) -> None:
        """tool_input present in event data → tool_input_preview stored on ToolExecution node."""
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
                "tool_input": {"file_path": "/tmp/test.txt"},
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        props = node["properties"]
        assert "tool_input_preview" in props
        assert props["tool_input_preview"] == str({"file_path": "/tmp/test.txt"})

    async def test_tool_input_preview_truncated_to_500(self, services: HookStateService) -> None:
        """tool_input longer than 500 chars should be truncated to RESULT_PREVIEW_MAX_LEN."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)
        long_input = "x" * 1000
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": TOOL1_TIMESTAMP,
                "tool_call_id": "call_001",
                "tool_name": "read_file",
                "parallel_group_id": "pg1",
                "tool_input": long_input,
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        assert len(node["properties"]["tool_input_preview"]) == RESULT_PREVIEW_MAX_LEN

    async def test_tool_input_preview_absent_when_tool_input_missing(
        self, services: HookStateService
    ) -> None:
        """When tool_input is absent from event data, tool_input_preview must NOT be set."""
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
                # no tool_input key
            },
        )
        node = await services.graph.get_node(EXPECTED_TE1_ID)
        assert node is not None
        assert "tool_input_preview" not in node["properties"]
