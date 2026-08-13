"""Tests for progress.py — upload job progress file read/write."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from amplifier_module_tool_context_intelligence_upload.progress import (
    ProgressTracker,
    TwoLevelProgressRenderer,
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
        """All required fields are present with correct initial values."""
        file_path = tmp_path / "progress.json"
        ProgressTracker("my-job", file_path, sessions_total=7)
        data = json.loads(file_path.read_text())

        assert data["job_id"] == "my-job"
        assert data["sessions_total"] == 7
        assert data["sessions_completed"] == 0
        assert data["current_session_id"] is None
        assert data["current_session_events_total"] == 0
        assert data["current_session_events_sent"] == 0
        assert data["failed_at"] is None
        assert "started_at" in data
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
        """event_sent increments current_session_events_sent; 3 calls → counter=3."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("sess-1", events_total=10)
        tracker.event_sent()
        tracker.event_sent()
        tracker.event_sent()
        data = json.loads(file_path.read_text())
        assert data["current_session_events_sent"] == 3

    def test_session_completed_increments_counter(self, tmp_path: Path) -> None:
        """session_completed increments sessions_completed by 1."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=3)
        tracker.session_completed()
        data = json.loads(file_path.read_text())
        assert data["sessions_completed"] == 1

    def test_file_updated_after_every_event(self, tmp_path: Path) -> None:
        """File on disk reflects incremented counter after each event_sent call."""
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("sess-1", events_total=5)

        tracker.event_sent()
        assert json.loads(file_path.read_text())["current_session_events_sent"] == 1

        tracker.event_sent()
        assert json.loads(file_path.read_text())["current_session_events_sent"] == 2

        tracker.event_sent()
        assert json.loads(file_path.read_text())["current_session_events_sent"] == 3


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
# TestSessionLabel
# ---------------------------------------------------------------------------


class TestSessionLabel:
    """The human label rendered in the outer progress line."""

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


# ---------------------------------------------------------------------------
# TestTwoLevelProgressRendererNonTty
# ---------------------------------------------------------------------------


class TestTwoLevelProgressRendererNonTty:
    """When stdout is not a TTY: one plain completion line per session, no ANSI."""

    def _renderer(self, tmp_path: Path, monkeypatch, sessions_total: int = 2):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        stream = io.StringIO()
        renderer = TwoLevelProgressRenderer(
            "job-1",
            tmp_path / "progress.json",
            sessions_total,
            labels={"s1": "proj/s1", "s2": "proj/s2"},
            stream=stream,
        )
        return renderer, stream

    def test_one_plain_line_per_completed_session(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream = self._renderer(tmp_path, monkeypatch)

        renderer.start_session("s1", events_total=2)
        renderer.event_sent()
        renderer.event_sent()
        renderer.session_completed()

        renderer.start_session("s2", events_total=1)
        renderer.event_sent()
        renderer.session_completed()

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 2

    def test_plain_line_carries_counter_label_and_event_counts(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        renderer, stream = self._renderer(tmp_path, monkeypatch)
        renderer.start_session("s1", events_total=2)
        renderer.event_sent()
        renderer.event_sent()
        renderer.session_completed()

        line = stream.getvalue().splitlines()[0]
        assert "[1/2]" in line
        assert "proj/s1" in line
        assert "2/2" in line

    def test_non_tty_output_has_no_carriage_return(self, tmp_path: Path, monkeypatch) -> None:
        renderer, stream = self._renderer(tmp_path, monkeypatch)
        renderer.start_session("s1", events_total=1)
        renderer.event_sent()
        renderer.session_completed()
        assert "\r" not in stream.getvalue()

    def test_progress_json_file_behaviour_is_unchanged(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The renderer is a ProgressTracker — the JSON contract must still hold."""
        file_path = tmp_path / "progress.json"
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        renderer = TwoLevelProgressRenderer("job-9", file_path, 3, stream=io.StringIO())

        renderer.start_session("sess-abc", events_total=10)
        renderer.event_sent()
        renderer.session_completed()

        data = json.loads(file_path.read_text())
        assert data["job_id"] == "job-9"
        assert data["sessions_total"] == 3
        assert data["current_session_id"] == "sess-abc"
        assert data["current_session_events_total"] == 10
        assert data["current_session_events_sent"] == 1
        assert data["sessions_completed"] == 1

    def test_unlabelled_session_falls_back_to_session_id(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        stream = io.StringIO()
        renderer = TwoLevelProgressRenderer("job-1", tmp_path / "p.json", 1, stream=stream)
        renderer.start_session("unknown-id", events_total=1)
        renderer.event_sent()
        renderer.session_completed()
        assert "unknown-id" in stream.getvalue()
