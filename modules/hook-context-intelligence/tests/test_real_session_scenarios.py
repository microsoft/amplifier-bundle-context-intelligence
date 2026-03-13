"""Integration tests using real event sequences from production Amplifier sessions.

Source session: 44be6956-a0bb-4dfb-8e79-837c9c6d57c4
Project: context-intelligence-bundle-v2
Date: 2026-03-06
Orchestrator: loop-streaming
Model: claude-opus-4-6

These tests replay real event data through the full handler pipeline to verify
that production event sequences produce correct graph structures end-to-end.
Timestamps are truncated to µs precision (Python datetime resolution).
"""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.handlers.step import StepHandler
from amplifier_module_hook_context_intelligence.handlers.tool_execution import (
    ToolExecutionHandler,
)
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id

# ═══════════════════════════════════════════════════════════════════════════
# Constants from REAL session data
# ═══════════════════════════════════════════════════════════════════════════

REAL_SESSION_ID = "44be6956-a0bb-4dfb-8e79-837c9c6d57c4"

# Real turn: "proceed with full verification" — 8 iterations, 15 tool calls
TURN_PROMPT = "proceed with full verification"
TURN_PROMPT_TIMESTAMP = "2026-03-06T19:04:59.286390+00:00"
TURN_EXEC_START_TIMESTAMP = "2026-03-06T19:04:59.286390+00:00"

# Real provider:request iterations (timestamps truncated from ns → µs)
ITERATIONS = [
    {"iteration": 1, "provider": "anthropic", "timestamp": "2026-03-06T19:04:59.288039+00:00"},
    {"iteration": 2, "provider": "anthropic", "timestamp": "2026-03-06T19:05:10.297646+00:00"},
    {"iteration": 3, "provider": "anthropic", "timestamp": "2026-03-06T19:05:23.270894+00:00"},
]

# Real llm:request/response data
LLM_MODEL = "claude-opus-4-6"
# Raw Amplifier usage format (keys: input, output, cache_write, cache_read).
# Handler expects input_tokens/output_tokens/cache_read_input_tokens — tests
# translate the values at event-construction time.
LLM_RESPONSE_USAGE_1 = {"cache_write": 124657, "input": 3, "output": 488}
LLM_RESPONSE_USAGE_2 = {"cache_read": 90483, "cache_write": 35056, "input": 3, "output": 522}

# Real parallel tool group — 3 parallel bash calls.
# Tool 2 timestamp nudged +1 ms (original shared .926 with tool 1, which would
# cause a make_node_id collision at ms resolution).
PARALLEL_GROUP_ID = "95086541-d8aa-4006-bf5e-6741522b9c82"
TOOL_CALLS = [
    {
        "tool_call_id": "toolu_01DmwReiPwc4NwWFC8CxBwbe",
        "tool_name": "bash",
        "timestamp": "2026-03-06T19:05:18.926+00:00",
    },
    {
        "tool_call_id": "toolu_01SpmCcHzrnkbeVwu2cEzoWe",
        "tool_name": "bash",
        "timestamp": "2026-03-06T19:05:18.927+00:00",
    },
    {
        "tool_call_id": "toolu_01SiZgWFrAbj1eSST2hPKbYT",
        "tool_name": "bash",
        "timestamp": "2026-03-06T19:05:18.928+00:00",
    },
]

# Real delegation event data
DELEGATE_TOOL_CALL_ID = "toolu_011KkxRcgRkeuL2sAYh6gJ67"
DELEGATE_PARALLEL_GROUP = "2df46d41-faa9-495c-9049-8d4ead25b087"
DELEGATE_AGENT = "foundation:git-ops"
DELEGATE_CHILD_SESSION = "0000000000000000-156afb681ee04058_foundation-git-ops"
DELEGATE_TIMESTAMP = "2026-03-06T19:09:41.143060+00:00"
DELEGATE_SPAWNED_TIMESTAMP = "2026-03-06T19:09:41.146113+00:00"
DELEGATE_COMPLETED_TIMESTAMP = "2026-03-06T19:10:58.228205+00:00"

# orchestrator:complete data
ORCHESTRATOR_COMPLETE_TIMESTAMP = "2026-03-06T19:06:59.742140+00:00"
ORCHESTRATOR_TURN_COUNT = 8

