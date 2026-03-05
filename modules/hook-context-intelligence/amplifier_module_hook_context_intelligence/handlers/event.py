"""SystemEventHandler — owns known system events (compaction, cancellation)."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class SystemEventHandler:
    """Labels preserve full event scope: :Event:ContextCompaction, :Event:CancelRequested, etc."""

    handled_events: frozenset[str] = frozenset(
        {
            "context:compaction",
            "cancel:requested",
            "cancel:completed",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")
