"""DTU test fixture: contributes a non-kernel event via observability.events.

This module's sole purpose is to register a custom event that is NOT in
ALL_EVENTS so that integration tests can verify on_session_ready() picks
up late-contributed events. This event MUST NOT be added to ALL_EVENTS.
"""

from __future__ import annotations

from typing import Any

__amplifier_module_type__ = "tool"

# The sentinel event name used by DTU integration tests to verify
# on_session_ready() picked up late contributions.
STUB_CONTRIBUTED_EVENT = "stub-event-contributor:test-event"


async def mount(coordinator: Any, config: dict | None = None) -> None:
    """Contribute a test event to observability.events."""
    coordinator.contribute("observability.events", [STUB_CONTRIBUTED_EVENT])
