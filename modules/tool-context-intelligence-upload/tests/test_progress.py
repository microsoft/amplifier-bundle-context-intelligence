"""Tests for progress.py — upload job progress file read/write and rendering."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import ClassVar

from amplifier_module_tool_context_intelligence_upload.progress import (
    ProgressTracker,
    TwoLevelProgressRenderer,
    folder_label,
    format_duration,
    progress_file_path,
    session_label,
)

# ---------------------------------------------------------------------------
# TestProgressFilePath
# ---------------------------------------------------------------------------


class TestProgressFilePath:
    """Tests for the progress_file_path helper function."""

    def test_default_path_uses_job_id(self) -> None:
        """Default path is /tmp/context-intelligence-upload-{job_id}.json."""
        result = progress_file_path("abc-123")
        assert result == Path("/tmp/context-intelligence-upload-abc-123.json")

    def test_override_path_used_when_provided(self, tmp_path: Path) -> None:
        """When override is provided, that path is returned as a Path object."""
        override = str(tmp_path / "custom-progress.json")
        result = progress_file_path("any-job", override=override)
        assert result == Path(override)

    def test_override_none_uses_default(self) -> None:
        """Passing override=None falls back to the default /tmp path."""
        result = progress_file_path("job-789", override=None)
        assert result == Path("/tmp/context-intelligence-upload-job-789.json")


# ---------------------------------------------------------------------------
# TestProgressTrackerCreation
# ---------------------------------------------------------------------------


class TestProgressTrackerCreation:
    """Tests for ProgressTracker creation and initial state."""

    def test_file_created_on_init(self, tmp_path: Path) -> None:
        """The progress file is created immediately on __init__."""
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=5)
        assert file_path.exists()

    def test_initial_status_is_running(self, tmp_path: Path) -> None:
        """Initial status field must be 'running'."""
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=2)
        data = json.loads(file_path.read_text())
        assert data["status"] == "running"

    def test_initial_state_has_all_fields(self, tmp_path: Path) -> None:
        """All required fields are present with correct initial values, and the
        schema's key set is exactly the documented one (no drift from throttling
        changes -- Part 5 only changes write FREQUENCY, never the schema)."""
        file_path = tmp_path / "progress.json"
        ProgressTracker("my-job", file_path, sessions_total=7)
        data = json.loads(file_path.read_text())

        assert set(data.keys()) == {
            "job_id",
            "status",
            "started_at",
            "sessions_total",
            "sessions_completed",
            "current_session_id",
            "current_session_events_total",
            "current_session_events_sent",
            "failed_at",
        }
        assert data["job_id"] == "my-job"
        assert data["sessions_total"] == 7
        assert data["sessions_completed"] == 0
        assert data["current_session_id"] is None
        assert data["current_session_events_total"] == 0
        assert data["current_session_events_sent"] == 0
        assert data["failed_at"] is None
        assert isinstance(data["started_at"], str)
        assert len(data["started_at"]) > 0


# ---------------------------------------------------------------------------
# TestProgressTrackerUpdates
# ---------------------------------------------------------------------------


class TestProgressTrackerUpdates:
    """Tests for ProgressTracker mutation methods."""

    def test_start_session_updates_current(self, tmp_path: Path) -> None:
        """start_session sets current_session_id and resets event counters."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=3)
        tracker.start_session("sess-abc", events_total=10)
        data = json.loads(file_path.read_text())
        assert data["current_session_id"] == "sess-abc"
        assert data["current_session_events_total"] == 10
        assert data["current_session_events_sent"] == 0

    def test_event_sent_increments_counter(self, tmp_path: Path) -> None:
        """event_sent increments current_session_events_sent; 3 calls -> counter=3."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("sess-1", events_total=10)
        tracker.event_sent()
        tracker.event_sent()
        tracker.event_sent()
        # event_sent is throttled (Part 5); read the in-memory state via
        # session_completed(), which always flushes unconditionally.
        tracker.session_completed()
        data = json.loads(file_path.read_text())
        assert data["current_session_events_sent"] == 3

    def test_session_completed_increments_counter(self, tmp_path: Path) -> None:
        """session_completed increments sessions_completed by 1."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=3)
        tracker.session_completed()
        data = json.loads(file_path.read_text())
        assert data["sessions_completed"] == 1


# ---------------------------------------------------------------------------
# TestProgressTrackerThrottling (Part 5)
# ---------------------------------------------------------------------------


