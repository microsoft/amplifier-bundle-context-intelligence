"""Progress file read/write for context-intelligence upload jobs.

Provides a helper function and a ProgressTracker class for tracking the
state of an upload job, persisting progress to a JSON file on disk using
an atomic write pattern (write to .tmp then rename).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


def progress_file_path(job_id: str, override: str | None = None) -> Path:
    """Return the progress file path for *job_id*.

    Returns ``/tmp/context-intelligence-upload-{job_id}.json`` by default,
    or ``Path(override)`` if *override* is provided.
    """
    if override is not None:
        return Path(override)
    return Path(f"/tmp/context-intelligence-upload-{job_id}.json")


def session_label(session_dir: Path, metadata: dict[str, Any]) -> str:
    """Return the human label for a session: ``<project>/<session_id>``.

    Context-intelligence-native sessions live at
    ``.../<project>/sessions/<id>/context-intelligence`` so the project name is
    the path component immediately before ``sessions``.  Layouts without a
    ``sessions`` component (e.g. legacy hooks-logging) fall back to the bare
    session id, and a session with no recorded id falls back to its directory
    name.
    """
    session_id = str(metadata.get("session_id") or session_dir.name)
    parts = session_dir.parts
    if "sessions" in parts:
        index = parts.index("sessions")
        if index > 0:
            return f"{parts[index - 1]}/{session_id}"
    return session_id


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


class TwoLevelProgressRenderer(ProgressTracker):
    """A ProgressTracker that also renders human-facing progress to a stream.

    Subclasses :class:`ProgressTracker` so the machine-readable JSON progress
    file behaves EXACTLY as before (every override calls ``super()`` first);
    the terminal rendering is layered on top and ``uploader.run_upload`` needs
    no changes.

    Rendering goes to *stream* (default ``sys.stderr``) because stdout is
    reserved for the result JSON.  TTY-awareness is decided by
    ``sys.stdout.isatty()``: on a TTY we redraw an inner event bar in place;
    when stdout is piped we emit one plain completion line per session and no
    ANSI/carriage-return control characters at all.
    """

    _BAR_CELLS = 20

    def __init__(
        self,
        job_id: str,
        file_path: Path,
        sessions_total: int,
        *,
        labels: dict[str, str] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self._labels = labels or {}
        self._stream: TextIO = stream if stream is not None else sys.stderr
        self._tty = sys.stdout.isatty()
        self._sessions_total = sessions_total
        self._session_index = 0
        self._current_label = ""
        self._events_total = 0
        self._events_sent = 0
        super().__init__(job_id, file_path, sessions_total)

    def start_session(self, session_id: str, events_total: int) -> None:
        super().start_session(session_id, events_total)
        self._session_index += 1
        self._current_label = self._labels.get(session_id, session_id)
        self._events_total = events_total
        self._events_sent = 0
        if self._tty:
            self._redraw()

    def event_sent(self) -> None:
        super().event_sent()
        self._events_sent += 1
        if self._tty:
            self._redraw()

    def session_completed(self) -> None:
        super().session_completed()
        if self._tty:
            self._stream.write("\n")
        else:
            self._stream.write(
                f"[{self._session_index}/{self._sessions_total}] {self._current_label} "
                f"{self._events_sent}/{self._events_total} events\n"
            )
        self._stream.flush()

    def final_summary(
        self,
        *,
        destination_name: str,
        destination_url: str,
        sessions_uploaded: int,
        events_sent: int,
        events_skipped: int,
        filtered_out: int,
        duration_s: float,
    ) -> str:
        """Return the end-of-run summary block.

        Returned rather than printed so the caller decides the stream (the CLI
        writes it to stderr) and so it is trivially testable.
        """
        return (
            "summary:\n"
            f"  destination:       {destination_name} ({destination_url})\n"
            f"  sessions uploaded: {sessions_uploaded}\n"
            f"  events sent:       {events_sent}\n"
            f"  events skipped:    {events_skipped}\n"
            f"  filtered out:      {filtered_out}\n"
            f"  duration:          {duration_s:.1f}s"
        )

    def _redraw(self) -> None:
        """Redraw the outer counter + inner event bar in place (TTY only)."""
        total = self._events_total or 1
        percent = int(self._events_sent * 100 / total)
        filled = int(self._BAR_CELLS * percent / 100)
        bar = "#" * filled + "-" * (self._BAR_CELLS - filled)
        self._stream.write(
            f"\r[{self._session_index}/{self._sessions_total}] {self._current_label} "
            f"|{bar}| {percent}% ({self._events_sent}/{self._events_total})"
        )
        self._stream.flush()
