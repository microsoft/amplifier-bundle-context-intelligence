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
import shutil
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
    #: The bar shrinks as terminal width tightens but is dropped entirely
    #: once it can't fit at this many cells -- below that, a sliver of
    #: "#"/"-" stops being a legible bar.
    _MIN_BAR_CELLS = 4
    _REDRAW_EVENTS = 100
    _REDRAW_SECONDS = 0.25
    _NONTTY_INTERVAL_SECONDS = 30.0
    #: Below this elapsed time (or with zero events sent), the run hasn't
    #: produced enough throughput data yet for an ETA to mean anything.
    _ETA_MIN_ELAPSED_S = 2.0
    #: Sanity clamp: an ETA extrapolated from a tiny fraction of progress
    #: can be wildly disproportionate to the time actually spent -- e.g. 3
    #: of 700 events sent at the 22s mark implies ~1h25m remaining, 232x
    #: the elapsed time. Once the extrapolated remaining time exceeds this
    #: multiple of elapsed time, the estimate isn't trustworthy yet -- keep
    #: showing "estimating..." instead of an alarming or silly number. A
    #: pure elapsed-time or event-count floor was considered and rejected:
    #: either would need per-job tuning (a threshold right for 700 events
    #: is wrong for 95,000), whereas this ratio scales with the job and
    #: catches exactly the "one or two lucky/unlucky early events" case
    #: that produces a wild extrapolation.
    _ETA_MAX_REMAINING_MULTIPLE = 20

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
        if remaining_s > elapsed_s * self._ETA_MAX_REMAINING_MULTIPLE:
            # Too little progress yet to trust the extrapolation (see
            # _ETA_MAX_REMAINING_MULTIPLE docstring) -- keep estimating
            # rather than print a wild, possibly alarming, number.
            return "estimating\u2026"
        return f"~{format_duration(remaining_s)} remaining"

    def _bar(self, cells: int, percent: int) -> str:
        """Render a progress bar of exactly *cells* cells at *percent*."""
        if cells <= 0:
            return ""
        filled = int(cells * percent / 100)
        return "#" * filled + "-" * (cells - filled)

    def _render_line1(self, width: int, percent: int) -> str:
        """Build line 1 (bar + percent + counts), degrading to fit *width*.

        Degradation ladder, highest priority (never dropped) first:
        percentage > session counts > event counts. The bar shrinks
        continuously from :data:`_BAR_CELLS` down to :data:`_MIN_BAR_CELLS`
        before any segment is dropped, and is omitted entirely once even
        the minimum size doesn't fit. This is a best-effort layout pass --
        the caller applies an unconditional hard truncate afterward as the
        safety net, so failing to fit here is never a correctness bug.
        """
        percent_seg = f"{percent}%"
        sessions_seg = f"{self._sessions_completed:,}/{self._sessions_total:,} sessions"
        events_seg = f"{self._events_sent_total:,}/{self._events_total:,} events"
        budget = width - 1

        for segments in ([sessions_seg, events_seg], [sessions_seg], []):
            tail = _FIELD_SEP.join([percent_seg, *segments])
            for cells in range(self._BAR_CELLS, self._MIN_BAR_CELLS - 1, -1):
                candidate = f"  |{self._bar(cells, percent)}|  {tail}"
                if len(candidate) <= budget:
                    return candidate
            candidate = f"  {tail}"
            if len(candidate) <= budget:
                return candidate
        return f"  {percent_seg}"

    def _render_line2(self, width: int, elapsed_str: str, eta_str: str) -> str:
        """Build line 2 (elapsed + ETA + current session), degrading to fit *width*.

        Degradation ladder, highest priority (dropped last) first: elapsed
        > ETA > current-session name. The ``now:`` segment is omitted
        outright whenever there is no current session yet (Fix 3),
        independent of width.
        """
        segments = [f"{elapsed_str} elapsed", eta_str]
        if self._current_folder_label:
            segments.append(f"now: {self._current_folder_label}")
        budget = width - 1

        for end in range(len(segments), 0, -1):
            candidate = "  " + _FIELD_SEP.join(segments[:end])
            if len(candidate) <= budget:
                return candidate
        return "  "

    def _render_lines(self) -> tuple[str, str]:
        """Render both progress lines, guaranteed to fit one physical row each.

        Terminal width is re-read fresh on every call (via
        ``shutil.get_terminal_size``) so a mid-upload resize is picked up
        on the next redraw. Each line is built by a width-aware,
        priority-ordered degradation pass (see :meth:`_render_line1` /
        :meth:`_render_line2`), then unconditionally hard-truncated to
        ``width - 1`` visible characters -- the truncate is the
        correctness guarantee the ``\\033[1A\\033[1A`` two-row redraw
        depends on; the degradation above only makes narrow output more
        useful than a bare chop.
        """
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        percent = self._percent()
        elapsed_s = time.monotonic() - self._start_monotonic
        elapsed_str = format_duration(elapsed_s)
        eta_str = self._eta_str(elapsed_s)

        line1 = self._render_line1(width, percent)
        line2 = self._render_line2(width, elapsed_str, eta_str)
        budget = max(0, width - 1)
        return line1[:budget], line2[:budget]

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
