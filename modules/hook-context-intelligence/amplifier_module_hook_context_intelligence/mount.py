"""5-state deterministic mount flow state machine for context-intelligence hook."""

from __future__ import annotations

import enum
import fnmatch
import logging
from typing import Any, Callable

from .blob_processor import process_event_data
from .handlers.default import DefaultHandler
from .handlers.event import SystemEventHandler
from .handlers.orchestrator_run import OrchestratorRunHandler
from .handlers.recipe import RecipeHandler
from .handlers.session import SessionHandler
from .handlers.step import StepHandler
from .handlers.tool_execution import ToolExecutionHandler
from .services import HookStateService
from .utils import make_node_id

logger = logging.getLogger(__name__)


class MountState(enum.Enum):
    """States of the mount flow state machine."""

    INIT = "init"
    STATE_CREATED = "state_created"
    HANDLERS_INSTANTIATED = "handlers_instantiated"
    SPECIFIC_REGISTERED = "specific_registered"
    READY = "ready"


class MountFlow:
    """5-state deterministic mount flow state machine.

    Transitions:
        INIT → STATE_CREATED → HANDLERS_INSTANTIATED
        → SPECIFIC_REGISTERED → READY

    Conscious design decision: events are discovered once in mount()
    and passed down via run(coordinator, events). MountFlow does not
    query the coordinator independently. Eliminates dual-discovery
    divergence risk.
    """

    def __init__(
        self,
        config: dict[str, Any],
        graph_store: Any = None,
        resolver: Any = None,
        blob_store: Any = None,
    ) -> None:
        self._config = config
        self._graph_store = graph_store
        self._resolver = resolver
        self._blob_store = blob_store
        self.state = MountState.INIT
        self.services: HookStateService | None = None
        self.entity_handlers: list[Any] = []
        self.default_handler: DefaultHandler | None = None
        self.claimed_events: set[str] = set()
        self.remaining_events: set[str] = set()
        self._unregister_fns: list[Callable] = []

    def create_services(self, coordinator: Any) -> None:
        """INIT → STATE_CREATED: Instantiate HookStateService from config."""
        if self._resolver is not None:
            self.services = HookStateService(
                resolver=self._resolver,
                coordinator=coordinator,
                graph_store=self._graph_store,
                blob_store=self._blob_store,
            )
        else:
            self.services = HookStateService(
                self._config,
                coordinator=coordinator,
                graph_store=self._graph_store,
                blob_store=self._blob_store,
            )
        self.state = MountState.STATE_CREATED

    def instantiate_handlers(self) -> None:
        """STATE_CREATED → HANDLERS_INSTANTIATED: Instantiate all 7 handlers."""
        if self.services is None:
            raise RuntimeError("create_services() must be called first")
        svc = self.services
        self.entity_handlers = [
            SessionHandler(svc),
            OrchestratorRunHandler(svc),
            StepHandler(svc),
            RecipeHandler(svc),
            ToolExecutionHandler(svc),
            SystemEventHandler(svc),
        ]
        self.default_handler = DefaultHandler(svc)

        # Collect all patterns declared by entity handlers (including wildcards)
        self.claimed_events = set()
        for handler in self.entity_handlers:
            self.claimed_events |= set(handler.handled_events)

        self.state = MountState.HANDLERS_INSTANTIATED

    def _find_handler_for_event(self, event: str) -> Any | None:
        """Return the first entity handler whose patterns match *event*, or None."""
        for handler in self.entity_handlers:
            if any(fnmatch.fnmatch(event, pattern) for pattern in handler.handled_events):
                return handler
        return None

    def _wrap_with_session_guarantee(self, handler: Any) -> Any:
        """Wrap a handler so it ensures a Session node exists before dispatch.

        This prevents orphaned child nodes in Neo4j when session:start
        is not emitted (e.g. --mode single emits execution:start instead).

        If a blob_store is configured on services, large event fields are
        offloaded to blob storage before the handler is invoked.  The handler
        receives the processed clone; the original *data* dict is never mutated.
        """
        services = self.services

        async def wrapper(event: str, data: dict[str, Any]) -> Any:
            session_id = data.get("session_id")
            if session_id and services is not None:
                await services.ensure_session_node(session_id, data)

            handler_data = data
            if services is not None and services.blob_store is not None and session_id:
                timestamp = data.get("timestamp", "")
                if timestamp:
                    node_id = make_node_id(session_id, event, timestamp)
                    handler_data = await process_event_data(
                        data, services.blob_store, session_id, node_id
                    )

            return await handler(event, handler_data)

        return wrapper

    def register_specific_handlers(self, coordinator: Any) -> None:
        """HANDLERS_INSTANTIATED → SPECIFIC_REGISTERED: Register entity handlers for known events."""
        for event in self.remaining_events:
            handler = self._find_handler_for_event(event)
            if handler is not None:
                wrapped = self._wrap_with_session_guarantee(handler)
                unreg = coordinator.hooks.register(
                    event, wrapped, priority=90, name=type(handler).__name__
                )
                self._unregister_fns.append(unreg)

        self.state = MountState.SPECIFIC_REGISTERED

    def register_default_handler(self, coordinator: Any) -> None:
        """SPECIFIC_REGISTERED → READY: Register DefaultHandler for all unclaimed events."""
        if self.default_handler is None:
            raise RuntimeError("instantiate_handlers() must be called first")
        unclaimed = {
            event for event in self.remaining_events if self._find_handler_for_event(event) is None
        }

        self.default_handler.handled_events = unclaimed
        wrapped_default = self._wrap_with_session_guarantee(self.default_handler)

        for event in unclaimed:
            unreg = coordinator.hooks.register(
                event, wrapped_default, priority=90, name="DefaultHandler"
            )
            self._unregister_fns.append(unreg)

        self.state = MountState.READY

    async def run(self, coordinator: Any, events: set[str]) -> Callable:
        """Execute all state transitions and return a cleanup callable.

        Args:
            coordinator: The Amplifier coordinator instance.
            events: Pre-resolved event set from _discover_events().
                    Exclusion filtering is applied here from HookConfig.

        The cleanup callable calls every unregister function returned by
        ``coordinator.hooks.register``, tearing down all registered hooks.
        """
        self.create_services(coordinator)
        self.instantiate_handlers()

        # Apply exclusion filter from config (graph path only)
        assert self.services is not None  # type-narrowing: create_services() was just called
        config = self.services.config
        self.remaining_events = {e for e in events if not config.is_excluded(e)}

        self.register_specific_handlers(coordinator)
        self.register_default_handler(coordinator)

        unregister_fns = self._unregister_fns

        def cleanup() -> None:
            for unreg in unregister_fns:
                unreg()

        return cleanup
