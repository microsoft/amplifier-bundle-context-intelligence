"""RecipeHandler — recipe orchestration events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id
from .default import DefaultHandler

logger = logging.getLogger(__name__)

_LIFECYCLE_EVENTS: frozenset[str] = frozenset(
    {
        "recipe:start",
        "recipe:step",
        "recipe:complete",
        "recipe:approval",
    }
)

_LOOP_EVENTS: frozenset[str] = frozenset(
    {
        "recipe:loop_iteration",
        "recipe:loop_complete",
    }
)

_APPROVAL_PROMPT_MAX_LEN = 500


class RecipeHandler:
    handled_events: frozenset[str] = _LIFECYCLE_EVENTS | _LOOP_EVENTS

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("RecipeHandler", logger)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)

        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        if event in _LIFECYCLE_EVENTS:
            await self._handle_lifecycle_event(event, data, session_id, log)
        elif event in _LOOP_EVENTS:
            await self._handle_loop_event(event, data, session_id, log)

        return HookResult(action="continue")

    async def _handle_lifecycle_event(
        self,
        event: str,
        data: dict[str, Any],
        session_id: str,
        log: EventLogContext,
    ) -> None:
        timestamp = data.get("timestamp", "")
        derived = DefaultHandler.derive_label(event)
        node_id = make_node_id(session_id, event, timestamp)

        # Common properties for all lifecycle events
        properties: dict[str, Any] = {
            "event_name": event,
            "occurred_at": timestamp,
            "recipe_name": data.get("recipe_name", ""),
            "description": data.get("description", ""),
            "total_steps": data.get("total_steps", 0),
            "status": data.get("status", ""),
        }

        # Event-specific extras
        if event == "recipe:step":
            current_step = data.get("current_step", 0)
            properties["step_index"] = current_step
            steps = data.get("steps", [])
            if steps and 0 <= current_step < len(steps):
                properties["step_id"] = steps[current_step].get("id", "")

        elif event == "recipe:complete":
            properties["success"] = data.get("success", False)

        elif event == "recipe:approval":
            properties["stage_name"] = data.get("stage_name", "")
            properties["current_step"] = data.get("current_step", 0)
            prompt = data.get("approval_prompt", "")
            properties["approval_prompt"] = prompt[:_APPROVAL_PROMPT_MAX_LEN]

        # Create Event node with {Event, derived} labels
        await self.services.graph.upsert_node(
            node_id,
            {"Event", derived},
            properties,
        )

        # Create HAS_EVENT edge from session to event node
        await self.services.graph.upsert_edge(
            session_id, node_id, "HAS_EVENT", {"occurred_at": timestamp}
        )

        log.info("Created %s node %s", derived, node_id)

    async def _handle_loop_event(
        self,
        event: str,
        data: dict[str, Any],
        session_id: str,
        log: EventLogContext,
    ) -> None:
        timestamp = data.get("timestamp", "")
        derived = DefaultHandler.derive_label(event)
        node_id = make_node_id(session_id, event, timestamp)

        # Common properties for all loop events
        properties: dict[str, Any] = {
            "event_name": event,
            "occurred_at": timestamp,
            "step_id": data.get("step_id", ""),
            "max_iterations": data.get("max_iterations", 0),
        }

        # Event-specific extras
        if event == "recipe:loop_iteration":
            properties["iteration"] = data.get("iteration", 0)

        elif event == "recipe:loop_complete":
            properties["iterations_completed"] = data.get("iterations_completed", 0)
            properties["results_count"] = data.get("results_count", 0)

        # Create Event node with {Event, derived} labels
        await self.services.graph.upsert_node(
            node_id,
            {"Event", derived},
            properties,
        )

        # Create HAS_EVENT edge from session to event node
        await self.services.graph.upsert_edge(
            session_id, node_id, "HAS_EVENT", {"occurred_at": timestamp}
        )

        log.info("Created %s node %s", derived, node_id)