# ── Derived timestamps (infill for events not in the raw extract) ─────────

SESSION_START_TIMESTAMP = "2026-03-06T19:00:00+00:00"
EXEC_END_TIMESTAMP = "2026-03-06T19:06:50.000000+00:00"
LLM_REQ_TIMESTAMP_1 = "2026-03-06T19:05:00.000000+00:00"
LLM_RESP_TIMESTAMP_1 = "2026-03-06T19:05:05.000000+00:00"
LLM_REQ_TIMESTAMP_2 = "2026-03-06T19:05:12.000000+00:00"
LLM_RESP_TIMESTAMP_2 = "2026-03-06T19:05:17.000000+00:00"

# Turn 2 timestamps for multi-turn test
TURN2_PROMPT_TIMESTAMP = "2026-03-06T19:12:00.000000+00:00"
TURN2_EXEC_START_TIMESTAMP = "2026-03-06T19:12:00.100000+00:00"
TURN2_PROVIDER_TIMESTAMP = "2026-03-06T19:12:01.000000+00:00"
TURN2_EXEC_END_TIMESTAMP = "2026-03-06T19:13:00.000000+00:00"
TURN2_COMPLETE_TIMESTAMP = "2026-03-06T19:13:01.000000+00:00"

# tool:post timestamps (one per parallel tool)
TOOL_POST_TIMESTAMPS = [
    "2026-03-06T19:05:20.100+00:00",
    "2026-03-06T19:05:20.200+00:00",
    "2026-03-06T19:05:20.300+00:00",
]


# ═══════════════════════════════════════════════════════════════════════════
# Seed helpers
# ═══════════════════════════════════════════════════════════════════════════


async def _seed_session(services: HookStateService) -> None:
    """Create a Session node via SessionHandler with the real session ID."""
    handler = SessionHandler(services)
    await handler(
        "session:start",
        {"session_id": REAL_SESSION_ID, "timestamp": SESSION_START_TIMESTAMP},
    )


async def _seed_run(services: HookStateService) -> str:
    """Seed session → prompt:submit → execution:start; return run node ID."""
    await _seed_session(services)
    run_handler = OrchestratorRunHandler(services)
    await run_handler(
        "prompt:submit",
        {
            "session_id": REAL_SESSION_ID,
            "timestamp": TURN_PROMPT_TIMESTAMP,
            "prompt": TURN_PROMPT,
        },
    )
    await run_handler(
        "execution:start",
        {"session_id": REAL_SESSION_ID, "timestamp": TURN_EXEC_START_TIMESTAMP},
    )
    run_id = services.get_cursors(REAL_SESSION_ID).current_run_id
    assert run_id is not None
    return run_id


async def _seed_step(services: HookStateService) -> str:
    """Seed session → run → first provider:request; return step node ID."""
    await _seed_run(services)
    step_handler = StepHandler(services)
    await step_handler(
        "provider:request",
        {
            "session_id": REAL_SESSION_ID,
            "timestamp": ITERATIONS[0]["timestamp"],
            "iteration": ITERATIONS[0]["iteration"],
            "provider": ITERATIONS[0]["provider"],
        },
    )
    step_id = services.get_cursors(REAL_SESSION_ID).current_step_id
    assert step_id is not None
    return step_id


# ═══════════════════════════════════════════════════════════════════════════
# TestRealTurnAssembly
# ═══════════════════════════════════════════════════════════════════════════


