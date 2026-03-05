"""SessionHandler — owns :Session node lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class SessionHandler:
    handled_events: set[str] = frozenset(
        {
            "session:start",
            "session:fork",
            "session:end",
            "session:resume",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")
