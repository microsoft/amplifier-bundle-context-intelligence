"""Context Intelligence hook — thin event forwarder.

Writes session events to local JSONL and dispatches them to the
Context Intelligence server when ``context_intelligence_server_url``
is configured.

Configuration keys
------------------
context_intelligence_server_url : str, optional
    Base URL of the Context Intelligence server, e.g.
    ``http://localhost:8000``.  When set, every event is POSTed
    to ``{url}/events``.
workspace : str, optional
    Workspace identifier used to scope graph data on the server.
    Resolved automatically from the coordinator when not set
    (see ConfigResolver.workspace).
log_level : str, optional
    Logging level.  Default ``"INFO"``.
base_path : str, optional
    Root directory for JSONL output.  Defaults to the coordinator
    working directory.
exclude_events : list[str], optional
    Event name patterns (fnmatch) to suppress from logging and dispatch.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)

__amplifier_module_type__ = "hook"


async def _discover_events(coordinator: Any) -> set[str]:
    """Union of ALL_EVENTS + module contributions + legacy capability."""
    from amplifier_core.events import ALL_EVENTS  # type: ignore[import-not-found]

    discovered: set[str] = set(ALL_EVENTS)

    contributions = await coordinator.collect_contributions("observability.events")
    for event_list in contributions:
        discovered.update(event_list)

    capability = coordinator.get_capability("observability.events")
    if capability is not None:
        raw = capability() if callable(capability) else capability
        if isinstance(raw, (list, set, frozenset, tuple)):
            discovered.update(raw)

    return discovered


async def mount(
    coordinator: Any, config: dict[str, Any]
) -> Callable[[], Coroutine[Any, Any, None]]:
    """Mount the context-intelligence hook.

    Always:
    - Registers ConfigResolver as ``context_intelligence.config_resolver`` capability
    - LoggingHandler  — writes events.jsonl + dispatches to CI server
    """
    from .config_resolver import ConfigResolver
    from .handlers.logging_handler import LoggingHandler

    resolver = ConfigResolver(config, coordinator)
    coordinator.register_capability("context_intelligence.config_resolver", resolver)
    events = await _discover_events(coordinator)

    exclude = resolver.exclude_events
    active_events = {e for e in events if not any(fnmatch.fnmatch(e, p) for p in exclude)}

    logging_handler = LoggingHandler(resolver)
    unregister_fns: list[Callable[[], None]] = []
    for event in active_events:
        unreg = coordinator.hooks.register(
            event, logging_handler, priority=100, name="LoggingHandler"
        )
        unregister_fns.append(unreg)

    async def cleanup() -> None:
        # Drain pending dispatch tasks and close the HTTP client *before*
        # unregistering hooks — this gives in-flight POSTs a chance to land.
        try:
            await logging_handler.close()
        except Exception:
            log.debug("LoggingHandler.close() failed during cleanup")

        for unreg in unregister_fns:
            try:
                unreg()
            except Exception:
                pass
        try:
            coordinator.register_capability("context_intelligence.config_resolver", None)
        except Exception:
            pass

    return cleanup