class TestRealTurnAssembly:
    """Replays an entire turn from the real session and verifies the full
    graph structure: Session → HAS_RUN → OrchestratorRun → HAS_STEP (×N)
    → Steps linked by NEXT chain.
    """

    async def test_full_turn_creates_orchestrator_run(self, services: HookStateService) -> None:
        """prompt:submit + execution:start creates an OrchestratorRun node."""
        run_id = await _seed_run(services)
        node = await services.graph.get_node(run_id)
        assert node is not None
        assert node["labels"] == {"OrchestratorRun"}
        props = node["properties"]
        assert props["run_number"] == 1
        assert props["started_at"] == TURN_EXEC_START_TIMESTAMP
        assert props["status"] == "in_progress"

    async def test_full_turn_graph_structure(self, services: HookStateService) -> None:
        """Full event sequence produces correct node/edge graph with NEXT chain."""
        await _seed_session(services)
        run_handler = OrchestratorRunHandler(services)
        step_handler = StepHandler(services)

        # prompt:submit + execution:start
        await run_handler(
            "prompt:submit",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": TURN_PROMPT_TIMESTAMP,
                "prompt": TURN_PROMPT,
            },
        )
        await run_handler(
            "execution:start",
            {"session_id": REAL_SESSION_ID, "timestamp": TURN_EXEC_START_TIMESTAMP},
        )

        run_id = make_node_id(REAL_SESSION_ID, "execution:start", TURN_EXEC_START_TIMESTAMP)
        prompt_step_id = make_node_id(REAL_SESSION_ID, "prompt:submit", TURN_PROMPT_TIMESTAMP)

        # 3 provider:request iterations
        step_ids: list[str] = []
        for it in ITERATIONS:
            await step_handler(
                "provider:request",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": it["timestamp"],
                    "iteration": it["iteration"],
                    "provider": it["provider"],
                },
            )
            step_ids.append(make_node_id(REAL_SESSION_ID, "provider:request", it["timestamp"]))

        # execution:end + orchestrator:complete
        await run_handler(
            "execution:end",
            {"session_id": REAL_SESSION_ID, "timestamp": EXEC_END_TIMESTAMP},
        )
        await run_handler(
            "orchestrator:complete",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": ORCHESTRATOR_COMPLETE_TIMESTAMP,
                "status": "success",
                "turn_count": ORCHESTRATOR_TURN_COUNT,
            },
        )

        # ── Session → HAS_RUN → OrchestratorRun ──
        has_run = await services.graph.get_edge(REAL_SESSION_ID, run_id, "HAS_RUN")
        assert has_run is not None
        assert has_run["properties"]["seq"] == 1

        # ── OrchestratorRun → HAS_STEP (PromptStep at seq=0) ──
        has_step_prompt = await services.graph.get_edge(run_id, prompt_step_id, "HAS_STEP")
        assert has_step_prompt is not None
        assert has_step_prompt["properties"]["seq"] == 0

        # ── OrchestratorRun → HAS_STEP (AssistantSteps at seq=1,2,3) ──
        for i, sid in enumerate(step_ids, start=1):
            edge = await services.graph.get_edge(run_id, sid, "HAS_STEP")
            assert edge is not None, f"Missing HAS_STEP for step {i}"
            assert edge["properties"]["seq"] == i

        # ── NEXT chain: PromptStep → Step1 → Step2 → Step3 ──
        assert await services.graph.get_edge(prompt_step_id, step_ids[0], "NEXT") is not None
        assert await services.graph.get_edge(step_ids[0], step_ids[1], "NEXT") is not None
        assert await services.graph.get_edge(step_ids[1], step_ids[2], "NEXT") is not None

    async def test_orchestrator_run_final_properties(self, services: HookStateService) -> None:
        """After orchestrator:complete, run has status=complete and turn_count=8."""
        run_id = await _seed_run(services)
        run_handler = OrchestratorRunHandler(services)

        await run_handler(
            "execution:end",
            {"session_id": REAL_SESSION_ID, "timestamp": EXEC_END_TIMESTAMP},
        )
        await run_handler(
            "orchestrator:complete",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": ORCHESTRATOR_COMPLETE_TIMESTAMP,
                "status": "success",
                "turn_count": ORCHESTRATOR_TURN_COUNT,
            },
        )

        node = await services.graph.get_node(run_id)
        assert node is not None
        props = node["properties"]
        assert props["run_number"] == 1
        assert props["started_at"] == TURN_EXEC_START_TIMESTAMP
        assert props["status"] == "complete"
        assert props["turn_count"] == ORCHESTRATOR_TURN_COUNT
        assert props["ended_at"] == ORCHESTRATOR_COMPLETE_TIMESTAMP
        assert props["execution_ended_at"] == EXEC_END_TIMESTAMP

    async def test_step_properties_from_real_iterations(self, services: HookStateService) -> None:
        """Each AssistantStep has correct iteration, provider, and model."""
        await _seed_run(services)
        step_handler = StepHandler(services)

        for it in ITERATIONS:
            await step_handler(
                "provider:request",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": it["timestamp"],
                    "iteration": it["iteration"],
                    "provider": it["provider"],
                },
            )
            await step_handler(
                "llm:request",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": it["timestamp"],
                    "model": LLM_MODEL,
                },
            )

        for it in ITERATIONS:
            step_id = make_node_id(REAL_SESSION_ID, "provider:request", it["timestamp"])
            node = await services.graph.get_node(step_id)
            assert node is not None
            assert node["labels"] == {"Step", "AssistantStep"}
            assert node["properties"]["iteration"] == it["iteration"]
            assert node["properties"]["provider"] == it["provider"]
            assert node["properties"]["model"] == LLM_MODEL

    async def test_prompt_step_stores_real_prompt(self, services: HookStateService) -> None:
        """PromptStep node stores the real prompt text."""
        await _seed_run(services)
        prompt_step_id = make_node_id(REAL_SESSION_ID, "prompt:submit", TURN_PROMPT_TIMESTAMP)
        node = await services.graph.get_node(prompt_step_id)
        assert node is not None
        assert node["labels"] == {"Step", "PromptStep"}
        assert node["properties"]["prompt_text"] == TURN_PROMPT
        assert node["properties"]["prompt_preview"] == TURN_PROMPT


