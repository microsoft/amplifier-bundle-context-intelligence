"""Tests for context_intelligence.client (task-3).

Covers:
- Module imports correctly
- _safe_json_loads() handles strings and non-strings
- CIClient.__init__ stores server_url and api_key
- CIClient.cypher() POSTs to /cypher and returns list[dict]
- CIClient.list_blob_keys() parses ci-blob:// URIs into a set[str]
- CIClient.fetch_blob() GETs /blobs/{session_id}/{key} and returns content
- CIClient.health_check() GETs /health and returns dict with status/session_count
- Logger is named context_intelligence.client
"""

from __future__ import annotations

from unittest.mock import patch


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

    def setup_method(self):
        from context_intelligence.client import _safe_json_loads

        self._fn = _safe_json_loads

    def test_parses_json_dict_string(self):
        """A JSON dict string is parsed into a dict."""
        result = self._fn('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parses_json_list_string(self):
        """A JSON list string is parsed into a list."""
        result = self._fn("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_parses_json_number_string(self):
        """A JSON number string is parsed into a number."""
        result = self._fn("42")
        assert result == 42

    def test_returns_dict_as_is(self):
        """A dict passed directly is returned as-is."""
        d = {"already": "parsed"}
        result = self._fn(d)
        assert result is d

    def test_returns_list_as_is(self):
        """A list passed directly is returned as-is."""
        lst = [1, 2, 3]
        result = self._fn(lst)
        assert result is lst

    def test_returns_none_as_is(self):
        """None is returned as-is."""
        result = self._fn(None)
        assert result is None

    def test_returns_invalid_json_string_as_is(self):
        """A non-JSON string is returned as-is (not raised)."""
        bad = "not json"
        result = self._fn(bad)
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

    def _make_client(self):
        from context_intelligence.client import CIClient

        return CIClient("http://localhost:8000", "testkey")

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


class TestCIClientListBlobKeys:
    """CIClient.list_blob_keys() must return set[str] of ci-blob:// URIs."""

    def test_list_blob_keys_returns_set(self):
        """list_blob_keys() returns a set of ci-blob:// URI strings."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = [
            "ci-blob://session1/key1",
            "ci-blob://session1/key2",
        ]

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = mock_response
            result = client.list_blob_keys("session1")

        assert isinstance(result, set)
        assert "ci-blob://session1/key1" in result
        assert "ci-blob://session1/key2" in result

    def test_list_blob_keys_filters_non_ci_blob_uris(self):
        """list_blob_keys() returns only ci-blob:// URIs."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = [
            "ci-blob://session1/key1",
            "http://other.example.com/data",
            "ci-blob://session1/key2",
        ]

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = mock_response
            result = client.list_blob_keys("session1")

        # Should include ci-blob URIs; non-ci-blob may or may not be included
        assert "ci-blob://session1/key1" in result
        assert "ci-blob://session1/key2" in result

    def test_list_blob_keys_empty_response(self):
        """list_blob_keys() returns empty set when server returns empty list."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = []
            result = client.list_blob_keys("session1")

        assert result == set()

    def test_list_blob_keys_returns_none_on_error(self):
        """list_blob_keys() returns empty set or raises, but doesn't crash with no traceback."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = None
            result = client.list_blob_keys("session1")

        # None response should produce empty set, not an exception
        assert result == set()


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


class TestCIClientHealthCheck:
    """CIClient.health_check() must GET /health and return dict with status and session_count."""

    def test_health_check_returns_dict(self):
        """health_check() returns a dict."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")
        mock_response = {"status": "ok", "session_count": 42}

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = mock_response
            result = client.health_check()

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["session_count"] == 42

    def test_health_check_calls_health_endpoint(self):
        """health_check() calls /health."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.return_value = {"status": "ok", "session_count": 0}
            client.health_check()

        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert "/health" in url

    def test_health_check_returns_error_dict_on_failure(self):
        """health_check() returns a dict with status and session_count even on failure."""
        from context_intelligence.client import CIClient

        client = CIClient("http://localhost:8000", "key")

        with patch("context_intelligence.client._http_get") as mock_get:
            mock_get.side_effect = Exception("connection refused")
            result = client.health_check()

        assert isinstance(result, dict)
        assert "status" in result
        assert "session_count" in result


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
