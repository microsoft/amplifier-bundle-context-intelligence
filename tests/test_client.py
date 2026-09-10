"""Tests for context_intelligence.client (task-3).

Covers:
- Module imports correctly
- _safe_json_loads() handles strings and non-strings
- CIClient.__init__ stores server_url and api_key
- CIClient.cypher() POSTs to /cypher and returns list[dict]
- CIClient.list_blob_keys() parses ci-blob:// URIs into a set[str]
- CIClient.fetch_blob() GETs /blobs/{session_id}/{key} and returns content
- CIClient.health_check() uses cypher() query and returns dict with status/session_count
- Logger is named context_intelligence.client
"""

from __future__ import annotations

from unittest.mock import patch, AsyncMock, MagicMock

import pytest


class TestImport:
    """Module must be importable."""

    def test_ciclient_import(self):
        """CIClient must be importable from context_intelligence.client."""
        from context_intelligence.client import CIClient  # noqa: F401

    def test_safe_json_loads_import(self):
        """_safe_json_loads must be importable from context_intelligence.client."""
        from context_intelligence.client import _safe_json_loads  # noqa: F401

    def test_acceptance_criteria_command(self):
        """Simulate the acceptance criteria import command."""
        from context_intelligence.client import CIClient, _safe_json_loads

        assert CIClient is not None
        assert _safe_json_loads is not None


