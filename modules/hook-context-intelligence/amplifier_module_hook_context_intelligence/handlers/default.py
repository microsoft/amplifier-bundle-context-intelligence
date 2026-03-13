"""DefaultHandler — catches all unclaimed, non-excluded events."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import make_node_id

logger = logging.getLogger(__name__)


class DefaultHandler:
    """Creates :Event:{DerivedLabel} nodes from unclaimed events.

    For every event that no entity handler claims, the DefaultHandler:
    1. Derives a PascalCase label from the event name.
    2. Creates an Event node with labels {Event, DerivedLabel}.
    3. Attaches it to the active OrchestratorRun (if one is running) or to
       the Session otherwise via a HAS_EVENT edge.

    This covers app-level events (e.g. session:resume) that don't need
    special entity-node mutations — they are simply recorded as Event
    nodes in the graph.
    """

    handled_events: set[str]

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self.handled_events = set()

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            logger.debug("DefaultHandler: no session_id in %s, skipping", event)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")
        derived = self.derive_label(event)

        # Create Event node
        event_node_id = make_node_id(session_id, event, timestamp)
        await self.services.graph.upsert_node(
            event_node_id,
            {"Event", derived},
            {"event_name": event, "occurred_at": timestamp, "data": json.dumps(data)},
        )

        # Attach to active run if one exists, otherwise to session
        cursors = self.services.get_cursors(session_id)
        parent_id = cursors.current_run_id if cursors.current_run_id else session_id

        await self.services.graph.upsert_edge(
            parent_id, event_node_id, "HAS_EVENT", {"occurred_at": timestamp}
        )

        return HookResult(action="continue")

    @staticmethod
    def derive_label(event_name: str) -> str:
        """Derive PascalCase label. "context:compaction" -> "ContextCompaction"."""
        parts = re.split(r"[:_]", event_name)
        return "".join(part.capitalize() for part in parts if part)
