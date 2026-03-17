"""Tests for BlobReadTool implementation."""

from __future__ import annotations

import pathlib
import shutil
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from amplifier_core import ToolResult

# ---------------------------------------------------------------------------
# Module-level helper functions (NO conftest.py, NO pytest fixtures)
# ---------------------------------------------------------------------------


def _make_coordinator(resolver: Any) -> MagicMock:
    """Return a MagicMock coordinator whose get_capability returns *resolver*."""
    coordinator = MagicMock()
    coordinator.get_capability.return_value = resolver
    return coordinator


def _make_resolver(server_url: str | None) -> MagicMock:
    """Return a MagicMock resolver with context_intelligence_server_url set."""
    resolver = MagicMock()
    resolver.context_intelligence_server_url = server_url
    return resolver


def _make_mock_client(text_return: str, status_code: int) -> tuple[AsyncMock, MagicMock]:
    """Return (mock_client, mock_cls) ready for patching httpx.AsyncClient.

    mock_client is the object yielded by ``async with httpx.AsyncClient() as c``.
    mock_cls replaces ``httpx.AsyncClient`` itself.

    For status_code >= 400, ``mock_response.raise_for_status()`` raises
    ``httpx.HTTPStatusError`` so the implementation can detect HTTP failures.
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text_return

    if status_code >= 400:
        mock_request = MagicMock()
        http_error = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=mock_request,
            response=mock_response,
        )
        mock_response.raise_for_status.side_effect = http_error
    else:
        mock_response.raise_for_status.return_value = None

    # The object returned inside `async with httpx.AsyncClient() as client:`
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    # The context manager object returned by httpx.AsyncClient()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    # The class-level mock that replaces httpx.AsyncClient
    mock_cls = MagicMock(return_value=mock_ctx)

    return mock_client, mock_cls


def _make_error_client(side_effect: BaseException) -> MagicMock:
    """Return mock_cls where client.get raises *side_effect*.

    Mirrors the shape of _make_mock_client but for error-path testing where
    the GET call itself raises rather than returning a response.
    """
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=side_effect)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_ctx)


# ---------------------------------------------------------------------------
# Module-level fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_blob_dir():  # type: ignore[no-untyped-def]
    """Remove /tmp/ci-blobs/ before each test to prevent cross-test pollution."""
    blob_dir = pathlib.Path("/tmp/ci-blobs")
    if blob_dir.exists():
        shutil.rmtree(blob_dir)
    yield
    # Leave files after test for debugging; next test's setup cleans up


# ---------------------------------------------------------------------------
# (1) Protocol surface
# ---------------------------------------------------------------------------


class TestBlobReadToolProtocol:
    """Tool protocol surface: name, description, schema, execute return type."""

    def test_name_is_blob_read(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert tool.name == "blob_read"

    def test_description_mentions_ci_blob_scheme(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert "ci-blob://" in tool.description

    def test_description_mentions_file_path(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert "file path" in tool.description.lower()
        assert "disk path" not in tool.description.lower()

    def test_schema_type_is_object(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert tool.get_schema()["type"] == "object"

    def test_schema_has_uri_required(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert "uri" in tool.get_schema()["required"]

    def test_schema_uri_is_string(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert tool.get_schema()["properties"]["uri"]["type"] == "string"

    async def test_execute_returns_tool_result(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        result = await tool.execute({"uri": "ci-blob://session/key"})
        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# (2) Lazy capability resolution
# ---------------------------------------------------------------------------


class TestLazyCapabilityResolution:
    """execute() must resolve the config capability lazily and cache it."""

    async def test_capability_not_found_returns_configuration_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        # get_capability returns None → capability not registered
        tool = BlobReadTool(_make_coordinator(None))
        result = await tool.execute({"uri": "ci-blob://session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"
        assert "not configured" in result.error["message"].lower()

    async def test_server_url_none_returns_configuration_error_with_url(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver(None)
        tool = BlobReadTool(_make_coordinator(resolver))
        result = await tool.execute({"uri": "ci-blob://session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"
        assert "url" in result.error["message"].lower()

    async def test_server_url_empty_returns_configuration_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("")
        tool = BlobReadTool(_make_coordinator(resolver))
        result = await tool.execute({"uri": "ci-blob://session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"

    async def test_resolver_cached_after_first_lookup(self) -> None:
        """get_capability should be called exactly once across two execute() calls."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        coordinator = _make_coordinator(resolver)
        tool = BlobReadTool(coordinator)

        _, mock_cls = _make_mock_client('{"data": "first"}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            await tool.execute({"uri": "ci-blob://session/key1"})
            await tool.execute({"uri": "ci-blob://session/key2"})

        coordinator.get_capability.assert_called_once()


# ---------------------------------------------------------------------------
# (3) URI parsing
# ---------------------------------------------------------------------------


class TestURIParsing:
    """execute() must validate and parse ci-blob:// URIs before fetching."""

    async def test_missing_scheme_returns_uri_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))
        # No "ci-blob://" prefix at all
        result = await tool.execute({"uri": "session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "uri_error"
        assert "ci-blob://" in result.error["message"]

    async def test_no_slash_after_scheme_returns_uri_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))
        # Scheme present but no "/" separating session_id from key
        result = await tool.execute({"uri": "ci-blob://sessiononly"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "uri_error"
        assert "session_id/key" in result.error["message"]

    async def test_valid_uri_extracts_session_and_key(self) -> None:
        """A well-formed URI must produce a GET to the correct URL."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        mock_client, mock_cls = _make_mock_client('{"ok": true}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            await tool.execute({"uri": "ci-blob://my-session/my-key"})

        mock_client.get.assert_called_once_with("http://localhost:8080/blobs/my-session/my-key")


# ---------------------------------------------------------------------------
# (4) Path sanitization
# ---------------------------------------------------------------------------


class TestPathSanitization:
    """Output file paths must be confined to /tmp/ci-blobs/ regardless of key."""

    async def test_slashes_sanitized(self) -> None:
        """Slashes in the key must not create unexpected subdirectory depth."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        _, mock_cls = _make_mock_client('{"data": "test"}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/path/with/slashes"})

        assert result.success is True
        assert result.output is not None
        output = str(result.output["path"])
        assert output.startswith("/tmp/ci-blobs/")
        assert ".." not in output

    async def test_special_chars_sanitized(self) -> None:
        """Path traversal sequences in the key must be neutralized in the output path."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        _, mock_cls = _make_mock_client('{"data": "test"}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/../../etc/passwd"})

        assert result.success is True
        assert result.output is not None
        output = str(result.output["path"])
        assert output.startswith("/tmp/ci-blobs/")

    async def test_path_traversal_neutralized(self) -> None:
        """Output path must always stay under /tmp/ci-blobs/ even with traversal key."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        _, mock_cls = _make_mock_client('{"data": "test"}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/../../etc/passwd"})

        assert result.success is True
        assert result.output is not None
        output = str(result.output["path"])
        # Resolved path must be strictly under /tmp/ci-blobs/
        assert output.startswith("/tmp/ci-blobs/")
        # Must not have escaped to /etc/
        assert "/etc/" not in output