# ═══════════════════════════════════════════════════════════════════════════
# TestRealParallelToolBatch
# ═══════════════════════════════════════════════════════════════════════════


class TestRealParallelToolBatch:
    """Replays 3 parallel bash tool calls and verifies the PARALLEL_WITH mesh."""

    async def test_three_tool_executions_created(self, services: HookStateService) -> None:
        """3 tool:pre events with same parallel_group_id create 3 ToolExecution nodes."""
        await _seed_step(services)
        tool_handler = ToolExecutionHandler(services)

        te_ids: list[str] = []
        for tc in TOOL_CALLS:
            await tool_handler(
                "tool:pre",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": tc["timestamp"],
                    "tool_call_id": tc["tool_call_id"],
                    "tool_name": tc["tool_name"],
                    "parallel_group_id": PARALLEL_GROUP_ID,
                },
            )
            te_ids.append(make_node_id(REAL_SESSION_ID, "tool:pre", tc["timestamp"], disambiguator=tc["tool_call_id"]))

        for te_id in te_ids:
            node = await services.graph.get_node(te_id)
            assert node is not None
            assert node["labels"] == {"ToolExecution"}
            assert node["properties"]["tool_name"] == "bash"
            assert node["properties"]["status"] == "executing"

    async def test_triggered_edges_from_step(self, services: HookStateService) -> None:
        """3 TRIGGERED edges exist from the current AssistantStep."""
        step_id = await _seed_step(services)
        tool_handler = ToolExecutionHandler(services)

        for tc in TOOL_CALLS:
            await tool_handler(
                "tool:pre",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": tc["timestamp"],
                    "tool_call_id": tc["tool_call_id"],
                    "tool_name": tc["tool_name"],
                    "parallel_group_id": PARALLEL_GROUP_ID,
                },
            )

        for tc in TOOL_CALLS:
            te_id = make_node_id(REAL_SESSION_ID, "tool:pre", tc["timestamp"], disambiguator=tc["tool_call_id"])
            edge = await services.graph.get_edge(step_id, te_id, "TRIGGERED")
            assert edge is not None, f"Missing TRIGGERED edge for {tc['tool_call_id']}"

    async def test_parallel_with_complete_mesh(self, services: HookStateService) -> None:
        """Complete PARALLEL_WITH mesh: tc2→tc1, tc3→tc1, tc3→tc2."""
        await _seed_step(services)
        tool_handler = ToolExecutionHandler(services)

        te_ids: list[str] = []
        for tc in TOOL_CALLS:
            await tool_handler(
                "tool:pre",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": tc["timestamp"],
                    "tool_call_id": tc["tool_call_id"],
                    "tool_name": tc["tool_name"],
                    "parallel_group_id": PARALLEL_GROUP_ID,
                },
            )
            te_ids.append(make_node_id(REAL_SESSION_ID, "tool:pre", tc["timestamp"], disambiguator=tc["tool_call_id"]))

        # tc2 → tc1 (second tool links to first)
        assert await services.graph.get_edge(te_ids[1], te_ids[0], "PARALLEL_WITH") is not None
        # tc3 → tc1 (third tool links to first)
        assert await services.graph.get_edge(te_ids[2], te_ids[0], "PARALLEL_WITH") is not None
        # tc3 → tc2 (third tool links to second)
        assert await services.graph.get_edge(te_ids[2], te_ids[1], "PARALLEL_WITH") is not None

    async def test_tool_post_completes_all(self, services: HookStateService) -> None:
        """3 tool:post events set status=complete on all ToolExecution nodes."""
        await _seed_step(services)
        tool_handler = ToolExecutionHandler(services)

        te_ids: list[str] = []
        for tc in TOOL_CALLS:
            await tool_handler(
                "tool:pre",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": tc["timestamp"],
                    "tool_call_id": tc["tool_call_id"],
                    "tool_name": tc["tool_name"],
                    "parallel_group_id": PARALLEL_GROUP_ID,
                },
            )
            te_ids.append(make_node_id(REAL_SESSION_ID, "tool:pre", tc["timestamp"], disambiguator=tc["tool_call_id"]))

        for i, tc in enumerate(TOOL_CALLS):
            await tool_handler(
                "tool:post",
                {
                    "session_id": REAL_SESSION_ID,
                    "timestamp": TOOL_POST_TIMESTAMPS[i],
                    "tool_call_id": tc["tool_call_id"],
                    "result": f"output from bash invocation {i + 1}",
                },
            )

        for i, te_id in enumerate(te_ids):
            node = await services.graph.get_node(te_id)
            assert node is not None
            assert node["properties"]["status"] == "complete"
            assert node["properties"]["ended_at"] == TOOL_POST_TIMESTAMPS[i]
            assert "output from bash" in node["properties"]["result_preview"]


