"""Tests for Neo4jGraphStore – protocol conformance and skeleton verification."""

from __future__ import annotations

import pytest
from typing import Any

from tests.conftest import (  # noqa: F401 — re-exported for downstream tests
    NEO4J_AUTH,
    NEO4J_DATABASE,
    NEO4J_URI,
    PROMPT_NODE_ID,
    RUN_NODE_ID,
    SESSION_ID,
    SESSION_NODE_ID,
    TOOL_NODE_ID,
    reference_edges,
    reference_nodes,
)


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------
class TestProtocolConformance:
    """Neo4jGraphStore must satisfy both GraphStore and QueryableStore protocols."""

    def test_isinstance_graph_store(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        assert isinstance(store, GraphStore)

    def test_isinstance_queryable_store(self):
        from amplifier_module_hook_context_intelligence.graph_store import QueryableStore
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        assert isinstance(store, QueryableStore)


# ---------------------------------------------------------------------------
# TestProtocolDefinition — generic protocol shape tests (migrated from
# test_graph_store.py which is deleted as part of the Neo4j simplification)
# ---------------------------------------------------------------------------
class TestProtocolDefinition:
    """GraphStore and QueryableStore protocol definitions are correct."""

    def test_graph_store_is_runtime_checkable(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        assert hasattr(GraphStore, "__protocol_attrs__") or hasattr(
            GraphStore, "_is_runtime_protocol"
        )

    def test_conforming_fake_class_passes_isinstance(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        class FakeStore:
            @property
            def graph_forest_name(self) -> str:
                return "test"

            async def upsert_node(
                self, node_id: str, labels: set[str], properties: dict[str, Any]
            ) -> None: ...

            async def upsert_edge(
                self, source: str, target: str, edge_type: str, properties: dict[str, Any]
            ) -> None: ...

            async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

            async def get_edge(
                self, source: str, target: str, edge_type: str
            ) -> dict[str, Any] | None: ...

            async def flush(self) -> None: ...

            async def close(self) -> None: ...

        store = FakeStore()
        assert isinstance(store, GraphStore)

    def test_missing_upsert_node_fails_isinstance(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        class BadStore:
            async def upsert_edge(
                self, source: str, target: str, edge_type: str, properties: dict[str, Any]
            ) -> None: ...

            async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

            async def get_edge(
                self, source: str, target: str, edge_type: str
            ) -> dict[str, Any] | None: ...

            async def flush(self) -> None: ...

            async def close(self) -> None: ...

        store = BadStore()
        assert not isinstance(store, GraphStore)

    def test_missing_flush_fails_isinstance(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        class BadStore:
            async def upsert_node(
                self, node_id: str, labels: set[str], properties: dict[str, Any]
            ) -> None: ...

            async def upsert_edge(
                self, source: str, target: str, edge_type: str, properties: dict[str, Any]
            ) -> None: ...

            async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

            async def get_edge(
                self, source: str, target: str, edge_type: str
            ) -> dict[str, Any] | None: ...

            async def close(self) -> None: ...

        store = BadStore()
        assert not isinstance(store, GraphStore)

    def test_missing_graph_forest_name_fails_isinstance(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        class MissingForestName:
            async def upsert_node(
                self, node_id: str, labels: set[str], properties: dict[str, Any]
            ) -> None: ...

            async def upsert_edge(
                self, source: str, target: str, edge_type: str, properties: dict[str, Any]
            ) -> None: ...

            async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

            async def get_edge(
                self, source: str, target: str, edge_type: str
            ) -> dict[str, Any] | None: ...

            async def flush(self) -> None: ...

            async def close(self) -> None: ...

        store = MissingForestName()
        assert not isinstance(store, GraphStore)

    def test_graph_state_conforms_to_graph_store(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert isinstance(graph, GraphStore)

    def test_queryable_store_is_runtime_checkable(self):
        from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

        assert hasattr(QueryableStore, "__protocol_attrs__") or hasattr(
            QueryableStore, "_is_runtime_protocol"
        )

    def test_queryable_missing_supported_dialects_fails(self):
        from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

        class MissingDialects:
            @property
            def graph_forest_name(self) -> str:
                return "test"

            async def upsert_node(
                self, node_id: str, labels: set[str], properties: dict[str, Any]
            ) -> None: ...

            async def upsert_edge(
                self, source: str, target: str, edge_type: str, properties: dict[str, Any]
            ) -> None: ...

            async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

            async def get_edge(
                self, source: str, target: str, edge_type: str
            ) -> dict[str, Any] | None: ...

            async def flush(self) -> None: ...

            async def close(self) -> None: ...

            async def execute_query(
                self,
                query: str,
                params: dict[str, Any] | None = None,
                dialect: str | None = None,
            ) -> list[dict[str, Any]]: ...

        store = MissingDialects()
        assert not isinstance(store, QueryableStore)

    def test_base_graph_store_is_not_queryable(self):
        from amplifier_module_hook_context_intelligence.graph_store import (
            GraphStore,
            QueryableStore,
        )

        class BaseOnly:
            @property
            def graph_forest_name(self) -> str:
                return "test"

            async def upsert_node(
                self, node_id: str, labels: set[str], properties: dict[str, Any]
            ) -> None: ...

            async def upsert_edge(
                self, source: str, target: str, edge_type: str, properties: dict[str, Any]
            ) -> None: ...

            async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

            async def get_edge(
                self, source: str, target: str, edge_type: str
            ) -> dict[str, Any] | None: ...

            async def flush(self) -> None: ...

            async def close(self) -> None: ...

        store = BaseOnly()
        assert isinstance(store, GraphStore)
        assert not isinstance(store, QueryableStore)

    def test_execute_query_signature_has_graph_forest_name(self):
        import inspect

        from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

        sig = inspect.signature(QueryableStore.execute_query)
        assert "graph_forest_name" in sig.parameters, (
            "QueryableStore.execute_query must declare a graph_forest_name parameter"
        )
        param = sig.parameters["graph_forest_name"]
        assert param.default is None, "graph_forest_name must default to None"


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------
class TestConstructor:
    """Constructor wiring and graph_forest_name read-only property."""

    def test_graph_forest_name_returns_value(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="my-project",
        )
        assert store.graph_forest_name == "my-project"

    def test_graph_forest_name_defaults_to_default(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        assert store.graph_forest_name == "default"

    def test_graph_forest_name_is_settable(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        assert store.graph_forest_name == "default"  # None init → property returns "default"
        store.graph_forest_name = "-workspace"
        assert store.graph_forest_name == "-workspace"

    def test_driver_created_on_init(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        assert store._driver is not None

    def test_buffers_empty_on_init(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        assert store._node_buffer == {}
        assert store._edge_buffer == {}

    def test_schema_not_initialized_on_init(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        assert store._schema_initialized is False


# ---------------------------------------------------------------------------
# Shared fixture for buffer-only tests
# ---------------------------------------------------------------------------
@pytest.fixture
async def store():
    """Fresh Neo4jGraphStore for buffer-only tests (no Neo4j interaction)."""
    from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
    yield store
    await store._driver.close()


# ---------------------------------------------------------------------------
# TestBufferWrites
# ---------------------------------------------------------------------------
class TestBufferWrites:
    """In-memory buffer writes with merge semantics (pure dict ops, no Neo4j)."""

    @pytest.mark.asyncio
    async def test_upsert_node_writes_to_buffer(self, store):
        await store.upsert_node("n1", {"Label"}, {"key": "val"})
        assert "n1" in store._node_buffer

    @pytest.mark.asyncio
    async def test_upsert_node_buffer_shape(self, store):
        await store.upsert_node("n1", {"A", "B"}, {"x": 1})
        entry = store._node_buffer["n1"]
        assert entry["id"] == "n1"
        assert entry["labels"] == {"A", "B"}
        assert entry["properties"] == {"x": 1}

    @pytest.mark.asyncio
    async def test_upsert_edge_writes_to_buffer(self, store):
        await store.upsert_edge("a", "b", "REL", {"w": 1})
        assert ("a", "b", "REL") in store._edge_buffer

    @pytest.mark.asyncio
    async def test_upsert_edge_buffer_shape(self, store):
        await store.upsert_edge("a", "b", "REL", {"w": 1})
        entry = store._edge_buffer[("a", "b", "REL")]
        assert entry["source"] == "a"
        assert entry["target"] == "b"
        assert entry["type"] == "REL"
        assert entry["properties"] == {"w": 1}

    @pytest.mark.asyncio
    async def test_upsert_node_merges_labels(self, store):
        await store.upsert_node("n1", {"A"}, {})
        await store.upsert_node("n1", {"B"}, {})
        assert store._node_buffer["n1"]["labels"] == {"A", "B"}

    @pytest.mark.asyncio
    async def test_upsert_node_merges_properties(self, store):
        await store.upsert_node("n1", set(), {"a": 1})
        await store.upsert_node("n1", set(), {"b": 2})
        props = store._node_buffer["n1"]["properties"]
        assert props == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_upsert_node_last_write_wins(self, store):
        await store.upsert_node("n1", set(), {"key": "old"})
        await store.upsert_node("n1", set(), {"key": "new"})
        assert store._node_buffer["n1"]["properties"]["key"] == "new"

    @pytest.mark.asyncio
    async def test_upsert_edge_merges_properties(self, store):
        await store.upsert_edge("a", "b", "REL", {"x": 1})
        await store.upsert_edge("a", "b", "REL", {"y": 2})
        props = store._edge_buffer[("a", "b", "REL")]["properties"]
        assert props == {"x": 1, "y": 2}


# ---------------------------------------------------------------------------
# Live Neo4j fixture (connects to test container, cleans between tests)
# ---------------------------------------------------------------------------
@pytest.fixture
async def neo4j_store():
    """Connect to the live Neo4j test container, clean all data before each test."""
    from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
    try:
        # Clean all data before the test
        async with store._driver.session(database=store._database) as session:
            await session.run("MATCH (n) DETACH DELETE n")
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# TestBufferFirstReads
# ---------------------------------------------------------------------------
class TestBufferFirstReads:
    """Buffer-first reads: check in-memory buffer first, fall back to Neo4j."""

    @pytest.mark.asyncio
    async def test_get_node_returns_buffered_data(self, neo4j_store):
        await neo4j_store.upsert_node(
            PROMPT_NODE_ID, {"Step", "PromptStep"}, {"iteration": 0, "prompt_text": "hello"}
        )
        result = await neo4j_store.get_node(PROMPT_NODE_ID)
        assert result is not None
        assert result["id"] == PROMPT_NODE_ID
        assert result["labels"] == {"Step", "PromptStep"}
        assert result["properties"]["iteration"] == 0
        assert result["properties"]["prompt_text"] == "hello"

    @pytest.mark.asyncio
    async def test_get_edge_returns_buffered_data(self, neo4j_store):
        await neo4j_store.upsert_edge("src", "tgt", "LINKS_TO", {"weight": 42})
        result = await neo4j_store.get_edge("src", "tgt", "LINKS_TO")
        assert result is not None
        assert result["source"] == "src"
        assert result["target"] == "tgt"
        assert result["type"] == "LINKS_TO"
        assert result["properties"]["weight"] == 42

    @pytest.mark.asyncio
    async def test_get_nonexistent_node_returns_none(self, neo4j_store):
        result = await neo4j_store.get_node("does-not-exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_nonexistent_edge_returns_none(self, neo4j_store):
        result = await neo4j_store.get_edge("no-src", "no-tgt", "NO_REL")
        assert result is None

    @pytest.mark.asyncio
    async def test_buffer_wins_over_stale_neo4j(self, neo4j_store):
        """Buffer value must win even after flush writes an older value to Neo4j."""
        # First write + flush to persist in Neo4j
        await neo4j_store.upsert_node("n1", {"Tag"}, {"val": "old"})
        await neo4j_store.flush()
        # Second write (only in buffer, not yet flushed)
        await neo4j_store.upsert_node("n1", {"Tag"}, {"val": "new"})
        result = await neo4j_store.get_node("n1")
        assert result is not None
        assert result["properties"]["val"] == "new"


# ---------------------------------------------------------------------------
# TestFlush
# ---------------------------------------------------------------------------
class TestFlush:
    """Flush writes buffered nodes/edges to Neo4j, clears buffers, restores on failure."""

    @pytest.mark.asyncio
    async def test_flush_writes_nodes_to_neo4j(self, neo4j_store):
        """Upsert a node, flush, verify it exists in Neo4j via raw Cypher MATCH."""
        await neo4j_store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("MATCH (n {node_id: $nid}) RETURN n", nid="n1")
            record = await result.single()
        assert record is not None
        node = record["n"]
        assert node["node_id"] == "n1"
        assert node["name"] == "Alice"
        assert "Person" in node.labels

    @pytest.mark.asyncio
    async def test_flush_writes_edges_to_neo4j(self, neo4j_store):
        """Upsert source+target nodes and edge, flush, verify via raw Cypher."""
        await neo4j_store.upsert_node("src", {"A"}, {})
        await neo4j_store.upsert_node("tgt", {"B"}, {})
        await neo4j_store.upsert_edge("src", "tgt", "KNOWS", {"since": 2020})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (s {node_id: 'src'})-[r:KNOWS]->(t {node_id: 'tgt'}) RETURN r"
            )
            record = await result.single()
        assert record is not None
        rel = record["r"]
        assert rel["since"] == 2020

    @pytest.mark.asyncio
    async def test_flush_clears_node_buffer(self, neo4j_store):
        """After a successful flush the node buffer must be empty."""
        await neo4j_store.upsert_node("n1", {"X"}, {"k": "v"})
        await neo4j_store.flush()
        assert neo4j_store._node_buffer == {}

    @pytest.mark.asyncio
    async def test_flush_clears_edge_buffer(self, neo4j_store):
        """After a successful flush the edge buffer must be empty."""
        await neo4j_store.upsert_node("a", {"X"}, {})
        await neo4j_store.upsert_node("b", {"X"}, {})
        await neo4j_store.upsert_edge("a", "b", "REL", {"w": 1})
        await neo4j_store.flush()
        assert neo4j_store._edge_buffer == {}

    @pytest.mark.asyncio
    async def test_get_node_from_neo4j_after_flush(self, neo4j_store):
        """Buffer empty after flush; get_node reads from Neo4j with full shape."""
        await neo4j_store.upsert_node("n1", {"Person", "Employee"}, {"name": "Bob", "age": 30})
        await neo4j_store.flush()

        # Buffer is empty, so get_node must fall back to Neo4j
        assert neo4j_store._node_buffer == {}
        result = await neo4j_store.get_node("n1")
        assert result is not None
        assert result["id"] == "n1"
        assert result["labels"] == {"Person", "Employee"}
        assert result["properties"]["name"] == "Bob"
        assert result["properties"]["age"] == 30

    @pytest.mark.asyncio
    async def test_get_edge_from_neo4j_after_flush(self, neo4j_store):
        """Buffer empty after flush; get_edge reads from Neo4j with full shape."""
        await neo4j_store.upsert_node("s", {"X"}, {})
        await neo4j_store.upsert_node("t", {"X"}, {})
        await neo4j_store.upsert_edge("s", "t", "LINKS", {"weight": 99})
        await neo4j_store.flush()

        # Buffer is empty, so get_edge must fall back to Neo4j
        assert neo4j_store._edge_buffer == {}
        result = await neo4j_store.get_edge("s", "t", "LINKS")
        assert result is not None
        assert result["source"] == "s"
        assert result["target"] == "t"
        assert result["type"] == "LINKS"
        assert result["properties"]["weight"] == 99
        # Internal fields must be stripped, consistent with get_node behavior
        assert "graph_forest_name" not in result["properties"]

    @pytest.mark.asyncio
    async def test_flush_empty_buffer_is_noop(self, neo4j_store):
        """Flushing with empty buffers should succeed without error."""
        await neo4j_store.flush()  # must not raise
        assert neo4j_store._node_buffer == {}
        assert neo4j_store._edge_buffer == {}

    @pytest.mark.asyncio
    async def test_flush_restores_buffers_on_failure(self, neo4j_store):
        """On flush failure, buffers are restored and no exception is raised."""
        await neo4j_store.upsert_node("n1", {"Tag"}, {"k": "v"})
        await neo4j_store.upsert_edge("a", "b", "REL", {"w": 1})

        # Sabotage the driver to force a failure
        real_driver = neo4j_store._driver
        neo4j_store._driver = None

        await neo4j_store.flush()  # must not raise

        # Buffers must be restored
        assert "n1" in neo4j_store._node_buffer
        assert ("a", "b", "REL") in neo4j_store._edge_buffer

        # Restore driver so fixture teardown can close it
        neo4j_store._driver = real_driver

    @pytest.mark.asyncio
    async def test_flush_enrichment_uses_match_not_merge(self, neo4j_store):
        """Empty-label upsert after labeled upsert must produce ONE node, not two.

        Step 1: upsert with labels (OrchestratorRun) + flush -> creates the node.
        Step 2: upsert same node_id with empty labels + flush -> enriches via MATCH.
        Result: exactly one node, original label preserved, enriched props applied.
        No 'Session' label should appear from a default fallback.
        """
        node_id = "enrich-test-node-1"

        # Step 1: create node with a real label
        await neo4j_store.upsert_node(node_id, {"OrchestratorRun"}, {"status": "running"})
        await neo4j_store.flush()

        # Step 2: enrich with empty labels (should MATCH, not MERGE with default label)
        await neo4j_store.upsert_node(node_id, set(), {"status": "complete", "extra": "value"})
        await neo4j_store.flush()

        # Verify exactly one node exists
        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n, labels(n) AS lbls",
                nid=node_id,
            )
            records = await result.fetch(10)

        assert len(records) == 1, (
            f"Expected exactly 1 node, got {len(records)}. "
            "Empty-label upsert must enrich via MATCH, not create a new node."
        )
        node = records[0]["n"]
        labels = records[0]["lbls"]

        # Original label is preserved
        assert "OrchestratorRun" in labels, "Original label OrchestratorRun must be preserved"
        # No spurious 'Session' label from old fallback
        assert "Session" not in labels, "No 'Session' label should appear from empty-label upsert"
        # Enriched properties applied
        assert node["status"] == "complete", "Enriched property status must overwrite"
        assert node["extra"] == "value", "Enriched extra property must be present"

    @pytest.mark.asyncio
    async def test_flush_enrichment_skips_nonexistent_node(self, neo4j_store):
        """Empty-label upsert on a node that doesn't exist must be a silent no-op.

        MATCH finds nothing when the node doesn't exist, so nothing is created.
        No crash, no node created.
        """
        node_id = "enrich-test-nonexistent-99"

        # Upsert with empty labels when node doesn't exist in Neo4j
        await neo4j_store.upsert_node(node_id, set(), {"some_prop": "some_value"})
        await neo4j_store.flush()

        # Verify no node was created
        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n",
                nid=node_id,
            )
            record = await result.single()

        assert record is None, (
            "Empty-label upsert on nonexistent node must create nothing (silent no-op)."
        )


# ---------------------------------------------------------------------------
# TestSchemaInitialization
# ---------------------------------------------------------------------------
class TestSchemaInitialization:
    """Idempotent index/constraint creation on first flush via _schema_initialized flag."""

    @pytest.mark.asyncio
    async def test_schema_initialized_after_first_flush(self, neo4j_store):
        """After upserting and flushing, _schema_initialized must be True."""
        await neo4j_store.upsert_node("n1", {"Tag"}, {"k": "v"})
        await neo4j_store.flush()
        assert neo4j_store._schema_initialized is True

    @pytest.mark.asyncio
    async def test_schema_flag_prevents_rerun(self, neo4j_store):
        """Two flushes: second doesn't fail and flag stays True."""
        await neo4j_store.upsert_node("n1", {"Tag"}, {"k": "v"})
        await neo4j_store.flush()
        assert neo4j_store._schema_initialized is True

        # Second flush with new data must also succeed
        await neo4j_store.upsert_node("n2", {"Tag"}, {"k": "v2"})
        await neo4j_store.flush()
        assert neo4j_store._schema_initialized is True

    @pytest.mark.asyncio
    async def test_node_id_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on node_id must exist (SHOW INDEXES check)."""
        await neo4j_store.upsert_node("n1", {"Tag"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["properties"] is not None and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No index with node_id in properties. Found: {records}"

    def test_session_index_uses_descriptive_name(self):
        """_ensure_schema must use 'idx_session_node_id', not the generic 'idx_node_id_any'."""
        import inspect

        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        source = inspect.getsource(Neo4jGraphStore._ensure_schema)
        assert "idx_session_node_id" in source, (
            "_ensure_schema must use idx_session_node_id for Session-label index"
        )
        assert "idx_node_id_any" not in source, (
            "_ensure_schema must not use the old misleading name idx_node_id_any"
        )

    @pytest.mark.asyncio
    async def test_forest_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on graph_forest_name must exist (SHOW INDEXES check)."""
        await neo4j_store.upsert_node("n1", {"Tag"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["properties"] is not None and "graph_forest_name" in record["properties"]
            for record in records
        )
        assert found, f"No index with graph_forest_name in properties. Found: {records}"

    @pytest.mark.asyncio
    async def test_orchestrator_run_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on OrchestratorRun.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"OrchestratorRun"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "OrchestratorRun" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No index for OrchestratorRun.node_id. Found: {records}"

    @pytest.mark.asyncio
    async def test_step_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on Step.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"Step"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "Step" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No index for Step.node_id. Found: {records}"

    @pytest.mark.asyncio
    async def test_tool_execution_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on ToolExecution.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"ToolExecution"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "ToolExecution" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No index for ToolExecution.node_id. Found: {records}"

    @pytest.mark.asyncio
    async def test_event_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on Event.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"Event"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "Event" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No index for Event.node_id. Found: {records}"


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() must flush remaining buffers before closing the async driver."""

    @pytest.mark.asyncio
    async def test_close_flushes_before_closing(self):
        """Upsert a node, close (without explicit flush), verify data persisted via fresh driver."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        # Clean data first
        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        async with store._driver.session(database=store._database) as session:
            await session.run("MATCH (n) DETACH DELETE n")

        # Upsert a node (stays in buffer, no explicit flush)
        await store.upsert_node("close-n1", {"Marker"}, {"val": "persisted"})
        await store.close()

        # Verify data persisted via a fresh driver
        store2 = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        try:
            async with store2._driver.session(database=store2._database) as session:
                result = await session.run("MATCH (n {node_id: $nid}) RETURN n", nid="close-n1")
                record = await result.single()
            assert record is not None, "Node should be persisted after close()"
            node = record["n"]
            assert node["val"] == "persisted"
        finally:
            await store2._driver.close()


# ---------------------------------------------------------------------------
# TestCloseEventLoopHandling
# ---------------------------------------------------------------------------
class TestCloseEventLoopHandling:
    """close() must handle event-loop mismatch RuntimeError from driver.close() gracefully.

    Defense-in-depth layer: even if the caller of cleanup() somehow reaches
    ``await store.close()`` while a loop incompatibility exists, the error must
    be swallowed (data was already flushed) rather than surfaced as an unhandled
    exception.  Non-loop RuntimeErrors must still be re-raised.
    """

    @pytest.mark.asyncio
    async def test_close_swallows_different_loop_runtime_error(self):
        """close() must not propagate RuntimeError('attached to a different loop').

        Fails with current code (no exception handling around driver.close()).
        Passes after the fix (try/except around driver.close()).
        """
        from unittest.mock import AsyncMock, MagicMock

        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri="bolt://localhost:9999", auth=None)
        # Empty buffers → flush() is an early-exit no-op (doesn't touch driver)
        assert not store._node_buffer
        assert not store._edge_buffer

        # Simulate the real driver raising the loop-mismatch error
        mock_driver = MagicMock()
        mock_driver.close = AsyncMock(
            side_effect=RuntimeError(
                "Task <Task pending> got Future <Future pending> attached to a different loop"
            )
        )
        store._driver = mock_driver

        # Must NOT raise — after the fix this is caught and logged as debug
        await store.close()

    @pytest.mark.asyncio
    async def test_close_reraises_unrelated_runtime_errors(self):
        """close() must re-raise RuntimeError that is NOT about a different loop."""
        from unittest.mock import AsyncMock, MagicMock

        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri="bolt://localhost:9999", auth=None)
        mock_driver = MagicMock()
        mock_driver.close = AsyncMock(
            side_effect=RuntimeError("Some completely unrelated runtime error")
        )
        store._driver = mock_driver

        with pytest.raises(RuntimeError, match="unrelated runtime error"):
            await store.close()


# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------
class TestPersistence:
    """Data written and closed survives reopen with a new store instance."""

    @pytest.mark.asyncio
    async def test_data_survives_close_and_reopen(self):
        """Write nodes+edges, close, reopen new store, verify full shapes."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        # Clean data first
        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        async with store._driver.session(database=store._database) as session:
            await session.run("MATCH (n) DETACH DELETE n")

        # Write nodes and edges
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.upsert_node(
            RUN_NODE_ID,
            {"OrchestratorRun"},
            {"session_id": SESSION_ID, "run_number": 1},
        )
        await store.upsert_edge(
            SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1, "custom": "data"}
        )
        await store.close()

        # Reopen with a new store instance
        store2 = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        try:
            # Verify get_node returns full shape with labels Session+Root
            node = await store2.get_node(SESSION_NODE_ID)
            assert node is not None, "Session node should survive close+reopen"
            assert node["id"] == SESSION_NODE_ID
            assert node["labels"] == {"Session", "Root"}
            assert node["properties"]["session_id"] == SESSION_ID
            assert node["properties"]["status"] == "running"

            # Verify get_edge returns full shape with properties
            edge = await store2.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
            assert edge is not None, "Edge should survive close+reopen"
            assert edge["source"] == SESSION_NODE_ID
            assert edge["target"] == RUN_NODE_ID
            assert edge["type"] == "HAS_RUN"
            assert edge["properties"]["seq"] == 1
            assert edge["properties"]["custom"] == "data"
        finally:
            await store2._driver.close()


# ---------------------------------------------------------------------------
# Seeded Neo4j fixture (reference graph pre-loaded)
# ---------------------------------------------------------------------------
@pytest.fixture
async def seeded_neo4j_store(neo4j_store):
    """neo4j_store with all reference nodes and edges upserted and flushed."""
    for node_id, labels, props in reference_nodes():
        await neo4j_store.upsert_node(node_id, labels, props)
    for src, tgt, etype, props in reference_edges():
        await neo4j_store.upsert_edge(src, tgt, etype, props)
    await neo4j_store.flush()
    yield neo4j_store


# ---------------------------------------------------------------------------
# TestExecuteQuery
# ---------------------------------------------------------------------------
class TestExecuteQuery:
    """Raw Cypher query execution with dialect validation and forest param injection."""

    def test_supported_dialects_returns_frozenset(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        dialects = store.supported_dialects
        assert isinstance(dialects, frozenset)
        assert "cypher" in dialects

    @pytest.mark.asyncio
    async def test_execute_query_returns_list_of_dicts(self, seeded_neo4j_store):
        result = await seeded_neo4j_store.execute_query(
            "MATCH (n) RETURN n.node_id AS node_id LIMIT 10"
        )
        assert isinstance(result, list)
        assert len(result) > 0
        for row in result:
            assert isinstance(row, dict)

    @pytest.mark.asyncio
    async def test_execute_query_with_explicit_cypher_dialect(self, seeded_neo4j_store):
        result = await seeded_neo4j_store.execute_query(
            "MATCH (n) RETURN n.node_id AS node_id LIMIT 10",
            dialect="cypher",
        )
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_execute_query_with_none_dialect_uses_default(self, seeded_neo4j_store):
        result = await seeded_neo4j_store.execute_query(
            "MATCH (n) RETURN n.node_id AS node_id LIMIT 10",
            dialect=None,
        )
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_execute_query_with_params(self, seeded_neo4j_store):
        result = await seeded_neo4j_store.execute_query(
            "MATCH (n {node_id: $node_id}) RETURN n.node_id AS node_id",
            params={"node_id": SESSION_NODE_ID},
        )
        assert len(result) == 1
        assert result[0]["node_id"] == SESSION_NODE_ID

    @pytest.mark.asyncio
    async def test_execute_query_with_invalid_dialect_raises(self, seeded_neo4j_store):
        with pytest.raises(ValueError, match="Unsupported dialect"):
            await seeded_neo4j_store.execute_query(
                "SELECT * FROM nodes",
                dialect="sql",
            )

    @pytest.mark.asyncio
    async def test_execute_query_injects_graph_forest_name_param(self, seeded_neo4j_store):
        # Verify execute_query injects $graph_forest_name param automatically.
        # Use a pure RETURN query to test injection without depending on seeded data.
        result = await seeded_neo4j_store.execute_query(
            "RETURN $graph_forest_name AS forest",
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["forest"] == seeded_neo4j_store.graph_forest_name

    @pytest.mark.asyncio
    async def test_execute_query_wildcard_forest_skips_injection(self, seeded_neo4j_store):
        """When graph_forest_name='*', $graph_forest_name is NOT injected into params."""
        from neo4j.exceptions import ClientError

        # A query referencing $graph_forest_name errors with wildcard because
        # the param is NOT injected — proving the skip branch works.
        with pytest.raises(ClientError, match="graph_forest_name"):
            await seeded_neo4j_store.execute_query(
                "RETURN $graph_forest_name AS forest",
                graph_forest_name="*",
            )

    @pytest.mark.asyncio
    async def test_execute_query_explicit_forest_overrides_default(self, seeded_neo4j_store):
        """Explicit graph_forest_name param overrides the instance default."""
        result = await seeded_neo4j_store.execute_query(
            "RETURN $graph_forest_name AS forest",
            graph_forest_name="custom-override",
        )
        assert len(result) == 1
        assert result[0]["forest"] == "custom-override"


# ---------------------------------------------------------------------------
# TestForestWrites
# ---------------------------------------------------------------------------
class TestForestWrites:
    """flush() must stamp graph_forest_name on all nodes and edges."""

    @pytest.mark.asyncio
    async def test_flush_stamps_forest_on_nodes(self, neo4j_store):
        """After flush, the node in Neo4j must carry the store's graph_forest_name."""
        await neo4j_store.upsert_node("fw-n1", {"Session"}, {"key": "val"})
        await neo4j_store.flush()
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run(
                "MATCH (n {node_id: 'fw-n1'}) RETURN n.graph_forest_name AS forest"
            )
            record = await result.single()
        assert record is not None
        assert record["forest"] == neo4j_store.graph_forest_name

    @pytest.mark.asyncio
    async def test_flush_stamps_forest_on_edges(self, neo4j_store):
        """After flush, the relationship in Neo4j must carry the store's graph_forest_name."""
        await neo4j_store.upsert_node("fw-a", {"Node"}, {})
        await neo4j_store.upsert_node("fw-b", {"Node"}, {})
        await neo4j_store.upsert_edge("fw-a", "fw-b", "KNOWS", {"weight": 1})
        await neo4j_store.flush()
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run(
                "MATCH ()-[r:`KNOWS`]->() RETURN r.graph_forest_name AS forest"
            )
            record = await result.single()
        assert record is not None
        assert record["forest"] == neo4j_store.graph_forest_name


# ---------------------------------------------------------------------------
# TestForestScoping
# ---------------------------------------------------------------------------
class TestForestScoping:
    """Two-store isolation: data written to one forest is NOT visible to another."""

    @pytest.mark.asyncio
    async def test_data_isolated_between_forests(self, neo4j_store):
        """Node upserted in forest-a must NOT appear when querying via forest-b.

        Creates two independent Neo4jGraphStore instances pointing at the same
        Neo4j instance but with different graph_forest_name values.  Confirms
        that execute_query scopes results to the store's own forest.
        """
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store_a = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-a",
        )
        store_b = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-b",
        )
        try:
            # Write a node via store_a (forest-a) and flush to Neo4j
            await store_a.upsert_node("isolation-n1", {"Tag"}, {"val": "from-a"})
            await store_a.flush()

            # Query via store_b — forest-b has no nodes, must return empty
            result_b = await store_b.execute_query(
                "MATCH (n) WHERE n.graph_forest_name = $graph_forest_name "
                "RETURN n.node_id AS node_id"
            )
            assert result_b == [], f"forest-b should see no nodes but got: {result_b}"

            # Query via store_a — must see the node written by store_a
            result_a = await store_a.execute_query(
                "MATCH (n) WHERE n.graph_forest_name = $graph_forest_name "
                "RETURN n.node_id AS node_id"
            )
            node_ids = [r["node_id"] for r in result_a]
            assert "isolation-n1" in node_ids, (
                f"forest-a should contain isolation-n1 but got: {node_ids}"
            )
        finally:
            await store_a.close()
            await store_b.close()

    @pytest.mark.asyncio
    async def test_wildcard_forest_spans_all(self, neo4j_store):
        """After seeding two forests, a wildcard query returns nodes from both.

        With graph_forest_name='*', execute_query does NOT inject
        $graph_forest_name into params.  The query therefore sees all nodes
        regardless of which forest they were written into.
        """
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store_a = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-a",
        )
        store_b = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-b",
        )
        try:
            # Seed one node in each forest
            await store_a.upsert_node("wildcard-n1", {"Tag"}, {"val": "from-a"})
            await store_a.flush()

            await store_b.upsert_node("wildcard-n2", {"Tag"}, {"val": "from-b"})
            await store_b.flush()

            # Wildcard query via store_a — query does NOT reference
            # $graph_forest_name because it is not injected for '*'
            result = await store_a.execute_query(
                "MATCH (n) WHERE n.node_id IN ['wildcard-n1', 'wildcard-n2'] "
                "RETURN n.node_id AS node_id ORDER BY n.node_id",
                graph_forest_name="*",
            )
            node_ids = [r["node_id"] for r in result]
            assert "wildcard-n1" in node_ids, (
                f"Expected wildcard-n1 in wildcard results but got: {node_ids}"
            )
            assert "wildcard-n2" in node_ids, (
                f"Expected wildcard-n2 in wildcard results but got: {node_ids}"
            )
        finally:
            await store_a.close()
            await store_b.close()


# ---------------------------------------------------------------------------
# TestGetEdgeForestFilter
# ---------------------------------------------------------------------------
class TestGetEdgeForestFilter:
    """get_edge Neo4j fallback must filter edges by graph_forest_name on the relationship."""

    @pytest.mark.asyncio
    async def test_get_edge_does_not_leak_across_forests(self, neo4j_store):
        """Edge written in forest-a must NOT be visible via get_edge in forest-b.

        Scenario that exposes the real bug: both forests write to the SAME node_ids
        (MERGE means the nodes end up with the LAST writer's forest name).  When
        store_b flushes AFTER store_a, the shared nodes carry graph_forest_name="forest-b".
        Without a relationship-level forest filter, store_b.get_edge would find the
        relationship written by store_a because the node filter now matches.

        Verification order matters: store_a must confirm its edge is visible BEFORE
        store_b overwrites the shared nodes — after that overwrite the nodes carry
        forest-b, so the node-level forest filter naturally excludes store_a too.
        The critical isolation assertion is that store_b sees None even though it
        "owns" the nodes at query time.
        """
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store_a = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-a",
        )
        store_b = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-b",
        )
        try:
            # store_a writes both shared nodes AND an edge, then flushes
            await store_a.upsert_node("ef-filter-src", {"Node"}, {})
            await store_a.upsert_node("ef-filter-tgt", {"Node"}, {})
            await store_a.upsert_edge("ef-filter-src", "ef-filter-tgt", "LINKED", {"w": 1})
            await store_a.flush()

            # Verify store_a can read its own edge (buffer cleared, reads from Neo4j)
            assert store_a._edge_buffer == {}
            result_a_before = await store_a.get_edge("ef-filter-src", "ef-filter-tgt", "LINKED")
            assert result_a_before is not None, (
                "Edge must be visible in forest-a before node overwrite"
            )
            assert result_a_before["properties"]["w"] == 1

            # store_b writes to the SAME node_ids (no edge), then flushes AFTER store_a.
            # This causes the Neo4j nodes to have graph_forest_name="forest-b" (last-write wins).
            # Without a relationship-level filter, store_b.get_edge would now wrongly
            # return the edge written by store_a (node filter now matches, but edge belongs to a).
            await store_b.upsert_node("ef-filter-src", {"Node"}, {})
            await store_b.upsert_node("ef-filter-tgt", {"Node"}, {})
            await store_b.flush()

            # store_b has empty edge buffer — must fall back to Neo4j.
            # This is the critical assertion: the relationship's graph_forest_name="forest-a"
            # must prevent the edge from being returned to forest-b even though the nodes
            # now have graph_forest_name="forest-b" and would match the node filter.
            assert store_b._edge_buffer == {}
            result_b = await store_b.get_edge("ef-filter-src", "ef-filter-tgt", "LINKED")
            assert result_b is None, (
                f"Edge from forest-a must NOT be visible via get_edge in forest-b "
                f"(relationship-level forest filter required), but got: {result_b}"
            )
        finally:
            await store_a.close()
            await store_b.close()


# ---------------------------------------------------------------------------
# TestStandingRuleDocstring
# ---------------------------------------------------------------------------
class TestStandingRuleDocstring:
    """Module docstring must contain the standing rule for skill synchronization."""

    def test_docstring_contains_standing_rule_section(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None, "Module docstring must not be None"
        assert "STANDING RULE" in doc

    def test_docstring_references_skill_path(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None
        assert "skills/context-intelligence-neo4j-search/SKILL.md" in doc

    def test_docstring_lists_all_schema_triggers(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None
        required_triggers = [
            "node labels",
            "relationship types",
            "property keys",
            "graph_forest_name",
            "indexed properties",
            "index definitions",
        ]
        for trigger in required_triggers:
            assert trigger in doc, f"Docstring missing trigger: {trigger!r}"

    def test_docstring_preserves_original_description(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None
        assert "Neo4jGraphStore" in doc
        assert "buffer-first reads with async Neo4j persistence" in doc


# ---------------------------------------------------------------------------
# TestTimestampConversion
# ---------------------------------------------------------------------------
class TestTimestampConversion:
    """_convert_timestamps() converts *_at ISO-8601 strings to native Neo4j DateTime."""

    @pytest.mark.asyncio
    async def test_node_occurred_at_stored_as_datetime(self, neo4j_store):
        """occurred_at on a node is stored as neo4j.time.DateTime after flush."""
        import neo4j.time

        await neo4j_store.upsert_node("ts-n1", {"Step"}, {"occurred_at": "2026-01-15T10:00:01Z"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n.occurred_at AS ts", nid="ts-n1"
            )
            record = await result.single()
        assert record is not None
        assert isinstance(record["ts"], neo4j.time.DateTime), (
            f"Expected neo4j.time.DateTime but got {type(record['ts'])}: {record['ts']}"
        )

    @pytest.mark.asyncio
    async def test_node_started_at_stored_as_datetime(self, neo4j_store):
        """started_at on a node is stored as neo4j.time.DateTime after flush."""
        import neo4j.time

        await neo4j_store.upsert_node("ts-n2", {"Session"}, {"started_at": "2026-01-15T10:00:00Z"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n.started_at AS ts", nid="ts-n2"
            )
            record = await result.single()
        assert record is not None
        assert isinstance(record["ts"], neo4j.time.DateTime), (
            f"Expected neo4j.time.DateTime but got {type(record['ts'])}: {record['ts']}"
        )

    @pytest.mark.asyncio
    async def test_edge_occurred_at_stored_as_datetime(self, neo4j_store):
        """occurred_at on an edge is stored as neo4j.time.DateTime after flush."""
        import neo4j.time

        await neo4j_store.upsert_node("ts-src", {"Node"}, {})
        await neo4j_store.upsert_node("ts-tgt", {"Node"}, {})
        await neo4j_store.upsert_edge(
            "ts-src", "ts-tgt", "OCCURRED", {"occurred_at": "2026-01-15T10:00:02Z"}
        )
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (s {node_id: 'ts-src'})-[r:OCCURRED]->(t {node_id: 'ts-tgt'}) "
                "RETURN r.occurred_at AS ts"
            )
            record = await result.single()
        assert record is not None
        assert isinstance(record["ts"], neo4j.time.DateTime), (
            f"Expected neo4j.time.DateTime but got {type(record['ts'])}: {record['ts']}"
        )

    def test_non_at_properties_unchanged(self):
        """Non-_at string properties are not converted by _convert_timestamps."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        props = {"name": "Alice", "status": "active", "count": 42}
        result = Neo4jGraphStore._convert_timestamps(props)
        assert result["name"] == "Alice"
        assert result["status"] == "active"
        assert result["count"] == 42

    def test_malformed_timestamp_passes_through_as_string(self):
        """Malformed _at string values pass through unchanged without raising."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        props = {"occurred_at": "not-a-valid-date"}
        result = Neo4jGraphStore._convert_timestamps(props)
        assert result["occurred_at"] == "not-a-valid-date"

    def test_empty_string_timestamp_passes_through(self):
        """Empty string _at values pass through unchanged without raising."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        props = {"occurred_at": ""}
        result = Neo4jGraphStore._convert_timestamps(props)
        assert result["occurred_at"] == ""


# ---------------------------------------------------------------------------
# TestSanitizeProperties
# ---------------------------------------------------------------------------
class TestSanitizeProperties:
    """_sanitize_properties() makes all values Neo4j-compatible."""

    def _sanitize(self, props: dict[str, Any]) -> dict[str, Any]:
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        return Neo4jGraphStore._sanitize_properties(props)

    # -- Primitives pass through unchanged --

    def test_string_passes_through(self):
        assert self._sanitize({"k": "hello"}) == {"k": "hello"}

    def test_int_passes_through(self):
        assert self._sanitize({"k": 42}) == {"k": 42}

    def test_float_passes_through(self):
        assert self._sanitize({"k": 3.14}) == {"k": 3.14}

    def test_bool_passes_through(self):
        assert self._sanitize({"k": True}) == {"k": True}

    def test_empty_string_passes_through(self):
        assert self._sanitize({"k": ""}) == {"k": ""}

    # -- None values are dropped --

    def test_none_dropped(self):
        result = self._sanitize({"k": None, "keep": "yes"})
        assert "k" not in result
        assert result["keep"] == "yes"

    def test_all_none_returns_empty(self):
        assert self._sanitize({"a": None, "b": None}) == {}

    # -- Homogeneous primitive lists pass through --

    def test_list_of_strings_passes_through(self):
        assert self._sanitize({"k": ["a", "b"]}) == {"k": ["a", "b"]}

    def test_list_of_ints_passes_through(self):
        assert self._sanitize({"k": [1, 2, 3]}) == {"k": [1, 2, 3]}

    def test_list_of_bools_passes_through(self):
        assert self._sanitize({"k": [True, False]}) == {"k": [True, False]}

    # -- Dicts are JSON-serialized --

    def test_dict_json_serialized(self):
        import json

        result = self._sanitize({"metadata": {"bundle": "test", "version": 1}})
        parsed = json.loads(result["metadata"])
        assert parsed == {"bundle": "test", "version": 1}

    def test_nested_dict_json_serialized(self):
        import json

        result = self._sanitize({"config": {"a": {"b": {"c": 1}}}})
        parsed = json.loads(result["config"])
        assert parsed == {"a": {"b": {"c": 1}}}

    def test_empty_dict_json_serialized(self):
        result = self._sanitize({"metadata": {}})
        assert result["metadata"] == "{}"

    # -- Mixed/nested lists are JSON-serialized --

    def test_list_with_dict_json_serialized(self):
        import json

        result = self._sanitize({"items": [{"id": 1}, {"id": 2}]})
        parsed = json.loads(result["items"])
        assert parsed == [{"id": 1}, {"id": 2}]

    def test_empty_list_json_serialized(self):
        """Empty list is JSON-serialized (cannot determine element type)."""
        result = self._sanitize({"items": []})
        assert result["items"] == "[]"

    def test_list_with_none_json_serialized(self):
        import json

        result = self._sanitize({"items": [1, None, 3]})
        parsed = json.loads(result["items"])
        assert parsed == [1, None, 3]

    # -- Non-standard types are stringified --

    def test_datetime_stringified(self):
        from datetime import datetime

        dt = datetime(2026, 1, 15, 10, 0, 0)
        result = self._sanitize({"ts": dt})
        assert isinstance(result["ts"], str)
        assert "2026" in result["ts"]

    # -- Mixed properties --

    def test_mixed_properties_all_handled(self):
        import json

        props = {
            "name": "Alice",
            "count": 42,
            "active": True,
            "metadata": {"bundle": "test"},
            "tags": ["a", "b"],
            "nested_list": [{"x": 1}],
            "gone": None,
        }
        result = self._sanitize(props)
        assert result["name"] == "Alice"
        assert result["count"] == 42
        assert result["active"] is True
        assert json.loads(result["metadata"]) == {"bundle": "test"}
        assert result["tags"] == ["a", "b"]
        assert json.loads(result["nested_list"]) == [{"x": 1}]
        assert "gone" not in result

    # -- The real-world SessionHandler case --

    def test_session_metadata_dict_serialized(self):
        """The exact pattern that caused the Map{} error in SessionHandler."""
        import json

        props = {
            "started_at": "2026-01-15T10:00:00Z",
            "status": "running",
            "metadata": {"bundle_name": "foundation", "session_type": "interactive"},
        }
        result = self._sanitize(props)
        assert result["started_at"] == "2026-01-15T10:00:00Z"
        assert result["status"] == "running"
        assert isinstance(result["metadata"], str)
        parsed = json.loads(result["metadata"])
        assert parsed["bundle_name"] == "foundation"

    # -- Integration: flush pipeline ordering --

    @pytest.mark.asyncio
    async def test_flush_handles_nested_dict_in_node_properties(self, neo4j_store):
        """Node with a dict property flushes without Map{} error."""
        await neo4j_store.upsert_node(
            "san-n1",
            {"Session"},
            {"status": "running", "metadata": {"key": "value", "nested": {"deep": True}}},
        )
        # This would raise "Property values can only be of primitive types"
        # without _sanitize_properties
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n.metadata AS meta", nid="san-n1"
            )
            record = await result.single()
        assert record is not None
        import json

        parsed = json.loads(record["meta"])
        assert parsed == {"key": "value", "nested": {"deep": True}}

    @pytest.mark.asyncio
    async def test_flush_handles_nested_dict_in_edge_properties(self, neo4j_store):
        """Edge with a dict property flushes without Map{} error."""
        await neo4j_store.upsert_node("san-src", {"Node"}, {})
        await neo4j_store.upsert_node("san-tgt", {"Node"}, {})
        await neo4j_store.upsert_edge(
            "san-src", "san-tgt", "HAS_CONTEXT", {"config": {"timeout": 30}}
        )
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (s {node_id: 'san-src'})-[r:HAS_CONTEXT]->(t {node_id: 'san-tgt'}) "
                "RETURN r.config AS cfg"
            )
            record = await result.single()
        assert record is not None
        import json

        parsed = json.loads(record["cfg"])
        assert parsed == {"timeout": 30}

    @pytest.mark.asyncio
    async def test_flush_drops_none_property_values(self, neo4j_store):
        """None values are dropped during flush, not written to Neo4j."""
        await neo4j_store.upsert_node("san-none", {"Tag"}, {"keep": "yes", "drop": None})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n.keep AS keep, n.drop AS drop",
                nid="san-none",
            )
            record = await result.single()
        assert record is not None
        assert record["keep"] == "yes"
        assert record["drop"] is None  # Neo4j returns null for missing props
