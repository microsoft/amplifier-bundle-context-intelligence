"""Tests for uploader.py — happy path, failure, workspace, URL/auth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx


from amplifier_module_tool_context_intelligence_upload.uploader import (
    UploadResult,
    run_upload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_session(
    tmp_path: Path,
    session_id: str,
    events: list[dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    """Creates session dir with metadata.json and events.jsonl."""
    session_dir = tmp_path / f"session-{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    metadata = {"session_id": session_id, "format": "context-intelligence"}
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events),
        encoding="utf-8",
    )
    return session_dir, metadata


def _make_events(n: int, workspace: str = "test-workspace") -> list[dict[str, Any]]:
    """Generates n fake event records."""
    return [{"event": f"event-{i}", "workspace": workspace, "data": {"index": i}} for i in range(n)]


def _mock_response(status_code: int = 200) -> MagicMock:
    """Creates mock httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    return response


# ---------------------------------------------------------------------------
# TestUploadResultSerialization
# ---------------------------------------------------------------------------


class TestUploadResultSerialization:
    """Tests for UploadResult.to_dict() serialization."""

    def test_success_result(self) -> None:
        """to_dict() returns status='completed', no error key."""
        result = UploadResult(success=True, sessions_uploaded=1, events_uploaded=5)
        d = result.to_dict()
        assert d["status"] == "completed"
        assert "error" not in d

    def test_failure_result(self) -> None:
        """to_dict() returns status='failed', error present."""
        result = UploadResult(
            success=False,
            sessions_uploaded=0,
            events_uploaded=2,
            error="HTTP 503",
        )
        d = result.to_dict()
        assert d["status"] == "failed"
        assert d["error"] == "HTTP 503"


# ---------------------------------------------------------------------------
# TestUploadHappyPath
# ---------------------------------------------------------------------------


