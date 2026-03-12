"""BlobStore protocol and DiskBlobStore implementation.

BlobStore is an async protocol for writing large event fields to per-session
blob directories and returning ci-blob:// URIs.

Disk layout: <root>/<session-id>/blobs/<key>.json
URI scheme:  ci-blob://<session-id>/<key>
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BlobStore(Protocol):
    """Async protocol for blob storage backends.

    Implementations write large event fields to persistent storage and return
    ci-blob:// URIs for later retrieval.
    """

    async def write(self, session_id: str, key: str, value: dict | list) -> str:
        """Write value to blob storage and return a ci-blob:// URI.

        Serializes value as JSON with json.dumps(value, default=str).
        Creates parent directories as needed (mkdir parents=True, exist_ok=True).

        Returns a ci-blob://<session-id>/<key> URI.
        """
        ...

    async def read(self, session_id: str, uri: str) -> dict | list:
        """Read and deserialize blob content from a ci-blob:// URI.

        Returns the original value that was written.
        """
        ...

    async def list(self, session_id: str) -> list[str]:
        """List all blob URIs for the given session.

        Returns a list of ci-blob:// URIs.
        """
        ...

    async def dump(self, uri: str, dest_path: str | None = None) -> str:
        """Copy blob file to dest_path (or /tmp/ci-blobs/<key>.json by default).

        Returns the path where the file was copied.
        Raises FileNotFoundError if the blob does not exist.
        """
        ...


class DiskBlobStore:
    """Disk-backed BlobStore implementation.

    Disk layout: <root>/<session-id>/blobs/<key>.json
    URI scheme:  ci-blob://<session-id>/<key>
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_uri(self, session_id: str, key: str) -> str:
        """Construct a ci-blob://<session-id>/<key> URI."""
        return f"ci-blob://{session_id}/{key}"

    def _parse_uri(self, uri: str) -> tuple[str, str]:
        """Split ci-blob://<session-id>/<key> into (session_id, key)."""
        prefix = "ci-blob://"
        if not uri.startswith(prefix):
            raise ValueError(f"Invalid URI scheme: {uri!r}")
        rest = uri[len(prefix) :]
        session_id, key = rest.split("/", 1)
        return session_id, key

    def _blob_path(self, session_id: str, key: str) -> Path:
        """Return the canonical path for a blob file."""
        return self._root / session_id / "blobs" / f"{key}.json"

    # ------------------------------------------------------------------
    # BlobStore protocol methods
    # ------------------------------------------------------------------

    async def write(self, session_id: str, key: str, value: dict | list) -> str:
        """Write value to disk and return a ci-blob:// URI."""
        path = self._blob_path(session_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, default=str))
        return self._make_uri(session_id, key)

    async def read(self, session_id: str, uri: str) -> dict | list:
        """Read and deserialize blob content from a ci-blob:// URI."""
        _, key = self._parse_uri(uri)
        path = self._blob_path(session_id, key)
        return json.loads(path.read_text())

    async def list(self, session_id: str) -> list[str]:
        """List all blob URIs for the given session."""
        blobs_dir = self._root / session_id / "blobs"
        if not blobs_dir.is_dir():
            return []
        return [self._make_uri(session_id, p.stem) for p in sorted(blobs_dir.glob("*.json"))]

    async def dump(self, uri: str, dest_path: str | None = None) -> str:
        """Copy blob file to dest_path (default: /tmp/ci-blobs/<key>.json).

        Raises FileNotFoundError if the blob does not exist.
        """
        session_id, key = self._parse_uri(uri)
        src = self._blob_path(session_id, key)
        if not src.exists():
            raise FileNotFoundError(f"Blob not found: {uri!r}")
        if dest_path is None:
            dest_path = f"/tmp/ci-blobs/{key}.json"
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return str(dest)
