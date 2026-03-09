"""OrchestratorRunHandler — owns :OrchestratorRun and :Step:PromptStep lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id

logger = logging.getLogger(__name__)

PREVIEW_MAX_LEN = 200


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

        # Stub: execution:start, execution:end, orchestrator:complete
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
