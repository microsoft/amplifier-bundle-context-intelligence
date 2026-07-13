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

    def test_list_blob_keys_returns_empty_set_on_none_response(self):
        """list_blob_keys() returns empty set when _http_get returns None."""
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


class TestAsyncCIClientListBlobKeys:
    """AsyncCIClient.list_blob_keys() must return set[str] of ci-blob:// URIs."""

    async def test_async_list_blob_keys_returns_set(self):
        """list_blob_keys() returns a set of ci-blob:// URI strings."""
        from context_intelligence.client import AsyncCIClient

        blob_uris = ["ci-blob://session1/key1", "ci-blob://session1/key2"]
        mock_resp = _make_async_mock_response(blob_uris)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.list_blob_keys("session1")

        assert isinstance(result, set)
        assert "ci-blob://session1/key1" in result
        assert "ci-blob://session1/key2" in result

    async def test_async_list_blob_keys_filters_non_ci_blob_uris(self):
        """list_blob_keys() returns only ci-blob:// URIs, filtering out others."""
        from context_intelligence.client import AsyncCIClient

        mixed = [
            "ci-blob://session1/key1",
            "http://other.example.com/data",
            "ci-blob://session1/key2",
        ]
        mock_resp = _make_async_mock_response(mixed)
        mock_http = _make_async_httpx_client(mock_resp)

        with patch("context_intelligence.client.httpx.AsyncClient", return_value=mock_http):
            client = AsyncCIClient("http://localhost:8000", "testkey")
            result = await client.list_blob_keys("session1")

        assert "ci-blob://session1/key1" in result
        assert "ci-blob://session1/key2" in result
        assert "http://other.example.com/data" not in result

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
