"""StepHandler — owns :Step:AssistantStep lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class StepHandler:
    handled_events: set[str] = frozenset(
        {
            "provider:request",
            "llm:response",
            "llm:request:*",
            "llm:response:*",
            "content_block:*",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")
