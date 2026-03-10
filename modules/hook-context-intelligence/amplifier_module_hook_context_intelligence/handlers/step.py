"""StepHandler — owns :Step:AssistantStep lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id

logger = logging.getLogger(__name__)


class StepHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "provider:request",
            "llm:request",
            "llm:response",
            "content_block:*",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("StepHandler", logger)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)

        if event == "provider:request":
            return await self._handle_provider_request(data, log)
        if event == "llm:request":
            return await self._handle_llm_request(data, log)
        if event == "llm:response":
            return await self._handle_llm_response(data, log)

        # content_block:* — claimed but no-op for v1
        return HookResult(action="continue")

    async def _handle_provider_request(
        self, data: dict[str, Any], log: EventLogContext
    ) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")
        cursors = self.services.get_cursors(session_id)

        # Increment step_counter and clear parallel_groups
        cursors.step_counter += 1
        cursors.parallel_groups.clear()

        # Generate deterministic step ID
        step_id = make_node_id(session_id, "provider:request", timestamp)

        # Build AssistantStep node properties
        properties: dict[str, Any] = {
            "iteration": data.get("iteration", cursors.step_counter),
            "provider": data.get("provider", ""),
            "request_at": timestamp,
            "occurred_at": timestamp,
            "session_id": session_id,
        }

        # Create AssistantStep node
        await self.services.graph.upsert_node(step_id, {"Step", "AssistantStep"}, properties)

        # Create HAS_STEP edge: OrchestratorRun → AssistantStep (only if run exists)
        run_id = cursors.current_run_id
        if run_id:
            await self.services.graph.upsert_edge(
                run_id,
                step_id,
                "HAS_STEP",
                {"seq": cursors.step_counter, "occurred_at": timestamp},
            )

        # Create NEXT edge: previous step → this step (only if previous step exists)
        previous_step_id = cursors.current_step_id
        if previous_step_id:
            await self.services.graph.upsert_edge(
                previous_step_id,
                step_id,
                "NEXT",
                {"occurred_at": timestamp},
            )

        # Update cursor state
        cursors.current_step_id = step_id

        log.info("Created AssistantStep node %s", step_id)

        return HookResult(action="continue")

    async def _handle_llm_request(self, data: dict[str, Any], log: EventLogContext) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        step_id = cursors.current_step_id
        if not step_id:
            log.warning("No current_step_id for session %s", session_id)
            return HookResult(action="continue")

        # Enrich AssistantStep with model
        properties: dict[str, Any] = {}
        model = data.get("model")
        if model is not None:
            properties["model"] = model

        if properties:
            await self.services.graph.upsert_node(step_id, set(), properties)
            log.info("Enriched AssistantStep %s with model", step_id)

        return HookResult(action="continue")

    async def _handle_llm_response(self, data: dict[str, Any], log: EventLogContext) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        step_id = cursors.current_step_id
        if not step_id:
            log.warning("No current_step_id for session %s", session_id)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        # Enrich AssistantStep with response data
        properties: dict[str, Any] = {"response_at": timestamp}

        # Extract usage tokens
        usage = data.get("usage")
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("input_tokens")
            if input_tokens is not None:
                properties["input_tokens"] = input_tokens
            output_tokens = usage.get("output_tokens")
            if output_tokens is not None:
                properties["output_tokens"] = output_tokens

        # Extract finish_reason / stop_reason
        finish_reason = data.get("finish_reason")
        if finish_reason is not None:
            properties["finish_reason"] = finish_reason
        stop_reason = data.get("stop_reason")
        if stop_reason is not None:
            properties["stop_reason"] = stop_reason

        await self.services.graph.upsert_node(step_id, set(), properties)
        log.info("Enriched AssistantStep %s with response data", step_id)

        return HookResult(action="continue")
