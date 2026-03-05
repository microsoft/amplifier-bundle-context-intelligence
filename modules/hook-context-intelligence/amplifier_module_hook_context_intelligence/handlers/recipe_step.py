"""RecipeStepHandler — owns :Step:RecipeStep lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class RecipeStepHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "recipe:step_started",
            "recipe:step_completed",
            "recipe:approval:*",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")
