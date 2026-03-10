"""Tests for Neo4jGraphStore – protocol conformance and skeleton verification."""

from __future__ import annotations

import pytest

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

    def test_graph_forest_name_is_readonly(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
        with pytest.raises(AttributeError):
            store.graph_forest_name = "new-value"  # type: ignore[assignment]

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
        # Query uses $graph_forest_name — results prove injection worked
        # (no rows would return if the param wasn't injected by execute_query)
        result = await seeded_neo4j_store.execute_query(
            "MATCH (n) WHERE n.graph_forest_name = $graph_forest_name RETURN n.node_id AS node_id",
        )
        assert isinstance(result, list)
        assert len(result) > 0
        for row in result:
            assert row["node_id"] is not None

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
