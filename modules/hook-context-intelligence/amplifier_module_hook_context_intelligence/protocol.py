"""EventHandler protocol — the contract all handlers conform to."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any, Protocol, runtime_checkable

from amplifier_core.models import HookResult


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for all context-intelligence event handlers."""

    handled_events: AbstractSet[str]
    """The set of event names this handler owns (set or frozenset)."""

    services: Any
    """HookStateService instance injected at construction."""

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a dispatched event."""
        ...
