"""Tests for HTTP-based BlobTool class.

8 tests covering:
- TestBlobListHTTP: calls correct URL, parses field and node, empty session, key without
  separator, raises on HTTP error
- TestBlobDumpHTTP: calls correct URL and writes file, default dest path, raises on HTTP error
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from amplifier_module_hook_context_intelligence.blob_tool import BlobTool


# ---------------------------------------------------------------------------
# TestBlobListHTTP
# ---------------------------------------------------------------------------


class TestBlobListHTTP:
    """blob_list() makes HTTP GET /blobs/{session_id} and parses blob metadata."""

    async def test_calls_correct_url(self) -> None:
        """blob_list() calls GET /blobs/{session_id} on the server URL (trailing slash stripped)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"blobs": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = BlobTool("http://localhost:8080/")
            await tool.blob_list("sess-1")

            mock_client.get.assert_called_once_with("http://localhost:8080/blobs/sess-1")

    async def test_parses_field_and_node(self) -> None:
        """blob_list() extracts field and node_id from compound keys via rfind on '__'."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"blobs": ["ci-blob://sess-1/node-abc__messages"]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = BlobTool("http://localhost:8080")
            result = await tool.blob_list("sess-1")

            assert len(result) == 1
            assert result[0]["uri"] == "ci-blob://sess-1/node-abc__messages"
            assert result[0]["field"] == "messages"
            assert result[0]["node_id"] == "node-abc"
            assert result[0]["size_bytes"] is None

    async def test_empty_session(self) -> None:
        """blob_list() returns [] when server returns empty blobs list."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"blobs": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = BlobTool("http://localhost:8080")
            result = await tool.blob_list("empty-session")

            assert result == []

    async def test_key_without_separator(self) -> None:
        """blob_list() sets field='unknown' and node_id=key when key has no '__' separator."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"blobs": ["ci-blob://sess-1/simple-key"]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = BlobTool("http://localhost:8080")
            result = await tool.blob_list("sess-1")

            assert len(result) == 1
            assert result[0]["field"] == "unknown"
            assert result[0]["node_id"] == "simple-key"
            assert result[0]["size_bytes"] is None

    async def test_raises_on_http_error(self) -> None:
        """blob_list() propagates httpx.HTTPStatusError on 4xx/5xx — not KeyError from error body."""
        mock_response = MagicMock()
        error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=MagicMock())
        mock_response.raise_for_status.side_effect = error

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = BlobTool("http://localhost:8080")
            with pytest.raises(httpx.HTTPStatusError):
                await tool.blob_list("sess-1")


# ---------------------------------------------------------------------------
# TestBlobDumpHTTP
# ---------------------------------------------------------------------------


class TestBlobDumpHTTP:
    """blob_dump() makes HTTP GET /blobs/{session_id}/{key} and writes content to disk."""

    async def test_calls_correct_url_and_writes_file(self, tmp_path: Path) -> None:
        """blob_dump() calls GET /blobs/{session_id}/{key} and writes resp.text to dest_path."""
        mock_response = MagicMock()
        mock_response.text = '{"data": "value"}'

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            dest = str(tmp_path / "output" / "blob.json")
            tool = BlobTool("http://localhost:8080")
            result = await tool.blob_dump("ci-blob://sess-1/my-key__field", dest)

            mock_client.get.assert_called_once_with(
                "http://localhost:8080/blobs/sess-1/my-key__field"
            )
            assert result == dest
            assert Path(dest).read_text() == '{"data": "value"}'

    async def test_default_dest_path(self) -> None:
        """blob_dump() defaults to tempfile.gettempdir()/ci-blobs/{key}.json."""
        mock_response = MagicMock()
        mock_response.text = '{"x": 1}'

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = BlobTool("http://localhost:8080")
            result = await tool.blob_dump("ci-blob://sess-1/my-key")

            expected = str(Path(tempfile.gettempdir()) / "ci-blobs" / "my-key.json")
            assert result == expected
            assert Path(result).read_text() == '{"x": 1}'

    async def test_raises_on_http_error(self, tmp_path: Path) -> None:
        """blob_dump() propagates httpx.HTTPStatusError — does NOT write error body to disk."""
        mock_response = MagicMock()
        mock_response.text = '{"error": "not found"}'  # error body that must not be written
        error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=MagicMock())
        mock_response.raise_for_status.side_effect = error

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            dest = str(tmp_path / "should_not_exist.json")
            tool = BlobTool("http://localhost:8080")
            with pytest.raises(httpx.HTTPStatusError):
                await tool.blob_dump("ci-blob://sess-1/my-key", dest)

            assert not Path(dest).exists()
