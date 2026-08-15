"""Progress file read/write and terminal rendering for upload jobs.

Provides:

- :class:`ProgressTracker` -- persists job state to a JSON file on disk using
  an atomic write pattern (write to ``.tmp`` then ``os.replace``), throttled
  to bound I/O on long runs (see :data:`ProgressTracker._THROTTLE_EVENTS`).
- :class:`TwoLevelProgressRenderer` -- a ``ProgressTracker`` subclass that
  additionally renders a live, in-place progress block to a terminal (or a
  periodic plain-text line when not attached to a TTY), plus the completion
  and failure blocks printed once the run ends.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

#: Column at which every "label: value" row's value starts, across the
#: completion and failure blocks. Derived from preview.py's alignment
#: (4-space indent + _LABEL_WIDTH=18 there == 22), so the destination block
#: in the completion summary lines up with the destination block shown in
#: the pre-upload preview.
_VALUE_COLUMN = 22

#: Middle-dot field separator used in the TTY progress block.
_FIELD_SEP = "   \u00b7   "


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


def folder_label(session_dir: Path, metadata: dict[str, Any]) -> str:
    """Return just the folder (project) component of :func:`session_label`.

    Used for the ``now:`` field in the live progress block, where showing a
    full session UUID would be noise -- the folder is the meaningful
    grouping an operator recognizes (the same grouping the preview's
    ``from N folders:`` section uses).  Falls back to the session id itself
    when no ``sessions`` path component exists to derive a folder from.
    """
    session_id = str(metadata.get("session_id") or session_dir.name)
    parts = session_dir.parts
    if "sessions" in parts:
        index = parts.index("sessions")
        if index > 0:
            return parts[index - 1]
    return session_id


def format_duration(total_seconds: float, *, zero_pad: bool = False) -> str:
    """Return a human duration string: ``Xh Ym``, ``Xm Ys``, or ``Ys``.

    *zero_pad* zero-pads the smallest displayed unit to 2 digits (used by
    the completion/failure blocks' ``took:`` line, e.g. ``11m 02s``); the
    live progress line leaves it unpadded (e.g. ``4m 12s``, which happens to
    look the same whenever the value is already >= 10).
    """
    total = max(0, round(total_seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        sec = f"{seconds:02d}" if zero_pad else f"{seconds}"
        return f"{minutes}m {sec}s"
    return f"{seconds}s"


def _aligned(indent: int, label: str, value: str) -> str:
    """Render one ``label: value`` row whose value starts at :data:`_VALUE_COLUMN`."""
    prefix = " " * indent + label
    pad = max(1, _VALUE_COLUMN - len(prefix))
    return prefix + " " * pad + value


class ProgressTracker:
    """Track and persist the progress of an upload job.

    State is kept in memory and flushed to *file_path* using an atomic write
    (write to ``.tmp`` suffix, then ``os.replace``).  Writes are throttled
    for :meth:`event_sent` (the highest-frequency mutation -- up to ~95k
    calls in a large run) to every :data:`_THROTTLE_EVENTS` events or
    :data:`_THROTTLE_SECONDS`, whichever comes first.  Every other mutation
    (:meth:`start_session`, :meth:`session_completed`, :meth:`mark_completed`,
    :meth:`mark_failed`) flushes unconditionally: those are the boundaries a
    resume needs to observe accurately, and the file must show 100% at the
    end.  The JSON schema itself is unchanged -- only write frequency.
    """

    _THROTTLE_EVENTS = 100
    _THROTTLE_SECONDS = 0.25

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
        self._events_since_write = 0
        self._last_write_monotonic = time.monotonic()
        self._write()

    def _write(self) -> None:
        """Atomically write state to *file_path* using a .tmp suffix + rename."""
        tmp_path = Path(str(self._file_path) + ".tmp")
        tmp_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        os.replace(tmp_path, self._file_path)
        self._events_since_write = 0
        self._last_write_monotonic = time.monotonic()

    def start_session(self, session_id: str, events_total: int) -> None:
        """Record the start of a new session. Always flushes."""
        self._state["current_session_id"] = session_id
        self._state["current_session_events_total"] = events_total
        self._state["current_session_events_sent"] = 0
        self._write()

    def event_sent(self) -> None:
        """Increment events sent for the current session. Throttled flush."""
        self._state["current_session_events_sent"] += 1
        self._events_since_write += 1
        now = time.monotonic()
        if (
            self._events_since_write >= self._THROTTLE_EVENTS
            or (now - self._last_write_monotonic) >= self._THROTTLE_SECONDS
        ):
            self._write()

    def session_completed(self) -> None:
        """Increment the count of completed sessions. Always flushes."""
        self._state["sessions_completed"] += 1
        self._write()

    def mark_completed(self) -> None:
        """Mark the job as completed. Always flushes."""
        self._state["status"] = "completed"
        self._write()

    def mark_failed(
        self,
        session_id: str,
        event_index: int,
        http_status: int,
        error: str,
    ) -> None:
        """Mark the job as failed and record failure details. Always flushes."""
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
    """A ProgressTracker that also renders a live progress block to a stream.

    Subclasses :class:`ProgressTracker` so the machine-readable JSON progress
    file behaves exactly as before (every override calls ``super()`` first);
    the terminal rendering is layered on top and ``uploader.run_upload``
    needs no changes.

    Rendering goes to *stream* (default ``sys.stderr``) because stdout is
    reserved for the result JSON.  TTY-awareness is decided by
    ``sys.stdout.isatty()``:

    - On a TTY, a fixed 2-line block is redrawn in place (nothing scrolls),
      driven by *overall* events progress (not per-session), throttled to
      redraw at most every 100 events or 250ms.
    - When stdout is piped, no ANSI/carriage-return control characters are
      emitted at all; instead one plain progress line is printed every 30s.
    """

    _BAR_CELLS = 20
    _REDRAW_EVENTS = 100
    _REDRAW_SECONDS = 0.25
    _NONTTY_INTERVAL_SECONDS = 30.0
    #: Below this elapsed time (or with zero events sent), the run hasn't
    #: produced enough throughput data yet for an ETA to mean anything.
    _ETA_MIN_ELAPSED_S = 2.0

    def __init__(
        self,
        job_id: str,
        file_path: Path,
        sessions_total: int,
        events_total: int,
        *,
        destination_name: str = "",
        folder_labels: dict[str, str] | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self._folder_labels = folder_labels or {}
        self._stream: TextIO = stream if stream is not None else sys.stderr
        self._tty = sys.stdout.isatty()
        self._sessions_total = sessions_total
        self._events_total = events_total
        self._sessions_completed = 0
        self._events_sent_total = 0
        self._current_folder_label = ""
        self._start_monotonic = time.monotonic()
        self._first_draw = True
        self._events_since_redraw = 0
        self._last_redraw_monotonic = self._start_monotonic
        self._last_periodic_monotonic = self._start_monotonic
        super().__init__(job_id, file_path, sessions_total)
        if destination_name:
            self._stream.write(f"uploading to {destination_name}\n\n")
            self._stream.flush()
        if self._tty:
            self._redraw()

    # -- ProgressTracker overrides -----------------------------------------

    def start_session(self, session_id: str, events_total: int) -> None:
        super().start_session(session_id, events_total)
        self._current_folder_label = self._folder_labels.get(session_id, session_id)
        if self._tty:
            self._redraw()
        else:
            self._maybe_emit_periodic()

    def event_sent(self) -> None:
        super().event_sent()
        self._events_sent_total += 1
        self._events_since_redraw += 1
        if self._tty:
            now = time.monotonic()
            if (
                self._events_since_redraw >= self._REDRAW_EVENTS
                or (now - self._last_redraw_monotonic) >= self._REDRAW_SECONDS
            ):
                self._redraw()
        else:
            self._maybe_emit_periodic()

    def session_completed(self) -> None:
        super().session_completed()
        self._sessions_completed += 1
        if self._tty:
            self._redraw()
        else:
            self._maybe_emit_periodic()

    def mark_completed(self) -> None:
        super().mark_completed()
        if self._tty:
            self._redraw()

    def mark_failed(
        self,
        session_id: str,
        event_index: int,
        http_status: int,
        error: str,
    ) -> None:
        super().mark_failed(session_id, event_index, http_status, error)
        if self._tty:
            self._redraw()

    # -- Rendered blocks (pure functions of state -> str) -------------------

    def completion_block(
        self,
        *,
        destination_name: str,
        destination_url: str,
        sessions_uploaded: int,
        events_sent: int,
        events_malformed: int,
        events_unreadable: int,
        retries: int,
        duration_s: float,
    ) -> str:
        """Return the end-of-run completion block.

        Returned rather than printed so the caller decides the stream (the
        CLI writes it to stderr) and so it is trivially testable.  The
        "N events skipped" line only appears when there were any; the
        parenthesized breakdown lists only non-zero categories.  Retries are
        NOT data loss and are never folded into the skipped count -- they
        get their own line, shown only when non-zero.
        """
        lines: list[str] = ["upload complete", "", "  sent:"]
        lines.append(f"    {sessions_uploaded:,} sessions  ({events_sent:,} events)")

        skipped = events_malformed + events_unreadable
        if skipped:
            parts = []
            if events_malformed:
                parts.append(f"{events_malformed:,} malformed")
            if events_unreadable:
                parts.append(f"{events_unreadable:,} unreadable")
            lines.append(f"    {skipped:,} events skipped  ({', '.join(parts)})")

        if retries:
            lines.append(f"    {retries:,} transient retries")

        lines.extend(
            [
                "",
                "  destination:",
                _aligned(4, "name:", destination_name),
                _aligned(4, "endpoint:", destination_url),
                "",
                _aligned(2, "took:", format_duration(duration_s, zero_pad=True)),
            ]
        )
        return "\n".join(lines)

    def failure_block(
        self,
        *,
        sessions_uploaded: int,
        events_sent: int,
        failed_session_id: str,
        failed_event_index: int,
        error: str,
        job_id: str,
        duration_s: float,
    ) -> str:
        """Return the end-of-run failure block.

        *error* is the fully-formatted display string for the failure (the
        caller composes it from the upload result, e.g. ``"HTTP 429 -- rate
        limited"``) -- this method only lays it out, matching the pattern of
        :meth:`completion_block` and the original ``final_summary``.
        """
        session_display = self._session_display(failed_session_id)
        lines: list[str] = [
            "upload failed",
            "",
            "  sent before failure:",
            f"    {sessions_uploaded:,} sessions  ({events_sent:,} events)",
            "",
            "  failed at:",
            _aligned(4, "session:", session_display),
            _aligned(4, "event:", f"#{failed_event_index:,}"),
            _aligned(4, "error:", error),
            "",
            "  resume with:",
            f"    context-intelligence-upload --job-id {job_id}",
            "",
            _aligned(2, "took:", format_duration(duration_s, zero_pad=True)),
        ]
        return "\n".join(lines)

    # -- internals -----------------------------------------------------------

    def _session_display(self, session_id: str) -> str:
        """Return ``folder/session_id``, falling back to the bare id."""
        folder = self._folder_labels.get(session_id, "")
        return f"{folder}/{session_id}" if folder else session_id

    def _percent(self) -> int:
        total = self._events_total or 1
        return int(self._events_sent_total * 100 / total)

    def _eta_str(self, elapsed_s: float) -> str:
        if elapsed_s < self._ETA_MIN_ELAPSED_S or self._events_sent_total == 0:
            return "estimating\u2026"
        fraction = self._events_sent_total / (self._events_total or 1)
        if fraction <= 0:
            return "estimating\u2026"
        remaining_s = elapsed_s * (1 - fraction) / fraction
        return f"~{format_duration(remaining_s)} remaining"

    def _render_lines(self) -> tuple[str, str]:
        percent = self._percent()
        filled = int(self._BAR_CELLS * percent / 100)
        bar = "#" * filled + "-" * (self._BAR_CELLS - filled)
        line1 = (
            f"  |{bar}|  {percent}%{_FIELD_SEP}"
            f"{self._sessions_completed:,}/{self._sessions_total:,} sessions{_FIELD_SEP}"
            f"{self._events_sent_total:,}/{self._events_total:,} events"
        )
        elapsed_s = time.monotonic() - self._start_monotonic
        elapsed_str = format_duration(elapsed_s)
        eta_str = self._eta_str(elapsed_s)
        line2 = f"  {elapsed_str} elapsed{_FIELD_SEP}{eta_str}{_FIELD_SEP}now: {self._current_folder_label}"
        return line1, line2

    def _redraw(self) -> None:
        """Redraw the fixed 2-line block in place (TTY only).

        Uses ``\\r\\033[K`` to clear each line and ``\\033[1A`` to move the
        cursor up one line (applied twice for the 2-line block, on every
        redraw after the first).  Each line ends with ``\\n`` so the cursor
        is always left on a fresh line -- no separate "clean up" step is
        needed before printing the completion/failure block afterward.
        """
        line1, line2 = self._render_lines()
        prefix = "" if self._first_draw else "\033[1A\033[1A"
        self._first_draw = False
        self._stream.write(f"{prefix}\r\033[K{line1}\n\r\033[K{line2}\n")
        self._stream.flush()
        self._events_since_redraw = 0
        self._last_redraw_monotonic = time.monotonic()

    def _maybe_emit_periodic(self) -> None:
        """Non-TTY path: print one plain progress line every 30s."""
        now = time.monotonic()
        if (now - self._last_periodic_monotonic) < self._NONTTY_INTERVAL_SECONDS:
            return
        elapsed_str = format_duration(time.monotonic() - self._start_monotonic)
        self._stream.write(
            f"progress: {self._percent()}% \u00b7 "
            f"{self._sessions_completed:,}/{self._sessions_total:,} sessions \u00b7 "
            f"{self._events_sent_total:,}/{self._events_total:,} events \u00b7 "
            f"{elapsed_str} elapsed\n"
        )
        self._stream.flush()
        self._last_periodic_monotonic = now
