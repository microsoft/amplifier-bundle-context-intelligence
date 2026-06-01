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
    (see HookConfigResolver.workspace).
log_level : str, optional
    Logging level.  Default ``"WARNING"``.
base_path : str, optional
    Root directory for JSONL output.  Defaults to the coordinator
    working directory.
exclude_events : list[str], default ["llm:stream_*delta"]
    Event name patterns (fnmatch) to suppress from both local JSONL logging and
    graph-server dispatch.  Defaults to ``["llm:stream_*delta"]``, matching the
    transient per-token streaming delta category while sparing the structural
    streaming events (block_start, block_end, stream_aborted).
    Set ``exclude_events: []`` to disable the filter and log/dispatch every event.
additional_events : list[str], optional
    Event names to register unconditionally, regardless of capability
    discovery order.  Use to capture events from modules that mount after
    this hook (e.g. ``delegate:agent_spawned``).
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
    - Registers HookConfigResolver as ``context_intelligence.hook_config_resolver`` capability
    - LoggingHandler  — writes events.jsonl + dispatches to CI server
    """
    from .config_resolver import HookConfigResolver
    from .handlers.logging_handler import LoggingHandler

    resolver = HookConfigResolver(config, coordinator)
    log.setLevel(resolver.log_level)
    coordinator.register_capability("context_intelligence.hook_config_resolver", resolver)

    unregister_fns: list[Callable[[], None]] = []

    logging_handler = LoggingHandler(resolver)

    # Share mutable state with on_session_ready via a private capability.
    # The cleanup closure closes over unregister_fns by reference — any entries
    # appended by on_session_ready() will be torn down automatically.
    _hook_state = {
        "unregister_fns": unregister_fns,
        "logging_handler": logging_handler,
        "resolver": resolver,
    }
    coordinator.register_capability("context_intelligence._hook_state", _hook_state)

    async def cleanup() -> None:
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
            coordinator.register_capability("context_intelligence.hook_config_resolver", None)
        except Exception:
            pass
        try:
            coordinator.register_capability("context_intelligence._hook_state", None)
        except Exception:
            pass

    return cleanup


async def on_session_ready(coordinator: Any) -> None:
    """Called after all modules mount — finalize event subscription.

    Discovers the full event set (ALL_EVENTS + module contributions +
    legacy capability + additional_events config) and registers the
    LoggingHandler for every active event. Runs after every module has
    mounted, so late-contributed events are captured.
    """
    state = coordinator.get_capability("context_intelligence._hook_state")
    if state is None:
        log.warning("on_session_ready: hook state not found — mount() may not have run")
        return

    resolver = state["resolver"]
    logging_handler = state["logging_handler"]
    unregister_fns = state["unregister_fns"]

    # Step 1: canonical kernel events + all module contributions
    # _discover_events returns: set(ALL_EVENTS) + collect_contributions
    #                           + legacy get_capability("observability.events")
    events = await _discover_events(coordinator)

    # Step 2: static additional_events from config (backward compat)
    events.update(resolver.additional_events)

    # Step 3: conditional exclude filter
    exclude = resolver.exclude_events
    active = (
        {e for e in events if not any(fnmatch.fnmatch(e, p) for p in exclude)}
        if exclude
        else events
    )

    # Step 4: register LoggingHandler for every active event
    for event in sorted(active):
        unreg = coordinator.hooks.register(
            event, logging_handler, priority=100, name="LoggingHandler"
        )
        unregister_fns.append(unreg)

    log.info("on_session_ready: registered %d events", len(active))