# ═══════════════════════════════════════════════════════════════════════════
# TestRealDelegationChain
# ═══════════════════════════════════════════════════════════════════════════


class TestRealDelegationChain:
    """Replays the full delegation sequence: tool:pre → agent_spawned → agent_completed."""

    async def _fire_delegation_sequence(self, services: HookStateService) -> str:
        """Run the full delegation event sequence and return TE node ID."""
        await _seed_step(services)
        tool_handler = ToolExecutionHandler(services)

        await tool_handler(
            "tool:pre",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": DELEGATE_TIMESTAMP,
                "tool_call_id": DELEGATE_TOOL_CALL_ID,
                "tool_name": "delegate",
                "parallel_group_id": DELEGATE_PARALLEL_GROUP,
            },
        )
        te_id = make_node_id(REAL_SESSION_ID, "tool:pre", DELEGATE_TIMESTAMP, disambiguator=DELEGATE_TOOL_CALL_ID)

        await tool_handler(
            "delegate:agent_spawned",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": DELEGATE_TOOL_CALL_ID,
                "child_session_id": DELEGATE_CHILD_SESSION,
                "child_agent": DELEGATE_AGENT,
            },
        )

        await tool_handler(
            "delegate:agent_completed",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": DELEGATE_COMPLETED_TIMESTAMP,
                "tool_call_id": DELEGATE_TOOL_CALL_ID,
            },
        )
        return te_id

    async def test_delegation_label_added(self, services: HookStateService) -> None:
        """ToolExecution gets :Delegation label after agent_spawned."""
        te_id = await self._fire_delegation_sequence(services)
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert "ToolExecution" in node["labels"]
        assert "Delegation" in node["labels"]

    async def test_spawned_edge_to_child_session(self, services: HookStateService) -> None:
        """SPAWNED edge created from ToolExecution to child session ID."""
        te_id = await self._fire_delegation_sequence(services)
        edge = await services.graph.get_edge(te_id, DELEGATE_CHILD_SESSION, "SPAWNED")
        assert edge is not None

    async def test_delegate_completed_at_set(self, services: HookStateService) -> None:
        """delegate:agent_completed writes delegate_completed_at timestamp."""
        te_id = await self._fire_delegation_sequence(services)
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert node["properties"]["delegate_completed_at"] == DELEGATE_COMPLETED_TIMESTAMP

    async def test_child_session_and_agent_stored(self, services: HookStateService) -> None:
        """child_session_id and child_agent stored as TE node properties."""
        te_id = await self._fire_delegation_sequence(services)
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert node["properties"]["child_session_id"] == DELEGATE_CHILD_SESSION
        assert node["properties"]["child_agent"] == DELEGATE_AGENT

    async def test_tool_name_is_delegate(self, services: HookStateService) -> None:
        """The TE tool_name is 'delegate' as in the real event."""
        te_id = await self._fire_delegation_sequence(services)
        node = await services.graph.get_node(te_id)
        assert node is not None
        assert node["properties"]["tool_name"] == "delegate"


