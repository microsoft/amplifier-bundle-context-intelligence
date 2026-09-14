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

import asyncio
import fnmatch
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
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


async def apply_active_dispatchers(
    coordinator: Any,
    resolver: Any,
    logging_handler: Any,
    destinations: dict[str, Any],
) -> tuple[str, list[str]]:
    """Compute active destinations for the live working_dir and install them.

    This is the reusable core of fan-out routing: match_key -> select_active ->
    build one dispatcher per active destination -> set_dispatchers (drain-safe
    swap). Called ONCE at on_session_ready, and AGAIN by the live-set-filters path
    (``context_intelligence.set_ingestion_filters``) after the destinations config
    is updated mid-session. ``set_dispatchers`` bounded-closes the previously
    installed dispatchers, so calling this repeatedly is safe.

    Returns ``(match_key, sorted_active_names)`` for reporting.
    """
    from .config_resolver import Destination
    from .fanout import normalize_match_key, select_active
    from .handlers.logging_handler import _DestinationDispatcher, _write_forwarding_record

    active: dict[str, Destination] = {}
    match_key: str = ""
    if destinations:
        get_cap = getattr(coordinator, "get_capability", None)
        working_dir = get_cap("session.working_dir") if get_cap else None
        if not working_dir:
            log.warning(
                "context-intelligence: session.working_dir capability is unavailable; "
                "fan-out disabled for this session (local JSONL only)."
            )
        else:
            match_key = normalize_match_key(working_dir)
            active = select_active(destinations, match_key)

    # Build one dispatcher per ACTIVE destination. Each construction is isolated:
    # _DestinationDispatcher.__init__ builds auth eagerly, and an environmental
    # auth failure for one destination must not abort local JSONL capture or any
    # other healthy destination.
    dispatchers: list[_DestinationDispatcher] = []
    for d in active.values():
        try:
            dispatchers.append(
                _DestinationDispatcher(
                    name=d.name,
                    url=d.url,
                    api_key=d.api_key,
                    workspace=resolver.workspace,
                    working_dir=resolver.working_dir,
                    dispatch_timeout=resolver.dispatch_timeout,
                    read_timeout=resolver.dispatch_read_timeout,
                    connect_timeout=resolver.dispatch_connect_timeout,
                    failure_threshold=resolver.dispatch_failure_threshold,
                    queue_capacity=resolver.dispatch_queue_capacity,
                    close_drain_timeout=resolver.close_drain_timeout,
                    backoff_initial=resolver.dispatch_backoff_initial,
                    backoff_max=resolver.dispatch_backoff_max,
                    backoff_jitter=resolver.dispatch_backoff_jitter,
                    storage_path=str(resolver.base_path),
                    forwarding_log_dir=resolver.forwarding_log_dir,
                    auth_mode=d.auth_mode,
                    auth_resource=d.auth_resource,
                )
            )
        except Exception as exc:
            _write_forwarding_record(
                resolver.forwarding_log_dir,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "destination": d.name,
                    "url": d.url,
                    "kind": "dispatcher_construction_failed",
                    "http_status": None,
                    "session_id": "",
                    "workspace": resolver.workspace or "",
                    "detail": (f"dispatcher construction failed: {type(exc).__name__}: {exc}"),
                },
            )
            log.error(
                "context-intelligence: destination %r failed to construct its dispatcher "
                "-- dispatch disabled for this destination only; local JSONL and any "
                "other configured destinations are unaffected.",
                d.name,
                exc_info=True,
            )
    await logging_handler.set_dispatchers(dispatchers)

    # Self-healing catch-up for anything a PREVIOUS session left undelivered.
    # Started after the dispatchers are installed so the live path owns this
    # session's own events and the sweep only ever chases what is already behind.
    #
    # Stashed on the hook state rather than returned, so this function's arity --
    # which set_ingestion_filters also depends on -- stays unchanged. Re-running
    # this function (a mid-session filter swap) cancels the previous round's
    # sweeps first: the destination set may have just changed underneath them.
    get_cap_state = getattr(coordinator, "get_capability", None)
    state = get_cap_state("context_intelligence._hook_state") if get_cap_state else None
    if isinstance(state, dict):
        for previous in state.get("sweep_tasks") or []:
            previous.cancel()
        state["sweep_tasks"] = schedule_backlog_sweeps(resolver, dispatchers)
    else:
        schedule_backlog_sweeps(resolver, dispatchers)

    if not destinations:
        log.info("context-intelligence fan-out: no destinations configured — local JSONL only")
    elif active:
        log.info("context-intelligence fan-out: active -> %s", ", ".join(sorted(active)))
    else:
        log.warning(
            "context-intelligence fan-out: routed to none (local-only) for working_dir=%s",
            match_key,
        )

    return match_key, sorted(active)


