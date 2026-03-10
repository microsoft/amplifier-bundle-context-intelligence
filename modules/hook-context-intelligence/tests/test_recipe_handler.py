"""Tests for RecipeHandler — recipe lifecycle graph mutations."""

from __future__ import annotations

from typing import Any

from amplifier_module_hook_context_intelligence.handlers.recipe import RecipeHandler
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id

SESSION_ID = "s1"
TIMESTAMP = "2026-03-10T10:00:00+00:00"


async def _seed_session(services: HookStateService, session_id: str = SESSION_ID) -> None:
    """Create a Session node via SessionHandler so it exists in the graph."""
    session_handler = SessionHandler(services)
    await session_handler(
        "session:start",
        {
            "session_id": session_id,
            "timestamp": "2026-03-10T09:00:00+00:00",
        },
    )


def _lifecycle_data(
    *,
    session_id: str = SESSION_ID,
    timestamp: str = TIMESTAMP,
    recipe_name: str = "code-review",
    description: str = "Automated code review",
    total_steps: int = 3,
    status: str = "running",
    current_step: int = 0,
    steps: list[dict[str, Any]] | None = None,
    success: bool = True,
    stage_name: str = "planning",
    approval_prompt: str = "Approve?",
) -> dict[str, Any]:
    """Build a lifecycle payload with sensible defaults."""
    if steps is None:
        steps = [
            {"id": "step-a"},
            {"id": "step-b"},
            {"id": "step-c"},
        ]
    return {
        "session_id": session_id,
        "timestamp": timestamp,
        "recipe_name": recipe_name,
        "description": description,
        "total_steps": total_steps,
        "status": status,
        "current_step": current_step,
        "steps": steps,
        "success": success,
        "stage_name": stage_name,
        "approval_prompt": approval_prompt,
    }


# ── recipe:start ─────────────────────────────────────────────────────


