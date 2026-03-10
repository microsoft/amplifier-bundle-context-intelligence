"""Tests for StepHandler — provider:request, llm:request, llm:response, content_block:* events."""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.handlers.step import StepHandler
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id

# ── Constants ────────────────────────────────────────────────────────────

SESSION_TIMESTAMP = "2026-03-06T00:00:00Z"
PROMPT_TIMESTAMP = "2026-03-06T01:00:00Z"
EXEC_TIMESTAMP = "2026-03-06T02:00:00Z"
STEP1_TIMESTAMP = "2026-03-06T03:00:00Z"
STEP2_TIMESTAMP = "2026-03-06T04:00:00Z"
LLM_REQ_TIMESTAMP = "2026-03-06T03:30:00Z"
LLM_RESP_TIMESTAMP = "2026-03-06T03:45:00Z"

# Short aliases used by LLM enrichment tests
LLM_REQ_TS = LLM_REQ_TIMESTAMP
LLM_RESP_TS = LLM_RESP_TIMESTAMP

EXPECTED_STEP1_ID = make_node_id("s1", "provider:request", STEP1_TIMESTAMP)
EXPECTED_STEP2_ID = make_node_id("s1", "provider:request", STEP2_TIMESTAMP)


# ── Helpers ──────────────────────────────────────────────────────────────


async def _seed_session_and_run(services: HookStateService, session_id: str = "s1") -> str:
    """Create Session + prompt:submit + execution:start so we have a current run."""
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
    return services.get_cursors(session_id).current_run_id  # type: ignore[return-value]


async def _seed_through_provider_request(services: HookStateService, session_id: str = "s1") -> str:
    """Seed session + run + provider:request so there is a current step to enrich."""
    await _seed_session_and_run(services, session_id)
    handler = StepHandler(services)
    await handler(
        "provider:request",
        {
            "session_id": session_id,
            "timestamp": STEP1_TIMESTAMP,
            "iteration": 1,
            "provider": "anthropic",
        },
    )
    return services.get_cursors(session_id).current_step_id  # type: ignore[return-value]


# ── TestProviderRequestHappyPath (8 tests) ───────────────────────────────