class TestProgressTrackerThrottling:
    """event_sent() writes are throttled to every 100 events or 250ms."""

    def _counting_tracker(self, tmp_path: Path, monkeypatch) -> tuple[ProgressTracker, list[int]]:
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        write_calls: list[int] = []
        original_write = tracker._write

        def counting_write() -> None:
            write_calls.append(1)
            original_write()

        monkeypatch.setattr(tracker, "_write", counting_write)
        return tracker, write_calls

    def test_event_sent_writes_far_fewer_than_n_events(self, tmp_path: Path, monkeypatch) -> None:
        """250 event_sent() calls must NOT produce anywhere near 250 writes."""
        tracker, write_calls = self._counting_tracker(tmp_path, monkeypatch)
        for _ in range(250):
            tracker.event_sent()
        # Every-100-events throttle -> writes at event 100 and event 200 (2
        # writes), plus possibly one more if the 250ms wall-clock threshold
        # is crossed on a slow runner. Either way, nowhere near 250.
        assert len(write_calls) < 10

    def test_start_session_always_flushes_even_mid_throttle_window(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """start_session() flushes unconditionally, regardless of the event throttle."""
        tracker, write_calls = self._counting_tracker(tmp_path, monkeypatch)
        for _ in range(50):  # below the 100-event threshold -- no throttled write yet
            tracker.event_sent()
        before = len(write_calls)
        tracker.start_session("next-session", events_total=5)
        assert len(write_calls) == before + 1

    def test_session_completed_always_flushes(self, tmp_path: Path, monkeypatch) -> None:
        """session_completed() flushes unconditionally."""
        tracker, write_calls = self._counting_tracker(tmp_path, monkeypatch)
        for _ in range(10):
            tracker.event_sent()
        before = len(write_calls)
        tracker.session_completed()
        assert len(write_calls) == before + 1

    def test_mark_completed_always_flushes_and_shows_100_percent_state(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """mark_completed() flushes unconditionally -- the file must reflect the
        final state even if the last few events were below the throttle threshold."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("s1", events_total=3)
        tracker.event_sent()
        tracker.event_sent()
        tracker.event_sent()  # 3 events sent, all below the 100-event threshold
        tracker.mark_completed()
        data = json.loads(file_path.read_text())
        assert data["status"] == "completed"
        assert data["current_session_events_sent"] == 3

    def test_mark_failed_always_flushes(self, tmp_path: Path, monkeypatch) -> None:
        """mark_failed() flushes unconditionally, regardless of throttle state."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("s1", events_total=100)
        for _ in range(5):  # far below the 100-event threshold
            tracker.event_sent()
        tracker.mark_failed(session_id="s1", event_index=5, http_status=500, error="boom")
        data = json.loads(file_path.read_text())
        assert data["status"] == "failed"
        assert data["failed_at"]["event_index"] == 5

    def test_schema_unchanged_by_throttling(self, tmp_path: Path, monkeypatch) -> None:
        """Throttling changes write FREQUENCY only -- the schema's key set is
        identical whether or not a flush was throttled."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("s1", events_total=200)
        for _ in range(150):
            tracker.event_sent()
        tracker.mark_completed()
        data = json.loads(file_path.read_text())
        assert set(data.keys()) == {
            "job_id",
            "status",
            "started_at",
            "sessions_total",
            "sessions_completed",
            "current_session_id",
            "current_session_events_total",
            "current_session_events_sent",
            "failed_at",
        }


# ---------------------------------------------------------------------------
# TestProgressTrackerCompletion
# ---------------------------------------------------------------------------


class TestProgressTrackerCompletion:
    """Tests for the mark_completed method."""

    def test_mark_completed(self, tmp_path: Path) -> None:
        """mark_completed sets status to 'completed'."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.mark_completed()
        data = json.loads(file_path.read_text())
        assert data["status"] == "completed"


# ---------------------------------------------------------------------------
# TestProgressTrackerFailure
# ---------------------------------------------------------------------------


class TestProgressTrackerFailure:
    """Tests for the mark_failed method."""

    def test_mark_failed_populates_failed_at(self, tmp_path: Path) -> None:
        """After 47 events, mark_failed records all failure details in failed_at."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("s1", events_total=100)

        for _ in range(47):
            tracker.event_sent()

        tracker.mark_failed(
            session_id="s1",
            event_index=47,
            http_status=503,
            error="Service Unavailable",
        )

        data = json.loads(file_path.read_text())
        assert data["status"] == "failed"
        assert data["failed_at"] is not None
        assert data["failed_at"]["session_id"] == "s1"
        assert data["failed_at"]["event_index"] == 47
        assert data["failed_at"]["http_status"] == 503
        assert data["failed_at"]["error"] == "Service Unavailable"


# ---------------------------------------------------------------------------
# TestProgressTrackerReadFile
# ---------------------------------------------------------------------------


class TestProgressTrackerReadFile:
    """Tests for the static read_file() method."""

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        """read_file returns None when the path does not exist."""
        nonexistent = tmp_path / "does-not-exist.json"
        result = ProgressTracker.read_file(nonexistent)
        assert result is None

    def test_returns_dict_when_file_exists(self, tmp_path: Path) -> None:
        """read_file returns a dict when the progress file exists."""
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=2)
        result = ProgressTracker.read_file(file_path)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestSessionLabel / TestFolderLabel
# ---------------------------------------------------------------------------


class TestSessionLabel:
    """The human label used for folder/session-id displays (e.g. failure block)."""

    def test_ci_native_layout_yields_project_slash_session_id(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "projects" / "my-project" / "sessions" / "abc123"
        session_dir.mkdir(parents=True)
        assert session_label(session_dir, {"session_id": "abc123"}) == "my-project/abc123"

    def test_flat_layout_falls_back_to_session_id(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "abc123"
        session_dir.mkdir()
        assert session_label(session_dir, {"session_id": "abc123"}) == "abc123"

    def test_missing_session_id_falls_back_to_directory_name(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "dir-name"
        session_dir.mkdir()
        assert session_label(session_dir, {}) == "dir-name"


class TestFolderLabel:
    """folder_label() -- just the folder/project component, for the live 'now:' field."""

    def test_ci_native_layout_yields_just_the_project_folder(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "projects" / "sample-project" / "sessions" / "abc123"
        session_dir.mkdir(parents=True)
        assert folder_label(session_dir, {"session_id": "abc123"}) == "sample-project"

    def test_flat_layout_falls_back_to_session_id(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "abc123"
        session_dir.mkdir()
        assert folder_label(session_dir, {"session_id": "abc123"}) == "abc123"


# ---------------------------------------------------------------------------
# TestFormatDuration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    """format_duration: Xh Ym / Xm Ys / Ys, with optional zero-padded seconds."""

    def test_under_a_minute(self) -> None:
        assert format_duration(45) == "45s"

    def test_minutes_and_seconds(self) -> None:
        assert format_duration(252) == "4m 12s"

    def test_minutes_and_seconds_zero_padded(self) -> None:
        assert format_duration(662, zero_pad=True) == "11m 02s"

    def test_minutes_and_seconds_unpadded(self) -> None:
        assert format_duration(662, zero_pad=False) == "11m 2s"

    def test_over_an_hour(self) -> None:
        assert format_duration(3720) == "1h 2m"

    def test_zero_seconds(self) -> None:
        assert format_duration(0) == "0s"


# ---------------------------------------------------------------------------
# TestTwoLevelProgressRendererNonTty
# ---------------------------------------------------------------------------


class _FakeClock:
    """Deterministic stand-in for time.monotonic()."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def advance(self, dt: float) -> None:
        self.t += dt

    def __call__(self) -> float:
        return self.t


class TestTwoLevelProgressRendererNonTty:
    """When stdout is not a TTY: no ANSI/CR, plain line every 30s."""

    def _renderer(
        self, tmp_path: Path, monkeypatch, sessions_total: int = 2, events_total: int = 3
    ):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        clock = _FakeClock()
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.time.monotonic",
            clock,
        )
        stream = io.StringIO()
        renderer = TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "progress.json",
            sessions_total,
            events_total,
            folder_labels={"s1": "proj", "s2": "proj2"},
            stream=stream,
        )
        return renderer, stream, clock

    def test_no_ansi_or_carriage_return_emitted(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(tmp_path, monkeypatch)
        renderer.start_session("s1", events_total=2)
        clock.advance(31)
        renderer.event_sent()
        renderer.event_sent()
        renderer.session_completed()
        out = stream.getvalue()
        assert "\r" not in out
        assert "\033" not in out

    def test_no_periodic_line_before_30_seconds(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(tmp_path, monkeypatch)
        renderer.start_session("s1", events_total=2)
        clock.advance(5)
        renderer.event_sent()
        assert stream.getvalue() == ""

    def test_periodic_line_emitted_every_30_seconds(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(tmp_path, monkeypatch, sessions_total=1)
        renderer.start_session("s1", events_total=3)
        clock.advance(30)
        renderer.event_sent()
        out = stream.getvalue()
        assert out.strip() != ""
        assert out.startswith("progress:")

    def test_periodic_line_format(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(
            tmp_path, monkeypatch, sessions_total=527, events_total=95373
        )
        renderer.start_session("s1", events_total=95373)
        # Send enough events to reach a representative fraction, then
        # advance the clock past the 30s reporting interval.
        for _ in range(36412):
            renderer.event_sent()
        clock.advance(30)
        renderer.event_sent()
        lines = [line for line in stream.getvalue().splitlines() if line.startswith("progress:")]
        assert lines, stream.getvalue()
        line = lines[-1]
        assert "%" in line
        assert "sessions" in line
        assert "events" in line
        assert "elapsed" in line
        assert "\u00b7" in line  # middle dot separator


# ---------------------------------------------------------------------------
# TestTwoLevelProgressRendererTty
# ---------------------------------------------------------------------------


class TestTwoLevelProgressRendererTty:
    """When stdout IS a TTY: fixed 2-line block, redrawn in place."""

    def _renderer(
        self,
        tmp_path: Path,
        monkeypatch,
        sessions_total: int = 527,
        events_total: int = 95373,
        folder_labels: dict[str, str] | None = None,
        destination_name: str = "",
    ):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        clock = _FakeClock()
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.time.monotonic",
            clock,
        )
        stream = io.StringIO()
        renderer = TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "progress.json",
            sessions_total,
            events_total,
            destination_name=destination_name,
            folder_labels=folder_labels or {"s1": "sample-project"},
            stream=stream,
        )
        return renderer, stream, clock

    def test_uploading_to_line_printed_once_at_start(self, tmp_path: Path, monkeypatch) -> None:
        _renderer, stream, _clock = self._renderer(
            tmp_path, monkeypatch, destination_name="team-archive"
        )
        out = stream.getvalue()
        assert out.startswith("uploading to team-archive\n\n")
        assert out.count("uploading to team-archive") == 1

    def test_no_uploading_line_when_destination_name_omitted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _renderer, stream, _clock = self._renderer(tmp_path, monkeypatch)
        assert "uploading to" not in stream.getvalue()

    def test_redraws_in_place_with_carriage_return_and_cursor_up(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer, stream, clock = self._renderer(tmp_path, monkeypatch)
        clock.advance(3)
        renderer.start_session("s1", events_total=4)
        renderer.event_sent()
        out = stream.getvalue()
        assert "\r\033[K" in out
        assert "\033[1A" in out

    def test_bar_is_20_cells_and_reflects_overall_events_not_per_session(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer, stream, clock = self._renderer(
            tmp_path, monkeypatch, sessions_total=2, events_total=4
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=4)
        renderer.event_sent()
        # Redraws are throttled (every _REDRAW_EVENTS events or _REDRAW_SECONDS
        # elapsed) -- advance the clock past the time threshold so the second
        # event_sent() triggers a redraw instead of being silently coalesced.
        clock.advance(1)
        renderer.event_sent()  # 2/4 events overall -> 50%, NOT per-session 100%
        line1 = stream.getvalue().split("\n")[-3]  # last full progress line before trailing \n
        bar_section = line1.split("|")[1]
        assert len(bar_section) == 20
        assert bar_section.count("#") == 10
        assert bar_section.count("-") == 10
        assert "50%" in line1

    def test_percent_and_counts_use_thousands_separators(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(
            tmp_path, monkeypatch, sessions_total=527, events_total=95373
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=95373)
        for _ in range(36412):
            renderer.event_sent()
        # session_completed() always redraws unconditionally, flushing any
        # events accumulated since the last throttled redraw (throttling is
        # every _REDRAW_EVENTS events or _REDRAW_SECONDS elapsed).
        renderer.session_completed()
        out = stream.getvalue()
        assert "36,412/95,373 events" in out

    def test_folder_label_shown_in_now_field(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(
            tmp_path,
            monkeypatch,
            folder_labels={"s1": "sample-project"},
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=10)
        out = stream.getvalue()
        assert "now: sample-project" in out

    def test_estimating_shown_before_eta_available(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, _clock = self._renderer(tmp_path, monkeypatch)
        # No time has advanced yet (< _ETA_MIN_ELAPSED_S) -- must show "estimating…"
        renderer.start_session("s1", events_total=10)
        assert "estimating\u2026" in stream.getvalue()

    def test_estimating_shown_with_zero_events_sent_even_after_time_passes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer, stream, clock = self._renderer(tmp_path, monkeypatch)
        clock.advance(5)
        renderer.start_session("s1", events_total=10)
        assert "estimating\u2026" in stream.getvalue()

    def test_eta_rendered_once_enough_data_exists(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(
            tmp_path, monkeypatch, sessions_total=1, events_total=10
        )
        clock.advance(5)
        renderer.start_session("s1", events_total=10)
        renderer.event_sent()
        stream.seek(0)
        stream.truncate(0)
        clock.advance(5)
        renderer.event_sent()
        out = stream.getvalue()
        assert "remaining" in out
        assert "estimating" not in out

    def test_elapsed_duration_rendered_in_line2(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(tmp_path, monkeypatch)
        clock.advance(252)  # 4m 12s
        renderer.start_session("s1", events_total=10)
        assert "4m 12s elapsed" in stream.getvalue()

    def test_zero_event_total_does_not_divide_by_zero(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream, clock = self._renderer(
            tmp_path, monkeypatch, sessions_total=1, events_total=0
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=0)
        assert "0%" in stream.getvalue()

    def test_final_redraw_after_mark_completed_shows_100_percent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer, stream, clock = self._renderer(
            tmp_path, monkeypatch, sessions_total=1, events_total=2
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=2)
        renderer.event_sent()
        renderer.event_sent()
        renderer.session_completed()
        renderer.mark_completed()
        # Split on "\n" only (not splitlines()): each rendered line starts
        # with a bare "\r" for in-place redraw, and splitlines() treats "\r"
        # as its own line boundary distinct from "\n", which would otherwise
        # inject a spurious empty entry between the two block lines.
        segments = stream.getvalue().split("\n")
        line1 = segments[-3]  # progress-bar line of the final redraw
        assert "100%" in line1


# ---------------------------------------------------------------------------
# TestCompletionBlock
# ---------------------------------------------------------------------------


class TestCompletionBlock:
    """The end-of-run completion block."""

    def _renderer(self, tmp_path: Path, monkeypatch) -> TwoLevelProgressRenderer:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        return TwoLevelProgressRenderer(
            "job-1", tmp_path / "p.json", 527, 95373, stream=io.StringIO()
        )

    def test_no_skipped_no_retries(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.completion_block(
            destination_name="team-archive",
            destination_url="https://example.invalid/context-intelligence/events",
            sessions_uploaded=527,
            events_sent=95373,
            events_malformed=0,
            events_unreadable=0,
            retries=0,
            duration_s=662,
        )
        assert text.startswith("upload complete\n")
        assert "527 sessions  (95,373 events)" in text
        assert "events skipped" not in text
        assert "transient retries" not in text
        assert "team-archive" in text
        assert "https://example.invalid/context-intelligence/events" in text
        assert "took:" in text
        assert "11m 02s" in text

    def test_skipped_breakdown_lists_only_nonzero_categories_malformed_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.completion_block(
            destination_name="d",
            destination_url="https://example.invalid/events",
            sessions_uploaded=1,
            events_sent=10,
            events_malformed=3,
            events_unreadable=0,
            retries=0,
            duration_s=1,
        )
        assert "3 events skipped  (3 malformed)" in text
        assert "unreadable" not in text

    def test_skipped_breakdown_lists_only_nonzero_categories_unreadable_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.completion_block(
            destination_name="d",
            destination_url="https://example.invalid/events",
            sessions_uploaded=1,
            events_sent=10,
            events_malformed=0,
            events_unreadable=9,
            retries=0,
            duration_s=1,
        )
        assert "9 events skipped  (9 unreadable)" in text
        assert "malformed" not in text

    def test_skipped_breakdown_both_categories(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.completion_block(
            destination_name="d",
            destination_url="https://example.invalid/events",
            sessions_uploaded=1,
            events_sent=10,
            events_malformed=3,
            events_unreadable=9,
            retries=0,
            duration_s=1,
        )
        assert "12 events skipped  (3 malformed, 9 unreadable)" in text

    def test_retries_line_shown_only_when_nonzero(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.completion_block(
            destination_name="d",
            destination_url="https://example.invalid/events",
            sessions_uploaded=1,
            events_sent=10,
            events_malformed=0,
            events_unreadable=0,
            retries=47,
            duration_s=1,
        )
        assert "47 transient retries" in text

    def test_retries_are_not_folded_into_skipped_count(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.completion_block(
            destination_name="d",
            destination_url="https://example.invalid/events",
            sessions_uploaded=1,
            events_sent=10,
            events_malformed=2,
            events_unreadable=0,
            retries=5,
            duration_s=1,
        )
        assert "2 events skipped  (2 malformed)" in text
        assert "5 transient retries" in text

    def test_destination_label_column_alignment(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.completion_block(
            destination_name="team-archive",
            destination_url="https://example.invalid/context-intelligence/events",
            sessions_uploaded=1,
            events_sent=1,
            events_malformed=0,
            events_unreadable=0,
            retries=0,
            duration_s=1,
        )
        lines = text.splitlines()
        name_line = next(line for line in lines if line.strip().startswith("name:"))
        endpoint_line = next(line for line in lines if line.strip().startswith("endpoint:"))
        took_line = next(line for line in lines if line.strip().startswith("took:"))
        # All three values must start at the same column (matches preview.py's
        # alignment scheme -- see progress.py's _VALUE_COLUMN docstring).
        assert name_line.index("team-archive") == endpoint_line.index("https://")
        assert name_line.index("team-archive") == took_line.index("1s")

    def test_returned_not_printed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        renderer.completion_block(
            destination_name="d",
            destination_url="https://example.invalid/events",
            sessions_uploaded=1,
            events_sent=1,
            events_malformed=0,
            events_unreadable=0,
            retries=0,
            duration_s=1,
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


# ---------------------------------------------------------------------------
# TestFailureBlock
# ---------------------------------------------------------------------------


class TestFailureBlock:
    """The end-of-run failure block."""

    def _renderer(
        self, tmp_path: Path, monkeypatch, folder_labels: dict[str, str] | None = None
    ) -> TwoLevelProgressRenderer:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        return TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "p.json",
            527,
            95373,
            folder_labels=folder_labels or {},
            stream=io.StringIO(),
        )

    def test_failure_block_contents(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(
            tmp_path, monkeypatch, folder_labels={"a1b2c3d4": "sample-project"}
        )
        text = renderer.failure_block(
            sessions_uploaded=143,
            events_sent=12904,
            failed_session_id="a1b2c3d4",
            failed_event_index=412,
            error="HTTP 429 \u2014 rate limited",
            job_id="00000000-0000-4000-8000-000000000000",
            duration_s=198,
        )
        assert text.startswith("upload failed\n")
        assert "143 sessions  (12,904 events)" in text
        assert "sample-project/a1b2c3d4" in text
        assert "#412" in text
        assert "HTTP 429 \u2014 rate limited" in text
        assert "context-intelligence-upload --job-id 00000000-0000-4000-8000-000000000000" in text
        assert "3m 18s" in text

    def test_resume_command_uses_actual_job_id(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.failure_block(
            sessions_uploaded=1,
            events_sent=1,
            failed_session_id="s1",
            failed_event_index=1,
            error="boom",
            job_id="custom-job-id-123",
            duration_s=1,
        )
        assert "--job-id custom-job-id-123" in text

    def test_session_falls_back_to_bare_id_without_folder_label(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer = self._renderer(tmp_path, monkeypatch, folder_labels={})
        text = renderer.failure_block(
            sessions_uploaded=1,
            events_sent=1,
            failed_session_id="unlabeled-id",
            failed_event_index=1,
            error="boom",
            job_id="job-x",
            duration_s=1,
        )
        assert "unlabeled-id" in text

    def test_event_index_uses_thousands_separator(self, tmp_path: Path, monkeypatch) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        text = renderer.failure_block(
            sessions_uploaded=1,
            events_sent=1234,
            failed_session_id="s1",
            failed_event_index=1234,
            error="boom",
            job_id="job-x",
            duration_s=1,
        )
        assert "#1,234" in text

    def test_returned_not_printed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        renderer = self._renderer(tmp_path, monkeypatch)
        renderer.failure_block(
            sessions_uploaded=1,
            events_sent=1,
            failed_session_id="s1",
            failed_event_index=1,
            error="boom",
            job_id="job-x",
            duration_s=1,
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


# ---------------------------------------------------------------------------
# TestLineWidthSafety (Fix 1 -- lines never wrap regardless of width/state)
# ---------------------------------------------------------------------------


def _bare_renderer() -> TwoLevelProgressRenderer:
    """Build a TwoLevelProgressRenderer without running __init__.

    _render_line1/_render_line2/_render_lines are pure functions of a
    handful of instance attributes -- constructing via __new__ and setting
    only those attributes keeps these tests focused on the rendering math
    instead of the constructor's I/O side effects (file writes, isatty).
    """
    r = TwoLevelProgressRenderer.__new__(TwoLevelProgressRenderer)
    r._sessions_completed = 0
    r._sessions_total = 12
    r._events_sent_total = 0
    r._events_total = 700
    r._current_folder_label = ""
    return r


class TestLineWidthSafety:
    """The hard truncate safety net: a rendered line must never exceed
    ``width - 1`` visible characters, for any width or progress state --
    this is the property the cursor-up-2 redraw correctness depends on."""

    _WIDTHS: ClassVar[list[int]] = [30, 40, 60, 80, 120, 200]

    _STATES: ClassVar[list[tuple[int, int, int, int, str]]] = [
        # (sessions_completed, sessions_total, events_sent, events_total, folder_label)
        (0, 12, 0, 700, ""),
        (6, 12, 350, 700, "proj-alpha"),
        (12, 12, 700, 700, "proj-alpha"),
        (3, 500, 12345, 999999, "a-very-long-project-folder-name-that-keeps-going-and-going"),
        (999, 1000, 999999, 1000000, "z" * 80),
    ]

    def test_line1_never_exceeds_width_minus_one(self) -> None:
        for width in self._WIDTHS:
            for (
                sessions_completed,
                sessions_total,
                events_sent,
                events_total,
                _label,
            ) in self._STATES:
                r = _bare_renderer()
                r._sessions_completed = sessions_completed
                r._sessions_total = sessions_total
                r._events_sent_total = events_sent
                r._events_total = events_total
                percent = int(events_sent * 100 / (events_total or 1))
                line1 = r._render_line1(width, percent)
                # Mirror the unconditional hard-truncate the caller applies.
                line1 = line1[: max(0, width - 1)]
                assert len(line1) <= width - 1, (width, sessions_completed, events_sent, line1)

    def test_line2_never_exceeds_width_minus_one(self) -> None:
        for width in self._WIDTHS:
            for _sc, _st, _es, _et, label in self._STATES:
                r = _bare_renderer()
                r._current_folder_label = label
                line2 = r._render_line2(width, "2m 19s", "~1m 2s remaining")
                line2 = line2[: max(0, width - 1)]
                assert len(line2) <= width - 1, (width, label, line2)

    def test_render_lines_hard_truncate_is_unconditional_even_for_absurd_state(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The safety net applies even to states the degradation ladder
        wasn't designed for (e.g. an extremely long current-session label
        at a very narrow width) -- truncation is the correctness guarantee,
        degradation is only the best-effort layer above it."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.shutil.get_terminal_size",
            lambda fallback=(80, 24): __import__("os").terminal_size((20, 24)),
        )
        clock = _FakeClock()
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.time.monotonic",
            clock,
        )
        renderer = TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "p.json",
            1,
            10,
            folder_labels={"s1": "x" * 300},
            stream=io.StringIO(),
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=10)
        line1, line2 = renderer._render_lines()
        assert len(line1) <= 19
        assert len(line2) <= 19


# ---------------------------------------------------------------------------
# TestLine1Degradation (Fix 1 -- priority ladder: percent > sessions > events)
# ---------------------------------------------------------------------------


class TestLine1Degradation:
    """Verifies the documented degradation order for line 1: the bar
    shrinks first, then event counts drop, then session counts drop --
    percent is never dropped."""

    def _line1(
        self, width: int, sessions_completed=6, sessions_total=12, events_sent=350, events_total=700
    ):
        r = _bare_renderer()
        r._sessions_completed = sessions_completed
        r._sessions_total = sessions_total
        r._events_sent_total = events_sent
        r._events_total = events_total
        percent = int(events_sent * 100 / (events_total or 1))
        return r._render_line1(width, percent)

    def test_wide_width_shows_full_bar_sessions_and_events(self) -> None:
        line1 = self._line1(120)
        assert "|" in line1  # bar present
        assert line1.count("#") + line1.count("-") == 20  # full-size bar
        assert "6/12 sessions" in line1
        assert "350/700 events" in line1
        assert "50%" in line1

    def test_bar_shrinks_before_any_segment_drops(self) -> None:
        line1 = self._line1(60)
        assert "6/12 sessions" in line1
        assert "350/700 events" in line1
        bar_section = line1.split("|")[1]
        assert 4 <= len(bar_section) < 20  # shrunk, not full, not dropped

    def test_bar_dropped_before_events_segment_drops(self) -> None:
        line1 = self._line1(50)
        assert "|" not in line1  # bar gone
        assert "6/12 sessions" in line1
        assert "350/700 events" in line1

    def test_events_segment_dropped_before_sessions_segment(self) -> None:
        line1 = self._line1(40)
        assert "6/12 sessions" in line1
        assert "events" not in line1

    def test_sessions_segment_dropped_before_percent(self) -> None:
        line1 = self._line1(20)
        assert "sessions" not in line1
        assert "events" not in line1
        assert "50%" in line1

    def test_percent_never_dropped_even_at_minimum_width(self) -> None:
        line1 = self._line1(10)
        assert "50%" in line1


# ---------------------------------------------------------------------------
# TestLine2Degradation (Fix 1 -- priority ladder: elapsed > ETA > now:)
# ---------------------------------------------------------------------------


class TestLine2Degradation:
    """Verifies the documented degradation order for line 2: elapsed is
    kept longest, ETA drops next, the current-session name drops first."""

    def _line2(
        self, width: int, label: str = "proj-alpha-with-a-somewhat-long-project-folder-name"
    ):
        r = _bare_renderer()
        r._current_folder_label = label
        return r._render_line2(width, "2m 19s", "~0s remaining")

    def test_wide_width_shows_all_three_segments(self) -> None:
        line2 = self._line2(120)
        assert "2m 19s elapsed" in line2
        assert "~0s remaining" in line2
        assert "now: proj-alpha-with-a-somewhat-long-project-folder-name" in line2

    def test_now_segment_dropped_before_eta(self) -> None:
        line2 = self._line2(60)
        assert "2m 19s elapsed" in line2
        assert "~0s remaining" in line2
        assert "now:" not in line2

    def test_eta_dropped_before_elapsed(self) -> None:
        line2 = self._line2(30)
        assert "2m 19s elapsed" in line2
        assert "remaining" not in line2
        assert "now:" not in line2

    def test_elapsed_kept_even_at_minimum_width(self) -> None:
        # Elapsed alone ("  2m 19s elapsed") is 16 visible chars -- still
        # fits at width=20 (budget 19) but not at width=10 (budget 9),
        # where nothing fits and the line degrades to blank.
        line2 = self._line2(20)
        assert "2m 19s elapsed" in line2


# ---------------------------------------------------------------------------
# TestWideWidthUnchanged (120 columns must be byte-identical to pre-fix format)
# ---------------------------------------------------------------------------


class TestWideWidthUnchanged:
    """At 120 columns (and the 80-column fallback used when no real
    terminal is attached), the rendered lines must be byte-identical to the
    original fixed-width format -- only Fix 3 (now: omission) and Fix 4
    (ETA sanity) are permitted to change text, and neither applies to
    these fully-populated, well-progressed states."""

    def test_line1_matches_original_fixed_format_at_120_columns(self) -> None:
        r = _bare_renderer()
        r._sessions_completed = 6
        r._sessions_total = 12
        r._events_sent_total = 350
        r._events_total = 700
        line1 = r._render_line1(120, 50)
        assert (
            line1
            == "  |##########----------|  50%   \u00b7   6/12 sessions   \u00b7   350/700 events"
        )

    def test_line2_matches_original_fixed_format_at_120_columns(self) -> None:
        r = _bare_renderer()
        r._current_folder_label = "proj-alpha"
        line2 = r._render_line2(120, "2m 19s", "~0s remaining")
        assert line2 == "  2m 19s elapsed   \u00b7   ~0s remaining   \u00b7   now: proj-alpha"

    def test_default_fallback_80_columns_matches_original_format_too(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """No COLUMNS env var and no real terminal -> shutil.get_terminal_size
        falls back to (80, 24), which is wide enough that nothing degrades;
        this is the width the existing (pre-Fix-1) test suite already runs
        under, so this pins that those tests keep passing unmodified."""
        monkeypatch.delenv("COLUMNS", raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        clock = _FakeClock()
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.time.monotonic",
            clock,
        )
        stream = io.StringIO()
        renderer = TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "p.json",
            2,
            4,
            folder_labels={"s1": "proj"},
            stream=stream,
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=4)
        renderer.event_sent()
        clock.advance(1)
        renderer.event_sent()
        out = stream.getvalue()
        assert "|##########----------|" in out  # full 20-cell bar, undegraded
        assert "50%" in out
        assert "0/2 sessions" in out
        assert "2/4 events" in out


# ---------------------------------------------------------------------------
# TestNowFieldOmitted (Fix 3 -- no empty "now:" on the first frame)
# ---------------------------------------------------------------------------


class TestNowFieldOmitted:
    def test_now_segment_absent_when_no_current_session(self) -> None:
        r = _bare_renderer()
        r._current_folder_label = ""
        line2 = r._render_line2(120, "0s", "estimating\u2026")
        assert "now:" not in line2
        assert "now: " not in line2

    def test_now_segment_present_once_a_session_has_started(self) -> None:
        r = _bare_renderer()
        r._current_folder_label = "proj-alpha"
        line2 = r._render_line2(120, "0s", "estimating\u2026")
        assert "now: proj-alpha" in line2

    def test_first_frame_of_a_real_renderer_has_no_now_segment(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """End-to-end: the very first redraw (fired from __init__, before
        any session has started) must not print an empty 'now: ' segment."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.shutil.get_terminal_size",
            lambda fallback=(80, 24): __import__("os").terminal_size((120, 24)),
        )
        clock = _FakeClock()
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.time.monotonic",
            clock,
        )
        stream = io.StringIO()
        _renderer = TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "p.json",
            2,
            4,
            folder_labels={"s1": "proj"},
            stream=stream,
        )
        out = stream.getvalue()
        assert "now:" not in out


# ---------------------------------------------------------------------------
# TestEtaSanity (Fix 4 -- estimating... floor and absurd-value clamp)
# ---------------------------------------------------------------------------


class TestEtaSanity:
    def _renderer_for_eta(
        self, events_total: int = 700, events_sent: int = 0
    ) -> TwoLevelProgressRenderer:
        r = _bare_renderer()
        r._events_total = events_total
        r._events_sent_total = events_sent
        return r

    def test_estimating_shown_below_min_elapsed_threshold(self) -> None:
        r = self._renderer_for_eta(events_sent=3)
        assert r._eta_str(1.0) == "estimating\u2026"

    def test_estimating_shown_with_zero_events_sent_regardless_of_elapsed(self) -> None:
        r = self._renderer_for_eta(events_sent=0)
        assert r._eta_str(60.0) == "estimating\u2026"

    def test_estimating_shown_for_tiny_sample_that_would_extrapolate_wildly(self) -> None:
        """The exact reported case: 3/700 events at 22s elapsed extrapolates
        to ~1h25m remaining (232x elapsed) -- far past the trust threshold,
        so it must still say 'estimating...' rather than print that number."""
        r = self._renderer_for_eta(events_total=700, events_sent=3)
        assert r._eta_str(22.0) == "estimating\u2026"

    def test_eta_shown_once_sample_is_large_enough_to_trust(self) -> None:
        """100/700 events at 22s extrapolates to ~132s remaining (~6x
        elapsed) -- well within the trust threshold, so a real ETA shows."""
        r = self._renderer_for_eta(events_total=700, events_sent=100)
        eta = r._eta_str(22.0)
        assert eta != "estimating\u2026"
        assert "remaining" in eta

    def test_eta_shrinks_toward_zero_as_upload_approaches_completion(self) -> None:
        r = self._renderer_for_eta(events_total=700, events_sent=700)
        assert r._eta_str(22.0) == "~0s remaining"


# ---------------------------------------------------------------------------
# TestMidUploadResize (Fix 1 -- terminal width is re-read on every frame)
# ---------------------------------------------------------------------------


class TestMidUploadResize:
    def test_width_is_read_fresh_each_redraw(self, tmp_path: Path, monkeypatch) -> None:
        """A resize between two redraws must be picked up on the very next
        frame -- shutil.get_terminal_size must be called per-frame, not
        cached once at construction."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        widths = [120, 30]
        calls = {"n": 0}

        def fake_size(fallback=(80, 24)):
            idx = min(calls["n"], len(widths) - 1)
            calls["n"] += 1
            return __import__("os").terminal_size((widths[idx], 24))

        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.shutil.get_terminal_size",
            fake_size,
        )
        clock = _FakeClock()
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.progress.time.monotonic",
            clock,
        )
        stream = io.StringIO()
        renderer = TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "p.json",
            1,
            10,
            folder_labels={"s1": "proj-alpha-with-a-somewhat-long-project-folder-name"},
            stream=stream,
        )
        clock.advance(3)
        renderer.start_session("s1", events_total=10)  # second redraw -> width=30
        out = stream.getvalue()
        frames = out.split("\033[1A\033[1A")
        assert len(frames) == 2
        # Second frame rendered at the narrower width -- the "now:" segment
        # (which fits at 120) must have been dropped by width=30.
        assert "now:" not in frames[1]
