"""Integration tests for all 8 edge types with occurred_at and native DateTime verification.

Verifies that each of the 8 canonical edge types (HAS_RUN, HAS_STEP, TRIGGERED, NEXT,
PARALLEL_WITH, SPAWNED, SUBSESSION_OF, HAS_EVENT) can be written to and read back from
a live Neo4j instance with occurred_at stored as native neo4j.time.DateTime.
"""

from __future__ import annotations

from typing import Any

import neo4j.time
import pytest

from tests.conftest import (
    ASSISTANT_STEP_NODE_ID,
    CHILD_SESSION_NODE_ID,
    DELEGATION_TE_NODE_ID,
    EVENT_NODE_ID,
    NEO4J_AUTH,
    NEO4J_DATABASE,
    NEO4J_URI,
    PROMPT_NODE_ID,
    RUN_NODE_ID,
    SESSION_NODE_ID,
    TOOL_NODE_2_ID,
    TOOL_NODE_ID,
    reference_edges,
    reference_nodes,
)


# ---------------------------------------------------------------------------
# seeded_store fixture
# ---------------------------------------------------------------------------
@pytest.fixture
async def seeded_store():
    """Connect to live Neo4j, clean all data, seed all 9 nodes and 8 edges, then flush."""
    from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
    try:
        # Clean all data before seeding
        async with store._driver.session(database=store._database) as session:
            await session.run("MATCH (n) DETACH DELETE n")

        # Seed all reference nodes and edges
        for node_id, labels, props in reference_nodes():
            await store.upsert_node(node_id, labels, props)
        for src, tgt, etype, props in reference_edges():
            await store.upsert_edge(src, tgt, etype, props)
        await store.flush()

        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# _assert_edge_exists helper
