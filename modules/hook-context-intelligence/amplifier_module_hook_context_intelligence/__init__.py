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

import asyncio
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
    from .skill_fetcher import (
        TOOL_SKILLS_DISCOVERY_CAPABILITY,
        WATCHED_SKILLS,
        SkillFetcher,
        _is_skills_capable,
    )

    resolver = ConfigResolver(config, coordinator)
    coordinator.register_capability("context_intelligence.config_resolver", resolver)
    events = await _discover_events(coordinator)

    # Skill fetch phase — runs before logging handler registration
    server_url = resolver.context_intelligence_server_url
    fetcher: SkillFetcher | None = None
    skills_discovery = None

    if not server_url:
        log.info("skill_fetch_skipped: no server_url configured")
    else:
        skills_discovery = coordinator.get_capability(TOOL_SKILLS_DISCOVERY_CAPABILITY)
        if skills_discovery is None:
            log.info("skill_fetch_skipped: skills_discovery capability not available at mount time")
        else:
            _tentative_fetcher = SkillFetcher(server_url)
            result = await _tentative_fetcher.check_server_version()
            log.info(
                "skill_fetch: server_url=%s reachable=%s version=%s",
                server_url,
                result.reachable,
                result.version,
            )

            if not result.reachable:
                # Branch A: server unreachable — delegation fallback stays, SKILL.md untouched
                log.info("skill_fetch_branch: A (server unreachable) — SKILL.md untouched")
            elif not _is_skills_capable(result.version):
                # Branch B: old server (DEPRECATED) — write bundled legacy content
                log.info(
                    "skill_fetch_branch: B (old server v%s) — writing legacy content",
                    result.version,
                )
                for skill_name in WATCHED_SKILLS:
                    metadata = skills_discovery.find(skill_name)
                    if metadata is None:
                        log.info(
                            "skill_fetch_skipped: %s — not found in skills_discovery", skill_name
                        )
                        continue
                    _tentative_fetcher.write_legacy_content(skill_name, metadata.path)
                    log.info(
                        "skill_legacy_written [DEPRECATED]: %s -> %s", skill_name, metadata.path
                    )
            else:
                # Branch C: new server — fetch from server
                log.info(
                    "skill_fetch_branch: C (new server v%s) — fetching from server", result.version
                )
                fetcher = _tentative_fetcher
                for skill_name in WATCHED_SKILLS:
                    metadata = skills_discovery.find(skill_name)
                    if metadata is None:
                        log.info(
                            "skill_fetch_skipped: %s — not found in skills_discovery", skill_name
                        )
                        continue
                    log.info("skill_fetch_attempt: %s -> %s", skill_name, metadata.path)
                    try:
                        fetched = await fetcher.fetch(skill_name, metadata.path)
                        log.info(
                            "skill_fetch_result: %s fetched=%s path=%s",
                            skill_name,
                            fetched,
                            metadata.path,
                        )
                    except Exception as exc:
                        log.warning("skill_fetch_failed during mount: %s — %s", skill_name, exc)

    exclude = resolver.exclude_events
    active_events = {e for e in events if not any(fnmatch.fnmatch(e, p) for p in exclude)}

    logging_handler = LoggingHandler(resolver)
    unregister_fns: list[Callable[[], None]] = []
    for event in active_events:
        unreg = coordinator.hooks.register(
            event, logging_handler, priority=100, name="LoggingHandler"
        )
        unregister_fns.append(unreg)

    # skill:unloaded handler — re-fetches watched skills when they are reloaded
    if fetcher is not None:
        # Invariant: fetcher is only created when skills_discovery is non-None
        assert skills_discovery is not None

        async def on_skill_unloaded(event_name: str, data: dict[str, Any]) -> None:
            skill_name = data.get("skill_name")
            if skill_name not in WATCHED_SKILLS:
                return
            metadata = skills_discovery.find(skill_name)
            if metadata is None:
                return
            try:
                asyncio.create_task(fetcher.fetch(skill_name, metadata.path))
            except RuntimeError:
                # Event loop is closing; skip scheduling
                pass

        unreg_skill = coordinator.hooks.register(
            "skill:unloaded", on_skill_unloaded, priority=100, name="SkillFetcher"
        )
        unregister_fns.append(unreg_skill)

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
