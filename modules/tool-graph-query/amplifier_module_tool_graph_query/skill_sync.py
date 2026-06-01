"""skill_sync — offline integrity + per-skill sync helpers.

Provides two helpers consumed by the graph-analyst sub-session:

_invalidate_if_drift
    Q2 offline-drift sidecar invalidation: compares the stored content hash
    against the current SKILL.md content and removes both sidecars when they
    no longer match (drift).  Content is always preserved.

_sync_skill
    Integrity pre-flight + conditional fetch: runs offline integrity when no
    server is reachable, or delegates to SkillFetcher when the server responds.
    One bad skill must not break the session — all fetch errors are logged and
    swallowed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .skill_fetcher import (
    _CONTENT_HASH_FILENAME,
    _ETAG_FILENAME,
    WATCHED_SKILLS,
    SkillFetcher,
    _sha256,
)

log = logging.getLogger(__name__)

# Capability identifiers consumed by graph_query_tool.py
TOOL_SKILLS_DISCOVERY_CAPABILITY: str = "skills_discovery"
_GRAPH_QUERY_TOOL_CAPABILITY: str = "context_intelligence._graph_query_tool"

__all__ = [
    "TOOL_SKILLS_DISCOVERY_CAPABILITY",
    "WATCHED_SKILLS",
    "_GRAPH_QUERY_TOOL_CAPABILITY",
    "on_session_ready",
]


def _invalidate_if_drift(
    skill_name: str,
    skill_path: Path,
    etag_path: Path,
    content_hash_path: Path,
) -> None:
    """Remove both sidecar files when offline content has drifted.

    Returns immediately (noop) when:
    - *skill_path* does not exist, or
    - *content_hash_path* does not exist (no baseline to compare against).

    When the stored hash matches the current file hash the skill is in sync
    and both sidecars are left untouched.  When the hashes diverge, both
    *etag_path* and *content_hash_path* are deleted so that the next
    online sync will perform an unconditional GET rather than send a stale
    ``If-None-Match``.  The content file is never deleted.
    """
    if not (skill_path.exists() and content_hash_path.exists()):
        return

    stored_hash = content_hash_path.read_text().strip()
    current_hash = _sha256(skill_path)

    if stored_hash == current_hash:
        return  # In sync — nothing to do.

    # Drift detected: remove both sidecars so the next online GET is unconditional.
    for path in (etag_path, content_hash_path):
        try:
            path.unlink()
        except OSError as exc:
            log.debug("skill_sidecar_unlink_failed: %s — %s", path.name, exc)

    log.warning(
        "skill_offline_drift_invalidated: %s — stored hash %s… != current %s…; "
        "ETag and content-hash sidecars removed",
        skill_name,
        stored_hash[:8],
        current_hash[:8],
    )


async def _sync_skill(
    skill_name: str,
    skill_path: Path,
    server_url: str | None,
    api_key: str | None,
) -> None:
    """Integrity pre-flight + conditional fetch for a single skill.

    Offline path (no server_url or server unreachable):
        Run _invalidate_if_drift so stale ETag sidecars are cleaned up before
        the next online session.

    Online path (server reachable):
        Delegate to SkillFetcher.fetch which handles conditional GET (ETag /
        If-None-Match) and content-hash drift internally.  Any exception is
        caught and logged so that one bad skill cannot break the session.
    """
    etag_path = skill_path.parent / _ETAG_FILENAME
    content_hash_path = skill_path.parent / _CONTENT_HASH_FILENAME

    if not server_url:
        _invalidate_if_drift(skill_name, skill_path, etag_path, content_hash_path)
        return

    fetcher = SkillFetcher(server_url, api_key=api_key)
    version = await fetcher.check_server_version()

    if not version.reachable:
        _invalidate_if_drift(skill_name, skill_path, etag_path, content_hash_path)
        return

    try:
        await fetcher.fetch(skill_name, skill_path)
    except Exception as exc:  # noqa: BLE001 — one bad skill must not break the session
        log.warning("skill_sync_failed: %s — %s", skill_name, exc)


async def _resync_all_watched(coordinator: object) -> None:
    """Re-sync all watched skills using coordinator capabilities.

    Hard guards:
    - Logs a WARNING and returns when skills_discovery capability is absent.
    - Logs a WARNING and skips a skill when discovery.find() returns None.

    Config is resolved via the tool's _resolve_server_config so that the
    correct server URL and API key are used for the current session.
    """
    discovery = coordinator.get_capability(TOOL_SKILLS_DISCOVERY_CAPABILITY)  # type: ignore[union-attr]
    if discovery is None:
        log.warning(
            "skill_sync_skipped: skills_discovery capability not available — "
            "skill sync will be deferred until the capability is registered"
        )
        return

    tool = coordinator.get_capability(_GRAPH_QUERY_TOOL_CAPABILITY)  # type: ignore[union-attr]

    for skill_name in WATCHED_SKILLS:
        meta = discovery.find(skill_name)
        if meta is None:
            log.warning(
                "skill_sync_skipped: %s — discovery.find() returned None; "
                "skill may not be registered in this session",
                skill_name,
            )
            continue

        skill_path = Path(meta.path)

        if tool is not None:
            server_url, api_key, _workspace = tool._resolve_server_config(coordinator)
        else:
            server_url, api_key = None, None

        await _sync_skill(skill_name, skill_path, server_url, api_key)


async def on_session_ready(coordinator: object) -> None:
    """Orchestrate skill sync on session start and register a reload handler.

    Performs an initial sync of all watched skills, then registers a
    ``skill:unloaded`` hook so that mid-session skill reloads trigger a
    re-sync automatically.
    """
    await _resync_all_watched(coordinator)

    async def _on_skill_unloaded(event_name: str, data: dict) -> None:  # type: ignore[type-arg]
        if data.get("skill_name") in WATCHED_SKILLS:
            await _resync_all_watched(coordinator)

    coordinator.hooks.register(  # type: ignore[union-attr]
        "skill:unloaded",
        _on_skill_unloaded,
        priority=100,
        name="SkillSync",
    )
