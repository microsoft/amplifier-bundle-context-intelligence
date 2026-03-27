"""Progress file read/write for context-intelligence upload jobs.

Provides a helper function and a ProgressTracker class for tracking the
state of an upload job, persisting progress to a JSON file on disk using
an atomic write pattern (write to .tmp then rename).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def progress_file_path(job_id: str, override: str | None = None) -> Path:
    """Return the progress file path for *job_id*.

    Returns ``/tmp/context-intelligence-upload-{job_id}.json`` by default,
    or ``Path(override)`` if *override* is provided.
    """
    if override is not None:
        return Path(override)
    return Path(f"/tmp/context-intelligence-upload-{job_id}.json")


class ProgressTracker:
    """Track and persist the progress of an upload job.

    State is kept in memory and flushed to *file_path* on every mutation
    using an atomic write (write to ``.tmp`` suffix, then ``os.replace``).
    """

    def __init__(self, job_id: str, file_path: Path, sessions_total: int) -> None:
        self._file_path = file_path
        self._state: dict[str, Any] = {
            "job_id": job_id,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "sessions_total": sessions_total,
            "sessions_completed": 0,
            "current_session_id": None,
            "current_session_events_total": 0,
            "current_session_events_sent": 0,
            "failed_at": None,
        }
        self._write()

    def _write(self) -> None:
        """Atomically write state to *file_path* using a .tmp suffix + rename."""
        tmp_path = Path(str(self._file_path) + ".tmp")
        tmp_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        os.replace(tmp_path, self._file_path)

    def start_session(self, session_id: str, events_total: int) -> None:
        """Record the start of a new session."""
        self._state["current_session_id"] = session_id
        self._state["current_session_events_total"] = events_total
        self._state["current_session_events_sent"] = 0
        self._write()

    def event_sent(self) -> None:
        """Increment the count of events sent for the current session."""
        self._state["current_session_events_sent"] += 1
        self._write()

    def session_completed(self) -> None:
        """Increment the count of completed sessions."""
        self._state["sessions_completed"] += 1
        self._write()

    def mark_completed(self) -> None:
        """Mark the job as completed."""
        self._state["status"] = "completed"
        self._write()

    def mark_failed(
        self,
        session_id: str,
        event_index: int,
        http_status: int,
        error: str,
    ) -> None:
        """Mark the job as failed and record failure details."""
        self._state["status"] = "failed"
        self._state["failed_at"] = {
            "session_id": session_id,
            "event_index": event_index,
            "http_status": http_status,
            "error": error,
        }
        self._write()

    def read(self) -> dict[str, Any]:
        """Read and parse the progress file, returning the current state."""
        return json.loads(self._file_path.read_text(encoding="utf-8"))

    @staticmethod
    def read_file(file_path: Path) -> dict[str, Any] | None:
        """Read and parse *file_path*, returning ``None`` if it does not exist."""
        if not file_path.exists():
            return None
        return json.loads(file_path.read_text(encoding="utf-8"))
