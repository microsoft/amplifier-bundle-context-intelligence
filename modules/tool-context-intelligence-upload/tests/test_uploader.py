"""Tests for uploader.py — core HTTP replay loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from amplifier_module_tool_context_intelligence_upload.uploader import (
    UploadResult,
    _count_lines,
    run_upload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tracker() -> MagicMock:
    """Return a mock ProgressTracker."""
    tracker = MagicMock()
    return tracker


def _write_events(path: Path, records: list[dict[str, Any]]) -> None:
    """Write records as JSONL to path."""
    path.write_text(
        "\n".join(json.dumps(r) for r in records),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# TestUploadResult
# ---------------------------------------------------------------------------


class TestUploadResult:
    """Tests for the UploadResult class."""

    def test_success_result_attributes(self) -> None:
        """UploadResult stores success, sessions_uploaded, events_uploaded."""
        result = UploadResult(success=True, sessions_uploaded=3, events_uploaded=42)
        assert result.success is True
        assert result.sessions_uploaded == 3
        assert result.events_uploaded == 42
        assert result.error is None

    def test_failed_result_with_error(self) -> None:
        """UploadResult stores error when provided."""
        result = UploadResult(
            success=False, sessions_uploaded=1, events_uploaded=5, error="HTTP 503"
        )
        assert result.success is False
        assert result.error == "HTTP 503"

    def test_to_dict_success(self) -> None:
        """to_dict() returns status='completed' on success."""
        result = UploadResult(success=True, sessions_uploaded=2, events_uploaded=10)
        d = result.to_dict()
        assert d["status"] == "completed"
        assert d["sessions_uploaded"] == 2
        assert d["events_uploaded"] == 10
        assert "error" not in d

    def test_to_dict_failed(self) -> None:
        """to_dict() returns status='failed' and includes error on failure."""
        result = UploadResult(
            success=False, sessions_uploaded=0, events_uploaded=0, error="Connection refused"
        )
        d = result.to_dict()
        assert d["status"] == "failed"
        assert d["sessions_uploaded"] == 0
        assert d["events_uploaded"] == 0
        assert d["error"] == "Connection refused"

    def test_to_dict_failed_no_error_field_absent_when_none(self) -> None:
        """to_dict() omits error key when error is None."""
        result = UploadResult(success=True, sessions_uploaded=0, events_uploaded=0, error=None)
        d = result.to_dict()
        assert "error" not in d


# ---------------------------------------------------------------------------
# TestCountLines
# ---------------------------------------------------------------------------


class TestCountLines:
    """Tests for _count_lines helper."""

    def test_counts_nonempty_file(self, tmp_path: Path) -> None:
        """Returns the number of lines in the file."""
        f = tmp_path / "test.jsonl"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        assert _count_lines(f) == 3

    def test_counts_file_without_trailing_newline(self, tmp_path: Path) -> None:
        """Handles files without a trailing newline."""
        f = tmp_path / "test.jsonl"
        f.write_text("line1\nline2", encoding="utf-8")
        assert _count_lines(f) == 2

    def test_empty_file_returns_zero(self, tmp_path: Path) -> None:
        """Empty file returns 0 lines."""
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        assert _count_lines(f) == 0


# ---------------------------------------------------------------------------
# TestRunUpload — happy path
# ---------------------------------------------------------------------------


class TestRunUploadHappyPath:
    """Tests for run_upload — successful upload scenarios."""

    def test_single_session_all_events_sent(self, tmp_path: Path) -> None:
        """All events in a session are POSTed and tracker is updated."""
        session_dir = tmp_path / "session-abc"
        session_dir.mkdir()
        records = [
            {"event": "tool_call", "workspace": "ws1", "data": {"key": "val"}},
            {"event": "tool_result", "workspace": "ws1", "data": {"key": "val2"}},
        ]
        _write_events(session_dir / "events.jsonl", records)
        metadata = {"session_id": "abc", "format": "context-intelligence"}
        sessions = [(session_dir, metadata)]
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = run_upload(sessions, "https://server", "mykey", tracker)

        assert result.success is True
        assert result.sessions_uploaded == 1
        assert result.events_uploaded == 2
        assert mock_client.post.call_count == 2

    def test_tracker_called_in_order(self, tmp_path: Path) -> None:
        """tracker.start_session, event_sent, session_completed, mark_completed are called."""
        session_dir = tmp_path / "session-abc"
        session_dir.mkdir()
        records = [{"event": "evt", "workspace": "ws", "data": {}}]
        _write_events(session_dir / "events.jsonl", records)
        metadata = {"session_id": "abc"}
        sessions = [(session_dir, metadata)]
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            run_upload(sessions, "https://server", "key", tracker)

        tracker.start_session.assert_called_once_with("abc", 1)
        tracker.event_sent.assert_called_once()
        tracker.session_completed.assert_called_once()
        tracker.mark_completed.assert_called_once()

    def test_posts_to_correct_url(self, tmp_path: Path) -> None:
        """Events are POSTed to {server_url}/events."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [{"event": "e", "workspace": "w", "data": {}}],
        )
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            run_upload(
                [(session_dir, metadata)],
                "https://myserver.example.com",
                "key",
                tracker,
            )

        url_called = mock_client.post.call_args[0][0]
        assert url_called == "https://myserver.example.com/events"

    def test_authorization_header_set(self, tmp_path: Path) -> None:
        """httpx.Client is created with Authorization: Bearer header."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [{"event": "e", "workspace": "w", "data": {}}],
        )
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            run_upload([(session_dir, metadata)], "https://server", "supersecretkey", tracker)

        _, kwargs = mock_client_cls.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer supersecretkey"

    def test_timeout_configuration(self, tmp_path: Path) -> None:
        """httpx.Client is created with connect=5.0, read=30.0, write=30.0, pool=5.0."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [{"event": "e", "workspace": "w", "data": {}}],
        )
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        _, kwargs = mock_client_cls.call_args
        timeout = kwargs.get("timeout")
        assert timeout is not None
        # Should be an httpx.Timeout with the correct values
        assert timeout.connect == 5.0
        assert timeout.read == 30.0
        assert timeout.write == 30.0
        assert timeout.pool == 5.0

    def test_workspace_from_record_not_overridden(self, tmp_path: Path) -> None:
        """Workspace comes from each events.jsonl record."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        records = [
            {"event": "e1", "workspace": "workspace-from-record", "data": {}},
        ]
        _write_events(session_dir / "events.jsonl", records)
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_payloads: list[Any] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                captured_payloads.append(kwargs.get("json"))
                return mock_response

            mock_client.post.side_effect = capture_post

            run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        assert len(captured_payloads) == 1
        assert captured_payloads[0]["workspace"] == "workspace-from-record"

    def test_multiple_sessions_all_completed(self, tmp_path: Path) -> None:
        """Multiple sessions are all processed and tracker.mark_completed called once."""
        sessions = []
        for i in range(3):
            session_dir = tmp_path / f"session-{i}"
            session_dir.mkdir()
            _write_events(
                session_dir / "events.jsonl",
                [{"event": "e", "workspace": "ws", "data": {}}],
            )
            sessions.append((session_dir, {"session_id": f"sid-{i}"}))

        tracker = _make_tracker()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = run_upload(sessions, "https://server", "key", tracker)

        assert result.success is True
        assert result.sessions_uploaded == 3
        assert result.events_uploaded == 3
        tracker.mark_completed.assert_called_once()
        assert tracker.session_completed.call_count == 3

    def test_empty_lines_skipped(self, tmp_path: Path) -> None:
        """Empty lines in events.jsonl are skipped without error."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        content = (
            json.dumps({"event": "e1", "workspace": "w", "data": {}})
            + "\n\n"
            + json.dumps({"event": "e2", "workspace": "w", "data": {}})
            + "\n"
        )
        (session_dir / "events.jsonl").write_text(content, encoding="utf-8")
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        assert result.events_uploaded == 2
        assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# TestRunUpload — error handling