# ---------------------------------------------------------------------------
async def _assert_edge_exists(
    store: Any,
    source: str,
    target: str,
    edge_type: str,
    expected_seq: int | None = None,
) -> None:
    """Verify an edge exists in Neo4j with correct direction and occurred_at as Neo4jDateTime.

    Args:
        store: A Neo4jGraphStore instance with an active driver.
        source: node_id of the source node.
        target: node_id of the target node.
        edge_type: Expected relationship type string.
        expected_seq: If provided, also assert r.seq equals this value.
    """
    async with store._driver.session(database=store._database) as session:
        result = await session.run(
            "MATCH (s {node_id: $source})-[r]->(t {node_id: $target}) "
            "WHERE type(r) = $edge_type "
            "RETURN r",
            source=source,
            target=target,
            edge_type=edge_type,
        )
        record = await result.single()

    assert record is not None, (
        f"Expected edge ({source!r})-[:{edge_type}]->({target!r}) to exist in Neo4j, "
        f"but no matching relationship was found."
    )
    rel = record["r"]

    # Verify occurred_at is present and is a native Neo4j DateTime
    assert "occurred_at" in rel, (
        f"Edge {edge_type} ({source!r}→{target!r}) is missing occurred_at property"
    )
    assert isinstance(rel["occurred_at"], neo4j.time.DateTime), (
        f"Expected neo4j.time.DateTime for occurred_at on {edge_type} edge, "
        f"got {type(rel['occurred_at']).__name__}: {rel['occurred_at']!r}"
    )

    # Verify seq if provided
    if expected_seq is not None:
        assert rel["seq"] == expected_seq, (
            f"Expected seq={expected_seq} on {edge_type} edge, got {rel['seq']!r}"
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeHasRun
# ---------------------------------------------------------------------------
class TestEdgeTypeHasRun:
    """HAS_RUN: Session -[:HAS_RUN]-> OrchestratorRun."""

    @pytest.mark.asyncio
    async def test_has_run_edge_exists(self, seeded_store):
        """HAS_RUN edge from Session to OrchestratorRun with seq=1 and DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            SESSION_NODE_ID,
            RUN_NODE_ID,
            "HAS_RUN",
            expected_seq=1,
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeHasStep
# ---------------------------------------------------------------------------
class TestEdgeTypeHasStep:
    """HAS_STEP: OrchestratorRun -[:HAS_STEP]-> Step."""

    @pytest.mark.asyncio
    async def test_has_step_edge_exists(self, seeded_store):
        """HAS_STEP edge from OrchestratorRun to PromptStep with seq=0 and DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            RUN_NODE_ID,
            PROMPT_NODE_ID,
            "HAS_STEP",
            expected_seq=0,
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeTriggered
# ---------------------------------------------------------------------------
class TestEdgeTypeTriggered:
    """TRIGGERED: Step -[:TRIGGERED]-> ToolExecution."""

    @pytest.mark.asyncio
    async def test_triggered_edge_exists(self, seeded_store):
        """TRIGGERED edge from PromptStep to ToolExecution with seq=1 and DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            PROMPT_NODE_ID,
            TOOL_NODE_ID,
            "TRIGGERED",
            expected_seq=1,
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeNext
# ---------------------------------------------------------------------------
class TestEdgeTypeNext:
    """NEXT: Step -[:NEXT]-> Step (consecutive steps in a run)."""

    @pytest.mark.asyncio
    async def test_next_edge_exists(self, seeded_store):
        """NEXT edge from PromptStep to AssistantStep with DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            PROMPT_NODE_ID,
            ASSISTANT_STEP_NODE_ID,
            "NEXT",
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeParallelWith
# ---------------------------------------------------------------------------
class TestEdgeTypeParallelWith:
    """PARALLEL_WITH: ToolExecution -[:PARALLEL_WITH]-> ToolExecution."""

    @pytest.mark.asyncio
    async def test_parallel_with_edge_exists(self, seeded_store):
        """PARALLEL_WITH edge between two ToolExecution nodes with DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            TOOL_NODE_2_ID,
            TOOL_NODE_ID,
            "PARALLEL_WITH",
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeSpawned
# ---------------------------------------------------------------------------
class TestEdgeTypeSpawned:
    """SPAWNED: DelegationTE -[:SPAWNED]-> ChildSession."""

    @pytest.mark.asyncio
    async def test_spawned_edge_exists(self, seeded_store):
        """SPAWNED edge from Delegation ToolExecution to ChildSession with DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            DELEGATION_TE_NODE_ID,
            CHILD_SESSION_NODE_ID,
            "SPAWNED",
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeSubsessionOf
# ---------------------------------------------------------------------------
class TestEdgeTypeSubsessionOf:
    """SUBSESSION_OF: ChildSession -[:SUBSESSION_OF]-> ParentSession."""

    @pytest.mark.asyncio
    async def test_subsession_of_edge_exists(self, seeded_store):
        """SUBSESSION_OF edge from ChildSession to parent Session with DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            CHILD_SESSION_NODE_ID,
            SESSION_NODE_ID,
            "SUBSESSION_OF",
        )


# ---------------------------------------------------------------------------
# TestEdgeTypeHasEvent
# ---------------------------------------------------------------------------
class TestEdgeTypeHasEvent:
    """HAS_EVENT: Session -[:HAS_EVENT]-> Event."""

    @pytest.mark.asyncio
    async def test_has_event_edge_exists(self, seeded_store):
        """HAS_EVENT edge from Session to Event node with DateTime occurred_at."""
        await _assert_edge_exists(
            seeded_store,
            SESSION_NODE_ID,
            EVENT_NODE_ID,
            "HAS_EVENT",
        )


# ---------------------------------------------------------------------------
# TestAllEdgeTypesPresent
# ---------------------------------------------------------------------------
class TestAllEdgeTypesPresent:
    """Cross-cutting verification: all 8 edge types present and correctly typed."""

    ALL_EDGE_TYPES = frozenset(
        {
            "HAS_RUN",
            "HAS_STEP",
            "TRIGGERED",
            "NEXT",
            "PARALLEL_WITH",
            "SPAWNED",
            "SUBSESSION_OF",
            "HAS_EVENT",
        }
    )

    @pytest.mark.asyncio
    async def test_all_eight_edge_types_present(self, seeded_store):
        """Query DISTINCT type(r) and verify all 8 edge types are present in Neo4j."""
        async with seeded_store._driver.session(database=seeded_store._database) as session:
            result = await session.run("MATCH ()-[r]->() RETURN DISTINCT type(r) AS edge_type")
            records = [record async for record in result]

        found_types = {record["edge_type"] for record in records}
        missing = self.ALL_EDGE_TYPES - found_types
        assert not missing, (
            f"Missing edge types in Neo4j: {missing!r}. Found types: {found_types!r}"
        )

    @pytest.mark.asyncio
    async def test_all_edges_have_occurred_at_as_datetime(self, seeded_store):
        """Verify every edge in the seeded graph has occurred_at as native Neo4jDateTime."""
        async with seeded_store._driver.session(database=seeded_store._database) as session:
            result = await session.run(
                "MATCH ()-[r]->() "
                "WHERE type(r) IN $edge_types "
                "RETURN type(r) AS edge_type, r.occurred_at AS occurred_at",
                edge_types=list(self.ALL_EDGE_TYPES),
            )
            records = [record async for record in result]

        # Must have exactly 8 edges (one per type)
        assert len(records) == 8, (
            f"Expected 8 edges (one per edge type), got {len(records)}. "
            f"Records: {[(r['edge_type'], r['occurred_at']) for r in records]}"
        )

        for record in records:
            edge_type = record["edge_type"]
            occurred_at = record["occurred_at"]
            assert isinstance(occurred_at, neo4j.time.DateTime), (
                f"Edge type {edge_type!r} has occurred_at={occurred_at!r} "
                f"(type={type(occurred_at).__name__!r}), expected neo4j.time.DateTime"
            )