class TestRecipeStart:
    async def test_creates_event_node(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        await handler("recipe:start", data)
        node_id = make_node_id(SESSION_ID, "recipe:start", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None

    async def test_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        await handler("recipe:start", data)
        node_id = make_node_id(SESSION_ID, "recipe:start", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeStart"}

    async def test_stores_properties(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        await handler("recipe:start", data)
        node_id = make_node_id(SESSION_ID, "recipe:start", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        props = node["properties"]
        assert props["recipe_name"] == "code-review"
        assert props["description"] == "Automated code review"
        assert props["total_steps"] == 3
        assert props["status"] == "running"
        assert props["event_name"] == "recipe:start"
        assert props["occurred_at"] == TIMESTAMP

    async def test_creates_has_event_edge(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        await handler("recipe:start", data)
        node_id = make_node_id(SESSION_ID, "recipe:start", TIMESTAMP)
        edge = await services.graph.get_edge(SESSION_ID, node_id, "HAS_EVENT")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == TIMESTAMP

    async def test_does_not_store_steps_array(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        await handler("recipe:start", data)
        node_id = make_node_id(SESSION_ID, "recipe:start", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert "steps" not in node["properties"]


# ── recipe:step ──────────────────────────────────────────────────────


class TestRecipeStep:
    async def test_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(current_step=1)
        await handler("recipe:step", data)
        node_id = make_node_id(SESSION_ID, "recipe:step", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeStep"}

    async def test_extracts_step_id(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(current_step=1)
        await handler("recipe:step", data)
        node_id = make_node_id(SESSION_ID, "recipe:step", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        props = node["properties"]
        assert props["step_id"] == "step-b"
        assert props["step_index"] == 1
        assert props["recipe_name"] == "code-review"
        assert props["total_steps"] == 3

    async def test_creates_has_event_edge(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(current_step=0)
        await handler("recipe:step", data)
        node_id = make_node_id(SESSION_ID, "recipe:step", TIMESTAMP)
        edge = await services.graph.get_edge(SESSION_ID, node_id, "HAS_EVENT")
        assert edge is not None


# ── recipe:complete ──────────────────────────────────────────────────


class TestRecipeComplete:
    async def test_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(success=True, status="complete")
        await handler("recipe:complete", data)
        node_id = make_node_id(SESSION_ID, "recipe:complete", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeComplete"}

    async def test_stores_success_and_status(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(success=True, status="complete")
        await handler("recipe:complete", data)
        node_id = make_node_id(SESSION_ID, "recipe:complete", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        props = node["properties"]
        assert props["success"] is True
        assert props["status"] == "complete"
        assert props["recipe_name"] == "code-review"
        assert props["total_steps"] == 3

    async def test_creates_has_event_edge(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        await handler("recipe:complete", data)
        node_id = make_node_id(SESSION_ID, "recipe:complete", TIMESTAMP)
        edge = await services.graph.get_edge(SESSION_ID, node_id, "HAS_EVENT")
        assert edge is not None


# ── recipe:approval ──────────────────────────────────────────


class TestRecipeApproval:
    async def test_creates_event_node_with_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(
            status="waiting_approval",
            stage_name="final-review",
            approval_prompt="Please approve the changes.",
        )
        await handler("recipe:approval", data)
        node_id = make_node_id(SESSION_ID, "recipe:approval", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeApproval"}
        props = node["properties"]
        assert props["status"] == "waiting_approval"
        assert props["stage_name"] == "final-review"
        assert props["approval_prompt"] == "Please approve the changes."

    async def test_stores_approval_properties(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(
            status="waiting_approval",
            stage_name="final-review",
            approval_prompt="Approve these changes.",
            current_step=5,
            total_steps=7,
            recipe_name="code-review",
        )
        await handler("recipe:approval", data)
        node_id = make_node_id(SESSION_ID, "recipe:approval", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        props = node["properties"]
        assert props["stage_name"] == "final-review"
        assert props["approval_prompt"] == "Approve these changes."
        assert props["current_step"] == 5
        assert props["total_steps"] == 7
        assert props["status"] == "waiting_approval"
        assert props["recipe_name"] == "code-review"

    async def test_truncates_long_prompt(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        long_prompt = "x" * 1000
        data = _lifecycle_data(
            status="waiting_approval",
            stage_name="final-review",
            approval_prompt=long_prompt,
        )
        await handler("recipe:approval", data)
        node_id = make_node_id(SESSION_ID, "recipe:approval", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert len(node["properties"]["approval_prompt"]) == 500

    async def test_creates_has_event_edge(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data(status="waiting_approval", stage_name="final-review")
        await handler("recipe:approval", data)
        node_id = make_node_id(SESSION_ID, "recipe:approval", TIMESTAMP)
        edge = await services.graph.get_edge(SESSION_ID, node_id, "HAS_EVENT")
        assert edge is not None


# ══════════════════════════════════════════════════════════════════════════════
# recipe:loop_iteration tests
# ══════════════════════════════════════════════════════════════════════════════


def _loop_iteration_data(
    *,
    step_id: str = "spec-review-loop",
    iteration: int = 1,
    max_iterations: int = 3,
    timestamp: str = TIMESTAMP,
) -> dict[str, Any]:
    """Build a recipe:loop_iteration event payload."""
    return {
        "session_id": SESSION_ID,
        "step_id": step_id,
        "iteration": iteration,
        "max_iterations": max_iterations,
        "context_snapshot": {"plan_path": "/tmp/plan.md", "quality_approved": True},
        "parent_id": None,
        "timestamp": timestamp,
    }


def _loop_complete_data(
    *,
    step_id: str = "spec-review-loop",
    iterations_completed: int = 2,
    max_iterations: int = 3,
    results_count: int = 1,
    timestamp: str = TIMESTAMP,
) -> dict[str, Any]:
    """Build a recipe:loop_complete event payload."""
    return {
        "session_id": SESSION_ID,
        "step_id": step_id,
        "iterations_completed": iterations_completed,
        "max_iterations": max_iterations,
        "results_count": results_count,
        "parent_id": None,
        "timestamp": timestamp,
    }


class TestRecipeLoopIteration:
    async def test_creates_event_node_with_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_iteration", _loop_iteration_data())
        node_id = make_node_id(SESSION_ID, "recipe:loop_iteration", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeLoopIteration"}

    async def test_stores_iteration_properties(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_iteration", _loop_iteration_data(iteration=2, max_iterations=5))
        node_id = make_node_id(SESSION_ID, "recipe:loop_iteration", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        props = node["properties"]
        assert props["step_id"] == "spec-review-loop"
        assert props["iteration"] == 2
        assert props["max_iterations"] == 5
        assert props["event_name"] == "recipe:loop_iteration"

    async def test_does_not_store_context_snapshot(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_iteration", _loop_iteration_data())
        node_id = make_node_id(SESSION_ID, "recipe:loop_iteration", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert "context_snapshot" not in node["properties"]

    async def test_creates_has_event_edge(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_iteration", _loop_iteration_data())
        node_id = make_node_id(SESSION_ID, "recipe:loop_iteration", TIMESTAMP)
        edge = await services.graph.get_edge(SESSION_ID, node_id, "HAS_EVENT")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == TIMESTAMP


# ══════════════════════════════════════════════════════════════════════════════
# recipe:loop_complete tests
# ══════════════════════════════════════════════════════════════════════════════


class TestRecipeLoopComplete:
    async def test_creates_event_node_with_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_complete", _loop_complete_data())
        node_id = make_node_id(SESSION_ID, "recipe:loop_complete", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeLoopComplete"}

    async def test_stores_completion_properties(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler(
            "recipe:loop_complete",
            _loop_complete_data(iterations_completed=3, max_iterations=5, results_count=2),
        )
        node_id = make_node_id(SESSION_ID, "recipe:loop_complete", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        props = node["properties"]
        assert props["step_id"] == "spec-review-loop"
        assert props["iterations_completed"] == 3
        assert props["max_iterations"] == 5
        assert props["results_count"] == 2
        assert props["event_name"] == "recipe:loop_complete"

    async def test_creates_has_event_edge(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_complete", _loop_complete_data())
        node_id = make_node_id(SESSION_ID, "recipe:loop_complete", TIMESTAMP)
        edge = await services.graph.get_edge(SESSION_ID, node_id, "HAS_EVENT")
        assert edge is not None


# ── Error paths ──────────────────────────────────────────────────────


class TestRecipeHandlerErrorPaths:
    async def test_missing_session_id_returns_continue(self, services: HookStateService) -> None:
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        data.pop("session_id")
        result = await handler("recipe:start", data)
        assert result.action == "continue"

    async def test_missing_session_id_creates_no_nodes(self, services: HookStateService) -> None:
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        data.pop("session_id")
        await handler("recipe:start", data)
        node_id = make_node_id(SESSION_ID, "recipe:start", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is None

    async def test_unknown_event_returns_continue(self, services: HookStateService) -> None:
        # Unknown event skips both lifecycle and loop branches, returns continue.
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        result = await handler("recipe:unknown_xyz", data)
        assert result.action == "continue"


# ══════════════════════════════════════════════════════════════════════════════
# Real session scenario: 44be6956
# ══════════════════════════════════════════════════════════════════════════════

REAL_SESSION_ID = "44be6956-a0bb-4dfb-8e79-837c9c6d57c4"


async def _seed_real_session(services: HookStateService) -> None:
    """Create the real session node so HAS_EVENT edges can reference it."""
    await _seed_session(services, session_id=REAL_SESSION_ID)


class TestRealSession44be6956:
    """Replay exact production payloads from session 44be6956."""

    async def test_loop_iteration_from_real_data(self, services: HookStateService) -> None:
        await _seed_real_session(services)
        handler = RecipeHandler(services)
        data = {
            "session_id": REAL_SESSION_ID,
            "step_id": "spec-review-loop",
            "iteration": 1,
            "max_iterations": 3,
            "context_snapshot": {
                "plan_path": "/workspace/plans/impl-plan.md",
                "task_implementation": "task-5 completed successfully",
                "quality_approved": True,
            },
            "timestamp": "2026-03-09T23:25:16.813650685+00:00",
        }
        await handler("recipe:loop_iteration", data)

        node_id = "44be6956-a0bb-4dfb-8e79-837c9c6d57c4__recipe_loop_iteration__1773098716813"
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeLoopIteration"}
        props = node["properties"]
        assert props["step_id"] == "spec-review-loop"
        assert props["iteration"] == 1
        assert props["max_iterations"] == 3
        assert "context_snapshot" not in props

    async def test_loop_complete_from_real_data(self, services: HookStateService) -> None:
        await _seed_real_session(services)
        handler = RecipeHandler(services)
        data = {
            "session_id": REAL_SESSION_ID,
            "step_id": "spec-review-loop",
            "iterations_completed": 0,
            "max_iterations": 3,
            "results_count": 1,
            "timestamp": "2026-03-09T23:27:38.142442741+00:00",
        }
        await handler("recipe:loop_complete", data)

        node_id = "44be6956-a0bb-4dfb-8e79-837c9c6d57c4__recipe_loop_complete__1773098858142"
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeLoopComplete"}
        props = node["properties"]
        assert props["step_id"] == "spec-review-loop"
        assert props["iterations_completed"] == 0
        assert props["max_iterations"] == 3
        assert props["results_count"] == 1

    async def test_approval_from_real_data(self, services: HookStateService) -> None:
        await _seed_real_session(services)
        handler = RecipeHandler(services)

        prompt = (
            "## Stage: final-review\n\n"
            "Execute implementation plan with fresh agents per task "
            "and two-stage review\n\n"
            "### Progress\n"
            "- Step 5 of 7 complete\n\n"
            "### Completed Steps\n"
            "1. task-1: Implement make_node_id utility\n"
            "2. task-2: Implement DefaultHandler.derive_label\n"
            "3. task-3: Implement RecipeHandler loop_iteration\n"
            "4. task-4: Implement RecipeHandler loop_complete\n"
            "5. task-5: Implement RecipeHandler approval truncation\n\n"
            "### Pending Steps\n"
            "6. task-6: Add real-session scenario test\n"
            "7. task-7: Final integration verification\n\n"
            "Review the implementation and approve to continue with "
            "the remaining tasks. All completed steps have passed spec "
            "review and code quality checks."
        )

        steps = [
            {"id": "task-1", "description": "Implement make_node_id utility"},
            {"id": "task-2", "description": "Implement DefaultHandler.derive_label"},
            {"id": "task-3", "description": "Implement RecipeHandler loop_iteration"},
            {"id": "task-4", "description": "Implement RecipeHandler loop_complete"},
            {"id": "task-5", "description": "Implement RecipeHandler approval truncation"},
            {"id": "task-6", "description": "Add real-session scenario test"},
            {"id": "task-7", "description": "Final integration verification"},
        ]

        data = {
            "session_id": REAL_SESSION_ID,
            "recipe_name": "subagent-driven-development",
            "stage_name": "final-review",
            "approval_prompt": prompt,
            "current_step": 5,
            "total_steps": 7,
            "steps": steps,
            "description": (
                "Execute implementation plan with fresh agents per task and two-stage review"
            ),
            "status": "waiting_approval",
            "timestamp": "2026-03-10T01:59:28.082627514+00:00",
        }
        await handler("recipe:approval", data)

        node_id = "44be6956-a0bb-4dfb-8e79-837c9c6d57c4__recipe_approval__1773107968082"
        node = await services.graph.get_node(node_id)
        assert node is not None
        assert node["labels"] == {"Event", "RecipeApproval"}
        props = node["properties"]
        assert props["recipe_name"] == "subagent-driven-development"
        assert props["stage_name"] == "final-review"
        assert props["current_step"] == 5
        assert props["total_steps"] == 7
        assert props["status"] == "waiting_approval"
        assert "steps" not in props
        assert len(props["approval_prompt"]) <= 500

    async def test_full_sequence_creates_all_edges(self, services: HookStateService) -> None:
        await _seed_real_session(services)
        handler = RecipeHandler(services)

        # 1. Replay loop_iteration
        await handler(
            "recipe:loop_iteration",
            {
                "session_id": REAL_SESSION_ID,
                "step_id": "spec-review-loop",
                "iteration": 1,
                "max_iterations": 3,
                "context_snapshot": {
                    "plan_path": "/workspace/plans/impl-plan.md",
                    "task_implementation": "task-5 completed successfully",
                    "quality_approved": True,
                },
                "timestamp": "2026-03-09T23:25:16.813650685+00:00",
            },
        )

        # 2. Replay loop_complete
        await handler(
            "recipe:loop_complete",
            {
                "session_id": REAL_SESSION_ID,
                "step_id": "spec-review-loop",
                "iterations_completed": 0,
                "max_iterations": 3,
                "results_count": 1,
                "timestamp": "2026-03-09T23:27:38.142442741+00:00",
            },
        )

        # 3. Replay approval
        await handler(
            "recipe:approval",
            {
                "session_id": REAL_SESSION_ID,
                "recipe_name": "subagent-driven-development",
                "stage_name": "final-review",
                "approval_prompt": "Review and approve.",
                "current_step": 5,
                "total_steps": 7,
                "steps": [{"id": f"task-{i}"} for i in range(1, 8)],
                "description": (
                    "Execute implementation plan with fresh agents per task and two-stage review"
                ),
                "status": "waiting_approval",
                "timestamp": "2026-03-10T01:59:28.082627514+00:00",
            },
        )

        # Verify all 3 HAS_EVENT edges from session to event nodes
        iter_node_id = "44be6956-a0bb-4dfb-8e79-837c9c6d57c4__recipe_loop_iteration__1773098716813"
        complete_node_id = (
            "44be6956-a0bb-4dfb-8e79-837c9c6d57c4__recipe_loop_complete__1773098858142"
        )
        approval_node_id = "44be6956-a0bb-4dfb-8e79-837c9c6d57c4__recipe_approval__1773107968082"

        edge1 = await services.graph.get_edge(REAL_SESSION_ID, iter_node_id, "HAS_EVENT")
        edge2 = await services.graph.get_edge(REAL_SESSION_ID, complete_node_id, "HAS_EVENT")
        edge3 = await services.graph.get_edge(REAL_SESSION_ID, approval_node_id, "HAS_EVENT")

        assert edge1 is not None
        assert edge2 is not None
        assert edge3 is not None