# ---------------------------------------------------------------------------


class TestRunUploadErrorHandling:
    """Tests for run_upload — error and skip scenarios."""

    def test_missing_events_jsonl_skips_session_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Session without events.jsonl is skipped with a warning to stderr."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        # No events.jsonl created
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        captured = capsys.readouterr()
        assert "sid" in captured.err or "events.jsonl" in captured.err
        assert mock_client.post.call_count == 0
        tracker.mark_completed.assert_called_once()

    def test_malformed_json_line_skipped_with_warning_and_event_sent_called(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Malformed JSON lines produce stderr warning and still call tracker.event_sent."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        content = "not-valid-json\n" + json.dumps({"event": "e", "workspace": "w", "data": {}})
        (session_dir / "events.jsonl").write_text(content, encoding="utf-8")
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        captured = capsys.readouterr()
        assert captured.err  # Warning was printed
        # event_sent called once for malformed + once for valid = 2
        assert tracker.event_sent.call_count == 2
        # But only 1 successful POST
        assert mock_client.post.call_count == 1
        assert result.events_uploaded == 1

    def test_http_error_returns_failed_result(self, tmp_path: Path) -> None:
        """On httpx.HTTPError, returns failed UploadResult with http_status=0."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [{"event": "e", "workspace": "w", "data": {}}],
        )
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")

            result = run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        assert result.success is False
        tracker.mark_failed.assert_called_once()
        # http_status=0 for HTTP errors
        _, kwargs = tracker.mark_failed.call_args
        assert kwargs.get("http_status") == 0

    def test_non_2xx_response_returns_failed_result(self, tmp_path: Path) -> None:
        """On non-2xx response, returns failed UploadResult with actual status code."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [{"event": "e", "workspace": "w", "data": {}}],
        )
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        assert result.success is False
        tracker.mark_failed.assert_called_once()
        # http_status=503 for non-2xx
        call_args = tracker.mark_failed.call_args
        # Check positional or keyword
        all_args = list(call_args[0]) + list(call_args[1].values())
        assert 503 in all_args

    def test_stops_immediately_on_non_2xx(self, tmp_path: Path) -> None:
        """On non-2xx response, no further events are uploaded."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [
                {"event": "e1", "workspace": "w", "data": {}},
                {"event": "e2", "workspace": "w", "data": {}},
            ],
        )
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 400

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        assert result.success is False
        # Only first POST attempted before failure
        assert mock_client.post.call_count == 1

    def test_stops_immediately_on_http_error(self, tmp_path: Path) -> None:
        """On httpx.HTTPError, stops immediately and doesn't process further events."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [
                {"event": "e1", "workspace": "w", "data": {}},
                {"event": "e2", "workspace": "w", "data": {}},
            ],
        )
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = httpx.ConnectError("fail")

            result = run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        assert result.success is False
        assert mock_client.post.call_count == 1

    def test_mark_failed_called_with_session_id_and_event_index(self, tmp_path: Path) -> None:
        """tracker.mark_failed called with session_id, event_index, http_status, error."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        _write_events(
            session_dir / "events.jsonl",
            [{"event": "e", "workspace": "w", "data": {}}],
        )
        metadata = {"session_id": "my-session"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        tracker.mark_failed.assert_called_once()
        call_args = tracker.mark_failed.call_args
        # session_id should be "my-session"
        all_positional = call_args[0]
        all_keyword = call_args[1]
        combined = {**{str(i): v for i, v in enumerate(all_positional)}, **all_keyword}
        assert "my-session" in combined.values()


# ---------------------------------------------------------------------------
# TestRunUploadBuildPayload
# ---------------------------------------------------------------------------


class TestRunUploadBuildPayload:
    """Tests that build_payload is correctly used."""

    def test_build_payload_called_with_event_workspace_data(self, tmp_path: Path) -> None:
        """run_upload calls build_payload with event, workspace, data from record."""
        session_dir = tmp_path / "s"
        session_dir.mkdir()
        records = [
            {
                "event": "tool_call",
                "workspace": "my-workspace",
                "data": {"tool": "bash", "args": ["ls"]},
            }
        ]
        _write_events(session_dir / "events.jsonl", records)
        metadata = {"session_id": "sid"}
        tracker = _make_tracker()

        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_payloads: list[Any] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture(url: str, **kwargs: Any) -> MagicMock:
                captured_payloads.append(kwargs.get("json"))
                return mock_response

            mock_client.post.side_effect = capture

            run_upload([(session_dir, metadata)], "https://server", "key", tracker)

        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        # build_payload adds idempotency_key
        assert "idempotency_key" in payload
        assert payload["event"] == "tool_call"
        assert payload["workspace"] == "my-workspace"
        assert payload["data"] == {"tool": "bash", "args": ["ls"]}