class TestProviderRequestHappyPath:
    async def test_creates_assistant_step_node(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        node = await services.graph.get_node(EXPECTED_STEP1_ID)
        assert node is not None

    async def test_correct_labels(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        node = await services.graph.get_node(EXPECTED_STEP1_ID)
        assert node is not None
        assert node["labels"] == {"Step", "AssistantStep"}

    async def test_node_properties(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        node = await services.graph.get_node(EXPECTED_STEP1_ID)
        assert node is not None
        props = node["properties"]
        assert props["iteration"] == 1
        assert props["provider"] == "anthropic"
        assert props["request_at"] == STEP1_TIMESTAMP
        assert props["occurred_at"] == STEP1_TIMESTAMP
        assert props["session_id"] == "s1"

    async def test_has_step_edge_from_run(self, services: HookStateService) -> None:
        run_id = await _seed_session_and_run(services)
        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        edge = await services.graph.get_edge(run_id, EXPECTED_STEP1_ID, "HAS_STEP")
        assert edge is not None
        # step_counter starts at 0 from prompt:submit, provider:request increments to 1
        assert edge["properties"]["seq"] == 1
        assert edge["properties"]["occurred_at"] == STEP1_TIMESTAMP

    async def test_step_counter_incremented(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        cursors = services.get_cursors("s1")
        assert cursors.step_counter == 1

    async def test_parallel_groups_cleared(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        # Seed some parallel_groups data
        cursors = services.get_cursors("s1")
        cursors.parallel_groups["group1"] = ["a", "b"]

        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        cursors = services.get_cursors("s1")
        assert cursors.parallel_groups == {}

    async def test_current_step_id_updated(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        cursors = services.get_cursors("s1")
        assert cursors.current_step_id == EXPECTED_STEP1_ID

    async def test_returns_hook_result_continue(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        result = await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        assert result.action == "continue"


# ── TestProviderRequestNextChain (2 tests) ───────────────────────────────


class TestProviderRequestNextChain:
    async def test_next_edge_from_first_to_second_step(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        # First provider:request
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        # Second provider:request
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP2_TIMESTAMP,
                "iteration": 2,
                "provider": "anthropic",
            },
        )
        edge = await services.graph.get_edge(EXPECTED_STEP1_ID, EXPECTED_STEP2_ID, "NEXT")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == STEP2_TIMESTAMP

    async def test_has_step_seq_increments(self, services: HookStateService) -> None:
        run_id = await _seed_session_and_run(services)
        handler = StepHandler(services)
        # First provider:request
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        # Second provider:request
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP2_TIMESTAMP,
                "iteration": 2,
                "provider": "anthropic",
            },
        )
        edge1 = await services.graph.get_edge(run_id, EXPECTED_STEP1_ID, "HAS_STEP")
        edge2 = await services.graph.get_edge(run_id, EXPECTED_STEP2_ID, "HAS_STEP")
        assert edge1 is not None
        assert edge2 is not None
        assert edge1["properties"]["seq"] == 1
        assert edge2["properties"]["seq"] == 2


# ── TestProviderRequestErrorPaths (2 tests) ──────────────────────────────


class TestProviderRequestErrorPaths:
    async def test_graceful_when_no_current_run_id(self, services: HookStateService) -> None:
        """provider:request without a current_run_id should not crash."""
        # Create session but no execution:start, so current_run_id is None
        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": "s1", "timestamp": SESSION_TIMESTAMP},
        )
        handler = StepHandler(services)
        result = await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        assert result.action == "continue"

    async def test_graceful_when_missing_session_id(self, services: HookStateService) -> None:
        """provider:request without session_id should not crash."""
        handler = StepHandler(services)
        result = await handler(
            "provider:request",
            {
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        assert result.action == "continue"


# ── LLM enrichment tests ────────────────────────────────────────────────


class TestLlmRequest:
    """llm:request enrichment — 4 tests."""

    async def test_enriches_step_with_model(self, services: HookStateService) -> None:
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:request",
            {
                "session_id": "s1",
                "timestamp": LLM_REQ_TS,
                "model": "claude-sonnet-4-20250514",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["model"] == "claude-sonnet-4-20250514"

    async def test_no_model_field_is_safe(self, services: HookStateService) -> None:
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        result = await handler(
            "llm:request",
            {"session_id": "s1", "timestamp": LLM_REQ_TS},
        )
        assert result.action == "continue"
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert "model" not in node["properties"]

    async def test_graceful_when_no_current_step(self, services: HookStateService) -> None:
        """llm:request with a session but no provider:request yet should not crash."""
        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": "s1", "timestamp": SESSION_TIMESTAMP},
        )
        handler = StepHandler(services)
        result = await handler(
            "llm:request",
            {
                "session_id": "s1",
                "timestamp": LLM_REQ_TS,
                "model": "claude-sonnet-4-20250514",
            },
        )
        assert result.action == "continue"

    async def test_missing_session_id(self, services: HookStateService) -> None:
        handler = StepHandler(services)
        result = await handler(
            "llm:request",
            {"timestamp": LLM_REQ_TS, "model": "claude-sonnet-4-20250514"},
        )
        assert result.action == "continue"


class TestLlmResponse:
    """llm:response enrichment — 5 tests."""

    async def test_enriches_step_with_tokens(self, services: HookStateService) -> None:
        """Full enrichment: input_tokens, output_tokens, cached_tokens, finish_reason, response_at."""
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 30,
                },
                "finish_reason": "end_turn",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        props = node["properties"]
        assert props["response_at"] == LLM_RESP_TS
        assert props["input_tokens"] == 100
        assert props["output_tokens"] == 50
        assert props["cached_tokens"] == 30
        assert props["finish_reason"] == "end_turn"

    async def test_handles_stop_reason_as_finish_reason(self, services: HookStateService) -> None:
        """stop_reason should be mapped to finish_reason when finish_reason absent."""
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["finish_reason"] == "end_turn"

    async def test_empty_usage_is_safe(self, services: HookStateService) -> None:
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        result = await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {},
            },
        )
        assert result.action == "continue"
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["response_at"] == LLM_RESP_TS
        assert "input_tokens" not in node["properties"]
        assert "output_tokens" not in node["properties"]

    async def test_graceful_when_no_current_step(self, services: HookStateService) -> None:
        """llm:response with a session but no provider:request yet should not crash."""
        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": "s1", "timestamp": SESSION_TIMESTAMP},
        )
        handler = StepHandler(services)
        result = await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "finish_reason": "end_turn",
            },
        )
        assert result.action == "continue"

    async def test_missing_session_id(self, services: HookStateService) -> None:
        handler = StepHandler(services)
        result = await handler(
            "llm:response",
            {
                "timestamp": LLM_RESP_TS,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
        assert result.action == "continue"


class TestContentBlockNoOp:
    async def test_content_block_returns_continue(self, services: HookStateService) -> None:
        handler = StepHandler(services)
        result = await handler(
            "content_block:start",
            {"session_id": "s1", "timestamp": "2026-03-06T05:00:00Z"},
        )
        assert result.action == "continue"
