"""OrchestratorRunHandler — owns :OrchestratorRun and :Step:PromptStep lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


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

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")
