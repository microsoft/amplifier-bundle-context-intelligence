"""Tests for OrchestratorRunHandler — full lifecycle events."""

from __future__ import annotations

import json

from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    PREVIEW_MAX_LEN,
    OrchestratorRunHandler,
    _STATUS_MAP,
)
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id

# Verify _STATUS_MAP constant matches spec
assert _STATUS_MAP == {"success": "complete", "cancelled": "cancelled", "error": "error"}

TIMESTAMP = "2026-03-06T01:00:00Z"
EXPECTED_NODE_ID = "s1__prompt_submit__1772758800000"


async def _seed_session(services: HookStateService, session_id: str = "s1") -> None:
    """Create a Session node via SessionHandler so it exists in the graph."""
    session_handler = SessionHandler(services)
    await session_handler(
        "session:start",
        {
            "session_id": session_id,
            "timestamp": "2026-03-06T00:00:00Z",
        },
    )


# ── Happy-path tests ─────────────────────────────────────────────────


class TestPromptSubmitHappyPath:
    async def test_creates_node(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None

    async def test_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["labels"] == {"Step", "PromptStep"}

    async def test_stores_prompt_text(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello world"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["properties"]["prompt_text"] == "Hello world"

    async def test_stores_prompt_preview(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello world"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["properties"]["prompt_preview"] == "Hello world"

    async def test_preview_truncated_to_200_chars(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        long_prompt = "x" * 300
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": long_prompt},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["properties"]["prompt_preview"] == "x" * 200
        assert node["properties"]["prompt_text"] == long_prompt

    async def test_properties(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        props = node["properties"]
        assert props["iteration"] == 0
        assert props["occurred_at"] == TIMESTAMP
        assert props["session_id"] == "s1"

    async def test_no_edges_created(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        # No HAS_STEP edge should be created from session to prompt step
        edge = await services.graph.get_edge("s1", EXPECTED_NODE_ID, "HAS_STEP")
        assert edge is None

    async def test_updates_cursors(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        # Set initial cursor state to verify changes
        cursors = services.get_cursors("s1")
        cursors.run_counter = 2
        cursors.step_counter = 5
        cursors.current_step_id = "old_step"
        cursors.prompt_preview = "old preview"

        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello world"},
        )

        cursors = services.get_cursors("s1")
        assert cursors.run_counter == 3  # incremented
        assert cursors.step_counter == 0  # reset
        assert cursors.current_step_id == EXPECTED_NODE_ID  # set to new node
        assert cursors.prompt_preview == "Hello world"  # stored

    async def test_node_id_matches_make_node_id(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        expected = make_node_id("s1", "prompt:submit", TIMESTAMP)
        assert expected == EXPECTED_NODE_ID
        node = await services.graph.get_node(expected)
        assert node is not None

    async def test_returns_hook_result_continue(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        assert result.action == "continue"


# ── Error-path tests ─────────────────────────────────────────────────


class TestPromptSubmitErrorPaths:
    async def test_missing_session_id_returns_continue(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "prompt:submit",
            {"timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        assert result.action == "continue"

    async def test_missing_session_id_creates_no_nodes(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        # No PromptStep node should exist
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is None

    async def test_session_not_found_returns_continue(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        # session_id provided but no session node seeded
        result = await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        assert result.action == "continue"

    async def test_session_not_found_creates_no_nodes(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hi"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is None


# ── execution:start constants ────────────────────────────────────────────

EXEC_TIMESTAMP = "2026-03-06T02:00:00Z"
EXPECTED_RUN_NODE_ID = make_node_id("s1", "execution:start", EXEC_TIMESTAMP)


async def _seed_session_and_prompt(services: HookStateService) -> None:
    """Seed a session and a prompt:submit so cursors are populated."""
    await _seed_session(services)
    handler = OrchestratorRunHandler(services)
    await handler(
        "prompt:submit",
        {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello world"},
    )


# ── execution:start happy-path tests ─────────────────────────────────────


class TestExecutionStartHappyPath:
    async def test_creates_node(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        node = await services.graph.get_node(EXPECTED_RUN_NODE_ID)
        assert node is not None

    async def test_correct_labels(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        node = await services.graph.get_node(EXPECTED_RUN_NODE_ID)
        assert node is not None
        assert node["labels"] == {"OrchestratorRun"}

    async def test_properties(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        node = await services.graph.get_node(EXPECTED_RUN_NODE_ID)
        assert node is not None
        props = node["properties"]
        assert props["run_number"] == 1  # run_counter set by prompt:submit
        assert props["started_at"] == EXEC_TIMESTAMP
        assert props["status"] == "in_progress"
        assert props["prompt_preview"] == "Hello world"
        assert props["session_id"] == "s1"

    async def test_has_run_edge(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        edge = await services.graph.get_edge("s1", EXPECTED_RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["properties"]["seq"] == 1  # matches run_counter
        assert edge["properties"]["occurred_at"] == EXEC_TIMESTAMP

    async def test_has_step_edge_to_prompt_step(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        # Grab current_step_id from cursors (set by prompt:submit)
        cursors = services.get_cursors("s1")
        prompt_step_id = cursors.current_step_id
        assert prompt_step_id is not None

        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        edge = await services.graph.get_edge(EXPECTED_RUN_NODE_ID, prompt_step_id, "HAS_STEP")
        assert edge is not None
        assert edge["properties"]["seq"] == 0
        assert edge["properties"]["occurred_at"] == EXEC_TIMESTAMP

    async def test_updates_current_run_id(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        cursors = services.get_cursors("s1")
        assert cursors.current_run_id == EXPECTED_RUN_NODE_ID

    async def test_returns_continue(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        assert result.action == "continue"


# ── execution:start error-path tests ─────────────────────────────────────


class TestExecutionStartErrorPaths:
    async def test_missing_session_id_returns_continue(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "execution:start",
            {"timestamp": EXEC_TIMESTAMP},
        )
        assert result.action == "continue"


# ── execution:end constants ────────────────────────────────────────────

END_TIMESTAMP = "2026-03-06T03:00:00Z"


async def _seed_full_run(services: HookStateService) -> str:
    """Seed session + prompt + execution:start, return run node ID."""
    await _seed_session_and_prompt(services)
    handler = OrchestratorRunHandler(services)
    await handler(
        "execution:start",
        {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
    )
    return EXPECTED_RUN_NODE_ID


# ── execution:end tests ───────────────────────────────────────────────


class TestExecutionEnd:
    async def test_enriches_with_timestamp(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:end",
            {"session_id": "s1", "timestamp": END_TIMESTAMP},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        assert node["properties"]["execution_ended_at"] == END_TIMESTAMP

    async def test_preserves_existing_status(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:end",
            {"session_id": "s1", "timestamp": END_TIMESTAMP},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        # Status should still be "in_progress" from execution:start — NOT changed
        assert node["properties"]["status"] == "in_progress"

    async def test_stores_response_preview(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        long_response = "y" * 300
        await handler(
            "execution:end",
            {
                "session_id": "s1",
                "timestamp": END_TIMESTAMP,
                "response": long_response,
            },
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        assert node["properties"]["response_preview"] == "y" * PREVIEW_MAX_LEN

    async def test_graceful_when_no_current_run(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        # No execution:start fired, so current_run_id is None
        result = await handler(
            "execution:end",
            {"session_id": "s1", "timestamp": END_TIMESTAMP},
        )
        assert result.action == "continue"

    async def test_missing_session_id(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "execution:end",
            {"timestamp": END_TIMESTAMP},
        )
        assert result.action == "continue"


# ── orchestrator:complete constants ────────────────────────────────────

COMPLETE_TIMESTAMP = "2026-03-06T04:00:00Z"


# ── orchestrator:complete tests ────────────────────────────────────────


class TestOrchestratorComplete:
    async def test_closes_with_complete_status(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "success"},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        assert node["properties"]["status"] == "complete"
        assert node["properties"]["ended_at"] == COMPLETE_TIMESTAMP

    async def test_maps_cancelled_status(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "cancelled"},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        assert node["properties"]["status"] == "cancelled"

    async def test_maps_error_status(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "error"},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        assert node["properties"]["status"] == "error"

    async def test_stores_turn_count(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "orchestrator:complete",
            {
                "session_id": "s1",
                "timestamp": COMPLETE_TIMESTAMP,
                "status": "success",
                "turn_count": 7,
            },
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        assert node["properties"]["turn_count"] == 7

    async def test_clears_current_run_id(self, services: HookStateService) -> None:
        await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        cursors = services.get_cursors("s1")
        assert cursors.current_run_id is not None  # sanity check

        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "success"},
        )
        assert cursors.current_run_id is None

    async def test_clears_tool_call_map(self, services: HookStateService) -> None:
        await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        cursors = services.get_cursors("s1")
        cursors.tool_call_map["call_1"] = "tool_node_1"  # seed some data

        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "success"},
        )
        assert cursors.tool_call_map == {}

    async def test_graceful_when_no_current_run(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        # No execution:start fired, so current_run_id is None
        result = await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "success"},
        )
        assert result.action == "continue"

    async def test_missing_session_id(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "orchestrator:complete",
            {"timestamp": COMPLETE_TIMESTAMP, "status": "success"},
        )
        assert result.action == "continue"

    async def test_unknown_status_passes_through(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "timeout"},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        # Unknown status not in _STATUS_MAP passes through unchanged
        assert node["properties"]["status"] == "timeout"

    async def test_defaults_to_success_when_status_missing(
        self, services: HookStateService
    ) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        # No status in event data → defaults to 'success' → maps to 'complete'
        assert node["properties"]["status"] == "complete"


# ── data property tests ─────────────────────────────────────────────────────


class TestPromptSubmitDataProperty:
    async def test_stores_data_property(self, services: HookStateService) -> None:
        """prompt:submit stores full event dict as 'data' JSON property."""
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        event_data = {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello world"}
        await handler("prompt:submit", event_data)
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        stored_data = json.loads(node["properties"]["data"])
        assert stored_data["session_id"] == "s1"
        assert stored_data["prompt"] == "Hello world"

    async def test_data_is_complete_event_clone(self, services: HookStateService) -> None:
        """data property preserves extra fields not otherwise used by the handler."""
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": TIMESTAMP,
            "prompt": "Hello",
            "extra_field": "preserved",
        }
        await handler("prompt:submit", event_data)
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        stored_data = json.loads(node["properties"]["data"])
        assert stored_data["extra_field"] == "preserved"


class TestExecutionStartDataProperty:
    async def test_stores_data_property(self, services: HookStateService) -> None:
        """execution:start stores full event dict as 'data' JSON property."""
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        event_data = {"session_id": "s1", "timestamp": EXEC_TIMESTAMP}
        await handler("execution:start", event_data)
        node = await services.graph.get_node(EXPECTED_RUN_NODE_ID)
        assert node is not None
        stored_data = json.loads(node["properties"]["data"])
        assert stored_data["session_id"] == "s1"
        assert stored_data["timestamp"] == EXEC_TIMESTAMP


class TestExecutionEndDataProperty:
    async def test_stores_data_execution_end_property(self, services: HookStateService) -> None:
        """execution:end enriches OrchestratorRun with 'data_execution_end' JSON property."""
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        event_data = {"session_id": "s1", "timestamp": END_TIMESTAMP, "response": "some response"}
        await handler("execution:end", event_data)
        node = await services.graph.get_node(run_id)
        assert node is not None
        stored_data = json.loads(node["properties"]["data_execution_end"])
        assert stored_data["session_id"] == "s1"
        assert stored_data["timestamp"] == END_TIMESTAMP
        assert stored_data["response"] == "some response"


class TestOrchestratorCompleteDataProperty:
    async def test_stores_data_and_calls_flush(self, services: HookStateService) -> None:
        """orchestrator:complete enriches with 'data_orchestrator_complete' and calls flush()."""
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": COMPLETE_TIMESTAMP,
            "status": "success",
        }

        # Track flush calls via a simple counter wrapper
        flush_call_count = 0
        original_flush = services.graph.flush

        async def tracking_flush() -> None:
            nonlocal flush_call_count
            flush_call_count += 1
            await original_flush()

        services.graph.flush = tracking_flush  # type: ignore[method-assign]

        await handler("orchestrator:complete", event_data)

        node = await services.graph.get_node(run_id)
        assert node is not None
        stored_data = json.loads(node["properties"]["data_orchestrator_complete"])
        assert stored_data["session_id"] == "s1"
        assert stored_data["status"] == "success"
        assert flush_call_count == 1, "flush() must be called exactly once on orchestrator:complete"


# ── Problem 4: Orphan Session node investigation ───────────────────────────


class TestOrchestratorCompleteDoesNotCreateOrphan:
    """orchestrator:complete must enrich the existing OrchestratorRun node,
    not create a spurious Session node with the run's node_id.

    Investigation for Problem 4: orphan Session node created during
    orchestrator:complete.  The session-guarantee wrapper in mount.py
    calls ensure_session_node(session_id, data) for every event.  If
    session_id in the orchestrator:complete payload were somehow the
    synthetic run node_id (containing '__' separators) rather than the
    real session UUID, ensure_session_node would stamp Session labels
    onto the OrchestratorRun node.

    This test verifies the unit-test scenario is clean.  If the test
    passes without a code fix, the orphan is a Neo4j async-flush race
    that is not reproducible in unit tests (scenario c from the spec).
    """

    async def test_no_orphan_session_node_after_orchestrator_complete(
        self, services: HookStateService
    ) -> None:
        """The full lifecycle should not create a Session node with the run's node_id."""
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)

        # Create the OrchestratorRun
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": "2026-03-06T01:00:00Z", "prompt": "Hello"},
        )
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": "2026-03-06T02:00:00Z"},
        )

        run_id = services.get_cursors("s1").current_run_id
        assert run_id is not None

        # Close the run
        await handler(
            "orchestrator:complete",
            {
                "session_id": "s1",
                "timestamp": "2026-03-06T03:00:00Z",
                "status": "success",
                "turn_count": 1,
            },
        )

        # The OrchestratorRun should be updated with status=complete
        run_node = await services.graph.get_node(run_id)
        assert run_node is not None
        assert run_node["properties"]["status"] == "complete"

        # There should NOT be a Session node with node_id = run_id
        orphan = await services.graph.get_node(run_id)
        if orphan is not None:
            # The node exists (it's the OrchestratorRun), but it must NOT have Session labels
            assert "Session" not in orphan["labels"], (
                f"Orphan Session node detected: a node with run_id {run_id} has Session label"
            )
