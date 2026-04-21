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
    Logging level.  Default ``"WARNING"``.
base_path : str, optional
    Root directory for JSONL output.  Defaults to the coordinator
    working directory.
exclude_events : list[str], optional
    Event name patterns (fnmatch) to suppress from logging and dispatch.
additional_events : list[str], optional
    Event names to register unconditionally, regardless of capability
    discovery order.  Use to capture events from modules that mount after
    this hook (e.g. ``delegate:agent_spawned``).
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .skill_fetcher import SkillFetcher

log = logging.getLogger(__name__)

__amplifier_module_type__ = "hook"

# Path to the bundle root — works regardless of cache location or mounting order
# Path(__file__).parent = amplifier_module_hook_context_intelligence/
# .parent               = hook-context-intelligence/
# .parent               = modules/
# .parent               = bundle root (where skills/ lives)
_BUNDLE_ROOT = Path(__file__).parent.parent.parent.parent


def _resolve_skill_path(skill_name: str, coordinator: Any) -> Path | None:
    """Resolve the filesystem path for a watched skill's SKILL.md file.

    Primary: queries the ``skills_discovery`` coordinator capability
    (registered by the tool-skills module at mount time).  Returns
    ``metadata.path`` when the capability finds the skill.

    Fallback: returns ``_BUNDLE_ROOT / 'skills' / skill_name / 'SKILL.md'``
    when the parent directory exists on disk.

    Returns ``None`` when neither source can provide a valid path.
    """
    from .skill_fetcher import TOOL_SKILLS_DISCOVERY_CAPABILITY

    # Primary: use skills_discovery capability
    discovery = coordinator.get_capability(TOOL_SKILLS_DISCOVERY_CAPABILITY)
    if discovery is not None:
        metadata = discovery.find(skill_name)
        if metadata is not None:
            log.debug(
                "skill_path_resolved: %s -> %s (via skills_discovery)",
                skill_name,
                metadata.path,
            )
            return metadata.path

    # Fallback: check bundle root location
    fallback = _BUNDLE_ROOT / "skills" / skill_name / "SKILL.md"
    if fallback.parent.exists():
        log.debug(
            "skill_path_resolved: %s -> %s (via bundle root fallback)",
            skill_name,
            fallback,
        )
        return fallback

    log.warning(
        "skill_path_unresolvable: %s — not found via skills_discovery or bundle root", skill_name
    )
    return None


async def _refresh_watched_skills(
    coordinator: Any,
    fetcher: "SkillFetcher",
    skills_capable: bool,
) -> None:
    """Refresh all watched skills by resolving their paths and updating content.

    Branch B (not skills_capable): writes bundled legacy content via
    ``fetcher.write_legacy_content``.

    Branch C (skills_capable): fetches live content from the server via
    ``fetcher.fetch``, wrapped in a try/except to skip individual failures.
    """
    from .skill_fetcher import WATCHED_SKILLS

    for skill_name in WATCHED_SKILLS:
        skill_path = _resolve_skill_path(skill_name, coordinator)
        if skill_path is None:
            continue

        if not skills_capable:
            # Branch B: old server — write bundled legacy content
            fetcher.write_legacy_content(skill_name, skill_path)
        else:
            # Branch C: new server — fetch live content
            try:
                await fetcher.fetch(skill_name, skill_path)
            except Exception as exc:
                # Swallow per-skill failures — one bad skill must not block others
                log.warning("skill_fetch_failed: %s — %s", skill_name, exc)


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
    log.setLevel(resolver.log_level)
    coordinator.register_capability("context_intelligence.config_resolver", resolver)
    events = await _discover_events(coordinator)
    events.update(resolver.additional_events)  # static config, no timing dependency

    # Skill fetch phase — deferred to skills:discovered event
    server_url = resolver.context_intelligence_server_url
    fetcher: SkillFetcher | None = None
    skills_capable: bool = False

    if not server_url:
        log.info("skill_fetch_skipped: no server_url in config")
    else:
        _tentative_fetcher = SkillFetcher(server_url)
        result = await _tentative_fetcher.check_server_version()
        log.info(
            "skill_version_check: server=%s reachable=%s version=%s",
            server_url,
            result.reachable,
            result.version,
        )

        if not result.reachable:
            # Branch A: server unreachable — delegation fallback stays, SKILL.md untouched
            log.info("skill_fetch_branch=A: server unreachable — SKILL.md unchanged")
        else:
            # Reachable: defer skill fetch to skills:discovered event
            fetcher = _tentative_fetcher
            skills_capable = _is_skills_capable(result.version)

            async def on_skills_discovered(event_name: str, data: dict[str, Any]) -> None:
                await _refresh_watched_skills(coordinator, fetcher, skills_capable)

            coordinator.hooks.register(
                "skills:discovered",
                on_skills_discovered,
                priority=50,
                name="SkillFetcher-trigger",
            )
            log.info("skill_fetch_deferred: registered skills:discovered handler")
            # tools mount before hooks in Amplifier: if skills_discovery is
            # already registered (tool-skills already ran), fetch immediately.
            # The event handler above handles the reverse order if it ever occurs.
            if coordinator.get_capability(TOOL_SKILLS_DISCOVERY_CAPABILITY) is not None:
                log.info(
                    "skill_fetch_immediate: skills_discovery already registered "
                    "(tools mount before hooks) — fetching now"
                )
                await _refresh_watched_skills(coordinator, fetcher, skills_capable)

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

        async def on_skill_unloaded(event_name: str, data: dict[str, Any]) -> None:
            if data.get("skill_name") in WATCHED_SKILLS:
                await _refresh_watched_skills(coordinator, fetcher, skills_capable)  # type: ignore[arg-type]

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
