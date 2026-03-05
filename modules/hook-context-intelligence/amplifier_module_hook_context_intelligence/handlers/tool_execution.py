"""ToolExecutionHandler — owns :ToolExecution lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class ToolExecutionHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "tool:pre",
            "tool:post",
            "tool:error",
            "delegate:agent_spawned",
            "delegate:agent_completed",
            "delegate:context_inherited",
            "delegate:session_resumed",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        return HookResult(action="continue")