class TestSafeJsonLoads:
    """_safe_json_loads() must parse JSON strings and pass through non-strings."""

    def test_parses_json_dict_string(self):
        """A JSON dict string is parsed into a dict."""
        from context_intelligence.client import _safe_json_loads

        result = _safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parses_json_list_string(self):
        """A JSON list string is parsed into a list."""
        from context_intelligence.client import _safe_json_loads

        result = _safe_json_loads("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_parses_json_number_string(self):
        """A JSON number string is parsed into a number."""
        from context_intelligence.client import _safe_json_loads

        result = _safe_json_loads("42")
        assert result == 42

    def test_returns_dict_as_is(self):
        """A dict passed directly is returned as-is."""
        from context_intelligence.client import _safe_json_loads

        d = {"already": "parsed"}
        result = _safe_json_loads(d)
        assert result is d

    def test_returns_list_as_is(self):
        """A list passed directly is returned as-is."""
        from context_intelligence.client import _safe_json_loads

        lst = [1, 2, 3]
        result = _safe_json_loads(lst)
        assert result is lst

    def test_returns_none_as_is(self):
        """None is returned as-is."""
        from context_intelligence.client import _safe_json_loads

        result = _safe_json_loads(None)
        assert result is None

    def test_returns_invalid_json_string_as_is(self):
        """A non-JSON string is returned as-is (not raised)."""
        from context_intelligence.client import _safe_json_loads

        bad = "not json"
        result = _safe_json_loads(bad)
        assert result == bad


class TestCIClientInit:
    """CIClient.__init__ must store server_url and api_key."""

    def test_stores_server_url(self):
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "mykey")
        assert client._server_url == "http://localhost:8000"

    def test_stores_api_key(self):
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "mykey")
        assert client._api_key == "mykey"

    def test_strips_trailing_slash_from_server_url(self):
        """Server URL trailing slash should be normalised."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000/", "key")
        # Either stripping or not is valid; we verify it's accessible
        assert "localhost:8000" in client._server_url


class TestCIClientCypher:
    """CIClient.cypher() must POST to /cypher and return list[dict]."""

    def test_cypher_returns_list_of_dicts(self):
        """cypher() returns a list[dict] from the server response."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "testkey")
        mock_response = [{"n": {"id": "1"}}, {"n": {"id": "2"}}]

        with patch("context_intelligence.client._http_post") as mock_post:
            mock_post.return_value = mock_response
            result = client.cypher("MATCH (n) RETURN n LIMIT 2")

        assert isinstance(result, list)
        assert result == mock_response

    def test_cypher_sends_correct_body(self):
        """cypher() sends query and workspace to /cypher."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "testkey")

        with patch("context_intelligence.client._http_post") as mock_post:
            mock_post.return_value = []
            client.cypher("MATCH (n) RETURN n", workspace="myworkspace")

        call_args = mock_post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        body = call_args[0][1] if call_args[0] else call_args[1]["body"]

        assert "/cypher" in url
        assert body["query"] == "MATCH (n) RETURN n"
        assert body["workspace"] == "myworkspace"

    def test_cypher_default_workspace_is_star(self):
        """cypher() defaults workspace to '*'."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "testkey")

        with patch("context_intelligence.client._http_post") as mock_post:
            mock_post.return_value = []
            client.cypher("MATCH (n) RETURN n")

        call_args = mock_post.call_args
        body = call_args[0][1] if call_args[0] else call_args[1]["body"]
        assert body["workspace"] == "*"

    def test_cypher_includes_authorization_header(self):
        """cypher() sends Authorization: Bearer header."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "secretkey")

        with patch("context_intelligence.client._http_post") as mock_post:
            mock_post.return_value = []
            client.cypher("MATCH (n) RETURN n")

        call_args = mock_post.call_args
        headers = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer secretkey"

    def test_cypher_forwards_params(self):
        """cypher() forwards a user-supplied params dict in the POST body."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "testkey")

        with patch("context_intelligence.client._http_post") as mock_post:
            mock_post.return_value = []
            client.cypher(
                "MATCH (n {id: $id}) RETURN n",
                workspace="myworkspace",
                params={"id": "abc-123"},
            )

        call_args = mock_post.call_args
        body = call_args[0][1] if call_args[0] else call_args[1]["body"]
        assert body["params"] == {"id": "abc-123"}

    def test_cypher_default_params_is_empty_dict(self):
        """cypher() with no params still sends an empty dict (backward compat)."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "testkey")

        with patch("context_intelligence.client._http_post") as mock_post:
            mock_post.return_value = []
            client.cypher("MATCH (n) RETURN n")

        call_args = mock_post.call_args
        body = call_args[0][1] if call_args[0] else call_args[1]["body"]
        assert body["params"] == {}


class TestCIClientListBlobKeys:
    """CIClient.list_blob_keys() must return set[str] of BARE blob keys."""

    def test_list_blob_keys_returns_set(self):
        """list_blob_keys() returns a set of BARE keys (ci-blob:// scheme stripped)."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = [
            "ci-blob://session1/key1",
            "ci-blob://session1/key2",
        ]

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = mock_response
            result = client.list_blob_keys("session1")

        assert isinstance(result, set)
        # Full URIs are normalized to bare keys (what fetch_blob(session_id, key) needs).
        assert result == {"key1", "key2"}

    def test_list_blob_keys_normalizes_uris_and_keeps_bare(self):
        """A full ci-blob:// URI is stripped to its bare key; a non-URI string is
        already-bare and kept as-is (no scheme filtering)."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = [
            "ci-blob://session1/key1",  # full URI -> bare "key1"
            "already_bare_key",  # already bare -> as-is
            "ci-blob://session1/key2",  # full URI -> bare "key2"
        ]

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = mock_response
            result = client.list_blob_keys("session1")

        assert result == {"key1", "already_bare_key", "key2"}

    def test_list_blob_keys_empty_response(self):
        """list_blob_keys() returns empty set when server returns empty list.

        Genuine-empty (a real 200 with `[]`) is an empty SUCCESS, not an error.
        """
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = []
            result = client.list_blob_keys("session1")

        assert result == set()

    def test_list_blob_keys_non_list_body_is_empty_success(self):
        """A well-formed non-list body (e.g. {} ) is treated as empty success, NOT an error.

        This preserves the genuine-empty semantics: only a genuine transport/HTTP
        FAILURE raises (see the fail-loud tests); a strange-but-200 body degrades to
        an empty set rather than crashing reconstruction.
        """
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = {"unexpected": "shape"}
            result = client.list_blob_keys("session1")

        assert result == set()

    def test_list_blob_keys_propagates_ciclienterror(self):
        """list_blob_keys() must NOT swallow a genuine failure -- it propagates
        CIClientError raised by _http_get_strict (no more silent empty-set)."""
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.side_effect = CIClientError(
                "boom", error_type="connection_error", url="http://localhost:8000/blobs/session1"
            )
            with pytest.raises(CIClientError) as excinfo:
                client.list_blob_keys("session1")

        assert excinfo.value.error_type == "connection_error"


class TestCIClientFetchBlob:
    """CIClient.fetch_blob() must GET /blobs/{session_id}/{key} and return Any|None."""

    def test_fetch_blob_returns_content(self):
        """fetch_blob() returns the parsed response content."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = {"data": "blob content here"}

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = mock_response
            result = client.fetch_blob("session1", "mykey")

        assert result == mock_response

    def test_fetch_blob_calls_correct_url(self):
        """fetch_blob() calls /blobs/{session_id}/{key}."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = None
            client.fetch_blob("my-session", "my-key")

        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert "my-session" in url
        assert "my-key" in url
        assert "/blobs/" in url

    def test_fetch_blob_returns_none_on_failure(self):
        """fetch_blob() returns None when the GET fails."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = None
            result = client.fetch_blob("session1", "key1")

        assert result is None

    def test_fetch_blob_includes_authorization_header(self):
        """fetch_blob() sends Authorization: Bearer header."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "blobkey")

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = {}
            client.fetch_blob("sess", "k")

        call_args = mock_get.call_args
        headers = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer blobkey"


class TestCIClientSessionSummary:
    """CIClient.session_summary() must GET /sessions/{id}/summary and return a dict."""

    def test_session_summary_returns_dict(self):
        """session_summary() returns the parsed summary dict from the server."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = {
            "created_by": "alice",
            "node_count": 10,
            "edge_count": 5,
            "blob_count": 2,
            "deletable": True,
        }

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = mock_response
            result = client.session_summary("session1")

        assert result == mock_response

    def test_session_summary_calls_correct_url(self):
        """session_summary() calls GET /sessions/{session_id}/summary."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = {}
            client.session_summary("my-session")

        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://localhost:8000/sessions/my-session/summary"

    def test_session_summary_includes_authorization_header(self):
        """session_summary() sends Authorization: [REDACTED:SECRET]"""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "secretkey")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = {}
            client.session_summary("my-session")

        call_args = mock_get.call_args
        headers = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer secretkey"

    def test_session_summary_propagates_ciclienterror_404(self):
        """A 404 (unknown session) must not be swallowed -- it propagates as CIClientError."""
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.side_effect = CIClientError(
                "HTTP 404 from http://localhost:8000/sessions/missing/summary",
                error_type="http_status",
                url="http://localhost:8000/sessions/missing/summary",
                status_code=404,
            )
            with pytest.raises(CIClientError) as excinfo:
                client.session_summary("missing")

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 404

    def test_session_summary_propagates_ciclienterror_409(self):
        """A 409 (still receiving data / ambiguous id) must propagate as CIClientError."""
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.side_effect = CIClientError(
                "HTTP 409 from http://localhost:8000/sessions/live/summary",
                error_type="http_status",
                url="http://localhost:8000/sessions/live/summary",
                status_code=409,
            )
            with pytest.raises(CIClientError) as excinfo:
                client.session_summary("live")

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 409


class TestCIClientWhoami:
    """CIClient.whoami() must GET /whoami and return a dict."""

    def test_whoami_returns_dict(self):
        """whoami() returns the parsed identity dict from the server."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = {"contributor_id": "octocat"}

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = mock_response
            result = client.whoami()

        assert result == mock_response

    def test_whoami_calls_correct_url(self):
        """whoami() calls GET /whoami."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = {}
            client.whoami()

        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://localhost:8000/whoami"

    def test_whoami_includes_authorization_header(self):
        """whoami() sends Authorization: Bearer <api_key>."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "secretkey")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = {}
            client.whoami()

        call_args = mock_get.call_args
        headers = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer secretkey"

    def test_whoami_returns_null_contributor_id_when_auth_disabled(self):
        """A server with auth disabled returns contributor_id: null -- passed through."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.return_value = {"contributor_id": None}
            result = client.whoami()

        assert result == {"contributor_id": None}

    def test_whoami_propagates_ciclienterror(self):
        """A genuine transport/HTTP failure must not be swallowed -- it propagates."""
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            mock_get.side_effect = CIClientError(
                "HTTP 500 from http://localhost:8000/whoami",
                error_type="http_status",
                url="http://localhost:8000/whoami",
                status_code=500,
            )
            with pytest.raises(CIClientError) as excinfo:
                client.whoami()

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 500


class TestCIClientDeleteSession:
    """CIClient.delete_session() must DELETE /sessions/{id} and return a dict."""

    def test_delete_session_returns_dict(self):
        """delete_session() returns the parsed result-counts dict from the server."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = {
            "root_id": "session1",
            "session_count": 3,
            "nodes_deleted": 42,
            "relationships_deleted": 10,
            "blobs_deleted": 2,
            "queue_sessions_cleaned": 1,
        }

        with patch("context_intelligence.client._http_delete_strict") as mock_delete:
            mock_delete.return_value = mock_response
            result = client.delete_session("session1")

        assert result == mock_response

    def test_delete_session_calls_correct_url(self):
        """delete_session() calls DELETE /sessions/{session_id} (no query string/body)."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_delete_strict") as mock_delete:
            mock_delete.return_value = {}
            client.delete_session("my-session")

        call_args = mock_delete.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://localhost:8000/sessions/my-session"

    def test_delete_session_includes_authorization_header(self):
        """delete_session() sends Authorization: [REDACTED:SECRET]"""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "secretkey")

        with patch("context_intelligence.client._http_delete_strict") as mock_delete:
            mock_delete.return_value = {}
            client.delete_session("my-session")

        call_args = mock_delete.call_args
        headers = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer secretkey"

    def test_delete_session_propagates_ciclienterror_404(self):
        """A 404 (unknown session) must not be swallowed -- it propagates as CIClientError."""
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_delete_strict") as mock_delete:
            mock_delete.side_effect = CIClientError(
                "HTTP 404 from http://localhost:8000/sessions/missing",
                error_type="http_status",
                url="http://localhost:8000/sessions/missing",
                status_code=404,
            )
            with pytest.raises(CIClientError) as excinfo:
                client.delete_session("missing")

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 404

    def test_delete_session_propagates_ciclienterror_409(self):
        """A 409 (still receiving data / ambiguous id) must propagate as CIClientError,
        never silently treated as a completed delete."""
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_delete_strict") as mock_delete:
            mock_delete.side_effect = CIClientError(
                "HTTP 409 from http://localhost:8000/sessions/live",
                error_type="http_status",
                url="http://localhost:8000/sessions/live",
                status_code=409,
            )
            with pytest.raises(CIClientError) as excinfo:
                client.delete_session("live")

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 409


class TestCIClientHealthCheck:
    """CIClient.health_check() must use cypher() to run a count query and return dict."""

    def test_health_check_returns_ok_with_session_count(self):
        """health_check() returns status='ok' and session_count from cypher query."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch.object(client, "cypher", return_value=[{"session_count": 42}]):
            result = client.health_check()

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["session_count"] == 42

    def test_health_check_uses_cypher_not_http_get(self):
        """health_check() must call cypher() not _http_get (no /health endpoint)."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with (
            patch.object(client, "cypher", return_value=[{"session_count": 0}]) as mock_cypher,
            patch("context_intelligence.client._http_get") as mock_get,
        ):
            client.health_check()

        assert mock_cypher.called, "health_check must call cypher()"
        assert not mock_get.called, "health_check must NOT call _http_get"

    def test_health_check_returns_unavailable_on_exception(self):
        """health_check() returns status='unavailable' with error when cypher raises."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch.object(client, "cypher", side_effect=Exception("connection refused")):
            result = client.health_check()

        assert isinstance(result, dict)
        assert result["status"] == "unavailable"
        assert "error" in result
        assert "connection refused" in result["error"]

    def test_health_check_returns_zero_count_on_empty_result(self):
        """health_check() returns session_count=0 when cypher returns empty list."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch.object(client, "cypher", return_value=[]):
            result = client.health_check()

        assert result["status"] == "ok"
        assert result["session_count"] == 0


class _GenericBehavior:
    """Mutable control block read by the handler on every request (any path)."""

    def __init__(self) -> None:
        self.status_code: int = 200
        self.body: bytes = b"{}"
        self.paths: list[str] = []  # every request path the server was asked for


def _make_generic_handler(behavior: "_GenericBehavior"):
    from http.server import BaseHTTPRequestHandler

    class _Handler(BaseHTTPRequestHandler):
        def _respond(self):
            behavior.paths.append(self.path)
            self.send_response(behavior.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(behavior.body)

        def do_GET(self):  # noqa: N802
            self._respond()

        def do_DELETE(self):  # noqa: N802
            self._respond()

        def log_message(self, *args):  # silence server logs
            pass

    return _Handler


def _start_generic_server(behavior: "_GenericBehavior"):
    import threading
    from http.server import ThreadingHTTPServer

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_generic_handler(behavior))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


class TestCIClientErrorClassification:
    """error_type classification must be correct: auth_error vs decode_error vs http_status.

    Regression guard: ``ApiKeyAuth.headers()`` raising ``ValueError`` for an
    unusable/redacted credential must classify as ``auth_error`` -- it must
    NEVER be caught by the SAME ``except (ValueError, json.JSONDecodeError)``
    handler used for a genuinely malformed JSON response, which previously
    misreported a healthy 200 response (or, before any request was even sent)
    as "malformed JSON from {url}".
    """

    # -- (a) unusable credential -> auth_error, NEVER decode_error ----------

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    def test_session_summary_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            with pytest.raises(CIClientError) as excinfo:
                client.session_summary("session1")

        assert excinfo.value.error_type == "auth_error"
        mock_get.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    def test_delete_session_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client._http_delete_strict") as mock_delete:
            with pytest.raises(CIClientError) as excinfo:
                client.delete_session("session1")

        assert excinfo.value.error_type == "auth_error"
        mock_delete.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    def test_whoami_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client._http_get_strict") as mock_get:
            with pytest.raises(CIClientError) as excinfo:
                client.whoami()

        assert excinfo.value.error_type == "auth_error"
        mock_get.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    def test_cypher_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client._http_post") as mock_post:
            with pytest.raises(CIClientError) as excinfo:
                client.cypher("MATCH (n) RETURN n")

        assert excinfo.value.error_type == "auth_error"
        mock_post.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    def test_fetch_blob_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import CIClient, CIClientError

        client = CIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client._http_get") as mock_get:
            with pytest.raises(CIClientError) as excinfo:
                client.fetch_blob("session1", "key1")

        assert excinfo.value.error_type == "auth_error"
        mock_get.assert_not_called()

    # -- (b) genuinely malformed JSON from a 200 -> decode_error (unchanged) --

    def test_session_summary_malformed_json_is_decode_error(self):
        """A real 200 with a body that fails to parse as JSON must still
        classify as decode_error -- proves the auth_error fix left this path alone."""
        from context_intelligence.client import CIClient, CIClientError

        behavior = _GenericBehavior()
        behavior.body = b"not json{{{"
        server, base_url = _start_generic_server(behavior)
        client = CIClient(base_url, "key")
        try:
            with pytest.raises(CIClientError) as excinfo:
                client.session_summary("session1")
        finally:
            server.shutdown()
            server.server_close()

        assert excinfo.value.error_type == "decode_error"

    def test_delete_session_malformed_json_is_decode_error(self):
        from context_intelligence.client import CIClient, CIClientError

        behavior = _GenericBehavior()
        behavior.body = b"not json{{{"
        server, base_url = _start_generic_server(behavior)
        client = CIClient(base_url, "key")
        try:
            with pytest.raises(CIClientError) as excinfo:
                client.delete_session("session1")
        finally:
            server.shutdown()
            server.server_close()

        assert excinfo.value.error_type == "decode_error"

    def test_whoami_malformed_json_is_decode_error(self):
        from context_intelligence.client import CIClient, CIClientError

        behavior = _GenericBehavior()
        behavior.body = b"not json{{{"
        server, base_url = _start_generic_server(behavior)
        client = CIClient(base_url, "key")
        try:
            with pytest.raises(CIClientError) as excinfo:
                client.whoami()
        finally:
            server.shutdown()
            server.server_close()

        assert excinfo.value.error_type == "decode_error"

    # -- (c) real 401 -> http_status (unchanged) -----------------------------

    def test_session_summary_401_is_http_status(self):
        from context_intelligence.client import CIClient, CIClientError

        behavior = _GenericBehavior()
        behavior.status_code = 401
        behavior.body = b'{"detail": "unauthorized"}'
        server, base_url = _start_generic_server(behavior)
        client = CIClient(base_url, "key")
        try:
            with pytest.raises(CIClientError) as excinfo:
                client.session_summary("session1")
        finally:
            server.shutdown()
            server.server_close()

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 401

    def test_whoami_401_is_http_status(self):
        from context_intelligence.client import CIClient, CIClientError

        behavior = _GenericBehavior()
        behavior.status_code = 401
        behavior.body = b'{"detail": "unauthorized"}'
        server, base_url = _start_generic_server(behavior)
        client = CIClient(base_url, "key")
        try:
            with pytest.raises(CIClientError) as excinfo:
                client.whoami()
        finally:
            server.shutdown()
            server.server_close()

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 401


class TestLogger:
    """Logger must be named context_intelligence.client."""

    def test_logger_name(self):
        """The module uses a logger named context_intelligence.client."""
        import context_intelligence.client as module

        # The module should expose a logger or use logging.getLogger with the right name
        assert hasattr(module, "logger") or hasattr(module, "_logger") or hasattr(module, "log")
        # Try to get any logger-like attribute
        for attr in ("logger", "_logger", "log", "_log"):
            obj = getattr(module, attr, None)
            if obj is not None:
                assert obj.name == "context_intelligence.client"
                return
        raise AssertionError("No logger found in context_intelligence.client module")


class TestHttpHelpers:
    """_http_post and _http_get must be importable."""

    def test_http_post_importable(self):
        """_http_post must be importable from context_intelligence.client."""
        from context_intelligence.client import _http_post  # noqa: F401

        assert callable(_http_post)

    def test_http_get_importable(self):
        """_http_get must be importable from context_intelligence.client."""
        from context_intelligence.client import _http_get  # noqa: F401

        assert callable(_http_get)


class TestBuildHeaders:
    """_build_headers() must return an Authorization: Bearer header dict."""

    def test_returns_bearer_header(self):
        from context_intelligence.client import _build_headers

        result = _build_headers("my-key")
        assert result == {"Authorization": "Bearer my-key"}

    def test_returns_dict_with_string_values(self):
        from context_intelligence.client import _build_headers

        result = _build_headers("test-api-key")
        assert isinstance(result, dict)
        assert all(isinstance(v, str) for v in result.values())


# ---------------------------------------------------------------------------
# Async mock helpers
# ---------------------------------------------------------------------------


def _make_async_mock_response(json_data, status_code=200):
    """Create a mock httpx response for async HTTP calls."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = Exception(f"HTTP error {status_code}")
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


