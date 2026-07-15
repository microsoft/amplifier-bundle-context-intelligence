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

    def test_stops_on_permanent_403(self, tmp_path: Path) -> None:
        """A permanent 4xx (403) on the 3rd call aborts immediately: uploaded=2, calls=3.

        (Issue #338: a *transient* status like 503 is now retried; this test uses a
        permanent status to prove the whole-run-stops-on-failure behaviour is preserved
        for errors retrying cannot fix.)
        """
        events = _make_events(5)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        post_calls: list[int] = []

        def side_effect(url: str, **kwargs: Any) -> MagicMock:
            post_calls.append(1)
            if len(post_calls) == 3:
                return _mock_response(403)
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
        """status='failed', failed_at has session_id, event_index=2, http_status=403.

        Uses a permanent 403 (not 503, which is now retried) so the run aborts at the
        failing event with no retry noise.
        """
        events = _make_events(5)
        session_dir, metadata = _write_session(tmp_path, "my-session", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        post_calls: list[int] = []

        def side_effect(url: str, **kwargs: Any) -> MagicMock:
            post_calls.append(1)
            if len(post_calls) == 3:
                return _mock_response(403)
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
        assert result.failed_at["http_status"] == 403


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

        # Issue #338: the Authorization header is now sent PER REQUEST (so token
        # refresh can fire mid-run), not baked into the httpx.Client constructor.
        post_kwargs = mock_client.post.call_args.kwargs
        headers = post_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer sk-my-key"

    def test_default_sends_replay_true_query_param(self, tmp_path: Path) -> None:
        """By default, run_upload posts with params={'replay': 'true'} on every call."""
        events = _make_events(1)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            run_upload(sessions, "https://my-server.example.com", "api-key", tracker)

        # URL still ends with /events (params do not mutate the URL string)
        url_called = mock_client.post.call_args[0][0]
        assert url_called.endswith("/events")
        # The replay query parameter is forwarded as the httpx `params` kwarg.
        # Use a string value ("true") not a Python bool (True) — httpx serialises
        # bool True as "True" (capital T), which the server would not recognise.
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs.get("params") == {"replay": "true"}

    def test_replay_false_sends_no_params(self, tmp_path: Path) -> None:
        """When replay=False, run_upload posts with params=None (no ?replay= query string)."""
        events = _make_events(1)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            run_upload(sessions, "https://my-server.example.com", "api-key", tracker, replay=False)

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs.get("params") is None


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

    def test_run_upload_counts_skipped_malformed_lines(self, tmp_path: Path) -> None:
        """events_skipped counts malformed lines; blank lines do not count as skips."""
        session_dir = tmp_path / "session-skip-count"
        session_dir.mkdir(parents=True)
        metadata = {"session_id": "skip-count-session", "format": "context-intelligence"}
        (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        # Two valid lines, one malformed line in between
        valid_event_1 = json.dumps({"event": "good-event-1", "workspace": "ws", "data": {}})
        malformed_line = "NOT JSON"
        valid_event_2 = json.dumps({"event": "good-event-2", "workspace": "ws", "data": {}})
        (session_dir / "events.jsonl").write_text(
            f"{valid_event_1}\n{malformed_line}\n{valid_event_2}\n", encoding="utf-8"
        )

        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(status_code=200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.events_uploaded == 2
        assert result.events_skipped == 1

    def test_run_upload_skips_non_dict_record_without_aborting(self, tmp_path: Path) -> None:
        """TB-1/TB-15: valid-JSON non-dict records (null, 42) are skipped and
        counted, never abort the batch with an uncaught AttributeError."""
        session_dir = tmp_path / "session-non-dict"
        session_dir.mkdir(parents=True)
        metadata = {"session_id": "non-dict-session", "format": "context-intelligence"}
        (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        good_event_1 = json.dumps({"event": "good-event-1", "workspace": "ws", "data": {}})
        good_event_2 = json.dumps({"event": "good-event-2", "workspace": "ws", "data": {}})
        (session_dir / "events.jsonl").write_text(
            f"{good_event_1}\n42\nnull\n{good_event_2}\n", encoding="utf-8"
        )

        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(status_code=200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is True
        assert result.events_uploaded == 2
        assert result.events_skipped == 2

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

            # max_retries=0 → single attempt, so a persistent transport error fails
            # immediately (no backoff sleeps in the unit test).
            result = run_upload(sessions, "https://server", "api-key", tracker, max_retries=0)

        assert result.success is False
        assert result.error == "Connection refused"
        assert result.failed_at is not None
        assert result.failed_at["http_status"] == 0
        assert result.failed_at["session_id"] == "net-fail-session"


# ---------------------------------------------------------------------------
# TestRunUploadParseFnInjection
# ---------------------------------------------------------------------------


class TestRunUploadParseFnInjection:
    """Tests for the injectable parse_fn parameter (Seam B)."""

    def test_run_upload_uses_injected_parse_fn(self, tmp_path: Path) -> None:
        """A custom parse_fn controls the (event, workspace, data) triple posted."""
        events = _make_events(2)
        session_dir, metadata = _write_session(tmp_path, "abc", events)
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        captured_payloads: list[Any] = []

        def fake_parse(
            line: str, session_dir: Path, metadata: dict[str, Any]
        ) -> tuple[str, str, dict[str, Any]]:
            return ("custom:event", "custom-ws", {"raw": line})

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                captured_payloads.append(kwargs.get("json"))
                return _mock_response(200)

            mock_client.post.side_effect = capture_post

            run_upload(sessions, "https://server", "api-key", tracker, parse_fn=fake_parse)

        assert len(captured_payloads) == 2
        assert captured_payloads[0]["event"] == "custom:event"
        assert captured_payloads[0]["workspace"] == "custom-ws"
        assert captured_payloads[1]["event"] == "custom:event"
        assert captured_payloads[1]["workspace"] == "custom-ws"


class TestWorkspaceFromPath:
    """Tests for _workspace_from_path — project-slug fallback for old sessions."""

    def test_extracts_project_slug_from_standard_path(self, tmp_path: Path) -> None:
        """Standard .amplifier/projects/{slug}/sessions/{id}/context-intelligence/ path."""
        from amplifier_module_tool_context_intelligence_upload.uploader import (
            _workspace_from_path,
        )

        session_dir = (
            tmp_path
            / "projects"
            / "my-project-slug"
            / "sessions"
            / "abc123"
            / "context-intelligence"
        )
        assert _workspace_from_path(session_dir) == "my-project-slug"

    def test_returns_empty_string_when_projects_not_in_path(self, tmp_path: Path) -> None:
        """When 'projects' segment absent, returns empty string gracefully."""
        from amplifier_module_tool_context_intelligence_upload.uploader import (
            _workspace_from_path,
        )

        session_dir = tmp_path / "sessions" / "abc123" / "context-intelligence"
        assert _workspace_from_path(session_dir) == ""

    def test_metadata_workspace_used_before_path_fallback(self, tmp_path: Path) -> None:
        """metadata.json workspace is used before folder-path derivation."""
        session_dir = (
            tmp_path / "projects" / "path-slug" / "sessions" / "s1" / "context-intelligence"
        )
        session_dir.mkdir(parents=True)
        # metadata has workspace — takes priority over path-derived slug
        metadata = {
            "session_id": "s1",
            "format": "context-intelligence",
            "workspace": "workspace-from-metadata",
        }
        event = json.dumps({"event": "session:start", "data": {}})
        (session_dir / "events.jsonl").write_text(event + "\n", encoding="utf-8")

        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        captured_payloads: list[Any] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.status_code = 200

            def capture(url: str, json: Any = None, **kwargs: Any) -> MagicMock:
                captured_payloads.append(json)
                return mock_response

            mock_client.post.side_effect = capture
            run_upload(sessions, "https://server", "api-key", tracker)

        assert len(captured_payloads) == 1
        # metadata workspace wins over path-derived "path-slug"
        assert captured_payloads[0]["workspace"] == "workspace-from-metadata"

    def test_workspace_fallback_used_when_record_missing_workspace(self, tmp_path: Path) -> None:
        """When events.jsonl record has no 'workspace' key, project slug is used."""
        # Build path structure that embeds project slug
        project_slug = "-home-user-my-project"
        session_dir = (
            tmp_path / "projects" / project_slug / "sessions" / "s1" / "context-intelligence"
        )
        session_dir.mkdir(parents=True)
        metadata = {"session_id": "s1", "format": "context-intelligence"}

        # Event record without 'workspace' key (old format)
        event = json.dumps(
            {"event": "session:start", "data": {"timestamp": "2026-03-18T00:00:00Z"}}
        )
        (session_dir / "events.jsonl").write_text(event + "\n", encoding="utf-8")

        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        captured_payloads: list[Any] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.post.return_value = mock_response

            def capture(url: str, json: Any = None, **kwargs: Any) -> MagicMock:
                captured_payloads.append(json)
                return mock_response

            mock_client.post.side_effect = capture
            run_upload(sessions, "https://server", "api-key", tracker)

        assert len(captured_payloads) == 1
        # Workspace in payload must be the project slug, not empty string
        assert captured_payloads[0]["workspace"] == project_slug


class TestErrorMessageIncludesServerBody:
    """Error messages from non-2xx responses must include the server response body."""

    def test_422_error_message_includes_server_body(self, tmp_path: Path) -> None:
        """HTTP 422 error message includes the server's response body."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        server_body = '{"detail": "workspace must not be empty"}'

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.status_code = 422
            mock_response.text = server_body
            mock_client.post.return_value = mock_response

            result = run_upload(sessions, "https://server/", "key", tracker)

        assert result.success is False
        assert result.error is not None
        assert "422" in result.error
        assert "workspace must not be empty" in result.error

    def test_500_error_message_includes_server_body(self, tmp_path: Path) -> None:
        """HTTP 500 error message includes truncated server response body."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_client.post.return_value = mock_response

            # 500 is transient (retried); max_retries=0 → single attempt so the test
            # asserts the terminal error message without incurring backoff sleeps.
            result = run_upload(sessions, "https://server/", "key", tracker, max_retries=0)

        assert result.success is False
        assert result.error is not None
        assert "500" in result.error
        assert "Internal Server Error" in result.error
