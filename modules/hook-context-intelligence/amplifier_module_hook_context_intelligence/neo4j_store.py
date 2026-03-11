"""Neo4jGraphStore – buffer-first reads with async Neo4j persistence.

Implements buffer-first reads with Neo4j fallback, in-memory buffer writes
with merge semantics, UNWIND-based batch flush, and connection lifecycle management.

STANDING RULE — Skill Synchronization
--------------------------------------
Any change to the schema (node labels, relationship types, property keys,
graph_forest_name scoping, indexed properties, index definitions in
``_ensure_schema``)
MUST be accompanied by an update to the Cypher skill at
``skills/context-intelligence-neo4j-search/SKILL.md``.

The skill is the contract between this storage layer and agents that generate
queries.  Stale skill = broken agent query generation.
"""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncGraphDatabase

logger = logging.getLogger(__name__)


class Neo4jGraphStore:
    """Async Neo4j graph store conforming to the QueryableStore protocol.

    Supports buffer-first reads (in-memory buffer checked before Neo4j),
    in-memory buffer writes with merge semantics, and connection lifecycle.
    """

    def __init__(
        self,
        uri: str = "neo4j://localhost:7687",
        auth: tuple[str, str] | None = ("neo4j", "neo4j"),
        database: str = "neo4j",
        graph_forest_name: str = "default",
    ) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=auth)
        self._database = database
        self._graph_forest_name = graph_forest_name

        # Write buffers
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}

        # Schema tracking
        self._schema_initialized: bool = False
        self._closed: bool = False

    # -- Properties ----------------------------------------------------------

    @property
    def graph_forest_name(self) -> str:
        """The forest this store writes to (read-only)."""
        return self._graph_forest_name

    @property
    def supported_dialects(self) -> frozenset[str]:
        """Query dialects this backend can execute."""
        return frozenset({"cypher"})

    # -- GraphStore methods (stubbed) ----------------------------------------

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        existing = self._node_buffer.get(node_id)
        if existing:
            existing["labels"] |= labels
            existing["properties"].update(properties)
        else:
            self._node_buffer[node_id] = {
                "id": node_id,
                "labels": set(labels),
                "properties": dict(properties),
            }

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        key = (source, target, edge_type)
        existing = self._edge_buffer.get(key)
        if existing:
            existing["properties"].update(properties)
        else:
            self._edge_buffer[key] = {
                "source": source,
                "target": target,
                "type": edge_type,
                "properties": dict(properties),
            }

    # Internal base label applied at MERGE time for index routing — not a domain label.
    _BASE_LABEL = "Node"

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        # Buffer-first: check in-memory buffer
        buffered = self._node_buffer.get(node_id)
        if buffered is not None:
            return {**buffered, "properties": dict(buffered["properties"])}

        # Fallback: query Neo4j
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $node_id, graph_forest_name: $gfn}) RETURN n",
                node_id=node_id,
                gfn=self._graph_forest_name,
            )
            record = await result.single()
            if record is None:
                return None
            neo4j_node = record["n"]
            props = dict(neo4j_node)
            props.pop("node_id", None)
            props.pop("graph_forest_name", None)
            # Strip the internal base label — it is a Neo4j index-routing label added
            # by the MERGE pattern (`MERGE (n:Node {...})`), not a domain label.
            domain_labels = set(neo4j_node.labels) - {self._BASE_LABEL}
            return {
                "id": node_id,
                "labels": domain_labels,
                "properties": props,
            }

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        # Buffer-first: check in-memory buffer
        key = (source, target, edge_type)
        buffered = self._edge_buffer.get(key)
        if buffered is not None:
            return {**buffered, "properties": dict(buffered["properties"])}

        # Fallback: query Neo4j
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                "MATCH (s {node_id: $source, graph_forest_name: $gfn})"
                "-[r]->"
                "(t {node_id: $target, graph_forest_name: $gfn}) "
                "WHERE type(r) = $edge_type RETURN r",
                source=source,
                target=target,
                edge_type=edge_type,
                gfn=self._graph_forest_name,
            )
            record = await result.single()
            if record is None:
                return None
            neo4j_rel = record["r"]
            props = dict(neo4j_rel)
            props.pop("graph_forest_name", None)
            return {
                "source": source,
                "target": target,
                "type": edge_type,
                "properties": props,
            }

    async def flush(self) -> None:
        """Flush buffered nodes and edges to Neo4j using UNWIND-based batch Cypher.

        Phase 1: snapshot buffers, optimistically clear them.
        Phase 2: early exit if both snapshots are empty.
        Phase 3: write to Neo4j in a single transaction; restore buffers on failure.
        """
        # Phase 1: snapshot and optimistically clear.
        # Shallow copies are sufficient here: inner dicts are shared references,
        # but originals are cleared immediately and the snapshot is only used for
        # restore-on-failure (no mutation of snapshot entries during the write phase).
        node_snapshot = dict(self._node_buffer)
        edge_snapshot = dict(self._edge_buffer)
        self._node_buffer.clear()
        self._edge_buffer.clear()

        # Phase 2: early exit if nothing to flush
        if not node_snapshot and not edge_snapshot:
            return

        # Phase 3: write to Neo4j
        try:
            await self._ensure_schema()

            async with self._driver.session(database=self._database) as session:
                tx = await session.begin_transaction()
                try:
                    # -- UNWIND nodes: MERGE on node_id, SET properties --
                    if node_snapshot:
                        node_rows = []
                        for node_id, entry in node_snapshot.items():
                            row: dict[str, Any] = {
                                "node_id": node_id,
                                "props": {
                                    **entry["properties"],
                                    "node_id": node_id,
                                    "graph_forest_name": self._graph_forest_name,
                                },
                                "labels": list(entry["labels"]),
                            }
                            node_rows.append(row)

                        await tx.run(
                            "UNWIND $rows AS row "
                            "MERGE (n:Node {node_id: row.node_id}) "
                            "SET n += row.props",
                            rows=node_rows,
                        )

                        # -- Apply labels in second pass grouped by distinct label set --
                        label_groups: dict[frozenset[str], list[str]] = {}
                        for row in node_rows:
                            key = frozenset(row["labels"])
                            if key:
                                label_groups.setdefault(key, []).append(row["node_id"])

                        for label_set, node_ids in label_groups.items():
                            label_clause = ":".join(f"`{lbl}`" for lbl in sorted(label_set))
                            # label_clause is safe: values come from internal frozenset keys
                            # populated by upsert_node() callers, never from raw user input.
                            await tx.run(
                                f"UNWIND $ids AS nid "  # noqa: S608 — label_clause built from internal frozenset keys, not user input
                                f"MATCH (n {{node_id: nid}}) "
                                f"SET n:{label_clause}",
                                ids=node_ids,
                            )

                    # -- UNWIND edges grouped by type --
                    if edge_snapshot:
                        edge_type_groups: dict[str, list[dict[str, Any]]] = {}
                        for (_src, _tgt, etype), entry in edge_snapshot.items():
                            edge_type_groups.setdefault(etype, []).append(
                                {
                                    "source": entry["source"],
                                    "target": entry["target"],
                                    "props": {
                                        **entry["properties"],
                                        "graph_forest_name": self._graph_forest_name,
                                    },
                                }
                            )

                        for rel_type, edge_rows in edge_type_groups.items():
                            # rel_type is safe: values come from internal edge_buffer keys
                            # set by upsert_edge() callers, never from raw user input.
                            await tx.run(
                                f"UNWIND $rows AS row "  # noqa: S608 — rel_type from internal edge_buffer keys, not user input
                                f"MATCH (s {{node_id: row.source}}) "
                                f"MATCH (t {{node_id: row.target}}) "
                                f"MERGE (s)-[r:`{rel_type}`]->(t) "
                                f"SET r += row.props",
                                rows=edge_rows,
                            )

                    await tx.commit()
                except Exception:
                    await tx.rollback()
                    raise

        except Exception:
            # Restore buffers on failure
            self._node_buffer.update(node_snapshot)
            self._edge_buffer.update(edge_snapshot)
            logger.warning("flush failed, buffers restored", exc_info=True)

    async def _ensure_schema(self) -> None:
        """Ensure Neo4j schema indexes exist (idempotent, runs once per instance)."""
        if self._schema_initialized:
            return

        try:
            async with self._driver.session(database=self._database) as session:
                await session.run(
                    "CREATE INDEX idx_node_id IF NOT EXISTS FOR (n:Node) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_forest IF NOT EXISTS FOR (n:Node) ON (n.graph_forest_name)"
                )
                await session.run(
                    "CREATE INDEX idx_session_node_id IF NOT EXISTS FOR (n:Session) ON (n.node_id)"
                )
        except Exception:
            logger.warning("schema initialization failed", exc_info=True)
            raise

        self._schema_initialized = True

    async def close(self) -> None:
        if not self._closed:
            await self.flush()
            await self._driver.close()
            self._closed = True

    # -- QueryableStore methods (stubbed) ------------------------------------

    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
        graph_forest_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a raw Cypher query with dialect validation and forest param injection."""
        # Validate dialect
        if dialect is not None and dialect not in self.supported_dialects:
            msg = f"Unsupported dialect {dialect!r}. Supported: {self.supported_dialects}"
            raise ValueError(msg)

        # Resolve forest name: explicit param > instance default
        forest = graph_forest_name if graph_forest_name is not None else self._graph_forest_name

        # Build params dict with forest injection
        resolved_params: dict[str, Any] = dict(params) if params else {}
        if forest != "*":
            resolved_params["graph_forest_name"] = forest

        # Execute query
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, resolved_params)
            return [dict(record) async for record in result]