# ═══════════════════════════════════════════════════════════════════════════
# TestRealMultiTurnSession
# ═══════════════════════════════════════════════════════════════════════════


async def _replay_turn(
    services: HookStateService,
    *,
    prompt: str,
    prompt_ts: str,
    exec_start_ts: str,
    provider_ts: str,
    exec_end_ts: str,
    complete_ts: str,
    turn_count: int | None = None,
) -> None:
    """Fire a full turn sequence: prompt → exec_start → provider → exec_end → complete."""
    run_handler = OrchestratorRunHandler(services)
    step_handler = StepHandler(services)

    await run_handler(
        "prompt:submit",
        {"session_id": REAL_SESSION_ID, "timestamp": prompt_ts, "prompt": prompt},
    )
    await run_handler(
        "execution:start",
        {"session_id": REAL_SESSION_ID, "timestamp": exec_start_ts},
    )
    await step_handler(
        "provider:request",
        {
            "session_id": REAL_SESSION_ID,
            "timestamp": provider_ts,
            "iteration": 1,
            "provider": "anthropic",
        },
    )
    await run_handler(
        "execution:end",
        {"session_id": REAL_SESSION_ID, "timestamp": exec_end_ts},
    )
    complete_data: dict = {
        "session_id": REAL_SESSION_ID,
        "timestamp": complete_ts,
        "status": "success",
    }
    if turn_count is not None:
        complete_data["turn_count"] = turn_count
    await run_handler("orchestrator:complete", complete_data)


