"""RecipeHandler — recipe orchestration events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class RecipeHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "recipe:start",
            "recipe:step",
            "recipe:complete",
            "recipe:approval",
            "recipe:loop_iteration",
            "recipe:loop_complete",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")
