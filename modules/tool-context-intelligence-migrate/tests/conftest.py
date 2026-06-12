"""Shared fixtures for all migrate module tests."""

from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Legacy record factory
# ---------------------------------------------------------------------------


def make_legacy_record(
    *,
    event: str = "tool:pre",
    ts: str = "2026-03-18T00:00:00.000+00:00",
    session_id: str = "sess-abc-001",
    status: str = "ok",
    extra_data: dict | None = None,
) -> dict:
    """Minimal valid hooks-logging legacy record."""
    data: dict = {}
    if extra_data:
        data.update(extra_data)
    return {
        "ts": ts,
        "lvl": "INFO",
        "schema": {"name": "amplifier.log", "ver": "1.0.0"},
        "event": event,
        "session_id": session_id,
        "status": status,
        "data": data,
    }


def write_legacy_events(path: Path, records: list[dict]) -> None:
    """Write a list of records as a legacy events.jsonl."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def make_metadata_json(
    *,
    session_id: str = "sess-abc-001",
    working_dir: str = "/Users/me/project",
    parent_id: str = "",
) -> dict:
    """Minimal context-intelligence metadata dict."""
    return {
        "format": "context-intelligence",
        "version": "1.0.0",
        "session_id": session_id,
        "parent_id": parent_id,
        "working_dir": working_dir,
    }


# ---------------------------------------------------------------------------
# Session directory factory
# ---------------------------------------------------------------------------


def build_pre_ci_session(
    tmp_path: Path,
    *,
    project_slug: str = "my-project",
    session_id: str = "sess-001",
    legacy_records: list[dict] | None = None,
    working_dir: str = "/Users/me/project",
) -> Path:
    """Create a pre_ci session: legacy events.jsonl only, no context-intelligence/."""
    session_dir = tmp_path / project_slug / "sessions" / session_id
    session_dir.mkdir(parents=True)

    recs = legacy_records or [make_legacy_record(session_id=session_id)]
    # Also add session:end so it's not treated as live
    recs = list(recs)
    recs.append(make_legacy_record(event="session:end", session_id=session_id))
    write_legacy_events(session_dir / "events.jsonl", recs)

    # Transcript / metadata at session_dir level (never deleted)
    (session_dir / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": working_dir}), encoding="utf-8"
    )
    return session_dir


def build_double_session(
    tmp_path: Path,
    *,
    project_slug: str = "my-project",
    session_id: str = "sess-002",
    working_dir: str = "/Users/me/project",
) -> Path:
    """Create a double session: both legacy events.jsonl and context-intelligence/."""
    session_dir = build_pre_ci_session(
        tmp_path, project_slug=project_slug, session_id=session_id, working_dir=working_dir
    )
    ci_dir = session_dir / "context-intelligence"
    ci_dir.mkdir()
    (ci_dir / "events.jsonl").write_text(
        '{"event":"tool:pre","workspace":"","timestamp":"","data":{}}\n',
        encoding="utf-8",
    )
    (ci_dir / "metadata.json").write_text(
        json.dumps(make_metadata_json(session_id=session_id, working_dir=working_dir)),
        encoding="utf-8",
    )
    return session_dir


def build_ci_only_session(
    tmp_path: Path,
    *,
    project_slug: str = "my-project",
    session_id: str = "sess-003",
    working_dir: str = "/Users/me/project",
) -> Path:
    """Create a ci_only session: context-intelligence/ only, no legacy events.jsonl."""
    session_dir = tmp_path / project_slug / "sessions" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": working_dir}), encoding="utf-8"
    )

    ci_dir = session_dir / "context-intelligence"
    ci_dir.mkdir()
    (ci_dir / "events.jsonl").write_text(
        '{"event":"tool:pre","workspace":"-Users-me-project","timestamp":"2026-01-01T00:00:00Z","data":{}}\n',
        encoding="utf-8",
    )
    (ci_dir / "metadata.json").write_text(
        json.dumps(make_metadata_json(session_id=session_id, working_dir=working_dir)),
        encoding="utf-8",
    )
    return session_dir
