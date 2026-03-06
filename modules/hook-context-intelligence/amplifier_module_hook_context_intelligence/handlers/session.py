"""SessionHandler — owns :Session node lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id

logger = logging.getLogger(__name__)


class SessionHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "session:start",
            "session:fork",
            "session:end",
            "session:resume",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("SessionHandler", logger)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)

        session_id = data.get("session_id")
        if not session_id:
            log.error("received event without session_id")
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        if event == "session:start":
            await self._handle_start(session_id, timestamp, data)
        elif event == "session:fork":
            await self._handle_fork(session_id, timestamp, data, log)
        elif event == "session:end":
            await self._handle_end(session_id, timestamp, data)
        elif event == "session:resume":
            await self._handle_resume(session_id, timestamp, data)

        return HookResult(action="continue")

    async def _handle_start(self, session_id: str, timestamp: str, data: dict[str, Any]) -> None:
        parent_id = (data.get("parent_id") or "").strip()

        if parent_id:
            labels: set[str] = {"Session", "Subsession"}
        else:
            labels = {"Session", "Root"}

        properties: dict[str, Any] = {
            "started_at": timestamp,
            "status": "running",
            "metadata": data.get("metadata", {}),
        }

        await self.services.graph.upsert_node(session_id, labels, properties)

        if parent_id:
            await self.services.graph.upsert_edge(
                session_id, parent_id, "SUBSESSION_OF", {"occurred_at": timestamp}
            )

    async def _handle_fork(
        self, session_id: str, timestamp: str, data: dict[str, Any], log: EventLogContext
    ) -> None:
        parent = data.get("parent")

        if parent:
            labels: set[str] = {"Session", "Subsession", "ForkedSession"}
        else:
            labels = {"Session", "Root", "ForkedSession"}
            log.warning("session:fork for %r has no parent — degrading to Root", session_id)

        properties: dict[str, Any] = {
            "started_at": timestamp,
            "status": "running",
            "metadata": data.get("metadata", {}),
        }

        await self.services.graph.upsert_node(session_id, labels, properties)

        if parent:
            await self.services.graph.upsert_edge(
                session_id, parent, "SUBSESSION_OF", {"occurred_at": timestamp}
            )

    async def _handle_end(self, session_id: str, timestamp: str, data: dict[str, Any]) -> None:
        labels: set[str] = {"Session"}
        properties: dict[str, Any] = {
            "ended_at": timestamp,
            "status": data.get("status", "completed"),
        }

        await self.services.graph.upsert_node(session_id, labels, properties)

    async def _handle_resume(self, session_id: str, timestamp: str, data: dict[str, Any]) -> None:
        # Add Resumed label to the session node
        await self.services.graph.upsert_node(session_id, {"Session", "Resumed"}, {})

        # Create Event node
        event_node_id = make_node_id(session_id, "session:resume", timestamp)
        await self.services.graph.upsert_node(
            event_node_id,
            {"Event", "SessionResume"},
            {"occurred_at": timestamp},
        )

        # Create HAS_EVENT edge from session to event
        await self.services.graph.upsert_edge(
            session_id, event_node_id, "HAS_EVENT", {"occurred_at": timestamp}
        )
