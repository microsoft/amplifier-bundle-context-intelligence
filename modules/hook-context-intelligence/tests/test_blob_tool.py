"""Tests for BlobTool class.

6 tests covering:
- blob_list: empty session, returns metadata, extracts field/node from compound key
- blob_dump: returns file path, respects custom path, raises for missing blobs
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_module_hook_context_intelligence.blob_store import DiskBlobStore
from amplifier_module_hook_context_intelligence.blob_tool import BlobTool


# ---------------------------------------------------------------------------
# TestBlobList
# ---------------------------------------------------------------------------


class TestBlobList:
    """blob_list() returns metadata for all blobs in a session."""

    async def test_list_empty_session(self, tmp_path: Path) -> None:
        """blob_list() returns [] for a session with no blobs."""
        store = DiskBlobStore(root=tmp_path)
        tool = BlobTool(store)
        result = await tool.blob_list("session-empty")
        assert result == []

    async def test_list_returns_blob_metadata(self, tmp_path: Path) -> None:
        """blob_list() returns list of dicts with uri, field, node_id, size_bytes."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "node-abc__messages", {"content": "hello"})
        await store.write("session-1", "node-def__tool_output", [{"result": "ok"}])
        tool = BlobTool(store)
        result = await tool.blob_list("session-1")
        assert len(result) == 2

        # Check that each item has the required keys and correct types
        for item in result:
            assert "uri" in item
            assert "field" in item
            assert "node_id" in item
            assert "size_bytes" in item
            assert isinstance(item["size_bytes"], int)
            assert item["size_bytes"] > 0

        # Check correct field/node_id extraction
        by_uri = {item["uri"]: item for item in result}
        assert by_uri["ci-blob://session-1/node-abc__messages"]["field"] == "messages"
        assert by_uri["ci-blob://session-1/node-abc__messages"]["node_id"] == "node-abc"
        assert by_uri["ci-blob://session-1/node-def__tool_output"]["field"] == "tool_output"
        assert by_uri["ci-blob://session-1/node-def__tool_output"]["node_id"] == "node-def"

    async def test_list_extracts_field_and_node_from_key(self, tmp_path: Path) -> None:
        """blob_list() splits compound key on LAST __ to get field and node_id.

        Ensures that node_id values containing __ separators are handled correctly —
        only the very last __ splits the key.
        """
        store = DiskBlobStore(root=tmp_path)
        # Key with multiple __ separators: node_id contains __, field is the last part
        await store.write("session-1", "session__tool_pre__1234__messages", {"data": "x"})
        tool = BlobTool(store)
        result = await tool.blob_list("session-1")
        assert len(result) == 1
        item = result[0]
        assert item["field"] == "messages"
        assert item["node_id"] == "session__tool_pre__1234"
        assert item["uri"] == "ci-blob://session-1/session__tool_pre__1234__messages"


# ---------------------------------------------------------------------------
# TestBlobDump
# ---------------------------------------------------------------------------


class TestBlobDump:
    """blob_dump() materializes blobs to disk; raises for missing blobs."""

    async def test_dump_returns_file_path(self, tmp_path: Path) -> None:
        """blob_dump() returns path where file was written and file contains correct content."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "my-key__field", {"data": "value"})
        uri = "ci-blob://session-1/my-key__field"
        dest = str(tmp_path / "output" / "blob.json")
        tool = BlobTool(store)
        result = await tool.blob_dump(uri, dest)
        assert result == dest
        assert Path(result).is_file()
        content = json.loads(Path(result).read_text())
        assert content == {"data": "value"}

    async def test_dump_to_custom_path(self, tmp_path: Path) -> None:
        """blob_dump() respects the dest_path parameter."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "my-key", {"x": 42})
        uri = "ci-blob://session-1/my-key"
        custom_dest = str(tmp_path / "custom" / "output.json")
        tool = BlobTool(store)
        result = await tool.blob_dump(uri, custom_dest)
        assert result == custom_dest
        assert Path(result).is_file()

    async def test_dump_missing_blob_returns_error(self, tmp_path: Path) -> None:
        """blob_dump() raises FileNotFoundError for a blob that does not exist."""
        store = DiskBlobStore(root=tmp_path)
        uri = "ci-blob://session-1/nonexistent"
        tool = BlobTool(store)
        with pytest.raises(FileNotFoundError):
            await tool.blob_dump(uri)
