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

import asyncio
import json
import logging
from datetime import datetime
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
        graph_forest_name: str | None = None,
    ) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=auth)
        self._database = database
        self._graph_forest_name: str | None = graph_forest_name

        # Write buffers
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}

        # Schema tracking
        self._schema_initialized: bool = False
        self._closed: bool = False

        # Background flush tracking
        self._flush_task: asyncio.Task[None] | None = None

    # -- Properties ----------------------------------------------------------

    @property
    def graph_forest_name(self) -> str:
        """The forest this store writes to.

        Returns the resolved name, or ``'default'`` if not yet set.
        """
        return self._graph_forest_name or "default"

    @graph_forest_name.setter
    def graph_forest_name(self, value: str) -> None:
        """Set the forest name from runtime data (e.g. coordinator project slug)."""
        self._graph_forest_name = value

    @property
    def supported_dialects(self) -> frozenset[str]:
        """Query dialects this backend can execute."""
        return frozenset({"cypher"})

    # -- GraphStore methods --------------------------------------------------

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

    # -- Neo4j-compatible primitive types ------------------------------------
    _NEO4J_PRIMITIVES = (str, int, float, bool)

    @staticmethod
    def _sanitize_properties(props: dict[str, Any]) -> dict[str, Any]:
        """Sanitize property values for Neo4j compatibility.

        Neo4j property values must be primitives (str, int, float, bool)
        or homogeneous arrays thereof.  This method:

        - Keeps primitives as-is.
        - Keeps homogeneous lists of primitives as-is.
        - JSON-serializes dicts and mixed/nested lists to strings.
        - Drops ``None`` values (Neo4j does not support null properties).

        Applied in ``flush()`` before ``_convert_timestamps()`` so that
        nested structures are safely stringified before any further
        property transforms run.
        """
        result: dict[str, Any] = {}
        for key, value in props.items():
            if value is None:
                continue  # Neo4j does not support null property values
            if isinstance(value, Neo4jGraphStore._NEO4J_PRIMITIVES):
                result[key] = value
            elif isinstance(value, list):
                if value and all(
                    isinstance(item, Neo4jGraphStore._NEO4J_PRIMITIVES) for item in value
                ):
                    result[key] = value  # homogeneous primitive array
                else:
                    result[key] = json.dumps(value, default=str)
            elif isinstance(value, dict):
                result[key] = json.dumps(value, default=str)
            else:
                # datetime, custom objects, etc. — stringify
                result[key] = str(value)
        return result

    @staticmethod
    def _convert_timestamps(props: dict[str, Any]) -> dict[str, Any]:
        """Convert *_at ISO-8601 string properties to Python datetime objects.

        The Neo4j Python driver maps Python datetime → native Neo4j DateTime,
        enabling temporal queries (<, >, ORDER BY, duration.between()).
        Malformed or empty *_at values are kept as-is (logged as warnings).
        """
        result = {}
        for key, value in props.items():
            if key.endswith("_at") and isinstance(value, str) and value:
                try:
                    result[key] = datetime.fromisoformat(value)
                except ValueError:
                    logger.warning(
                        "Could not parse timestamp for property %r: %r — keeping as string",
                        key,
                        value,
                    )
                    result[key] = value
            else:
                result[key] = value
        return result

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
                gfn=self.graph_forest_name,
            )
            record = await result.single()
            if record is None:
                return None
            neo4j_node = record["n"]
            props = dict(neo4j_node)
            props.pop("node_id", None)
            props.pop("graph_forest_name", None)
            return {
                "id": node_id,
                "labels": set(neo4j_node.labels),
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
                "WHERE type(r) = $edge_type AND r.graph_forest_name = $gfn RETURN r",
                source=source,
                target=target,
                edge_type=edge_type,
                gfn=self.graph_forest_name,
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
                    forest = self.graph_forest_name

                    # -- UNWIND nodes grouped by primary label --
                    # Each node MERGEs on its primary (first sorted) label
                    # so domain-label indexes are used. Additional labels
                    # are applied in a second pass.
                    if node_snapshot:
                        # Group by primary label for MERGE efficiency
                        primary_groups: dict[str, list[dict[str, Any]]] = {}
                        for node_id, entry in node_snapshot.items():
                            labels = entry["labels"]
                            primary = sorted(labels)[0] if labels else "Session"
                            row: dict[str, Any] = {
                                "node_id": node_id,
                                "props": {
                                    **self._convert_timestamps(
                                        self._sanitize_properties(entry["properties"])
                                    ),
                                    "node_id": node_id,
                                    "graph_forest_name": forest,
                                },
                                "labels": list(labels),
                            }
                            primary_groups.setdefault(primary, []).append(row)

                        for primary_label, rows in primary_groups.items():
                            # primary_label is safe: comes from handler code
                            # (e.g. "Session", "OrchestratorRun"), not user input.
                            await tx.run(
                                f"UNWIND $rows AS row "  # noqa: S608
                                f"MERGE (n:`{primary_label}` {{node_id: row.node_id}}) "
                                f"SET n += row.props",
                                rows=rows,
                            )

                        # -- Apply additional labels in second pass --
                        all_rows = [r for group in primary_groups.values() for r in group]
                        label_groups: dict[frozenset[str], list[str]] = {}
                        for row in all_rows:
                            key = frozenset(row["labels"])
                            if len(key) > 1:  # only needed when >1 label
                                label_groups.setdefault(key, []).append(row["node_id"])

                        for label_set, node_ids in label_groups.items():
                            label_clause = ":".join(f"`{lbl}`" for lbl in sorted(label_set))
                            # label_clause is safe: values come from internal frozenset keys
                            # populated by upsert_node() callers, never from raw user input.
                            await tx.run(
                                f"UNWIND $ids AS nid "  # noqa: S608
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
                                        **self._convert_timestamps(
                                            self._sanitize_properties(entry["properties"])
                                        ),
                                        "graph_forest_name": forest,
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
                # Per-label node_id indexes (used by MERGE)
                await session.run(
                    "CREATE INDEX idx_session_node_id IF NOT EXISTS FOR (n:Session) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_orchestrator_run_node_id IF NOT EXISTS FOR (n:OrchestratorRun) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_step_node_id IF NOT EXISTS FOR (n:Step) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_tool_execution_node_id IF NOT EXISTS FOR (n:ToolExecution) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_event_node_id IF NOT EXISTS FOR (n:Event) ON (n.node_id)"
                )
                # Forest filtering index on Session (most common query entry point)
                await session.run(
                    "CREATE INDEX idx_session_forest IF NOT EXISTS "
                    "FOR (n:Session) ON (n.graph_forest_name)"
                )
        except Exception:
            logger.warning("schema initialization failed", exc_info=True)
            raise

        self._schema_initialized = True

    def schedule_flush(self) -> None:
        """Schedule a non-blocking background flush.

        Never blocks the caller — the flush runs as an async task.
        Multiple calls while a flush is in-flight are coalesced (no-op).
        Use this from event handlers to keep writes off the hot path.
        Use ``close()`` for guaranteed final flush before shutdown.
        """
        if self._flush_task is not None and not self._flush_task.done():
            return  # flush already in-flight, will pick up buffered data
        try:
            loop = asyncio.get_running_loop()
            self._flush_task = loop.create_task(self._background_flush())
        except RuntimeError:
            pass  # no event loop — flush will happen at close()

    async def _background_flush(self) -> None:
        """Wrapper for flush() that logs errors without propagating."""
        try:
            await self.flush()
        except Exception:
            logger.warning("background flush failed", exc_info=True)

    async def close(self) -> None:
        if not self._closed:
            # Await any pending background flush before final flush
            if self._flush_task is not None and not self._flush_task.done():
                try:
                    await self._flush_task
                except Exception:
                    pass  # errors already logged by _background_flush
            await self.flush()
            await self._driver.close()
            self._closed = True

    # -- QueryableStore methods ----------------------------------------------

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

        # Resolve forest name: explicit param > instance default (property)
        forest = graph_forest_name if graph_forest_name is not None else self.graph_forest_name

        # Build params dict with forest injection
        resolved_params: dict[str, Any] = dict(params) if params else {}
        if forest != "*":
            resolved_params["graph_forest_name"] = forest

        # Execute query
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, resolved_params)
            return [dict(record) async for record in result]
