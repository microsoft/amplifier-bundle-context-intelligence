"""Tests for progress.py — upload job progress file read/write."""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_module_tool_context_intelligence_upload.progress import (
    ProgressTracker,
    progress_file_path,
)


# ---------------------------------------------------------------------------
# TestProgressFilePath
# ---------------------------------------------------------------------------


class TestProgressFilePath:
    """Tests for the progress_file_path helper function."""

    def test_default_path_uses_tmp(self):
        result = progress_file_path("my-job-123")
        assert result == Path("/tmp/context-intelligence-upload-my-job-123.json")

    def test_default_path_returns_path_object(self):
        result = progress_file_path("job-abc")
        assert isinstance(result, Path)

    def test_default_path_includes_job_id(self):
        job_id = "unique-job-456"
        result = progress_file_path(job_id)
        assert job_id in str(result)

    def test_override_returns_path_of_override(self, tmp_path):
        override = str(tmp_path / "custom-progress.json")
        result = progress_file_path("any-job", override=override)
        assert result == Path(override)

    def test_override_none_uses_default(self):
        result = progress_file_path("job-789", override=None)
        assert result == Path("/tmp/context-intelligence-upload-job-789.json")

    def test_override_returns_path_object(self, tmp_path):
        override = str(tmp_path / "custom.json")
        result = progress_file_path("job-id", override=override)
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# TestProgressTrackerInit
# ---------------------------------------------------------------------------


