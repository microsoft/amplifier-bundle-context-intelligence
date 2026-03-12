"""OrchestratorRunHandler — owns :OrchestratorRun and :Step:PromptStep lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id

logger = logging.getLogger(__name__)

PREVIEW_MAX_LEN = 200

_STATUS_MAP: dict[str, str] = {
    "success": "complete",
    "cancelled": "cancelled",
    "error": "error",
}


class OrchestratorRunHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "prompt:submit",
            "execution:start",
            "execution:end",
            "orchestrator:complete",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("OrchestratorRunHandler", logger)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)

        if event == "prompt:submit":
            return await self._handle_prompt_submit(data, log)
        if event == "execution:start":
            return await self._handle_execution_start(data, log)
        if event == "execution:end":
            return await self._handle_execution_end(data, log)
        if event == "orchestrator:complete":
            return await self._handle_orchestrator_complete(data, log)

        return HookResult(action="continue")

    async def _handle_prompt_submit(self, data: dict[str, Any], log: EventLogContext) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        # Validate session exists in graph
        session_node = await self.services.graph.get_node(session_id)
        if session_node is None:
            log.error("Session node not found")
            return HookResult(action="continue")

        # Generate deterministic node ID
        node_id = make_node_id(session_id, "prompt:submit", timestamp)

        # Build properties
        prompt_text = data.get("prompt", "")
        prompt_preview = prompt_text[:PREVIEW_MAX_LEN]

        properties: dict[str, Any] = {
            "iteration": 0,
            "prompt_text": prompt_text,
            "prompt_preview": prompt_preview,
            "occurred_at": timestamp,
            "session_id": session_id,
        }

        # Upsert PromptStep node only — edges deferred to execution:start
        await self.services.graph.upsert_node(node_id, {"Step", "PromptStep"}, properties)

        # Update cursor state
        cursors = self.services.get_cursors(session_id)
        cursors.run_counter += 1
        cursors.step_counter = 0
        cursors.current_step_id = node_id
        cursors.prompt_preview = prompt_preview

        log.info("Created PromptStep node %s", node_id)

        return HookResult(action="continue")

    async def _handle_execution_start(
        self, data: dict[str, Any], log: EventLogContext
    ) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        # Generate deterministic node ID
        run_id = make_node_id(session_id, "execution:start", timestamp)

        # Read cursor state populated by prompt:submit
        cursors = self.services.get_cursors(session_id)

        # Build OrchestratorRun node properties
        properties: dict[str, Any] = {
            "run_number": cursors.run_counter,
            "started_at": timestamp,
            "status": "in_progress",
            "prompt_preview": cursors.prompt_preview,
            "session_id": session_id,
        }

        # Create OrchestratorRun node
        await self.services.graph.upsert_node(run_id, {"OrchestratorRun"}, properties)

        # Create HAS_RUN edge: Session → OrchestratorRun
        await self.services.graph.upsert_edge(
            session_id,
            run_id,
            "HAS_RUN",
            {"seq": cursors.run_counter, "occurred_at": timestamp},
        )

        # Create HAS_STEP edge: OrchestratorRun → PromptStep (if prompt step exists)
        if cursors.current_step_id:
            await self.services.graph.upsert_edge(
                run_id,
                cursors.current_step_id,
                "HAS_STEP",
                {"seq": 0, "occurred_at": timestamp},
            )

        # Update cursor state
        cursors.current_run_id = run_id

        log.info("Created OrchestratorRun node %s", run_id)

        return HookResult(action="continue")

    async def _handle_execution_end(self, data: dict[str, Any], log: EventLogContext) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        run_id = cursors.current_run_id
        if not run_id:
            log.warning("No current_run_id for session %s", session_id)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        # Enrich OrchestratorRun with execution_ended_at — do NOT change status
        properties: dict[str, Any] = {"execution_ended_at": timestamp}

        # Optionally store response_preview
        response = data.get("response")
        if response is not None:
            properties["response_preview"] = str(response)[:PREVIEW_MAX_LEN]

        await self.services.graph.upsert_node(run_id, set(), properties)

        # Terminal event — await flush directly. execution:end may be the last
        # event before process exit (--mode single doesn't emit session:end).
        # schedule_flush() is for intermediate events only.
        await self.services.graph.flush()

        log.info("Enriched OrchestratorRun %s with execution_ended_at", run_id)

        return HookResult(action="continue")

    async def _handle_orchestrator_complete(
        self, data: dict[str, Any], log: EventLogContext
    ) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        cursors = self.services.get_cursors(session_id)
        run_id = cursors.current_run_id
        if not run_id:
            log.warning("No current_run_id for session %s", session_id)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        # Map event status to graph status via _STATUS_MAP
        raw_status = data.get("status", "success")
        mapped_status = _STATUS_MAP.get(raw_status, raw_status)

        # Build properties to close the OrchestratorRun
        properties: dict[str, Any] = {
            "ended_at": timestamp,
            "status": mapped_status,
        }

        # Optionally store turn_count
        turn_count = data.get("turn_count")
        if turn_count is not None:
            properties["turn_count"] = turn_count

        await self.services.graph.upsert_node(run_id, set(), properties)

        # Clear cursor state
        cursors.current_run_id = None
        cursors.tool_call_map.clear()

        log.info("Closed OrchestratorRun %s with status %s", run_id, mapped_status)

        return HookResult(action="continue")
