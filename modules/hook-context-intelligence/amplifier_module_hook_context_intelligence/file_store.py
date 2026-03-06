"""FileGraphStore — JSON file-based GraphStore with buffer-first reads.

STANDING RULE — Skill Synchronization
--------------------------------------
Any change to the file layout (directory structure, filename patterns,
JSON schema for node/edge files, new label types, new edge types)
MUST be accompanied by an update to the SQL/PGQ skill at
``skills/context-intelligence-graph-search/SKILL.md``.

The skill is the contract between this storage layer and agents that generate
queries.  Stale skill = broken agent query generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .utils import make_edge_id

logger = logging.getLogger(__name__)


class FileGraphStore:
    """Graph store backed by flat JSON files with in-memory write buffers.

    Writes are buffered in Python dicts for instant access.  ``flush()``
    persists buffers to JSON files in ``{location}/nodes/`` and
    ``{location}/edges/`` directories.  Reads check the buffer first,
    falling back to disk only when the buffer has no entry.
    """

    def __init__(self, location: str) -> None:
        self._location = Path(location).expanduser()
        self._nodes_dir = self._location / "nodes"
        self._edges_dir = self._location / "edges"
        self._nodes_dir.mkdir(parents=True, exist_ok=True)
        self._edges_dir.mkdir(parents=True, exist_ok=True)
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, fn: Any) -> Any:  # noqa: ANN401
        """Run a blocking callable in the default executor."""
        return asyncio.get_running_loop().run_in_executor(None, fn)

    # ------------------------------------------------------------------
    # Writes (buffer only, no I/O)
    # ------------------------------------------------------------------

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        """Insert or update a node in the write buffer."""
        existing = self._node_buffer.get(node_id)
        if existing is not None:
            existing["labels"] |= labels
            existing["properties"].update(properties)
            return
        self._node_buffer[node_id] = {
            "id": node_id,
            "labels": set(labels),
            "properties": dict(properties),
        }

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        """Insert or update an edge in the write buffer."""
        key = (source, target, edge_type)
        existing = self._edge_buffer.get(key)
        if existing is not None:
            existing["properties"].update(properties)
            return
        self._edge_buffer[key] = {
            "source": source,
            "target": target,
            "type": edge_type,
            "properties": dict(properties),
        }

    # ------------------------------------------------------------------
    # Reads (buffer-first, then disk)
    # ------------------------------------------------------------------

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node by ID.  Checks buffer first, then disk."""
        buffered = self._node_buffer.get(node_id)
        if buffered is not None:
            return buffered

        def _read() -> dict[str, Any] | None:
            path = self._nodes_dir / f"{node_id}.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return {
                "id": data["id"],
                "labels": set(data.get("labels", [])),
                "properties": data.get("properties", {}),
            }

        return await self._run(_read)

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        """Retrieve an edge by composite key.  Checks buffer first, then disk."""
        key = (source, target, edge_type)
        buffered = self._edge_buffer.get(key)
        if buffered is not None:
            return buffered

        edge_id = make_edge_id(source, target, edge_type)

        def _read() -> dict[str, Any] | None:
            path = self._edges_dir / f"{edge_id}.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return {
                "source": data["source"],
                "target": data["target"],
                "type": data["type"],
                "properties": data.get("properties", {}),
            }

        return await self._run(_read)

    # ------------------------------------------------------------------
    # Flush (persist buffers to JSON files)
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """Persist buffered writes to JSON files."""
        nodes = self._node_buffer
        edges = self._edge_buffer
        self._node_buffer = {}
        self._edge_buffer = {}

        if not nodes and not edges:
            return

        def _write() -> None:
            try:
                for node_id, node in nodes.items():
                    path = self._nodes_dir / f"{node_id}.json"
                    if path.exists():
                        existing = json.loads(path.read_text())
                        existing["labels"] = sorted(
                            set(existing.get("labels", [])) | node["labels"]
                        )
                        existing["properties"].update(node["properties"])
                        path.write_text(json.dumps(existing))
                    else:
                        data = {
                            "id": node["id"],
                            "labels": sorted(node["labels"]),
                            "properties": node["properties"],
                        }
                        path.write_text(json.dumps(data))

                for (source, target, edge_type), edge in edges.items():
                    edge_id = make_edge_id(source, target, edge_type)
                    path = self._edges_dir / f"{edge_id}.json"
                    if path.exists():
                        existing = json.loads(path.read_text())
                        existing["properties"].update(edge["properties"])
                        path.write_text(json.dumps(existing))
                    else:
                        data = {
                            "source": edge["source"],
                            "target": edge["target"],
                            "type": edge["type"],
                            "properties": edge["properties"],
                        }
                        path.write_text(json.dumps(data))
            except Exception:
                self._node_buffer.update(nodes)
                self._edge_buffer.update(edges)
                logger.warning("flush failed; buffers restored for retry", exc_info=True)

        await self._run(_write)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Not supported — use grep/jq on the JSON files directly."""
        raise NotImplementedError(
            "FileGraphStore does not support execute_query. Use grep/jq on the JSON files directly."
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Shut down the store.  Flushes pending data first."""
        await self.flush()