class TestProgressTrackerInit:
    """Tests for ProgressTracker.__init__."""

    def test_init_creates_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=5)
        assert file_path.exists()

    def test_initial_state_has_job_id(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("my-job", file_path, sessions_total=3)
        data = json.loads(file_path.read_text())
        assert data["job_id"] == "my-job"

    def test_initial_status_is_running(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=2)
        data = json.loads(file_path.read_text())
        assert data["status"] == "running"

    def test_initial_state_has_started_at(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=2)
        data = json.loads(file_path.read_text())
        assert "started_at" in data
        # Verify it's a non-empty string (ISO format)
        assert isinstance(data["started_at"], str)
        assert len(data["started_at"]) > 0

    def test_initial_state_has_correct_sessions_total(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=42)
        data = json.loads(file_path.read_text())
        assert data["sessions_total"] == 42

    def test_initial_sessions_completed_is_zero(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=5)
        data = json.loads(file_path.read_text())
        assert data["sessions_completed"] == 0

    def test_initial_current_session_id_is_none(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=5)
        data = json.loads(file_path.read_text())
        assert data["current_session_id"] is None

    def test_initial_current_session_events_total_is_zero(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=5)
        data = json.loads(file_path.read_text())
        assert data["current_session_events_total"] == 0

    def test_initial_current_session_events_sent_is_zero(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=5)
        data = json.loads(file_path.read_text())
        assert data["current_session_events_sent"] == 0

    def test_initial_failed_at_is_none(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=5)
        data = json.loads(file_path.read_text())
        assert data["failed_at"] is None

    def test_init_stores_all_required_fields(self, tmp_path):
        """All nine required state fields must be present after init."""
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-x", file_path, sessions_total=1)
        data = json.loads(file_path.read_text())
        required = {
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
        assert required.issubset(set(data.keys()))


# ---------------------------------------------------------------------------
# TestAtomicWrite
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    """Tests for the atomic write pattern (_write method)."""

    def test_write_does_not_leave_tmp_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker._write()
        tmp_file = Path(str(file_path) + ".tmp")
        assert not tmp_file.exists()

    def test_write_creates_final_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        # Delete the file to check _write creates it again
        file_path.unlink()
        tracker._write()
        assert file_path.exists()

    def test_write_produces_valid_json(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker._write()
        data = json.loads(file_path.read_text())
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# TestStartSession
# ---------------------------------------------------------------------------


class TestStartSession:
    """Tests for the start_session method."""

    def test_start_session_sets_current_session_id(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.start_session("sess-abc", events_total=10)
        data = json.loads(file_path.read_text())
        assert data["current_session_id"] == "sess-abc"

    def test_start_session_sets_events_total(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.start_session("sess-abc", events_total=42)
        data = json.loads(file_path.read_text())
        assert data["current_session_events_total"] == 42

    def test_start_session_resets_events_sent(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.start_session("sess-first", events_total=5)
        # Simulate some events sent
        tracker.event_sent()
        tracker.event_sent()
        # Start a new session — events_sent should reset
        tracker.start_session("sess-second", events_total=3)
        data = json.loads(file_path.read_text())
        assert data["current_session_events_sent"] == 0

    def test_start_session_writes_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        file_path.unlink()  # Remove to verify write happens
        tracker.start_session("sess-abc", events_total=5)
        assert file_path.exists()


# ---------------------------------------------------------------------------
# TestEventSent
# ---------------------------------------------------------------------------


class TestEventSent:
    """Tests for the event_sent method."""

    def test_event_sent_increments_counter(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("sess-1", events_total=10)
        tracker.event_sent()
        data = json.loads(file_path.read_text())
        assert data["current_session_events_sent"] == 1

    def test_event_sent_increments_multiple_times(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("sess-1", events_total=10)
        for _ in range(5):
            tracker.event_sent()
        data = json.loads(file_path.read_text())
        assert data["current_session_events_sent"] == 5

    def test_event_sent_writes_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.start_session("sess-1", events_total=5)
        file_path.unlink()
        tracker.event_sent()
        assert file_path.exists()


# ---------------------------------------------------------------------------
# TestSessionCompleted
# ---------------------------------------------------------------------------


class TestSessionCompleted:
    """Tests for the session_completed method."""

    def test_session_completed_increments_counter(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=3)
        tracker.session_completed()
        data = json.loads(file_path.read_text())
        assert data["sessions_completed"] == 1

    def test_session_completed_increments_multiple(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=3)
        tracker.session_completed()
        tracker.session_completed()
        data = json.loads(file_path.read_text())
        assert data["sessions_completed"] == 2

    def test_session_completed_writes_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=3)
        file_path.unlink()
        tracker.session_completed()
        assert file_path.exists()


# ---------------------------------------------------------------------------
# TestMarkCompleted
# ---------------------------------------------------------------------------


class TestMarkCompleted:
    """Tests for the mark_completed method."""

    def test_mark_completed_sets_status(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        tracker.mark_completed()
        data = json.loads(file_path.read_text())
        assert data["status"] == "completed"

    def test_mark_completed_writes_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=1)
        file_path.unlink()
        tracker.mark_completed()
        assert file_path.exists()


# ---------------------------------------------------------------------------
# TestMarkFailed
# ---------------------------------------------------------------------------


class TestMarkFailed:
    """Tests for the mark_failed method."""

    def test_mark_failed_sets_status(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.mark_failed("sess-1", 5, 500, "Internal Server Error")
        data = json.loads(file_path.read_text())
        assert data["status"] == "failed"

    def test_mark_failed_populates_failed_at_dict(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.mark_failed("sess-abc", 10, 503, "Service Unavailable")
        data = json.loads(file_path.read_text())
        assert data["failed_at"] is not None
        assert isinstance(data["failed_at"], dict)

    def test_mark_failed_stores_session_id(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.mark_failed("sess-xyz", 3, 400, "Bad Request")
        data = json.loads(file_path.read_text())
        assert data["failed_at"]["session_id"] == "sess-xyz"

    def test_mark_failed_stores_event_index(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.mark_failed("sess-1", 7, 500, "Error")
        data = json.loads(file_path.read_text())
        assert data["failed_at"]["event_index"] == 7

    def test_mark_failed_stores_http_status(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.mark_failed("sess-1", 0, 422, "Unprocessable Entity")
        data = json.loads(file_path.read_text())
        assert data["failed_at"]["http_status"] == 422

    def test_mark_failed_stores_error(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.mark_failed("sess-1", 0, 500, "Connection timeout")
        data = json.loads(file_path.read_text())
        assert data["failed_at"]["error"] == "Connection timeout"

    def test_mark_failed_writes_file(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        file_path.unlink()
        tracker.mark_failed("sess-1", 0, 500, "Error")
        assert file_path.exists()


# ---------------------------------------------------------------------------
# TestRead
# ---------------------------------------------------------------------------


class TestRead:
    """Tests for the read() instance method."""

    def test_read_returns_dict(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        result = tracker.read()
        assert isinstance(result, dict)

    def test_read_returns_current_state(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=2)
        tracker.mark_completed()
        result = tracker.read()
        assert result["status"] == "completed"

    def test_read_reflects_modifications(self, tmp_path):
        file_path = tmp_path / "progress.json"
        tracker = ProgressTracker("job-1", file_path, sessions_total=3)
        tracker.start_session("sess-a", events_total=10)
        tracker.event_sent()
        tracker.event_sent()
        result = tracker.read()
        assert result["current_session_id"] == "sess-a"
        assert result["current_session_events_sent"] == 2


# ---------------------------------------------------------------------------
# TestReadFile
# ---------------------------------------------------------------------------


class TestReadFile:
    """Tests for the static read_file() method."""

    def test_read_file_returns_none_if_not_exists(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist.json"
        result = ProgressTracker.read_file(nonexistent)
        assert result is None

    def test_read_file_returns_dict_if_exists(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=2)
        result = ProgressTracker.read_file(file_path)
        assert isinstance(result, dict)

    def test_read_file_returns_correct_data(self, tmp_path):
        file_path = tmp_path / "progress.json"
        ProgressTracker("my-special-job", file_path, sessions_total=7)
        result = ProgressTracker.read_file(file_path)
        assert result is not None
        assert result["job_id"] == "my-special-job"
        assert result["sessions_total"] == 7

    def test_read_file_is_static(self, tmp_path):
        """read_file can be called without an instance."""
        file_path = tmp_path / "progress.json"
        ProgressTracker("job-1", file_path, sessions_total=1)
        # Call as static method — no instance needed
        result = ProgressTracker.read_file(file_path)
        assert result is not None