def _describe_sweep(report: Any) -> None:
    """Console policy for one sweep result -- the loudness gate.

    The question this asks is deliberately NOT "is there a backlog?" (which is
    what the shutdown warning asks today, and why a perfectly healthy
    destination warned on 215 of 215 measured shutdowns). It asks **"is the
    backlog being retired?"** -- a question about progress over time, which only
    became answerable once a watermark existed.

    Quiet means the sweep delivered something and nothing is stuck. It never
    means "we hid a failure": the durable forwarding-*.jsonl record is written
    by the dispatcher regardless of anything decided here.
    """
    # LOUD 4 -- outside the age bound. These will NEVER be delivered
    # automatically, so the bound must be audible or it becomes a silent drop.
    if report.stranded_sessions:
        log.warning(
            "context-intelligence %s: %d session(s) hold events older than the sweep"
            " window (oldest %.0fh) and will NOT be delivered automatically."
            " Run: context-intelligence-upload",
            report.destination,
            report.stranded_sessions,
            report.oldest_stranded_age_hours,
        )

    if not report.had_work:
        return

    # LOUD 2 -- work exists and nothing moved. This is the real "delivery is
    # broken" signal; the auth-only circuit breaker cannot produce it.
    if not report.made_progress:
        log.warning(
            "context-intelligence %s: %d byte(s) of backlog and NOT shrinking"
            " -- sweep delivered 0 event(s).%s",
            report.destination,
            report.backlog_bytes_remaining,
            f" Last error: {report.last_error}" if report.last_error else "",
        )
        return

    # Progress, but still behind: proportional, and INFO rather than WARNING --
    # catching up is not a problem the user can act on.
    if report.backlog_bytes_remaining:
        log.info(
            "context-intelligence %s: delivered %d backlogged event(s);"
            " %d byte(s) remain -- catching up, no action needed.",
            report.destination,
            report.events_delivered,
            report.backlog_bytes_remaining,
        )
        return

    log.info(
        "context-intelligence %s: delivered %d backlogged event(s) -- backlog clear.",
        report.destination,
        report.events_delivered,
    )


def schedule_backlog_sweeps(resolver: Any, dispatchers: list[Any]) -> list[Any]:
    """Start one background catch-up sweep per active destination.

    Fire-and-forget by contract: event delivery is best-effort and must never
    block or slow a session. The tasks are cancelled during cleanup, and whatever
    the watermark says at that moment is simply where the next session resumes.
    """
    from .handlers.backlog_sweep import BacklogSweeper, SweepBounds

    if not resolver.sweep_enabled:
        log.debug("context-intelligence: backlog sweep disabled by config")
        return []

    bounds = SweepBounds(
        max_events=resolver.sweep_max_events,
        max_age_hours=resolver.sweep_max_age_hours,
        max_sessions=resolver.sweep_max_sessions,
        concurrency=resolver.sweep_concurrency,
    )
    project_dir = resolver.base_path / resolver.project_slug

    tasks: list[Any] = []
    for dispatcher in dispatchers:
        sweeper: Any = BacklogSweeper(
            destination=dispatcher.name,
            url=dispatcher.url,
            # Reuse the dispatcher's own strategy so a sweep cannot acquire a
            # second Entra token or diverge from live-path auth.
            auth=dispatcher.auth_strategy,
            project_dir=project_dir,
            bounds=bounds,
            timeout=resolver.dispatch_timeout,
        )

        async def _run(s: Any = sweeper) -> None:
            try:
                _describe_sweep(await s.run())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("context-intelligence backlog sweep failed", exc_info=True)

        task = asyncio.create_task(_run())
        task.add_done_callback(_retrieve_sweep_exception)
        tasks.append(task)
    return tasks


