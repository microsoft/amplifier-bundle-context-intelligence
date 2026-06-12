"""Session classification — bucket each session dir into pre_ci / double / ci_only / live."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Bucket = Literal["pre_ci", "double", "ci_only", "live"]

DEFAULT_SAFETY_WINDOW_HOURS: float = 24.0

#: Events that definitively mark a session as finished.
#: Empirically confirmed from real ~/.amplifier session files:
#: ended sessions contain at least one of these events, but the LAST line is
#: frequently a non-terminal event (e.g. ``cleanup:finally_end``,
#: ``prompt:complete``), so we scan ALL lines rather than checking only the last.
TERMINAL_EVENTS: frozenset[str] = frozenset(
    {"session:end", "orchestrator:complete", "execution:end"}
)


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    project_slug: str
    session_dir: Path
    bucket: Bucket
    legacy_events: Path | None  # session_dir/"events.jsonl" if it exists, else None
    ci_events: Path  # session_dir/"context-intelligence"/"events.jsonl"
    ci_dir: Path  # session_dir/"context-intelligence"/
    reason: str  # human explanation, esp. for "live"


def has_terminal_event(legacy_events: Path) -> bool:
    """Return True if *legacy_events* contains any event in :data:`TERMINAL_EVENTS`.

    Scans ALL lines — not just the last line — because real session files
    frequently end with non-terminal events (``cleanup:finally_end``,
    ``prompt:complete``, etc.) even when the session has fully ended.
    """
    if not legacy_events.exists():
        return False
    try:
        for line in legacy_events.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("event") in TERMINAL_EVENTS:
                    return True
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return False


def _max_mtime(session_dir: Path) -> float | None:
    """Return the max mtime of the three sentinel files, or None if none exist."""
    candidates = [
        session_dir / "events.jsonl",
        session_dir / "transcript.jsonl",
        session_dir / "metadata.json",
    ]
    mtimes = []
    for p in candidates:
        try:
            mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else None


def is_live_session(
    legacy_events: Path,
    *,
    safety_window_hours: float = DEFAULT_SAFETY_WINDOW_HOURS,
    now: float | None = None,
) -> bool:
    """Public API for tests: return True if the legacy_events file indicates a live session.

    A session is live if it has no terminal event OR if the file was recently modified.
    This is a convenience wrapper that infers session_dir from legacy_events.parent.
    """
    live, _ = is_live(
        legacy_events.parent,
        legacy_events,
        safety_window_hours=safety_window_hours,
        now=now,
    )
    return live


def is_live(
    session_dir: Path,
    legacy_events: Path | None,
    *,
    safety_window_hours: float = DEFAULT_SAFETY_WINDOW_HOURS,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return (live, reason).

    LIVE iff EITHER:
      (a) legacy_events exists but contains none of the :data:`TERMINAL_EVENTS`
          (``session:end``, ``orchestrator:complete``, ``execution:end``), OR
      (b) max mtime of {events.jsonl, transcript.jsonl, metadata.json} is within
          safety_window_hours of *now*.

    Note: rule (a) scans ALL lines — real sessions often end with non-terminal
    events as their last line (e.g. ``cleanup:finally_end``).
    """
    _now = now if now is not None else time.time()
    reasons: list[str] = []

    # Rule (a): no terminal event in legacy file (scans all lines)
    if legacy_events is not None and legacy_events.exists():
        if not has_terminal_event(legacy_events):
            reasons.append(
                "no terminal event (session:end/orchestrator:complete/execution:end) found (rule a)"
            )

    # Rule (b): recently modified
    mtime = _max_mtime(session_dir)
    if mtime is not None and (_now - mtime) < safety_window_hours * 3600:
        reasons.append(f"files modified within safety window of {safety_window_hours}h (rule b)")

    if reasons:
        return True, "; ".join(reasons)
    return False, ""


def bucket_session(
    session_dir: Path,
    *,
    project_slug: str = "",
    safety_window_hours: float = DEFAULT_SAFETY_WINDOW_HOURS,
    now: float | None = None,
) -> SessionInfo | None:
    """Classify a single session directory.

    Returns None if the session has neither a legacy events.jsonl nor a
    context-intelligence/events.jsonl (nothing to migrate or verify).
    """
    session_id = session_dir.name
    legacy_events_path = session_dir / "events.jsonl"
    ci_dir = session_dir / "context-intelligence"
    ci_events_path = ci_dir / "events.jsonl"

    has_legacy = legacy_events_path.exists()
    has_ci = ci_events_path.exists()

    if not has_legacy and not has_ci:
        return None

    legacy_events: Path | None = legacy_events_path if has_legacy else None

    live, live_reason = is_live(
        session_dir,
        legacy_events,
        safety_window_hours=safety_window_hours,
        now=now,
    )

    if live:
        return SessionInfo(
            session_id=session_id,
            project_slug=project_slug,
            session_dir=session_dir,
            bucket="live",
            legacy_events=legacy_events,
            ci_events=ci_events_path,
            ci_dir=ci_dir,
            reason=live_reason,
        )

    if has_legacy and not has_ci:
        bucket: Bucket = "pre_ci"
        reason = "legacy events.jsonl, no context-intelligence/events.jsonl"
    elif has_legacy and has_ci:
        bucket = "double"
        reason = "both legacy events.jsonl and context-intelligence/events.jsonl present"
    else:  # not has_legacy and has_ci
        bucket = "ci_only"
        reason = "only context-intelligence/events.jsonl, no legacy events.jsonl"

    return SessionInfo(
        session_id=session_id,
        project_slug=project_slug,
        session_dir=session_dir,
        bucket=bucket,
        legacy_events=legacy_events,
        ci_events=ci_events_path,
        ci_dir=ci_dir,
        reason=reason,
    )


def scan_projects(
    projects_root: Path,
    *,
    safety_window_hours: float = DEFAULT_SAFETY_WINDOW_HOURS,
    now: float | None = None,
) -> list[SessionInfo]:
    """Walk ``projects_root/*/sessions/*`` and classify each session.

    Returns a flat list ordered by (project_slug, session_id).
    """
    results: list[SessionInfo] = []
    if not projects_root.is_dir():
        return results

    for project_dir in sorted(projects_root.iterdir()):
        if not project_dir.is_dir():
            continue
        project_slug = project_dir.name
        sessions_dir = project_dir / "sessions"
        if not sessions_dir.is_dir():
            continue
        for session_dir in sorted(sessions_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            info = bucket_session(
                session_dir,
                project_slug=project_slug,
                safety_window_hours=safety_window_hours,
                now=now,
            )
            if info is not None:
                results.append(info)

    return results
