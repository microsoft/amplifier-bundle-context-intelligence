"""Tests for BlobStore protocol and DiskBlobStore implementation.

14 tests covering:
- write/read round-trip (returns URI, returns original value, list values,
  creates session blobs dir, creates JSON file on disk)
- URI scheme (format, special session IDs)
- list (empty session, all URIs, session isolation)
- dump (materializes file, default path, missing blob raises)
- disk layout (blobs dir under session, file naming)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_module_hook_context_intelligence.blob_store import BlobStore, DiskBlobStore


# ---------------------------------------------------------------------------
# write/read round-trip
# ---------------------------------------------------------------------------


class TestWriteReadRoundTrip:
    """write() and read() preserve data exactly."""

    async def test_write_returns_uri(self, tmp_path: Path) -> None:
        """write() returns a ci-blob:// URI."""
        store = DiskBlobStore(root=tmp_path)
        uri = await store.write("session-1", "my-key", {"data": "value"})
        assert uri.startswith("ci-blob://")

    async def test_read_returns_original_value(self, tmp_path: Path) -> None:
        """read() returns the same dict value that was written."""
        store = DiskBlobStore(root=tmp_path)
        value: dict = {"nested": {"key": "val"}, "list": [1, 2, 3]}
        uri = await store.write("session-1", "my-key", value)
        result = await store.read("session-1", uri)
        assert result == value

    async def test_write_read_list_value(self, tmp_path: Path) -> None:
        """write/read round-trip works with list values."""
        store = DiskBlobStore(root=tmp_path)
        value: list = [{"event": "a"}, {"event": "b"}]
        uri = await store.write("session-1", "events", value)
        result = await store.read("session-1", uri)
        assert result == value

    async def test_write_creates_session_blobs_dir(self, tmp_path: Path) -> None:
        """write() creates the <root>/<session-id>/blobs/ directory."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "my-key", {"data": "value"})
        blobs_dir = tmp_path / "session-1" / "blobs"
        assert blobs_dir.is_dir()

    async def test_write_creates_json_file_on_disk(self, tmp_path: Path) -> None:
        """write() creates a .json file at <root>/<session-id>/blobs/<key>.json."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "my-key", {"data": "value"})
        json_file = tmp_path / "session-1" / "blobs" / "my-key.json"
        assert json_file.is_file()


# ---------------------------------------------------------------------------
# URI scheme
# ---------------------------------------------------------------------------


class TestURIScheme:
    """URI format is ci-blob://<session-id>/<key>."""

    async def test_uri_format(self, tmp_path: Path) -> None:
        """URI has exact format ci-blob://<session-id>/<key>."""
        store = DiskBlobStore(root=tmp_path)
        uri = await store.write("session-abc", "key-xyz", {"x": 1})
        assert uri == "ci-blob://session-abc/key-xyz"

    async def test_special_session_ids(self, tmp_path: Path) -> None:
        """write() handles session IDs with special characters (underscores, dashes)."""
        store = DiskBlobStore(root=tmp_path)
        session_id = "session_2024-01-15_abc123"
        uri = await store.write(session_id, "events", [1, 2, 3])
        assert uri == f"ci-blob://{session_id}/events"
        result = await store.read(session_id, uri)
        assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------


class TestList:
    """list() returns only URIs for the specified session."""

    async def test_list_empty_session(self, tmp_path: Path) -> None:
        """list() returns empty list for a session with no blobs."""
        store = DiskBlobStore(root=tmp_path)
        result = await store.list("session-empty")
        assert result == []

    async def test_list_returns_all_uris(self, tmp_path: Path) -> None:
        """list() returns URIs for all blobs written to the session."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "key-a", {"a": 1})
        await store.write("session-1", "key-b", {"b": 2})
        uris = await store.list("session-1")
        assert set(uris) == {"ci-blob://session-1/key-a", "ci-blob://session-1/key-b"}

    async def test_list_session_isolation(self, tmp_path: Path) -> None:
        """list() returns only URIs for the specified session, not other sessions."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "key-a", {"a": 1})
        await store.write("session-2", "key-b", {"b": 2})
        uris = await store.list("session-1")
        assert len(uris) == 1
        assert "ci-blob://session-1/key-a" in uris
        assert all("session-2" not in u for u in uris)


# ---------------------------------------------------------------------------
# dump()
# ---------------------------------------------------------------------------


class TestDump:
    """dump() copies blob to dest_path; raises FileNotFoundError for missing blobs."""

    async def test_dump_materializes_file(self, tmp_path: Path) -> None:
        """dump() copies the blob file to the specified dest_path."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "my-key", {"data": "value"})
        uri = "ci-blob://session-1/my-key"
        dest = tmp_path / "output" / "my-blob.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        result_path = await store.dump(uri, str(dest))
        assert Path(result_path).is_file()
        content = json.loads(Path(result_path).read_text())
        assert content == {"data": "value"}

    async def test_dump_default_path(self, tmp_path: Path) -> None:
        """dump() without dest_path uses /tmp/ci-blobs/<key>.json."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "my-key", {"data": "value"})
        uri = "ci-blob://session-1/my-key"
        result_path = await store.dump(uri)
        assert result_path == "/tmp/ci-blobs/my-key.json"
        assert Path(result_path).is_file()

    async def test_dump_missing_blob_raises(self, tmp_path: Path) -> None:
        """dump() raises FileNotFoundError for a blob that does not exist."""
        store = DiskBlobStore(root=tmp_path)
        uri = "ci-blob://session-1/nonexistent-key"
        with pytest.raises(FileNotFoundError):
            await store.dump(uri)


# ---------------------------------------------------------------------------
# Disk layout
# ---------------------------------------------------------------------------


class TestDiskLayout:
    """Blobs are stored at <root>/<session-id>/blobs/<key>.json."""

    async def test_blobs_dir_under_session(self, tmp_path: Path) -> None:
        """Blob directory is <root>/<session-id>/blobs/."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("my-session", "my-key", {"x": 1})
        assert (tmp_path / "my-session" / "blobs").is_dir()

    async def test_file_naming(self, tmp_path: Path) -> None:
        """Blob files are named <key>.json inside the blobs directory."""
        store = DiskBlobStore(root=tmp_path)
        await store.write("session-1", "tool-outputs", [{"result": "ok"}])
        assert (tmp_path / "session-1" / "blobs" / "tool-outputs.json").is_file()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """DiskBlobStore satisfies the BlobStore protocol."""

    def test_disk_blob_store_satisfies_protocol(self, tmp_path: Path) -> None:
        """DiskBlobStore is an instance of BlobStore (runtime_checkable)."""
        store = DiskBlobStore(root=tmp_path)
        assert isinstance(store, BlobStore)