class TestRealMultiTurnSession:
    """Replays TWO complete turns and verifies multi-run structure."""

    async def test_two_runs_with_sequential_run_numbers(self, services: HookStateService) -> None:
        """Two turns produce 2 OrchestratorRun nodes: run_number 1 and 2."""
        await _seed_session(services)

        await _replay_turn(
            services,
            prompt=TURN_PROMPT,
            prompt_ts=TURN_PROMPT_TIMESTAMP,
            exec_start_ts=TURN_EXEC_START_TIMESTAMP,
            provider_ts=ITERATIONS[0]["timestamp"],
            exec_end_ts=EXEC_END_TIMESTAMP,
            complete_ts=ORCHESTRATOR_COMPLETE_TIMESTAMP,
            turn_count=ORCHESTRATOR_TURN_COUNT,
        )
        await _replay_turn(
            services,
            prompt="continue",
            prompt_ts=TURN2_PROMPT_TIMESTAMP,
            exec_start_ts=TURN2_EXEC_START_TIMESTAMP,
            provider_ts=TURN2_PROVIDER_TIMESTAMP,
            exec_end_ts=TURN2_EXEC_END_TIMESTAMP,
            complete_ts=TURN2_COMPLETE_TIMESTAMP,
        )

        run1_id = make_node_id(REAL_SESSION_ID, "execution:start", TURN_EXEC_START_TIMESTAMP)
        run2_id = make_node_id(REAL_SESSION_ID, "execution:start", TURN2_EXEC_START_TIMESTAMP)

        run1 = await services.graph.get_node(run1_id)
        run2 = await services.graph.get_node(run2_id)
        assert run1 is not None
        assert run2 is not None
        assert run1["properties"]["run_number"] == 1
        assert run2["properties"]["run_number"] == 2

    async def test_has_run_edges_with_sequential_seq(self, services: HookStateService) -> None:
        """Session → HAS_RUN edges have seq=1 and seq=2."""
        await _seed_session(services)

        await _replay_turn(
            services,
            prompt=TURN_PROMPT,
            prompt_ts=TURN_PROMPT_TIMESTAMP,
            exec_start_ts=TURN_EXEC_START_TIMESTAMP,
            provider_ts=ITERATIONS[0]["timestamp"],
            exec_end_ts=EXEC_END_TIMESTAMP,
            complete_ts=ORCHESTRATOR_COMPLETE_TIMESTAMP,
        )
        await _replay_turn(
            services,
            prompt="continue",
            prompt_ts=TURN2_PROMPT_TIMESTAMP,
            exec_start_ts=TURN2_EXEC_START_TIMESTAMP,
            provider_ts=TURN2_PROVIDER_TIMESTAMP,
            exec_end_ts=TURN2_EXEC_END_TIMESTAMP,
            complete_ts=TURN2_COMPLETE_TIMESTAMP,
        )

        run1_id = make_node_id(REAL_SESSION_ID, "execution:start", TURN_EXEC_START_TIMESTAMP)
        run2_id = make_node_id(REAL_SESSION_ID, "execution:start", TURN2_EXEC_START_TIMESTAMP)

        edge1 = await services.graph.get_edge(REAL_SESSION_ID, run1_id, "HAS_RUN")
        edge2 = await services.graph.get_edge(REAL_SESSION_ID, run2_id, "HAS_RUN")
        assert edge1 is not None
        assert edge2 is not None
        assert edge1["properties"]["seq"] == 1
        assert edge2["properties"]["seq"] == 2

    async def test_cursor_cleared_after_both_turns(self, services: HookStateService) -> None:
        """current_run_id is None after each turn completes."""
        await _seed_session(services)

        await _replay_turn(
            services,
            prompt=TURN_PROMPT,
            prompt_ts=TURN_PROMPT_TIMESTAMP,
            exec_start_ts=TURN_EXEC_START_TIMESTAMP,
            provider_ts=ITERATIONS[0]["timestamp"],
            exec_end_ts=EXEC_END_TIMESTAMP,
            complete_ts=ORCHESTRATOR_COMPLETE_TIMESTAMP,
        )

        cursors = services.get_cursors(REAL_SESSION_ID)
        assert cursors.current_run_id is None  # cleared after turn 1

        await _replay_turn(
            services,
            prompt="continue",
            prompt_ts=TURN2_PROMPT_TIMESTAMP,
            exec_start_ts=TURN2_EXEC_START_TIMESTAMP,
            provider_ts=TURN2_PROVIDER_TIMESTAMP,
            exec_end_ts=TURN2_EXEC_END_TIMESTAMP,
            complete_ts=TURN2_COMPLETE_TIMESTAMP,
        )

        cursors = services.get_cursors(REAL_SESSION_ID)
        assert cursors.current_run_id is None  # cleared after turn 2

    async def test_both_runs_are_complete(self, services: HookStateService) -> None:
        """Both OrchestratorRun nodes end with status=complete."""
        await _seed_session(services)

        await _replay_turn(
            services,
            prompt=TURN_PROMPT,
            prompt_ts=TURN_PROMPT_TIMESTAMP,
            exec_start_ts=TURN_EXEC_START_TIMESTAMP,
            provider_ts=ITERATIONS[0]["timestamp"],
            exec_end_ts=EXEC_END_TIMESTAMP,
            complete_ts=ORCHESTRATOR_COMPLETE_TIMESTAMP,
        )
        await _replay_turn(
            services,
            prompt="continue",
            prompt_ts=TURN2_PROMPT_TIMESTAMP,
            exec_start_ts=TURN2_EXEC_START_TIMESTAMP,
            provider_ts=TURN2_PROVIDER_TIMESTAMP,
            exec_end_ts=TURN2_EXEC_END_TIMESTAMP,
            complete_ts=TURN2_COMPLETE_TIMESTAMP,
        )

        run1_id = make_node_id(REAL_SESSION_ID, "execution:start", TURN_EXEC_START_TIMESTAMP)
        run2_id = make_node_id(REAL_SESSION_ID, "execution:start", TURN2_EXEC_START_TIMESTAMP)

        for run_id in (run1_id, run2_id):
            node = await services.graph.get_node(run_id)
            assert node is not None
            assert node["properties"]["status"] == "complete"