def _retrieve_sweep_exception(task: Any) -> None:
    """Retrieve a finished sweep's exception so asyncio never warns about it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.debug("context-intelligence backlog sweep raised", exc_info=exc)


def _read_destinations_from_settings(settings_path: str) -> dict[str, Any]:
    """Read the raw ``destinations`` block from a settings.yaml on disk.

    The hook itself never reads settings.yaml (the kernel merges/expands it and
    hands mount() a config dict). The set-filters path re-reads the file so an
    exclude edit made to it during a session is reflected in the running session.

    Looks first under ``overrides.hook-context-intelligence.config.destinations``
    (the settings.yaml shape), then falls back to a top-level ``destinations:``
    key. Returns {} if neither is
    present. Does NOT expand ${VAR}; callers writing on-disk config to set filters
    are expected to write already-resolved values (same contract the kernel
    applies before mount()).
    """
    import yaml

    with open(settings_path) as fh:
        doc = yaml.safe_load(fh) or {}
    try:
        nested = doc["overrides"]["hook-context-intelligence"]["config"]["destinations"]
        if isinstance(nested, dict):
            return nested
    except (KeyError, TypeError):
        pass
    top = doc.get("destinations")
    return top if isinstance(top, dict) else {}


def _patch_inherited_hook_config(coordinator: Any, raw_destinations: dict[str, Any]) -> bool:
    """Bundle-only: write the new destinations into the in-memory session config
    that FUTURE spawned sub-sessions inherit.

    A spawned sub-session copies its parent session's config dict as its
    starting point; it does not re-read settings.yaml. That dict is reachable
    from the coordinator this hook already holds. Updating this hook's entry in
    it is what carries a live filter change to every sub-session spawned
    afterward, without touching any module outside this bundle.

    Returns True if a hook-context-intelligence entry was patched.
    """
    session = getattr(coordinator, "session", None)
    cfg = (
        getattr(session, "config", None)
        if session is not None
        else getattr(coordinator, "config", None)
    )
    if not isinstance(cfg, dict):
        return False
    hooks = cfg.get("hooks")
    if not isinstance(hooks, list):
        return False
    patched = False
    for entry in hooks:
        if isinstance(entry, dict) and entry.get("module") == "hook-context-intelligence":
            entry.setdefault("config", {})["destinations"] = raw_destinations
            patched = True
    return patched


def _exclude_map(destinations: dict[str, Any]) -> dict[str, list[str]]:
    """Normalized {name: sorted(exclude patterns)} for a Destination map."""
    return {name: sorted(dest.exclude) for name, dest in destinations.items()}


def _disk_exclude_map(raw: dict[str, Any]) -> dict[str, list[str]]:
    """Normalized {name: sorted(exclude patterns)} for a raw on-disk block."""
    out: dict[str, list[str]] = {}
    for name, spec in raw.items():
        if isinstance(spec, dict):
            out[name] = sorted(spec.get("exclude") or [])
    return out


def _compare_exclude_maps(
    live: dict[str, list[str]],
    disk: dict[str, list[str]],
    dropped: set[str] | None = None,
) -> dict[str, Any]:
    """Compare live vs on-disk excludes over the destinations the FILE declares.

    Why not set equality: the live filter comes from the kernel's *merged*
    settings (global -> project -> local -> session, deep-merged), while
    ``_read_destinations_from_settings`` reads exactly ONE file. A workspace
    that declares or overrides destinations at project/local scope therefore
    legitimately has live destinations the file has never heard of. Requiring
    ``live == disk`` reports those as a fault and fails a filter that is in fact
    correct.

    So the comparison is scoped to the destinations actually present in the file
    that was read: for each of those, the live exclude must match. Live-only
    destinations are not evidence of inconsistency -- but they are not silently
    ignored either: they are returned in ``unverified`` so the caller can see
    exactly which destinations this file could not speak for.

    ``dropped`` is the set of destinations that validation REJECTED as
    misconfigured (missing url, unusable api_key, ...). Those are absent from
    the live filter for a reason that has nothing to do with the exclude
    filter, so counting them as a filter inconsistency is a false alarm --
    and an easy one to trigger, since a single unexpanded ``${VAR}`` is
    enough. They are reported under ``dropped_by_validation`` and excluded
    from the consistent/inconsistent decision.

    Returns {consistent, mismatched, missing_live, dropped_by_validation,
    unverified, live, disk}.
    """
    dropped = dropped or set()
    mismatched = {
        name: {"live": live.get(name), "disk": patterns}
        for name, patterns in disk.items()
        if name in live and live[name] != patterns
    }
    # Declared on disk, absent from the live filter, and NOT explained by
    # validation dropping it -- this is the case where the filter may be stale.
    missing_live = sorted(name for name in disk if name not in live and name not in dropped)
    # Declared on disk but rejected by validation: a destination-config problem,
    # not a filter problem. Surfaced, but never a consistency fault.
    dropped_by_validation = sorted(name for name in disk if name not in live and name in dropped)
    # Live destinations this file cannot speak for (declared in another scope).
    unverified = sorted(name for name in live if name not in disk)
    return {
        "consistent": not mismatched and not missing_live,
        "mismatched": mismatched,
        "missing_live": missing_live,
        "dropped_by_validation": dropped_by_validation,
        "unverified": unverified,
        "live": live,
        "disk": disk,
    }


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

    # --- Per-destination validation (C3) ---
    # Never raises: a misconfigured destination is logged (per-destination,
    # loud) and dropped from the returned dict. Local JSONL capture has no
    # dependency on any destination and must survive regardless of how many
    # (or all) configured destinations fail validation.
    all_destinations = resolver.validate_destinations()

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
        # Background catch-up sweeps, owned here so cleanup() can cancel them.
        "sweep_tasks": [],
    }
    coordinator.register_capability("context_intelligence._hook_state", _hook_state)

    async def set_ingestion_filters(
        raw_destinations: dict[str, Any] | None = None,
        settings_path: str | None = None,
        verify_disk: bool = True,
    ) -> dict[str, Any]:
        """Apply destination routing to this session's live hook, without a restart.

        Source of the new destinations block (exactly one):
          - ``settings_path``: re-read the block from a settings.yaml on disk
            (the real "user edited settings.yaml" path).
          - ``raw_destinations``: an explicit block (used to inject a
            live-only patch, e.g. to exercise the live-vs-disk fault check).

        Steps: update the resolver's destinations (cache-invalidated) ->
        re-validate -> refresh shared hook state -> rebuild + drain-safe swap the
        dispatchers via apply_active_dispatchers. Returns a report of the new
        active destinations and their include/exclude.

        When ``verify_disk`` and ``settings_path`` are both given, the resulting
        live filter is cross-checked against the on-disk block. A mismatch is
        REPORTED (``disk_consistent=False`` plus ``disk_check``), never raised:
        by the time the check runs the dispatcher swap has already happened, so
        raising would throw away the only record of what actually went live and
        leave the caller unable to tell "nothing was applied" from "everything
        was applied but I cannot attest it". The caller decides what an
        unattested-but-applied filter is worth.

        Failures BEFORE/DURING the swap still raise -- those mean the filter did
        not go live, which is a different and genuinely loud condition.
        """
        disk_raw = _read_destinations_from_settings(settings_path) if settings_path else None
        new_raw = raw_destinations if raw_destinations is not None else disk_raw
        if new_raw is None:
            raise ValueError("set_ingestion_filters: provide raw_destinations or settings_path")

        resolver.update_destinations(new_raw)
        new_dests = resolver.validate_destinations()
        _hook_state["destinations"] = new_dests
        match_key, active = await apply_active_dispatchers(
            coordinator, resolver, logging_handler, new_dests
        )

        # Bundle-only propagation to FUTURE sub-sessions: update the session
        # config snapshot a spawned sub-session copies from its parent.
        inherited_patched = _patch_inherited_hook_config(coordinator, new_raw)

        report: dict[str, Any] = {
            "match_key": match_key,
            "active": active,
            "inherited_snapshot_patched": inherited_patched,
            "destinations": {
                name: {"include": list(d.include), "exclude": list(d.exclude)}
                for name, d in new_dests.items()
            },
            "disk_consistent": None,
        }
        if verify_disk and settings_path is not None:
            check = _compare_exclude_maps(
                _exclude_map(new_dests),
                _disk_exclude_map(_read_destinations_from_settings(settings_path)),
                dropped=set(new_raw) - set(new_dests),
            )
            report["disk_consistent"] = check["consistent"]
            report["disk_check"] = check
            if not check["consistent"]:
                # Applied-but-unattested. Reported, not raised -- see docstring.
                log.warning(
                    "set_ingestion_filters: filters ARE live but disagree with %s "
                    "(mismatched=%r missing_live=%r); reporting disk_consistent=False.",
                    settings_path,
                    check["mismatched"],
                    check["missing_live"],
                )
        return report

    def verify_ingestion_consistency(settings_path: str) -> dict[str, Any]:
        """Fail-loud compare of the session's LIVE exclude filter vs on-disk.

        Pure check — mutates nothing. Raises when, for a destination the file
        DECLARES, the running session's live exclude disagrees (live patched but
        file not written; file written but session not reapplied).

        Scoped to the destinations present in the file that was read: the live
        filter is built from the kernel's merged settings (global -> project ->
        local -> session), so a single file cannot speak for destinations
        declared in another scope. Those are returned in ``unverified`` rather
        than treated as a fault -- see ``_compare_exclude_maps``.
        """
        _validated = resolver.validate_destinations()
        check = _compare_exclude_maps(
            _exclude_map(_validated),
            _disk_exclude_map(_read_destinations_from_settings(settings_path)),
            dropped=resolver.raw_destination_names - set(_validated),
        )
        if not check["consistent"]:
            raise RuntimeError(
                "context-intelligence: live ingestion filter disagrees with on-disk "
                f"settings {settings_path!r} (mismatched={check['mismatched']!r} "
                f"declared_on_disk_but_not_live={check['missing_live']!r})."
            )
        return {
            "live_exclude": check["live"],
            "disk_exclude": check["disk"],
            "unverified": check["unverified"],
            "consistent": True,
        }

    coordinator.register_capability(
        "context_intelligence.set_ingestion_filters", set_ingestion_filters
    )
    coordinator.register_capability(
        "context_intelligence.verify_ingestion_consistency", verify_ingestion_consistency
    )

    async def cleanup() -> None:
        # Cancel catch-up sweeps FIRST. Delivery is best-effort and must never
        # slow process exit; the watermark already on disk is where the next
        # session resumes from.
        for task in _hook_state.get("sweep_tasks") or []:
            task.cancel()
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

    state = coordinator.get_capability("context_intelligence._hook_state")
    if state is None:
        log.warning("on_session_ready: hook state not found — mount() may not have run")
        return

    resolver = state["resolver"]
    logging_handler = state["logging_handler"]
    unregister_fns = state["unregister_fns"]
    destinations: dict[str, Destination] = state["destinations"]

    # --- §C.3 mandatory startup consistency check (always-fire, read-only) ---
    # Compare what the READERS will compute (canonicalized env var, defaulting
    # when unset) against what the WRITER resolved (canonicalized base_path).
    # When they disagree the writer and readers target different roots — a silent
    # split that this check makes LOUD.  Never writes os.environ (multiplexed-safe).
    #
    # This fires in BOTH directions, covering the two ways relocation can break:
    #   1. env SET, writer at a different root  — binding did not expand, or a
    #      config override fought the env var.
    #   2. env UNSET, writer NOT at default     — someone relocated via
    #      config.base_path / settings.yaml, which the env-only readers CANNOT
    #      see (relocation is reader-visible ONLY via the env var). The earlier
    #      `if _env_raw:` guard missed this case entirely.
    import os  # local import — only this path needs the process env

    from context_intelligence.config import reader_writer_roots_disagree

    _ENV_VAR = "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"
    _env_raw = os.environ.get(_ENV_VAR)  # may be None/empty → readers fall to default
    # Pure, unit-tested divergence core (see tests/test_base_path_parity.py).
    _disagree, _reader_root, _writer_root = reader_writer_roots_disagree(
        _env_raw, resolver.base_path
    )
    if _disagree:
        log.warning(
            "context-intelligence: writer base_path (%s) and reader root (%s) disagree"
            " — readers (discover, recipe, navigation skills) resolve the root ONLY from"
            " %s, so captures written under %s will be invisible to them."
            ' Relocate via the env var (or bind base_path: "${%s:}" in the hook config),'
            " not via config.base_path alone.",
            _writer_root,
            _reader_root,
            _ENV_VAR,
            _writer_root,
            _ENV_VAR,
        )
    else:
        # Positive confirmation at the operator's surface (default level is INFO).
        # Fires ONLY when relocation is actually in effect, so the operator who
        # relocated can SEE it took effect — closing the "success and silent
        # misconfiguration look identical at the moment of action" gap. Stays
        # silent in the default (non-relocated) case so it adds no noise.
        from context_intelligence.config import DEFAULT_BASE_PATH

        if _writer_root != DEFAULT_BASE_PATH:
            log.info(
                "context-intelligence: capturing to %s"
                " (readers resolve the same root from %s)."
                " Relocation is per-process, not per-session.",
                _writer_root,
                _ENV_VAR,
            )

    # --- Destination selection + dispatcher install (C2: working_dir ONLY) ---
    # Factored into apply_active_dispatchers so the live-set-filters capability can
    # re-run the exact same routing computation mid-session.
    await apply_active_dispatchers(coordinator, resolver, logging_handler, destinations)

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