# ---------------------------------------------------------------------------
# (5) Successful blob read
# ---------------------------------------------------------------------------


class TestBlobReadSuccess:
    """Happy-path behaviour: file written to disk and path returned."""

    async def test_successful_get_writes_file_and_returns_path(self) -> None:
        """Response text must be written verbatim; output is the file path."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        content = '{"session": "test", "data": [1, 2, 3]}'
        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        mock_client, mock_cls = _make_mock_client(content, 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        assert result.output is not None
        written = pathlib.Path(result.output["path"])
        assert written.exists()
        assert written.read_text() == content

    async def test_output_path_structure(self) -> None:
        """Parent directory name == session_id, filename == key.json."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        _, mock_cls = _make_mock_client('{"ok": true}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        assert result.output is not None
        p = pathlib.Path(result.output["path"])
        assert p.parent.name == "my-session"
        assert p.name == "my-key.json"

    async def test_trailing_slash_stripped_from_server_url(self) -> None:
        """A trailing slash on the server URL must not produce a double-slash URL."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080/")  # trailing slash
        tool = BlobReadTool(_make_coordinator(resolver))

        mock_client, mock_cls = _make_mock_client('{"ok": true}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            await tool.execute({"uri": "ci-blob://my-session/my-key"})

        mock_client.get.assert_called_once_with("http://localhost:8080/blobs/my-session/my-key")

    async def test_httpx_client_uses_timeout(self) -> None:
        """Client must set an explicit timeout to prevent indefinite hangs."""
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))
        _, mock_cls = _make_mock_client('{"ok": true}', 200)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            await tool.execute({"uri": "ci-blob://session/key"})
        mock_cls.assert_called_once_with(timeout=30.0)


# ---------------------------------------------------------------------------
# (6) Error handling
# ---------------------------------------------------------------------------


class TestBlobReadErrors:
    """execute() must map HTTP and transport failures to typed ToolResult errors."""

    async def test_http_404_returns_http_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        _, mock_cls = _make_mock_client("Not Found", 404)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_error"
        assert "404" in result.error["message"]

    async def test_http_500_returns_http_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        _, mock_cls = _make_mock_client("Internal Server Error", 500)
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_error"
        assert "500" in result.error["message"]

    async def test_transport_error_returns_connection_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        mock_cls = _make_error_client(httpx.TransportError("Connection refused"))
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "connection_error"

    async def test_unexpected_runtime_error_returns_blob_error(self) -> None:
        from amplifier_module_tool_blob_read.blob_read_tool import BlobReadTool

        resolver = _make_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(resolver))

        mock_cls = _make_error_client(RuntimeError("Unexpected boom"))
        with patch(
            "amplifier_module_tool_blob_read.blob_read_tool.httpx.AsyncClient",
            mock_cls,
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "blob_error"