class TestUploadHappyPath:
    """Tests for run_upload — successful upload scenarios."""

    def test_all_events_sent_for_single_session(self, tmp_path: Path) -> None:
        """5 events, mock_client.post called 5 times."""
        events = _make_events(5)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert mock_client.post.call_count == 5
        assert result.events_uploaded == 5

    def test_events_sent_in_order(self, tmp_path: Path) -> None:
        """Captures payloads, verifies event-0, event-1, event-2 in order."""
        events = _make_events(3)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        captured_payloads: list[Any] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                captured_payloads.append(kwargs.get("json"))
                return _mock_response(200)

            mock_client.post.side_effect = capture_post

            run_upload(sessions, "https://server", "api-key", tracker)

        assert len(captured_payloads) == 3
        assert captured_payloads[0]["event"] == "event-0"
        assert captured_payloads[1]["event"] == "event-1"
        assert captured_payloads[2]["event"] == "event-2"

    def test_progress_updated_per_event(self, tmp_path: Path) -> None:
        """Result has status='completed', sessions_uploaded=1."""
        events = _make_events(3)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        d = result.to_dict()
        assert d["status"] == "completed"
        assert result.sessions_uploaded == 1
        assert tracker.event_sent.call_count == 3

    def test_multiple_sessions_uploaded_in_order(self, tmp_path: Path) -> None:
        """2 sessions, 5 total events (2 + 3)."""
        events1 = _make_events(2)
        events2 = _make_events(3)
        session_dir1, metadata1 = _write_session(tmp_path, "sess-1", events1)
        session_dir2, metadata2 = _write_session(tmp_path, "sess-2", events2)
        sessions = [(session_dir1, metadata1), (session_dir2, metadata2)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is True
        assert result.sessions_uploaded == 2
        assert result.events_uploaded == 5
        assert mock_client.post.call_count == 5


# ---------------------------------------------------------------------------
# TestUploadStopOnFailure
# ---------------------------------------------------------------------------


class TestUploadStopOnFailure:
    """Tests for run_upload — stop on failure scenarios."""

    def test_stops_on_503(self, tmp_path: Path) -> None:
        """Fail on 3rd call: events_uploaded=2, call_count=3."""
        events = _make_events(5)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        post_calls: list[int] = []

        def side_effect(url: str, **kwargs: Any) -> MagicMock:
            post_calls.append(1)
            if len(post_calls) == 3:
                return _mock_response(503)
            return _mock_response(200)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = side_effect

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is False
        assert result.events_uploaded == 2
        assert mock_client.post.call_count == 3

    def test_failed_at_populated_correctly(self, tmp_path: Path) -> None:
        """status='failed', failed_at has session_id, event_index=2, http_status=503."""
        events = _make_events(5)
        session_dir, metadata = _write_session(tmp_path, "my-session", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        post_calls: list[int] = []

        def side_effect(url: str, **kwargs: Any) -> MagicMock:
            post_calls.append(1)
            if len(post_calls) == 3:
                return _mock_response(503)
            return _mock_response(200)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = side_effect

            result = run_upload(sessions, "https://server", "api-key", tracker)

        d = result.to_dict()
        assert d["status"] == "failed"
        assert result.failed_at is not None
        assert result.failed_at["session_id"] == "my-session"
        assert result.failed_at["event_index"] == 2
        assert result.failed_at["http_status"] == 503


# ---------------------------------------------------------------------------
# TestUploadWorkspaceFromRecord
# ---------------------------------------------------------------------------


class TestUploadWorkspaceFromRecord:
    """Tests that workspace is taken from each event record."""

    def test_workspace_from_record_not_external(self, tmp_path: Path) -> None:
        """Payload workspace='workspace-from-record'."""
        events = [{"event": "e1", "workspace": "workspace-from-record", "data": {}}]
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        captured_payloads: list[Any] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                captured_payloads.append(kwargs.get("json"))
                return _mock_response(200)

            mock_client.post.side_effect = capture_post

            run_upload(sessions, "https://server", "api-key", tracker)

        assert len(captured_payloads) == 1
        assert captured_payloads[0]["workspace"] == "workspace-from-record"

    def test_different_workspaces_per_record(self, tmp_path: Path) -> None:
        """Two events with different workspaces, each payload has the correct workspace."""
        events = [
            {"event": "e1", "workspace": "workspace-alpha", "data": {}},
            {"event": "e2", "workspace": "workspace-beta", "data": {}},
        ]
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        captured_payloads: list[Any] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                captured_payloads.append(kwargs.get("json"))
                return _mock_response(200)

            mock_client.post.side_effect = capture_post

            run_upload(sessions, "https://server", "api-key", tracker)

        assert len(captured_payloads) == 2
        assert captured_payloads[0]["workspace"] == "workspace-alpha"
        assert captured_payloads[1]["workspace"] == "workspace-beta"


# ---------------------------------------------------------------------------
# TestUploadUrlAndAuth
# ---------------------------------------------------------------------------


class TestUploadUrlAndAuth:
    """Tests for URL and authorization header construction."""

    def test_posts_to_server_url_events_endpoint(self, tmp_path: Path) -> None:
        """URL ends with /events."""
        events = _make_events(1)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            run_upload(sessions, "https://my-server.example.com", "api-key", tracker)

        url_called = mock_client.post.call_args[0][0]
        assert url_called.endswith("/events")
        assert "my-server.example.com" in url_called

    def test_authorization_header_set(self, tmp_path: Path) -> None:
        """Bearer sk-my-key header is set."""
        events = _make_events(1)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            run_upload(sessions, "https://server", "sk-my-key", tracker)

        _, kwargs = mock_client_cls.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer sk-my-key"


# ---------------------------------------------------------------------------
# TestUploadEdgeCases
# ---------------------------------------------------------------------------


class TestUploadEdgeCases:
    """Tests for run_upload defensive code paths: missing file, malformed JSON, network error."""

    def test_session_with_missing_events_file_is_skipped(self, tmp_path: Path) -> None:
        """Session without events.jsonl is skipped; upload still returns success."""
        # Create session dir + metadata but NO events.jsonl
        session_dir = tmp_path / "session-no-events"
        session_dir.mkdir(parents=True)
        metadata = {"session_id": "no-events-session", "format": "context-intelligence"}
        (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(status_code=200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        # Session was skipped — no HTTP calls, no events uploaded, but overall success
        assert mock_client.post.call_count == 0
        assert result.success is True
        assert result.events_uploaded == 0
        assert result.sessions_uploaded == 0

    def test_malformed_json_line_is_skipped(self, tmp_path: Path) -> None:
        """Malformed JSON line is skipped; tracker.event_sent() called; valid line uploaded."""
        session_dir = tmp_path / "session-bad-json"
        session_dir.mkdir(parents=True)
        metadata = {"session_id": "bad-json-session", "format": "context-intelligence"}
        (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        # One valid line, one malformed line
        valid_event = json.dumps({"event": "good-event", "workspace": "ws", "data": {}})
        malformed_line = "{ this is not valid json !!!"
        (session_dir / "events.jsonl").write_text(
            f"{valid_event}\n{malformed_line}\n", encoding="utf-8"
        )

        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(status_code=200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        # Only the valid line was POSTed; the malformed line was skipped
        assert mock_client.post.call_count == 1
        # tracker.event_sent() called once for valid event, once for skipped malformed line
        assert tracker.event_sent.call_count == 2
        assert result.success is True
        assert result.events_uploaded == 1

    def test_network_error_returns_failure_with_http_status_zero(self, tmp_path: Path) -> None:
        """httpx.HTTPError → UploadResult(success=False) with failed_at[http_status] == 0."""
        session_dir = tmp_path / "session-network-fail"
        session_dir.mkdir(parents=True)
        metadata = {"session_id": "net-fail-session", "format": "context-intelligence"}
        (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        event = json.dumps({"event": "e1", "workspace": "ws", "data": {}})
        (session_dir / "events.jsonl").write_text(event + "\n", encoding="utf-8")

        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = httpx.HTTPError("Connection refused")

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is False
        assert result.error == "Connection refused"
        assert result.failed_at is not None
        assert result.failed_at["http_status"] == 0
        assert result.failed_at["session_id"] == "net-fail-session"
