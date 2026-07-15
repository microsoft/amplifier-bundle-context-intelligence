"""Test fixtures for legacy hooks-logging transform tests.

Provides helpers to build minimal valid hooks-logging legacy records and
write them out as an events.jsonl file, without needing a live hook to
produce them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def make_legacy_record(
    *,
    event: str = "tool:pre",
    ts: str = "2026-03-18T00:00:00.000+00:00",
    session_id: str = "sess-abc-001",
    status: str = "ok",
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a minimal valid hooks-logging legacy record dict."""
    return {
        "ts": ts,
        "lvl": "INFO",
        "schema": {"name": "amplifier.log", "ver": "1.0.0"},
        "event": event,
        "session_id": session_id,
        "status": status,
        "data": extra_data or {},
    }


def write_legacy_events(path: Path, records: list[dict[str, Any]]) -> None:
    """Write *records* as JSON lines to *path*, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_legacy_session(
    root: Path,
    *,
    session_id: str = "sess-legacy-001",
    working_dir: str | None = "/Users/me/project",
    records: list[dict[str, Any]] | None = None,
    terminal: bool = True,
    write_metadata: bool = True,
) -> Path:
    """Create a legacy tree: <root>/sessions/<id>/events.jsonl (+ metadata.json).

    No context-intelligence/ subfolder. When *terminal* is True a session:end
    record is appended so the session is not treated as live/in-progress.

    *working_dir* controls how a session's working directory is resolvable:
      - a path string -> written into metadata.json working_dir (normal case)
      - None          -> metadata.json omits working_dir AND no session:start
                         event carries one (simulates an UNRESOLVABLE session,
                         used by the discovery-robustness tests in Task 4).
    Set *write_metadata* False to omit metadata.json entirely.
    """
    session_dir = root / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    recs = list(records or [make_legacy_record(session_id=session_id)])
    if terminal:
        recs.append(make_legacy_record(event="session:end", session_id=session_id))
    write_legacy_events(session_dir / "events.jsonl", recs)
    if write_metadata:
        meta: dict[str, Any] = {}
        if working_dir is not None:
            meta["working_dir"] = working_dir
        (session_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return session_dir


def write_raw_events(path: Path, raw: bytes) -> None:
    """Write RAW bytes as events.jsonl (for the non-UTF8 discovery test, Task 4)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def prepend_line(path: Path, first_line: str) -> None:
    """Insert *first_line* at the top of an existing events.jsonl (corrupt-first-line test)."""
    existing = path.read_text(encoding="utf-8")
    path.write_text(first_line.rstrip("\n") + "\n" + existing, encoding="utf-8")


def build_ci_session(
    root: Path,
    *,
    session_id: str = "sess-ci-001",
) -> Path:
    """Create a native context-intelligence tree (should NOT be legacy-discovered)."""
    ci_dir = root / "sessions" / session_id / "context-intelligence"
    ci_dir.mkdir(parents=True, exist_ok=True)
    (ci_dir / "events.jsonl").write_text(
        '{"event":"tool:pre","workspace":"-Users-me-project","timestamp":"2026-01-01T00:00:00Z","data":{}}\n',
        encoding="utf-8",
    )
    (ci_dir / "metadata.json").write_text(
        json.dumps(
            {
                "format": "context-intelligence",
                "version": "1.0.0",
                "session_id": session_id,
                "parent_id": "",
                "working_dir": "/Users/me/project",
            }
        ),
        encoding="utf-8",
    )
    return ci_dir.parent
