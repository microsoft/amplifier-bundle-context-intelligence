"""BlobTool — agent-facing tool for inspecting and materializing blobs.

Agents never load blob content into the context window directly.
Instead they use blob_list() to discover blob metadata and blob_dump()
to materialize a blob to disk, then read it with file tools.
"""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.blob_store import DiskBlobStore


class BlobTool:
    """Agent-facing tool for blob inspection and materialization.

    Agents use blob_list() to discover blobs and blob_dump() to write
    them to disk for later inspection via file tools.
    """

    def __init__(self, store: DiskBlobStore) -> None:
        self._store = store

    async def blob_list(self, session_id: str) -> list[dict]:
        """List blob metadata for all blobs in a session.

        Returns a list of dicts, each containing:
            uri         - ci-blob:// URI
            field       - last component after splitting key on '__'
            node_id     - everything before the last '__' in the key
            size_bytes  - file size in bytes

        If the key has no '__' separator, node_id equals the key and
        field is 'unknown'.
        """
        uris = await self._store.list(session_id)
        result = []
        for uri in uris:
            _, key = self._store._parse_uri(uri)
            sep_idx = key.rfind("__")
            if sep_idx == -1:
                node_id = key
                field = "unknown"
            else:
                node_id = key[:sep_idx]
                field = key[sep_idx + 2 :]
            path = self._store._blob_path(session_id, key)
            size_bytes = path.stat().st_size
            result.append(
                {
                    "uri": uri,
                    "field": field,
                    "node_id": node_id,
                    "size_bytes": size_bytes,
                }
            )
        return result

    async def blob_dump(self, uri: str, dest_path: str | None = None) -> str:
        """Materialize a blob to disk and return the file path.

        Args:
            uri: A ci-blob:// URI identifying the blob.
            dest_path: Optional destination path.
                Defaults to /tmp/ci-blobs/<key>.json.

        Returns:
            Path where the blob file was written.

        Raises:
            FileNotFoundError: If the blob does not exist.
        """
        return await self._store.dump(uri, dest_path)