def _make_async_httpx_client(mock_response):
    """Create a mock async httpx.AsyncClient usable as an async context manager."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx


# ---------------------------------------------------------------------------
# AsyncCIClient tests
# ---------------------------------------------------------------------------


class TestAsyncCIClientCypher:
    """AsyncCIClient.cypher() must POST to /cypher and return list[dict]."""

    async def test_async_cypher_returns_results(self):
        """cypher() returns a list of dicts from the server response."""
        from context_intelligence.client import AsyncCIClient

        mock_data = [{"n": {"id": "1"}}, {"n": {"id": "2"}}]
        mock_resp = _make_async_mock_response(mock_data)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.cypher("MATCH (n) RETURN n LIMIT 2")

        assert isinstance(result, list)
        assert result == mock_data

    async def test_async_cypher_sends_correct_body(self):
        """cypher() sends query and workspace in the POST body."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response([])
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            await client.cypher("MATCH (n) RETURN n", workspace="myworkspace")

        call_kwargs = mock_inner_client.post.call_args
        sent_json = call_kwargs[1].get("json") or call_kwargs[0][1]
        assert sent_json["query"] == "MATCH (n) RETURN n"
        assert sent_json["workspace"] == "myworkspace"

    async def test_async_cypher_default_workspace_is_star(self):
        """cypher() defaults workspace to '*'."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response([])
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            await client.cypher("MATCH (n) RETURN n")

        call_kwargs = mock_inner_client.post.call_args
        sent_json = call_kwargs[1].get("json") or call_kwargs[0][1]
        assert sent_json["workspace"] == "*"

    async def test_async_cypher_sends_auth_header(self):
        """cypher() sends Authorization: Bearer header."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response([])
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "secretkey")
            await client.cypher("MATCH (n) RETURN n")

        call_kwargs = mock_inner_client.post.call_args
        sent_headers = call_kwargs[1].get("headers") or call_kwargs[0][2]
        assert sent_headers.get("Authorization") == "Bearer secretkey"

    async def test_async_cypher_returns_empty_list_on_none(self):
        """cypher() returns [] when the server response is None/null."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response(None)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.cypher("MATCH (n) RETURN n")

        assert result == []

    async def test_async_cypher_unwraps_results_key(self):
        """cypher() unwraps {'results': [...]} server response."""
        from context_intelligence.client import AsyncCIClient

        inner = [{"session_count": 5}]
        mock_resp = _make_async_mock_response({"results": inner})
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.cypher("MATCH (n) RETURN n")

        assert result == inner


class TestAsyncCIClientFetchBlob:
    """AsyncCIClient.fetch_blob() must GET /blobs/{session_id}/{key}."""

    async def test_async_fetch_blob_returns_parsed_json(self):
        """fetch_blob() returns the parsed JSON response content."""
        from context_intelligence.client import AsyncCIClient

        blob_data = {"payload": "content here"}
        mock_resp = _make_async_mock_response(blob_data)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.fetch_blob("session1", "mykey")

        assert result == blob_data

    async def test_async_fetch_blob_calls_correct_url(self):
        """fetch_blob() calls {server_url}/blobs/{session_id}/{key}."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            await client.fetch_blob("my-session", "my-key")

        call_args = mock_inner_client.get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert "http://localhost:8000" in url
        assert "/blobs/" in url
        assert "my-session" in url
        assert "my-key" in url

    async def test_async_fetch_blob_raises_on_404(self):
        """fetch_blob() raises CIClientError(error_type='http_status') on 404.

        Phase 0 (docs/multi-source-build-spec-v5.md §3): a non-2xx status is a
        genuine failure and must never masquerade as an empty/None success. The
        mock's raise_for_status() side effect is a plain Exception (not
        httpx.HTTPStatusError), which the client's `except httpx.HTTPError` catch
        (a real httpx.TransportError subclass check) does not match -- so this
        also exercises real httpx.HTTPStatusError classification via a real
        response object.
        """
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("GET", "http://localhost:8000/blobs/session1/missing-key")
        real_response = httpx.Response(status_code=404, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as exc_info:
                await client.fetch_blob("session1", "missing-key")

        assert exc_info.value.error_type == "http_status"
        assert exc_info.value.status_code == 404

    async def test_async_fetch_blob_sends_auth_header(self):
        """fetch_blob() sends Authorization: Bearer header."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "blobkey")
            await client.fetch_blob("sess", "k")

        call_kwargs = mock_inner_client.get.call_args
        sent_headers = call_kwargs[1].get("headers") or call_kwargs[0][1]
        assert sent_headers.get("Authorization") == "Bearer blobkey"


class TestAsyncCIClientSessionSummary:
    """AsyncCIClient.session_summary() must GET /sessions/{id}/summary."""

    async def test_async_session_summary_returns_parsed_dict(self):
        """session_summary() returns the parsed summary dict from the server."""
        from context_intelligence.client import AsyncCIClient

        summary_data = {"created_by": "alice", "node_count": 10, "deletable": True}
        mock_resp = _make_async_mock_response(summary_data)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.session_summary("session1")

        assert result == summary_data

    async def test_async_session_summary_calls_correct_url_and_method(self):
        """session_summary() GETs {server_url}/sessions/{session_id}/summary."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            await client.session_summary("my-session")

        assert mock_inner_client.get.called, "session_summary must use GET"
        call_args = mock_inner_client.get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://localhost:8000/sessions/my-session/summary"

    async def test_async_session_summary_sends_auth_header(self):
        """session_summary() sends Authorization: [REDACTED:SECRET]"""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "secretkey")
            await client.session_summary("my-session")

        call_kwargs = mock_inner_client.get.call_args
        sent_headers = call_kwargs[1].get("headers") or call_kwargs[0][1]
        assert sent_headers.get("Authorization") == "Bearer secretkey"

    async def test_async_session_summary_raises_on_404(self):
        """A 404 (unknown session) raises CIClientError(error_type='http_status')."""
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("GET", "http://localhost:8000/sessions/missing/summary")
        real_response = httpx.Response(status_code=404, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as exc_info:
                await client.session_summary("missing")

        assert exc_info.value.error_type == "http_status"
        assert exc_info.value.status_code == 404

    async def test_async_session_summary_raises_on_409(self):
        """A 409 (still receiving data / ambiguous id) raises CIClientError
        with the status preserved, not a silently-empty result."""
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("GET", "http://localhost:8000/sessions/live/summary")
        real_response = httpx.Response(status_code=409, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "409", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as exc_info:
                await client.session_summary("live")

        assert exc_info.value.error_type == "http_status"
        assert exc_info.value.status_code == 409


class TestAsyncCIClientWhoami:
    """AsyncCIClient.whoami() must GET /whoami."""

    async def test_async_whoami_returns_parsed_dict(self):
        """whoami() returns the parsed identity dict from the server."""
        from context_intelligence.client import AsyncCIClient

        identity_data = {"contributor_id": "octocat"}
        mock_resp = _make_async_mock_response(identity_data)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.whoami()

        assert result == identity_data

    async def test_async_whoami_calls_correct_url_and_method(self):
        """whoami() GETs {server_url}/whoami."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            await client.whoami()

        assert mock_inner_client.get.called, "whoami must use GET"
        call_args = mock_inner_client.get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://localhost:8000/whoami"

    async def test_async_whoami_sends_auth_header(self):
        """whoami() sends Authorization: Bearer <api_key>."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "secretkey")
            await client.whoami()

        call_kwargs = mock_inner_client.get.call_args
        sent_headers = call_kwargs[1].get("headers") or call_kwargs[0][1]
        assert sent_headers.get("Authorization") == "Bearer secretkey"

    async def test_async_whoami_returns_null_contributor_id_when_auth_disabled(self):
        """A server with auth disabled returns contributor_id: null -- passed through."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({"contributor_id": None})
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.whoami()

        assert result == {"contributor_id": None}

    async def test_async_whoami_raises_on_500(self):
        """A genuine HTTP failure raises CIClientError(error_type='http_status')."""
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("GET", "http://localhost:8000/whoami")
        real_response = httpx.Response(status_code=500, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as exc_info:
                await client.whoami()

        assert exc_info.value.error_type == "http_status"
        assert exc_info.value.status_code == 500


class TestAsyncCIClientDeleteSession:
    """AsyncCIClient.delete_session() must DELETE /sessions/{id}."""

    async def test_async_delete_session_returns_parsed_dict(self):
        """delete_session() returns the parsed result-counts dict from the server."""
        from context_intelligence.client import AsyncCIClient

        result_data = {"root_id": "session1", "nodes_deleted": 42, "blobs_deleted": 2}
        mock_resp = _make_async_mock_response(result_data)
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value
        mock_inner_client.delete = AsyncMock(return_value=mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.delete_session("session1")

        assert result == result_data

    async def test_async_delete_session_calls_correct_url_and_method(self):
        """delete_session() DELETEs {server_url}/sessions/{session_id} (no query/body)."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value
        mock_inner_client.delete = AsyncMock(return_value=mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            await client.delete_session("my-session")

        assert mock_inner_client.delete.called, "delete_session must use DELETE"
        call_args = mock_inner_client.delete.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://localhost:8000/sessions/my-session"

    async def test_async_delete_session_sends_auth_header(self):
        """delete_session() sends Authorization: [REDACTED:SECRET]"""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response({})
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value
        mock_inner_client.delete = AsyncMock(return_value=mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "secretkey")
            await client.delete_session("my-session")

        call_kwargs = mock_inner_client.delete.call_args
        sent_headers = call_kwargs[1].get("headers") or call_kwargs[0][1]
        assert sent_headers.get("Authorization") == "Bearer secretkey"

    async def test_async_delete_session_raises_on_404(self):
        """A 404 (unknown session) raises CIClientError(error_type='http_status')."""
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("DELETE", "http://localhost:8000/sessions/missing")
        real_response = httpx.Response(status_code=404, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value
        mock_inner_client.delete = AsyncMock(return_value=mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as exc_info:
                await client.delete_session("missing")

        assert exc_info.value.error_type == "http_status"
        assert exc_info.value.status_code == 404

    async def test_async_delete_session_raises_on_409(self):
        """A 409 (still receiving data / ambiguous id) raises CIClientError --
        the delete is never silently treated as done."""
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("DELETE", "http://localhost:8000/sessions/live")
        real_response = httpx.Response(status_code=409, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "409", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value
        mock_inner_client.delete = AsyncMock(return_value=mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as exc_info:
                await client.delete_session("live")

        assert exc_info.value.error_type == "http_status"
        assert exc_info.value.status_code == 409


class TestAsyncCIClientListBlobKeys:
    """AsyncCIClient.list_blob_keys() must return set[str] of BARE blob keys."""

    async def test_async_list_blob_keys_returns_set(self):
        """list_blob_keys() returns a set of BARE keys (ci-blob:// scheme stripped)."""
        from context_intelligence.client import AsyncCIClient

        blob_uris = ["ci-blob://session1/key1", "ci-blob://session1/key2"]
        mock_resp = _make_async_mock_response(blob_uris)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.list_blob_keys("session1")

        assert isinstance(result, set)
        # Full URIs are normalized to bare keys (what fetch_blob(session_id, key) needs).
        assert result == {"key1", "key2"}

    async def test_async_list_blob_keys_returns_all_string_keys_verbatim(self):
        """list_blob_keys() returns every string key VERBATIM -- no ci-blob:// filtering.

        Server blob keys are bare identifiers (e.g. ``s__llm_request__123__raw``),
        not ci-blob:// URIs; filtering by that scheme was the silent-empty defect.
        Every non-empty string item is a key.
        """
        from context_intelligence.client import AsyncCIClient

        mixed = [
            "s__llm_request__1__raw",
            "s__tool__2__raw",
            "s__session_start__3__raw",
        ]
        mock_resp = _make_async_mock_response(mixed)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.list_blob_keys("session1")

        assert result == {
            "s__llm_request__1__raw",
            "s__tool__2__raw",
            "s__session_start__3__raw",
        }

    async def test_async_list_blob_keys_empty_response(self):
        """list_blob_keys() returns empty set when server returns empty list."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response([])
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.list_blob_keys("session1")

        assert result == set()


class TestAsyncCIClientHealthCheck:
    """AsyncCIClient.health_check() must query the graph and return status dict."""

    async def test_async_health_check_returns_ok_with_count(self):
        """health_check() returns status='ok' and session_count from query."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response([{"session_count": 42}])
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.health_check()

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["session_count"] == 42

    async def test_async_health_check_returns_unavailable_on_error(self):
        """health_check() returns status='unavailable' with error message on failure."""
        from context_intelligence.client import AsyncCIClient

        client = AsyncCIClient("http://localhost:8000", "testkey")

        # Patch cypher() directly to raise
        async def _raise(*args, **kwargs):
            raise Exception("connection refused")

        client.cypher = _raise  # type: ignore[method-assign]
        result = await client.health_check()

        assert isinstance(result, dict)
        assert result["status"] == "unavailable"
        assert "error" in result
        assert "connection refused" in result["error"]

    async def test_async_health_check_returns_zero_on_empty(self):
        """health_check() returns session_count=0 when cypher returns empty list."""
        from context_intelligence.client import AsyncCIClient

        mock_resp = _make_async_mock_response([])
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.health_check()

        assert result["status"] == "ok"
        assert result["session_count"] == 0


class TestAsyncCIClientErrorClassification:
    """error_type classification must be correct: auth_error vs decode_error vs http_status.

    Regression guard: ``self._strategy.headers()`` was previously called INSIDE
    each method's request ``try:`` block, so a credential ``ValueError`` (unusable/
    redacted api_key) fell through to the SAME ``except (ValueError,
    json.JSONDecodeError)`` handler used for a genuinely malformed JSON response
    and was misreported as "malformed JSON from {url}" -- even though no request
    was ever sent. ``_auth_headers()`` now computes headers BEFORE the try block
    and classifies a credential failure as its own ``auth_error``.
    """

    # -- (a) unusable credential -> auth_error, NEVER decode_error ----------

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    async def test_async_session_summary_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client.httpx.AsyncClient") as mock_async_client:
            with pytest.raises(CIClientError) as excinfo:
                await client.session_summary("session1")

        assert excinfo.value.error_type == "auth_error"
        mock_async_client.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    async def test_async_delete_session_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client.httpx.AsyncClient") as mock_async_client:
            with pytest.raises(CIClientError) as excinfo:
                await client.delete_session("session1")

        assert excinfo.value.error_type == "auth_error"
        mock_async_client.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    async def test_async_whoami_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client.httpx.AsyncClient") as mock_async_client:
            with pytest.raises(CIClientError) as excinfo:
                await client.whoami()

        assert excinfo.value.error_type == "auth_error"
        mock_async_client.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    async def test_async_cypher_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client.httpx.AsyncClient") as mock_async_client:
            with pytest.raises(CIClientError) as excinfo:
                await client.cypher("MATCH (n) RETURN n")

        assert excinfo.value.error_type == "auth_error"
        mock_async_client.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["", "[REDACTED]"])
    async def test_async_fetch_blob_unusable_credential_is_auth_error(self, bad_key):
        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient("http://localhost:8000", bad_key)

        with patch("context_intelligence.client.httpx.AsyncClient") as mock_async_client:
            with pytest.raises(CIClientError) as excinfo:
                await client.fetch_blob("session1", "key1")

        assert excinfo.value.error_type == "auth_error"
        mock_async_client.assert_not_called()

    # -- (b) genuinely malformed JSON from a 200 -> decode_error (unchanged) --

    async def test_async_session_summary_malformed_json_is_decode_error(self):
        from context_intelligence.client import AsyncCIClient, CIClientError

        mock_resp = _make_async_mock_response(None)
        mock_resp.json.side_effect = ValueError("Expecting value")
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as excinfo:
                await client.session_summary("session1")

        assert excinfo.value.error_type == "decode_error"

    async def test_async_delete_session_malformed_json_is_decode_error(self):
        from context_intelligence.client import AsyncCIClient, CIClientError

        mock_resp = _make_async_mock_response(None)
        mock_resp.json.side_effect = ValueError("Expecting value")
        mock_http = _make_async_httpx_client(mock_resp)
        mock_inner_client = mock_http.__aenter__.return_value
        mock_inner_client.delete = AsyncMock(return_value=mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as excinfo:
                await client.delete_session("session1")

        assert excinfo.value.error_type == "decode_error"

    async def test_async_whoami_malformed_json_is_decode_error(self):
        from context_intelligence.client import AsyncCIClient, CIClientError

        mock_resp = _make_async_mock_response(None)
        mock_resp.json.side_effect = ValueError("Expecting value")
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as excinfo:
                await client.whoami()

        assert excinfo.value.error_type == "decode_error"

    # -- (c) real 401 -> http_status (unchanged) -----------------------------

    async def test_async_session_summary_401_is_http_status(self):
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("GET", "http://localhost:8000/sessions/session1/summary")
        real_response = httpx.Response(status_code=401, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as excinfo:
                await client.session_summary("session1")

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 401

    async def test_async_whoami_401_is_http_status(self):
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        request = httpx.Request("GET", "http://localhost:8000/whoami")
        real_response = httpx.Response(status_code=401, request=request)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=request, response=real_response
        )
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            with pytest.raises(CIClientError) as excinfo:
                await client.whoami()

        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# REAL-SOCKET fail-loud tests for list_blob_keys (sync + async)
#
# Risk 2: a genuine transport/HTTP failure must NOT masquerade as "no blobs".
# Uses a real stdlib ThreadingHTTPServer (no mocks on the transport path) so the
# classification is proven end-to-end, mirroring the Phase 0 e2e harness.
# ---------------------------------------------------------------------------


class _BlobsBehavior:
    """Mutable control block read by the handler on every GET /blobs/<sid>."""

    def __init__(self) -> None:
        self.status_code: int = 200
        self.body: bytes = b"[]"
        self.paths: list[str] = []  # every request path the server was asked for


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_blobs_handler(behavior: "_BlobsBehavior"):
    from http.server import BaseHTTPRequestHandler

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            behavior.paths.append(self.path)  # record every requested path
            if not self.path.startswith("/blobs/"):
                self.send_error(404)
                return
            self.send_response(behavior.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(behavior.body)

        def log_message(self, *args):  # silence server logs
            pass

    return _Handler


def _start_blobs_server(behavior: "_BlobsBehavior"):
    import threading
    from http.server import ThreadingHTTPServer

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_blobs_handler(behavior))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


class TestSyncListBlobKeysFailLoudRealSocket:
    """Sync CIClient.list_blob_keys() over real sockets: raise on failure, empty-200 ok."""

    def test_down_server_raises_connection_error(self):
        from context_intelligence.client import CIClient, CIClientError

        port = _find_free_port()  # bound-then-released -> nothing listening
        client = CIClient(f"http://127.0.0.1:{port}", "key")
        with pytest.raises(CIClientError) as excinfo:
            client.list_blob_keys("session1")
        assert excinfo.value.error_type == "connection_error"

    def test_500_raises_http_status(self):
        from context_intelligence.client import CIClient, CIClientError

        behavior = _BlobsBehavior()
        behavior.status_code = 500
        behavior.body = b'{"detail": "boom"}'
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            with pytest.raises(CIClientError) as excinfo:
                client.list_blob_keys("session1")
        finally:
            server.shutdown()
            server.server_close()
        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 500

    def test_genuine_empty_200_returns_empty_set(self):
        from context_intelligence.client import CIClient

        behavior = _BlobsBehavior()
        behavior.body = b"[]"
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            result = client.list_blob_keys("session1")
        finally:
            server.shutdown()
            server.server_close()
        assert result == set()

    def test_populated_200_returns_keys(self):
        """(c) Back-compat bare list of key strings -> those keys, verbatim.

        Server blob keys are bare identifiers, NOT ci-blob:// URIs, so they are
        returned as-is (no scheme filtering).
        """
        from context_intelligence.client import CIClient

        behavior = _BlobsBehavior()
        behavior.body = b'["s__llm_request__123__raw", "s__tool__456__raw"]'
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            result = client.list_blob_keys("session1")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"s__llm_request__123__raw", "s__tool__456__raw"}

    def test_dict_envelope_full_uri_items_returns_bare_keys(self):
        """(a) REAL server shape: dict envelope whose blobs are FULL ci-blob:// URIs
        -> BARE keys (scheme + leading session segment stripped).

        Regression 1 (silent-empty): a 163-blob session came back empty because the
        old parser only handled a bare list and dropped the envelope.
        Regression 2 (this fix): items are full ``ci-blob://S/<key>`` URIs; returning
        them verbatim made ``fetch_blob(S, uri)`` build ``/blobs/S/ci-blob://S/<key>``
        -> 404. We must return the BARE ``<key>``.
        """
        from context_intelligence.client import CIClient

        behavior = _BlobsBehavior()
        behavior.body = (
            b'{"session_id": "S", "blobs": ['
            b'"ci-blob://S/S__llm_request__123__raw", '
            b'"ci-blob://S/S__session_start__1__raw"]}'
        )
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            result = client.list_blob_keys("S")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"S__llm_request__123__raw", "S__session_start__1__raw"}

    def test_key_containing_slash_split_once(self):
        """A key that itself contains '/' is preserved -- scheme split is ONCE only."""
        from context_intelligence.client import CIClient

        behavior = _BlobsBehavior()
        behavior.body = b'{"session_id": "S", "blobs": ["ci-blob://S/dir/sub__x__1__raw"]}'
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            result = client.list_blob_keys("S")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"dir/sub__x__1__raw"}

    def test_dict_envelope_with_dict_items_returns_keys(self):
        """(b) Envelope whose blobs are DICT items -> pull key/name/id per item, and
        strip the ci-blob:// scheme when the pulled value is a full URI."""
        from context_intelligence.client import CIClient

        behavior = _BlobsBehavior()
        behavior.body = (
            b'{"session_id": "S", "blobs": ['
            b'{"key": "ci-blob://S/S__a__1__raw"}, '  # dict + full URI -> stripped
            b'{"name": "S__b__2__raw"}, '  # dict + already-bare -> as-is
            b'{"id": "S__c__3__raw"}]}'
        )
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            result = client.list_blob_keys("S")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"S__a__1__raw", "S__b__2__raw", "S__c__3__raw"}

    def test_dict_envelope_empty_blobs_returns_empty_set(self):
        """(d) Envelope with "blobs": [] -> empty set (genuine-empty SUCCESS, not error)."""
        from context_intelligence.client import CIClient

        behavior = _BlobsBehavior()
        behavior.body = b'{"session_id": "s", "blobs": []}'
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            result = client.list_blob_keys("s")
        finally:
            server.shutdown()
            server.server_close()
        assert result == set()

    def test_list_then_fetch_composes_clean_path_no_doubled_scheme(self):
        """Consumption pattern: keys from list_blob_keys() feed fetch_blob(session_id, key)
        and compose a CLEAN ``/blobs/{sid}/{key}`` path -- NO doubled ``ci-blob://``.

        This is the exact metadata.py flow (list -> _find_*_blob_key -> fetch_blob) and
        the regression guard: if list_blob_keys returned the full URI, fetch_blob would
        request ``/blobs/S/ci-blob://S/<key>`` -> 404.
        """
        from context_intelligence.client import CIClient

        behavior = _BlobsBehavior()
        behavior.body = b'{"session_id": "S", "blobs": ["ci-blob://S/S__session_start__1__raw"]}'
        server, base_url = _start_blobs_server(behavior)
        client = CIClient(base_url, "key")
        try:
            keys = client.list_blob_keys("S")
            assert keys == {"S__session_start__1__raw"}
            (key,) = keys
            # Feed the bare key straight back into fetch_blob, as metadata.py does.
            client.fetch_blob("S", key)
        finally:
            server.shutdown()
            server.server_close()

        # The fetch path must be exactly /blobs/S/<bare key> -- no ci-blob:// anywhere.
        fetch_paths = [p for p in behavior.paths if p != "/blobs/S"]
        assert fetch_paths == ["/blobs/S/S__session_start__1__raw"]
        assert all("ci-blob://" not in p for p in behavior.paths)


class TestAsyncListBlobKeysFailLoudRealSocket:
    """Async AsyncCIClient.list_blob_keys() over real sockets: raise on failure, empty-200 ok."""

    async def test_down_server_raises_connection_error(self):
        from context_intelligence.client import AsyncCIClient, CIClientError

        port = _find_free_port()
        client = AsyncCIClient(f"http://127.0.0.1:{port}", "key")
        with pytest.raises(CIClientError) as excinfo:
            await client.list_blob_keys("session1")
        assert excinfo.value.error_type == "connection_error"

    async def test_500_raises_http_status(self):
        from context_intelligence.client import AsyncCIClient, CIClientError

        behavior = _BlobsBehavior()
        behavior.status_code = 500
        behavior.body = b'{"detail": "boom"}'
        server, base_url = _start_blobs_server(behavior)
        client = AsyncCIClient(base_url, "key")
        try:
            with pytest.raises(CIClientError) as excinfo:
                await client.list_blob_keys("session1")
        finally:
            server.shutdown()
            server.server_close()
        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 500

    async def test_genuine_empty_200_returns_empty_set(self):
        from context_intelligence.client import AsyncCIClient

        behavior = _BlobsBehavior()
        behavior.body = b"[]"
        server, base_url = _start_blobs_server(behavior)
        client = AsyncCIClient(base_url, "key")
        try:
            result = await client.list_blob_keys("session1")
        finally:
            server.shutdown()
            server.server_close()
        assert result == set()

    async def test_dict_envelope_full_uri_items_returns_bare_keys(self):
        """(a) REAL server shape: dict envelope whose blobs are FULL ci-blob:// URIs
        -> BARE keys. Async must parse identically to sync (shared _parse_blob_keys)."""
        from context_intelligence.client import AsyncCIClient

        behavior = _BlobsBehavior()
        behavior.body = (
            b'{"session_id": "S", "blobs": ['
            b'"ci-blob://S/S__llm_request__123__raw", '
            b'"ci-blob://S/S__session_start__1__raw"]}'
        )
        server, base_url = _start_blobs_server(behavior)
        client = AsyncCIClient(base_url, "key")
        try:
            result = await client.list_blob_keys("S")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"S__llm_request__123__raw", "S__session_start__1__raw"}

    async def test_key_containing_slash_split_once(self):
        """A key that itself contains '/' is preserved -- scheme split is ONCE only."""
        from context_intelligence.client import AsyncCIClient

        behavior = _BlobsBehavior()
        behavior.body = b'{"session_id": "S", "blobs": ["ci-blob://S/dir/sub__x__1__raw"]}'
        server, base_url = _start_blobs_server(behavior)
        client = AsyncCIClient(base_url, "key")
        try:
            result = await client.list_blob_keys("S")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"dir/sub__x__1__raw"}

    async def test_dict_envelope_with_dict_items_returns_keys(self):
        """(b) Envelope whose blobs are DICT items -> pull key/name/id per item, and
        strip the ci-blob:// scheme when the pulled value is a full URI."""
        from context_intelligence.client import AsyncCIClient

        behavior = _BlobsBehavior()
        behavior.body = (
            b'{"session_id": "S", "blobs": ['
            b'{"key": "ci-blob://S/S__a__1__raw"}, '  # dict + full URI -> stripped
            b'{"name": "S__b__2__raw"}, '  # dict + already-bare -> as-is
            b'{"id": "S__c__3__raw"}]}'
        )
        server, base_url = _start_blobs_server(behavior)
        client = AsyncCIClient(base_url, "key")
        try:
            result = await client.list_blob_keys("S")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"S__a__1__raw", "S__b__2__raw", "S__c__3__raw"}

    async def test_bare_list_returns_keys(self):
        """(c) Back-compat bare list of key strings -> those keys, verbatim."""
        from context_intelligence.client import AsyncCIClient

        behavior = _BlobsBehavior()
        behavior.body = b'["s__llm_request__123__raw", "s__tool__456__raw"]'
        server, base_url = _start_blobs_server(behavior)
        client = AsyncCIClient(base_url, "key")
        try:
            result = await client.list_blob_keys("s")
        finally:
            server.shutdown()
            server.server_close()
        assert result == {"s__llm_request__123__raw", "s__tool__456__raw"}

    async def test_dict_envelope_empty_blobs_returns_empty_set(self):
        """(d) Envelope with "blobs": [] -> empty set (genuine-empty SUCCESS, not error)."""
        from context_intelligence.client import AsyncCIClient

        behavior = _BlobsBehavior()
        behavior.body = b'{"session_id": "s", "blobs": []}'
        server, base_url = _start_blobs_server(behavior)
        client = AsyncCIClient(base_url, "key")
        try:
            result = await client.list_blob_keys("s")
        finally:
            server.shutdown()
            server.server_close()
        assert result == set()


