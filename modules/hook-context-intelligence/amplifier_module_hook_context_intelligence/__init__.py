"""Context Intelligence hook — thin event forwarder.

Writes session events to local JSONL and, when fan-out destinations are configured,
dispatches them concurrently to one or more Context Intelligence servers based on
the session's working directory.

Configuration keys
------------------
destinations : dict, optional
    Fan-out destinations keyed by stable name. Each entry is a dict with:
      url     : str  — base URL of the CI server (app expands ${VAR} before mount).
      api_key : str  — bearer token.
      include : list[str], optional — pathspec (gitwildmatch) patterns; default ["**"].
      exclude : list[str], optional — exclude-wins patterns; default []
    Configured via overrides.hook-context-intelligence.config.destinations in settings.yaml.
    App-cli deep-merges project-over-user, so per-project overrides patch individual
    destination sub-keys without clobbering others.

context_intelligence_server_url : str, optional
    DEPRECATED. When set (and no `destinations` is given) it synthesizes a single
    "default" destination matching all sessions (include: ["**"]). Migrate to
    `destinations` for multi-server fan-out.
context_intelligence_api_key : str, optional
    DEPRECATED. Companion to context_intelligence_server_url.
workspace : str, optional
    Workspace identifier used to scope graph data on the server.
    Resolved automatically from the coordinator when not set
    (see ConfigResolver.workspace).
log_level : str, optional
    Logging level.  Default ``"WARNING"``
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
    - LoggingHandler  — writes events.jsonl + dispatches to CI server(s)
    """
    from .config_resolver import HookConfigResolver
    from .handlers.logging_handler import LoggingHandler

    resolver = HookConfigResolver(config, coordinator)
    log.setLevel(resolver.log_level)
    coordinator.register_capability("context_intelligence.hook_config_resolver", resolver)

    unregister_fns: list[Callable[[], None]] = []

    # --- Fail-fast validation (C3) ---
    all_destinations = resolver.validate_destinations()  # raises ValueError on misconfiguration

    # --- Migration warning (S1) ---
    # Detect legacy scalar config key rather than env var (D1: hook no longer reads env).
    # The app expands ${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL} into config before mount.
    if not config.get("destinations") and resolver.context_intelligence_server_url:
        log.warning(
            "context-intelligence: using legacy single-server config "
            "(context_intelligence_server_url). Migrate to "
            "overrides.hook-context-intelligence.config.destinations for multi-server fan-out."
        )

    logging_handler = LoggingHandler(resolver)

    # Share mutable state with on_session_ready via a private capability.
    # The cleanup closure closes over unregister_fns by reference — any entries
    # appended by on_session_ready() will be torn down automatically.
    _hook_state = {
        "unregister_fns": unregister_fns,
        "logging_handler": logging_handler,
        "resolver": resolver,
        "destinations": all_destinations,
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

    Also selects active fan-out destinations based on session.working_dir
    and installs per-destination dispatchers into the LoggingHandler.
    """
    from .config_resolver import Destination
    from .handlers.logging_handler import _DestinationDispatcher
    from .fanout import normalize_match_key, select_active

    state = coordinator.get_capability("context_intelligence._hook_state")
    if state is None:
        log.warning("on_session_ready: hook state not found — mount() may not have run")
        return

    resolver = state["resolver"]
    logging_handler = state["logging_handler"]
    unregister_fns = state["unregister_fns"]
    destinations: dict[str, Destination] = state["destinations"]

    # --- Destination selection (C2: working_dir capability ONLY, fail-loud) ---
    active: dict[str, Destination] = {}
    match_key: str = ""
    if destinations:
        get_cap = getattr(coordinator, "get_capability", None)
        working_dir = get_cap("session.working_dir") if get_cap else None
        if not working_dir:
            # working_dir capability unavailable. Do NOT raise here: the kernel
            # CATCHES on_session_ready exceptions (Phase 6, _session_init.py) and
            # continues the session, so a raise is swallowed AND aborts the rest of
            # this callback — silently disabling ALL capture, including the local
            # JSONL the design guarantees is always written. Degrade to local-only
            # (active = {}) with a discoverable WARNING and fall through so the
            # LoggingHandler is still registered below.
            log.warning(
                "context-intelligence: session.working_dir capability is unavailable; "
                "fan-out disabled for this session (local JSONL only)."
            )
        else:
            match_key = normalize_match_key(str(working_dir))
            active = select_active(destinations, match_key)

    # Build one dispatcher per ACTIVE destination (D9).
    dispatchers = [
        _DestinationDispatcher(
            name=d.name,
            url=d.url,
            api_key=d.api_key,
            workspace=resolver.workspace,
            dispatch_timeout=resolver.dispatch_timeout,
            read_timeout=resolver.dispatch_read_timeout,
            failure_threshold=resolver.dispatch_failure_threshold,
            queue_capacity=resolver.dispatch_queue_capacity,
            close_drain_timeout=resolver.close_drain_timeout,
            backoff_initial=resolver.dispatch_backoff_initial,
            backoff_max=resolver.dispatch_backoff_max,
            backoff_jitter=resolver.dispatch_backoff_jitter,
            storage_path=str(resolver.base_path),
            auth_mode=d.auth_mode,
            auth_resource=d.auth_resource,
        )
        for d in active.values()
    ]
    await logging_handler.set_dispatchers(dispatchers)

    # --- Fan-out log line (S2) ---
    if not destinations:
        log.info("context-intelligence fan-out: no destinations configured — local JSONL only")
    elif active:
        log.info("context-intelligence fan-out: active -> %s", ", ".join(sorted(active)))
    else:
        log.warning(
            "context-intelligence fan-out: routed to none (local-only) for working_dir=%s",
            match_key,
        )

    # Step 1: canonical kernel events + all module contributions
    # _discover_events returns: set(ALL_EVENTS) + collect_contributions
    #                           + legacy get_capability("observability.events")
    events = await _discover_events(coordinator)

    # Step 2: static additional_events from config (backward compat)
    events.update(resolver.additional_events)

    # Step 3: conditional exclude filter
    exclude = resolver.exclude_events
    active_events = (
        {e for e in events if not any(fnmatch.fnmatch(e, p) for p in exclude)}
        if exclude
        else events
    )

    # Step 4: register LoggingHandler for every active event
    for event in sorted(active_events):
        unreg = coordinator.hooks.register(
            event, logging_handler, priority=100, name="LoggingHandler"
        )
        unregister_fns.append(unreg)

    log.info("on_session_ready: registered %d events", len(active_events))
