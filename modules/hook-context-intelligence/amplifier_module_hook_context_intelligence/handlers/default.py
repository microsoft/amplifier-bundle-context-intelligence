"""DefaultHandler — catches all unclaimed, non-excluded events."""

from __future__ import annotations

import re
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class DefaultHandler:
    """Derives :Event:{FullScope} labels from event name dynamically."""

    handled_events: set[str]

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self.handled_events = set()

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")

    @staticmethod
    def derive_label(event_name: str) -> str:
        """Derive PascalCase label. "context:compaction" -> "ContextCompaction"."""
        parts = re.split(r"[:_]", event_name)
        return "".join(part.capitalize() for part in parts if part)
