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
