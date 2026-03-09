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
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import duckdb

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id    VARCHAR PRIMARY KEY,
    graph_forest_name VARCHAR NOT NULL DEFAULT 'default',
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
    graph_forest_name VARCHAR NOT NULL DEFAULT 'default',
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
    graph_forest_name VARCHAR NOT NULL DEFAULT 'default',
    session_id  VARCHAR NOT NULL,
    field_name  VARCHAR NOT NULL,
    content     VARCHAR NOT NULL,
    occurred_at TIMESTAMP
)
"""

# Edge types that get materialized as tables for property graph edge labels.
# duckpgq does not support WHERE-filtered edge tables in DDL, so we materialize
# one table per edge type and reference those in the property graph definition.
_PGQ_EDGE_TYPES: tuple[str, ...] = (
    "HAS_RUN",
    "HAS_STEP",
    "NEXT",
    "TRIGGERED",
    "PARALLEL_WITH",
    "SPAWNED",
    "SUBSESSION_OF",
    "HAS_EVENT",
)


def _build_create_property_graph() -> str:
    """Build CREATE PROPERTY GRAPH DDL referencing materialized edge tables.

    duckpgq requires: (1) all MATCH patterns bind to a vertex label, so we
    assign LABEL Session to the nodes table as a catch-all label; (2) separate
    physical tables per edge type because WHERE filtering in edge DDL is not
    supported.
    """
    edge_clauses = []
    for etype in _PGQ_EDGE_TYPES:
        tbl = f"pgq_e_{etype.lower()}"
        edge_clauses.append(
            f"    {tbl} SOURCE KEY (source) REFERENCES nodes (node_id)\n"
            f"          DESTINATION KEY (target) REFERENCES nodes (node_id)\n"
            f"          LABEL {etype}"
        )
    edges_sql = ",\n".join(edge_clauses)
    return (
        "CREATE PROPERTY GRAPH context_graph\n"
        "VERTEX TABLES (\n"
        "    nodes LABEL Session\n"
        ")\n"
        "EDGE TABLES (\n"
        f"{edges_sql}\n"
        ")"
    )


# Registry of (label, property) -> field_name mappings for search_index population.
# Add new entries here when additional node types or properties become searchable.
_INDEXABLE_FIELDS: dict[tuple[str, str], str] = {
    ("PromptStep", "prompt_text"): "prompt_text",
}

# Tables that get forest-scoped CTE wrappers when a forest filter is active.
_FOREST_FILTERED_TABLES: tuple[str, ...] = ("nodes", "edges", "search_index")


def _inject_forest_filter(
    query: str, forest: str, params: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Wrap *query* with CTEs that shadow base tables filtered by forest.

    Creates ``WITH nodes AS (...), edges AS (...), search_index AS (...)``
    CTEs so that any downstream SQL referencing those table names only sees
    rows belonging to *forest*.  Injects ``$forest`` into *params*.
    """
    cte_parts = []
    for tbl in _FOREST_FILTERED_TABLES:
        if tbl == "search_index":
            # Preserve the physical rowid so FTS match_bm25 correlates correctly.
            cte_parts.append(
                f"{tbl} AS ("
                f"SELECT rowid, t.* FROM main.{tbl} t "
                f"WHERE t.graph_forest_name = $forest)"
            )
        else:
            cte_parts.append(
                f"{tbl} AS (SELECT * FROM main.{tbl} WHERE graph_forest_name = $forest)"
            )
    cte_sql = "WITH " + ", ".join(cte_parts) + " "
    params["forest"] = forest
    return cte_sql + query, params


