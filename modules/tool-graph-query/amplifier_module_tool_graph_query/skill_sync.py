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

import hashlib
import logging
import os
from importlib import resources
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

#: Package holding the vendored offline skill bodies (see bundled_skill/__init__.py).
_BUNDLED_SKILL_PACKAGE: str = "amplifier_module_tool_graph_query.bundled_skill"

__all__ = [
    "TOOL_SKILLS_DISCOVERY_CAPABILITY",
    "WATCHED_SKILLS",
    "_GRAPH_QUERY_TOOL_CAPABILITY",
    "on_session_ready",
]


def _sha256_text(text: str) -> str:
    """Return the hex SHA-256 digest of *text* (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vendored_body(skill_name: str) -> str | None:
    """Return the vendored offline body for *skill_name*, or ``None`` if absent.

    The body is packaged inside ``bundled_skill/<skill_name>.md``.  A missing
    file (e.g. dropped from the wheel by a faulty build) returns ``None`` so the
    caller can fail loud rather than silently doing the wrong thing.
    """
    try:
        resource = resources.files(_BUNDLED_SKILL_PACKAGE).joinpath(f"{skill_name}.md")
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        log.error("vendored_skill_body_error: %s — %s", skill_name, exc)
    return None


def _install_vendored_body(skill_name: str, skill_path: Path) -> None:
    """Swap *skill_path*'s content for the vendored offline body — zero network.

    Used on the ``skill_sync_enabled=false`` path when a server IS configured:
    the shipped ``SKILL.md`` is the pessimistic "Server Unavailable" stub, so we
    replace it with the real bundled body, otherwise a working graph-analyst is
    handed a skill that tells it the graph is dead.

    Correctness properties (see issue #283 council review):
    - **Fail loud**: a missing vendored body logs an ERROR and leaves the
      on-disk file untouched — never a silent wrong result.
    - **Idempotent by SHA-256**: rewrites only when the on-disk content differs,
      so a single-command series writes once and then no-ops (zero disk churn).
    - **Crash-atomic, ETag-first**: the stale ``.etag`` sidecar is removed FIRST
      (a vendored body is not an ETag-validated server fetch), then the content
      is replaced via a temp-file + ``os.replace`` atomic rename, then the
      ``.content_hash`` sidecar is written.  Any crash window therefore leaves
      the skill in a clean "no ETag → next enabled sync does an unconditional
      GET" state — never a stale-ETag→304 freeze of the vendored body.
    """
    body = _vendored_body(skill_name)
    if body is None:
        log.error(
            "skill_swap_unavailable: %s — vendored offline body missing from the "
            "tool-graph-query package; leaving on-disk skill unchanged (the "
            "graph-analyst may see the 'Server Unavailable' stub). This indicates "
            "a broken build — the vendored body must ship in the wheel.",
            skill_name,
        )
        return

    new_hash = _sha256_text(body)
    etag_path = skill_path.parent / _ETAG_FILENAME
    content_hash_path = skill_path.parent / _CONTENT_HASH_FILENAME

    # ETag-first: a vendored body has no server ETag; drop any stale one so a
    # later re-enabled sync issues a clean unconditional GET.
    try:
        etag_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.debug("skill_swap_etag_unlink_failed: %s — %s", skill_name, exc)

    if skill_path.exists() and _sha256(skill_path) == new_hash:
        # Already the vendored body — keep the content-hash sidecar honest, no rewrite.
        if not content_hash_path.exists() or content_hash_path.read_text().strip() != new_hash:
            content_hash_path.write_text(new_hash)
        log.debug("skill_swap_noop: %s already matches vendored offline body", skill_name)
        return

    tmp_path = skill_path.parent / f".{skill_path.name}.swap.{os.getpid()}.tmp"
    tmp_path.write_text(body, encoding="utf-8")
    os.replace(tmp_path, skill_path)  # atomic on the same filesystem
    content_hash_path.write_text(new_hash)
    log.info(
        "skill_swap_applied: %s — installed vendored offline body (%d bytes, zero network)",
        skill_name,
        len(body),
    )


async def _apply_offline_skill_bodies(coordinator: object, tool: object) -> None:
    """Disabled-sync path: ensure each watched skill has a usable body, no network.

    For each watched skill:
    - **server configured** → swap the pessimistic stub for the vendored real
      body (``_install_vendored_body``).  ``server_url`` is read from config
      only — no reachability ping — so this stays strictly zero-network.
    - **no server configured** → retain the shipped "Server Unavailable" stub
      (correct: the graph genuinely is not there).

    Empty / whitespace / unexpanded-placeholder ``server_url`` resolves to
    ``None`` via the resolver and is treated as "not configured".
    """
    discovery = coordinator.get_capability(TOOL_SKILLS_DISCOVERY_CAPABILITY)  # type: ignore[union-attr]
    if discovery is None:
        log.info(
            "skill_sync_disabled: skills_discovery capability not available — "
            "nothing to swap; skipping (zero network)"
        )
        return

    server_url, _api_key, _workspace = tool._resolve_server_config(coordinator)  # type: ignore[attr-defined]
    server_configured = bool(server_url)

    for skill_name in WATCHED_SKILLS:
        meta = discovery.find(skill_name)
        if meta is None:
            log.debug(
                "skill_sync_disabled: %s — discovery.find() returned None; skipping",
                skill_name,
            )
            continue
        skill_path = Path(meta.path)
        if server_configured:
            log.info(
                "skill_sync_disabled: server configured — installing vendored offline "
                "body for %s without any network (no GET /version, no GET /skills/)",
                skill_name,
            )
            _install_vendored_body(skill_name, skill_path)
        else:
            log.info(
                "skill_sync_disabled: no server configured — retaining shipped "
                "'Server Unavailable' stub for %s (graph genuinely absent)",
                skill_name,
            )


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

    Opt-out gate: when the graph-query tool capability is present and reports
    ``skill_sync_enabled is False`` (the ``skill_sync_enabled`` config knob /
    ``AMPLIFIER_CONTEXT_INTELLIGENCE_SKILL_SYNC_ENABLED`` env var), this performs
    **zero per-turn network** — no ``GET /version`` ping, no skill fetch — and
    does **not** register the ``skill:unloaded`` reload handler.  It does NOT,
    however, leave a working graph-analyst stranded on the pessimistic "Server
    Unavailable" stub: when a server IS configured it swaps in the vendored
    offline body (a local copy, still zero network); when no server is
    configured it retains the stub.  See ``_apply_offline_skill_bodies``.  This
    lets headless / single-command-series workflows pay zero skill traffic per
    turn while keeping the graph-analyst usable.  When the tool capability is
    absent the gate does not fire and the existing offline-integrity path runs
    unchanged.
    """
    tool = coordinator.get_capability(_GRAPH_QUERY_TOOL_CAPABILITY)  # type: ignore[union-attr]
    if tool is not None and not getattr(tool, "skill_sync_enabled", True):
        await _apply_offline_skill_bodies(coordinator, tool)
        return

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