# ═══════════════════════════════════════════════════════════════════════════
# TestRealUsageAccumulation
# ═══════════════════════════════════════════════════════════════════════════


class TestRealUsageAccumulation:
    """Tests LLM response enrichment with real token counts from the session."""

    async def test_usage_without_cache_read(self, services: HookStateService) -> None:
        """Usage format {input, output, cache_write} — no cached_tokens stored."""
        step_id = await _seed_step(services)
        step_handler = StepHandler(services)

        await step_handler(
            "llm:request",
            {"session_id": REAL_SESSION_ID, "timestamp": LLM_REQ_TIMESTAMP_1, "model": LLM_MODEL},
        )
        # Translate raw Amplifier keys → handler-expected keys using real values
        await step_handler(
            "llm:response",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": LLM_RESP_TIMESTAMP_1,
                "usage": {
                    "input_tokens": LLM_RESPONSE_USAGE_1["input"],
                    "output_tokens": LLM_RESPONSE_USAGE_1["output"],
                },
                "finish_reason": "end_turn",
            },
        )

        node = await services.graph.get_node(step_id)
        assert node is not None
        props = node["properties"]
        assert props["model"] == LLM_MODEL
        assert props["input_tokens"] == 3
        assert props["output_tokens"] == 488
        assert props["finish_reason"] == "end_turn"
        assert props["response_at"] == LLM_RESP_TIMESTAMP_1
        # cache_write is not extracted by the handler; no cache_read → no cached_tokens
        assert "cached_tokens" not in props

    async def test_usage_with_cache_read(self, services: HookStateService) -> None:
        """Usage format {input, output, cache_read, cache_write} — cached_tokens stored."""
        step_id = await _seed_step(services)
        step_handler = StepHandler(services)

        await step_handler(
            "llm:request",
            {"session_id": REAL_SESSION_ID, "timestamp": LLM_REQ_TIMESTAMP_2, "model": LLM_MODEL},
        )
        await step_handler(
            "llm:response",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": LLM_RESP_TIMESTAMP_2,
                "usage": {
                    "input_tokens": LLM_RESPONSE_USAGE_2["input"],
                    "output_tokens": LLM_RESPONSE_USAGE_2["output"],
                    "cache_read_input_tokens": LLM_RESPONSE_USAGE_2["cache_read"],
                },
                "finish_reason": "end_turn",
            },
        )

        node = await services.graph.get_node(step_id)
        assert node is not None
        props = node["properties"]
        assert props["model"] == LLM_MODEL
        assert props["input_tokens"] == 3
        assert props["output_tokens"] == 522
        assert props["cached_tokens"] == 90483
        assert props["finish_reason"] == "end_turn"
        assert props["response_at"] == LLM_RESP_TIMESTAMP_2

    async def test_response_at_always_written(self, services: HookStateService) -> None:
        """llm:response always sets response_at regardless of usage content."""
        step_id = await _seed_step(services)
        step_handler = StepHandler(services)

        await step_handler(
            "llm:response",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": LLM_RESP_TIMESTAMP_1,
                "usage": {
                    "input_tokens": LLM_RESPONSE_USAGE_1["input"],
                    "output_tokens": LLM_RESPONSE_USAGE_1["output"],
                },
            },
        )

        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["response_at"] == LLM_RESP_TIMESTAMP_1

    async def test_stop_reason_fallback(self, services: HookStateService) -> None:
        """stop_reason is used as finish_reason when finish_reason is absent."""
        step_id = await _seed_step(services)
        step_handler = StepHandler(services)

        await step_handler(
            "llm:response",
            {
                "session_id": REAL_SESSION_ID,
                "timestamp": LLM_RESP_TIMESTAMP_1,
                "usage": {
                    "input_tokens": LLM_RESPONSE_USAGE_1["input"],
                    "output_tokens": LLM_RESPONSE_USAGE_1["output"],
                },
                "stop_reason": "end_turn",
            },
        )

        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["finish_reason"] == "end_turn"
