"""6-state deterministic mount flow state machine for context-intelligence hook."""

from __future__ import annotations

import enum
import fnmatch
import logging
from typing import Any, Callable

from .handlers.default import DefaultHandler
from .handlers.event import SystemEventHandler
from .handlers.orchestrator_run import OrchestratorRunHandler
from .handlers.recipe_step import RecipeStepHandler
from .handlers.session import SessionHandler
from .handlers.step import StepHandler
from .handlers.tool_execution import ToolExecutionHandler
from .services import HookStateService

logger = logging.getLogger(__name__)


class MountState(enum.Enum):
    """States of the mount flow state machine."""

    INIT = "init"
    STATE_CREATED = "state_created"
    HANDLERS_INSTANTIATED = "handlers_instantiated"
    EVENTS_DISCOVERED = "events_discovered"
    SPECIFIC_REGISTERED = "specific_registered"
    READY = "ready"


class MountFlow:
    """6-state deterministic mount flow state machine.

    Transitions:
        INIT → STATE_CREATED → HANDLERS_INSTANTIATED → EVENTS_DISCOVERED
        → SPECIFIC_REGISTERED → READY
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self.state = MountState.INIT
        self.services: HookStateService | None = None
        self.entity_handlers: list[Any] = []
        self.default_handler: DefaultHandler | None = None
        self.claimed_events: set[str] = set()
        self.remaining_events: set[str] = set()
        self._unregister_fns: list[Callable] = []

    def create_services(self) -> None:
        """INIT → STATE_CREATED: Instantiate HookStateService from config."""
        self.services = HookStateService(self._config)
        self.state = MountState.STATE_CREATED

    def instantiate_handlers(self) -> None:
        """STATE_CREATED → HANDLERS_INSTANTIATED: Instantiate all 7 handlers."""
        assert self.services is not None, "create_services() must be called first"
        svc = self.services
        self.entity_handlers = [
            SessionHandler(svc),
            OrchestratorRunHandler(svc),
            StepHandler(svc),
            RecipeStepHandler(svc),
            ToolExecutionHandler(svc),
            SystemEventHandler(svc),
        ]
        self.default_handler = DefaultHandler(svc)

        # Collect all patterns declared by entity handlers (including wildcards)
        self.claimed_events = set()
        for handler in self.entity_handlers:
            self.claimed_events |= set(handler.handled_events)

        self.state = MountState.HANDLERS_INSTANTIATED

    async def discover_events(self, coordinator: Any) -> None:
        """HANDLERS_INSTANTIATED → EVENTS_DISCOVERED: Collect events from coordinator.

        Two discovery channels:
        - contributions: ``coordinator.collect_contributions("observability.events")``
          returns a list of lists (async)
        - legacy capability: ``coordinator.get_capability("observability.events")``
          returns a callable or None (sync); call it to get the event list
        """
        discovered: set[str] = set()

        # Contributions channel — async, returns list[list[str]]
        contributions = await coordinator.collect_contributions("observability.events")
        for event_list in contributions:
            discovered.update(event_list)

        # Legacy capability channel — sync, returns callable or None
        capability_fn = coordinator.get_capability("observability.events")
        if capability_fn is not None:
            discovered.update(capability_fn())

        # Apply exclusion filter from config
        assert self.services is not None, "create_services() must be called first"
        config = self.services.config
        self.remaining_events = {e for e in discovered if not config.is_excluded(e)}

        self.state = MountState.EVENTS_DISCOVERED

    def _find_handler_for_event(self, event: str) -> Any | None:
        """Return the first entity handler whose patterns match *event*, or None."""
        for handler in self.entity_handlers:
            if any(fnmatch.fnmatch(event, pattern) for pattern in handler.handled_events):
                return handler
        return None

    def register_specific_handlers(self, coordinator: Any) -> None:
        """EVENTS_DISCOVERED → SPECIFIC_REGISTERED: Register entity handlers for known events."""
        for event in self.remaining_events:
            handler = self._find_handler_for_event(event)
            if handler is not None:
                unreg = coordinator.hooks.register(
                    event, handler, priority=90, name=type(handler).__name__
                )
                self._unregister_fns.append(unreg)

        self.state = MountState.SPECIFIC_REGISTERED

    def register_default_handler(self, coordinator: Any) -> None:
        """SPECIFIC_REGISTERED → READY: Register DefaultHandler for all unclaimed events."""
        assert self.default_handler is not None, "instantiate_handlers() must be called first"
        unclaimed = {
            event for event in self.remaining_events if self._find_handler_for_event(event) is None
        }

        self.default_handler.handled_events = unclaimed

        for event in unclaimed:
            unreg = coordinator.hooks.register(
                event, self.default_handler, priority=90, name="DefaultHandler"
            )
            self._unregister_fns.append(unreg)

        self.state = MountState.READY

    async def run(self, coordinator: Any) -> Callable:
        """Execute all state transitions and return a cleanup callable.

        The cleanup callable calls every unregister function returned by
        ``coordinator.hooks.register``, tearing down all registered hooks.
        """
        self.create_services()
        self.instantiate_handlers()
        await self.discover_events(coordinator)
        self.register_specific_handlers(coordinator)
        self.register_default_handler(coordinator)

        unregister_fns = self._unregister_fns

        def cleanup() -> None:
            for unreg in unregister_fns:
                unreg()

        return cleanup