class DuckDBGraphStore:
    """Graph store backed by DuckDB with in-memory write buffer.

    Writes are buffered in Python dicts for instant access.  ``flush()``
    persists buffers to DuckDB in a single transaction.  Reads check the
    buffer first, falling back to DuckDB only when the buffer has no entry.
    """

    def __init__(self, connection: str = ":memory:", graph_forest_name: str = "default") -> None:
        self._connection_str = connection
        self._graph_forest_name = graph_forest_name
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
        self._pgq_ready: bool = False
        self._fts_ready: bool = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, fn: Callable[[], _T]) -> asyncio.Future[_T]:
        """Run a blocking callable in the default executor."""
        return asyncio.get_running_loop().run_in_executor(None, fn)

    def _ensure_pgq(self) -> None:
        """Lazily load duckpgq and create the property graph (idempotent)."""
        if self._pgq_ready:
            return
        _install_err = (duckdb.CatalogException, duckdb.HTTPException, duckdb.IOException)
        try:
            self._conn.execute("INSTALL duckpgq; LOAD duckpgq;")
        except _install_err:
            try:
                self._conn.execute("INSTALL duckpgq FROM community; LOAD duckpgq;")
            except _install_err:
                self._conn.execute("LOAD duckpgq;")
        # Materialize per-edge-type tables for the property graph,
        # filtered to the current forest so PGQ queries are forest-scoped.
        forest = self._graph_forest_name
        for etype in _PGQ_EDGE_TYPES:
            tbl = f"pgq_e_{etype.lower()}"
            self._conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            self._conn.execute(
                f"CREATE TABLE {tbl} AS SELECT * FROM edges "
                "WHERE edge_type = $etype AND graph_forest_name = $forest",
                {"etype": etype, "forest": forest},
            )
        self._conn.execute("DROP PROPERTY GRAPH IF EXISTS context_graph")
        self._conn.execute(_build_create_property_graph())
        self._pgq_ready = True

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

    @property
    def graph_forest_name(self) -> str:
        """The graph forest name for this store instance."""
        return self._graph_forest_name

    @property
    def supported_dialects(self) -> frozenset[str]:
        """The set of query dialects this backend can execute."""
        return frozenset({"sql", "pgq"})

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
        forest = self._graph_forest_name
        nodes = self._node_buffer
        edges = self._edge_buffer
        search = self._search_buffer
        self._node_buffer = {}
        self._edge_buffer = {}
        self._search_buffer = []

        if not nodes and not edges and not search:
            return

        def _write() -> None:
            # session_id columns in nodes/edges are reserved for future use;
            # the authoritative session_id lives in the properties JSON.
            try:
                self._conn.execute("BEGIN TRANSACTION")
                for node in nodes.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO nodes "
                        "(node_id, graph_forest_name, session_id, labels, properties) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [
                            node["id"],
                            forest,
                            "",
                            list(node["labels"]),
                            json.dumps(node["properties"]),
                        ],
                    )
                for edge in edges.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO edges "
                        "(source, target, edge_type, graph_forest_name, session_id, properties) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            edge["source"],
                            edge["target"],
                            edge["type"],
                            forest,
                            "",
                            json.dumps(edge["properties"]),
                        ],
                    )
                for entry in search:
                    self._conn.execute(
                        "INSERT INTO search_index "
                        "(node_id, graph_forest_name, session_id, field_name, content, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            entry["node_id"],
                            forest,
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

    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
        graph_forest_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if dialect is not None and dialect not in self.supported_dialects:
            raise ValueError(
                f"Unsupported dialect {dialect!r}; supported: {sorted(self.supported_dialects)}"
            )

        # Resolve forest: caller override > instance default
        forest = self._graph_forest_name if graph_forest_name is None else graph_forest_name

        def _query() -> list[dict[str, Any]]:
            nonlocal query, params
            if dialect == "pgq":
                self._ensure_pgq()
            # Inject CTE forest filters for non-PGQ queries unless wildcard.
            # PGQ queries are already forest-scoped via _ensure_pgq materialization.
            if forest != "*" and dialect != "pgq":
                p = dict(params) if params is not None else {}
                query, p = _inject_forest_filter(query, forest, p)
                params = p
            # DuckDB requires omitting params arg when none provided
            if params is not None:
                result = self._conn.execute(query, params)
            else:
                result = self._conn.execute(query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]

        return await self._run(_query)

    # ------------------------------------------------------------------
    # Full-Text Search
    # ------------------------------------------------------------------

    def _build_fts_index(self) -> None:
        """Build FTS index on search_index table. Drops existing index first."""
        try:
            self._conn.execute("PRAGMA drop_fts_index('search_index')")
        except Exception:
            pass  # Index may not exist yet
        self._conn.execute("PRAGMA create_fts_index('search_index', 'rowid', 'content')")
        self._fts_ready = True

    async def rebuild_fts_index(self) -> None:
        """Rebuild the FTS index on search_index.

        Call after flush() when search results need to be current.
        NOT called automatically by flush() — this is an explicit action.
        """
        await self._run(self._build_fts_index)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self.flush()
        self._conn.close()
