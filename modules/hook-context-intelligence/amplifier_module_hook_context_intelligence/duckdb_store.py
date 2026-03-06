"""DuckDBGraphStore – buffer-first reads with async DuckDB persistence.

STANDING RULE — Skill Synchronization
--------------------------------------
Any change to the schema (tables, columns, property graph definition,
search_index, FTS indexes, new label types, new edge types,
new field_name values in search_index, _INDEXABLE_FIELDS entries)
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

import duckdb

logger = logging.getLogger(__name__)

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id    VARCHAR PRIMARY KEY,
    session_id VARCHAR DEFAULT '',
    labels     VARCHAR[],
    occurred_at TIMESTAMP,
    properties JSON
)
"""

_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    source     VARCHAR,
    target     VARCHAR,
    edge_type  VARCHAR,
    session_id VARCHAR DEFAULT '',
    occurred_at TIMESTAMP,
    seq        INTEGER,
    properties JSON,
    PRIMARY KEY (source, target, edge_type)
)
"""

_CREATE_SEARCH_INDEX = """
CREATE TABLE IF NOT EXISTS search_index (
    node_id     VARCHAR NOT NULL,
    session_id  VARCHAR NOT NULL,
    field_name  VARCHAR NOT NULL,
    content     VARCHAR NOT NULL,
    occurred_at TIMESTAMP
)
"""


_INDEXABLE_FIELDS: dict[tuple[str, str], str] = {
    ("PromptStep", "prompt_text"): "prompt_text",
}


class DuckDBGraphStore:
    """Graph store backed by DuckDB with in-memory write buffer.

    Writes are buffered in Python dicts for instant access.  ``flush()``
    persists buffers to DuckDB in a single transaction.  Reads check the
    buffer first, falling back to DuckDB only when the buffer has no entry.
    """

    def __init__(self, connection: str = ":memory:") -> None:
        self._connection_str = connection
        if connection != ":memory:":
            path = Path(connection).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection_str = str(path)
        self._conn = duckdb.connect(self._connection_str)
        self._conn.execute(_CREATE_NODES)
        self._conn.execute(_CREATE_EDGES)
        self._conn.execute(_CREATE_SEARCH_INDEX)
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._search_buffer: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, fn: Any) -> Any:  # noqa: ANN401
        """Run a blocking callable in the default executor."""
        return asyncio.get_running_loop().run_in_executor(None, fn)

    # ------------------------------------------------------------------
    # Writes (buffer only, no I/O)
    # ------------------------------------------------------------------

    def _index_searchable_content(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> None:
        """Append search entries to _search_buffer for indexable fields."""
        for (label, prop_key), field_name in _INDEXABLE_FIELDS.items():
            if label in labels and properties.get(prop_key):
                self._search_buffer.append(
                    {
                        "node_id": node_id,
                        "session_id": properties.get("session_id", ""),
                        "field_name": field_name,
                        "content": str(properties[prop_key]),
                        "occurred_at": properties.get("occurred_at"),
                    }
                )

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        existing = self._node_buffer.get(node_id)
        if existing is not None:
            existing["labels"] |= labels
            existing["properties"].update(properties)
            # Early return also prevents duplicate search indexing — _index_searchable_content
            # is only called on first insert (below), not on subsequent updates.
            return
        self._node_buffer[node_id] = {
            "id": node_id,
            "labels": set(labels),
            "properties": dict(properties),
        }
        self._index_searchable_content(node_id, labels, properties)

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
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
    # Reads (buffer-first, then DuckDB)
    # ------------------------------------------------------------------

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        buffered = self._node_buffer.get(node_id)
        if buffered is not None:
            return buffered

        def _query() -> tuple[Any, ...] | None:
            return self._conn.execute(
                "SELECT node_id, labels, properties FROM nodes WHERE node_id = ?",
                [node_id],
            ).fetchone()

        row = await self._run(_query)
        if row is None:
            return None
        return {
            "id": row[0],
            "labels": set(row[1]) if row[1] else set(),
            "properties": json.loads(row[2]) if row[2] else {},
        }

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        key = (source, target, edge_type)
        buffered = self._edge_buffer.get(key)
        if buffered is not None:
            return buffered

        def _query() -> tuple[Any, ...] | None:
            return self._conn.execute(
                "SELECT source, target, edge_type, properties FROM edges "
                "WHERE source = ? AND target = ? AND edge_type = ?",
                [source, target, edge_type],
            ).fetchone()

        row = await self._run(_query)
        if row is None:
            return None
        return {
            "source": row[0],
            "target": row[1],
            "type": row[2],
            "properties": json.loads(row[3]) if row[3] else {},
        }

    # ------------------------------------------------------------------
    # Flush (persist buffers to DuckDB)
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        # Snapshot and clear
        nodes = self._node_buffer
        edges = self._edge_buffer
        search = self._search_buffer
        self._node_buffer = {}
        self._edge_buffer = {}
        self._search_buffer = []

        if not nodes and not edges and not search:
            return

        def _write() -> None:
            try:
                self._conn.execute("BEGIN TRANSACTION")
                for node in nodes.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO nodes (node_id, session_id, labels, properties) "
                        "VALUES (?, ?, ?, ?)",
                        [
                            node["id"],
                            "",  # session_id: lives in properties; column reserved for future use
                            list(node["labels"]),
                            json.dumps(node["properties"]),
                        ],
                    )
                for edge in edges.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO edges "
                        "(source, target, edge_type, session_id, properties) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [
                            edge["source"],
                            edge["target"],
                            edge["type"],
                            "",  # session_id: lives in properties; column reserved for future use
                            json.dumps(edge["properties"]),
                        ],
                    )
                for entry in search:
                    self._conn.execute(
                        "INSERT INTO search_index "
                        "(node_id, session_id, field_name, content, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [
                            entry["node_id"],
                            entry["session_id"],
                            entry["field_name"],
                            entry["content"],
                            entry["occurred_at"],
                        ],
                    )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:
                    logger.warning("rollback also failed", exc_info=True)
                # Put items back for retry
                self._node_buffer.update(nodes)
                self._edge_buffer.update(edges)
                self._search_buffer.extend(search)
                logger.warning("flush failed; buffers restored for retry", exc_info=True)

        await self._run(_write)

    # ------------------------------------------------------------------
    # QueryableStore
    # ------------------------------------------------------------------

    @property
    def supported_dialects(self) -> frozenset[str]:
        """DuckDB speaks SQL and PGQ."""
        return frozenset({"sql", "pgq"})

    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
    ) -> list[dict[str, Any]]:
        if dialect is not None and dialect not in self.supported_dialects:
            raise ValueError(
                f"Unsupported dialect {dialect!r}; supported: {sorted(self.supported_dialects)}"
            )

        def _query() -> list[dict[str, Any]]:
            if params:
                result = self._conn.execute(query, params)
            else:
                result = self._conn.execute(query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]

        return await self._run(_query)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self.flush()
        self._conn.close()
