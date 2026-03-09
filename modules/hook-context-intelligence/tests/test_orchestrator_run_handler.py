"""Tests for OrchestratorRunHandler — prompt:submit and execution:start lifecycle."""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id

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
