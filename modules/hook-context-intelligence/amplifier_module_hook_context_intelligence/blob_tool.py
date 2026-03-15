"""BlobTool — agent-facing tool for inspecting and materializing blobs.

Agents never load blob content into the context window directly.
Instead they use blob_list() to discover blob metadata and blob_dump()
to materialize a blob to disk, then read it with file tools.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

_SEP = "__"  # key separator: <node_id>__<field>
_URI_SCHEME = "ci-blob://"


class BlobTool:
    """Agent-facing tool for blob inspection and materialization.

    Agents use blob_list() to discover blobs and blob_dump() to write
    them to disk for later inspection via file tools.
    """

    def __init__(self, server_url: str) -> None:
        self._server_url = server_url.rstrip("/")

    async def blob_list(self, session_id: str) -> list[dict]:
        """List blob metadata for all blobs in a session.

        Calls GET /blobs/{session_id} on the configured server.

        Returns a list of dicts, each containing:
            uri         - ci-blob:// URI
            field       - last component after splitting key on '__'
            node_id     - everything before the last '__' in the key
            size_bytes  - None (not available via HTTP)

        If the key has no '__' separator, node_id equals the key and
        field is 'unknown'.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self._server_url}/blobs/{session_id}")
            resp.raise_for_status()  # propagate 4xx/5xx as httpx.HTTPStatusError
            data = resp.json()

        result = []
        for uri in data.get("blobs", []):
            # Extract key from ci-blob://session_id/key
            prefix = f"{_URI_SCHEME}{session_id}/"
            key = uri[len(prefix) :]

            sep_idx = key.rfind(_SEP)
            if sep_idx == -1:
                node_id = key
                field = "unknown"
            else:
                node_id = key[:sep_idx]
                field = key[sep_idx + len(_SEP) :]

            result.append(
                {
                    "uri": uri,
                    "field": field,
                    "node_id": node_id,
                    "size_bytes": None,
                }
            )
        return result

    async def blob_dump(self, uri: str, dest_path: str | None = None) -> str:
        """Materialize a blob to disk and return the file path.

        Calls GET /blobs/{session_id}/{key} on the configured server.

        Args:
            uri: A ci-blob:// URI identifying the blob.
            dest_path: Optional destination path.
                Defaults to tempfile.gettempdir()/ci-blobs/<key>.json.

        Returns:
            Path where the blob file was written.
        """
        # Parse URI: ci-blob://session_id/key
        without_scheme = uri[len(_URI_SCHEME) :]
        session_id, key = without_scheme.split("/", 1)

        if dest_path is None:
            dest_path = str(Path(tempfile.gettempdir()) / "ci-blobs" / f"{key}.json")

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self._server_url}/blobs/{session_id}/{key}")
            resp.raise_for_status()  # propagate 4xx/5xx; don't write error bodies to disk

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(resp.text)

        return dest_path
