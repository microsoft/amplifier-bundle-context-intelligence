"""Append-only JSONL migration ledger — audit trail and idempotent resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Phase constants
# ---------------------------------------------------------------------------

LEDGER_PHASES = (
    "classified",
    "transformed",
    "archived",
    "uploaded",
    "verified",
    "deleted",
    "skipped",
    "failed",
)


# ---------------------------------------------------------------------------
# JSONL read / write
# ---------------------------------------------------------------------------


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Return all entries from *path* (empty list if the file does not exist)."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def append_entry(path: Path, entry: dict[str, Any]) -> None:
    """Atomic append of *entry* to *path* (one JSON object per line).

    Entry schema::

        {
            "ts":           "<ISO 8601 UTC>",
            "session_id":   "<str>",
            "project_slug": "<str>",
            "bucket":       "pre_ci|double|ci_only|live",
            "phase":        "<one of LEDGER_PHASES>",
            "workspace":    "<str>",
            "jsonl_lines":  <int | null>,
            "graph_count":  <int | null>,
            "archive_path": "<str | null>",
            "error":        "<str | null>"
        }
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def last_phase(entries: list[dict[str, Any]], session_id: str) -> str | None:
    """Return the ``phase`` of the most recent entry for *session_id*, or None."""
    for entry in reversed(entries):
        if entry.get("session_id") == session_id:
            return entry.get("phase")
    return None


def already_complete(entries: list[dict[str, Any]], session_id: str) -> bool:
    """Return True iff the session's most-recent entry phase is a terminal success.

    * ``"deleted"`` — terminal for pre_ci and double sessions.
    * ``"verified"`` with ``bucket == "ci_only"`` — terminal for ci_only sessions
      (which never have a delete step).
    """
    # Find the last entry for this session
    last_entry: dict[str, Any] | None = None
    for entry in reversed(entries):
        if entry.get("session_id") == session_id:
            last_entry = entry
            break

    if last_entry is None:
        return False

    phase = last_entry.get("phase")

    if phase == "deleted":
        return True

    if phase == "verified" and last_entry.get("bucket") == "ci_only":
        return True

    return False