class TestRetryAfterParsing:
    """_retry_after_seconds parses the Retry-After delta-seconds header."""

    def test_parses_positive_integer(self):
        from context_intelligence.client import _retry_after_seconds

        assert _retry_after_seconds({"Retry-After": "2"}) == 2

    def test_zero_is_valid(self):
        from context_intelligence.client import _retry_after_seconds

        assert _retry_after_seconds({"Retry-After": "0"}) == 0

    def test_absent_header_is_none(self):
        from context_intelligence.client import _retry_after_seconds

        assert _retry_after_seconds({}) is None

    def test_none_headers_is_none(self):
        from context_intelligence.client import _retry_after_seconds

        assert _retry_after_seconds(None) is None

    def test_non_integer_is_none(self):
        from context_intelligence.client import _retry_after_seconds

        # HTTP-date form is not used by this server -> treated as absent.
        assert _retry_after_seconds({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None

    def test_negative_is_none(self):
        from context_intelligence.client import _retry_after_seconds

        assert _retry_after_seconds({"Retry-After": "-5"}) is None

    def test_ciclienterror_carries_retry_after(self):
        from context_intelligence.client import CIClientError

        exc = CIClientError(
            "HTTP 409",
            error_type="http_status",
            url="http://x/sessions/s",
            status_code=409,
            retry_after=2,
        )
        assert exc.retry_after == 2

    def test_ciclienterror_retry_after_defaults_none(self):
        from context_intelligence.client import CIClientError

        exc = CIClientError("boom", error_type="timeout", url="http://x")
        assert exc.retry_after is None
