"""Tests validating the reference data model fixtures in conftest.py."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def store():
    """Fresh in-memory DuckDBGraphStore for test isolation."""
    from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

    s = DuckDBGraphStore()
    yield s
    s._conn.close()


# ---------------------------------------------------------------------------
# TestConstants
# ---------------------------------------------------------------------------
class TestConstants:
    """conftest.py must export the 5 reference ID constants."""

    def test_session_id_constant(self):
        from tests.conftest import SESSION_ID

        assert SESSION_ID == "55c8841a-test"

    def test_session_node_id_constant(self):
        from tests.conftest import SESSION_NODE_ID

        assert SESSION_NODE_ID == "55c8841a-test"

    def test_run_node_id_constant(self):
        from tests.conftest import RUN_NODE_ID

        assert RUN_NODE_ID == "55c8841a-test__execution_start__1737972000000"

    def test_prompt_node_id_constant(self):
        from tests.conftest import PROMPT_NODE_ID

        assert PROMPT_NODE_ID == "55c8841a-test__prompt_submit__1737972001000"

    def test_tool_node_id_constant(self):
        from tests.conftest import TOOL_NODE_ID

        assert TOOL_NODE_ID == "55c8841a-test__tool_pre__1737972002000"


# ---------------------------------------------------------------------------
# TestReferenceNodes
# ---------------------------------------------------------------------------
class TestReferenceNodes:
    """_reference_nodes() must return exactly 4 tuples with correct structure."""

    def test_returns_exactly_4_nodes(self):
        from tests.conftest import _reference_nodes

        nodes = _reference_nodes()
        assert len(nodes) == 4

    def test_session_node(self):
        from tests.conftest import _reference_nodes, SESSION_NODE_ID

        nodes = _reference_nodes()
        node_id, labels, props = nodes[0]
        assert node_id == SESSION_NODE_ID
        assert labels == {"Session", "Root"}
        assert "session_id" in props
        assert "status" in props
        assert "started_at" in props

    def test_run_node(self):
        from tests.conftest import _reference_nodes, RUN_NODE_ID

        nodes = _reference_nodes()
        node_id, labels, props = nodes[1]
        assert node_id == RUN_NODE_ID
        assert labels == {"OrchestratorRun"}
        assert "session_id" in props
        assert "run_number" in props
        assert "status" in props
        assert "started_at" in props

    def test_prompt_node(self):
        from tests.conftest import _reference_nodes, PROMPT_NODE_ID

        nodes = _reference_nodes()
        node_id, labels, props = nodes[2]
        assert node_id == PROMPT_NODE_ID
        assert labels == {"Step", "PromptStep"}
        assert "session_id" in props
        assert "iteration" in props
        assert "prompt_text" in props
        assert "prompt_preview" in props
        assert "occurred_at" in props

    def test_tool_node(self):
        from tests.conftest import _reference_nodes, TOOL_NODE_ID

        nodes = _reference_nodes()
        node_id, labels, props = nodes[3]
        assert node_id == TOOL_NODE_ID
        assert labels == {"ToolExecution"}
        assert "session_id" in props
        assert "tool_name" in props
        assert "tool_call_id" in props
        assert "status" in props


# ---------------------------------------------------------------------------
# TestReferenceEdges
# ---------------------------------------------------------------------------
class TestReferenceEdges:
    """_reference_edges() must return exactly 3 edge tuples."""

    def test_returns_exactly_3_edges(self):
        from tests.conftest import _reference_edges

        edges = _reference_edges()
        assert len(edges) == 3

    def test_has_run_edge(self):
        from tests.conftest import _reference_edges, SESSION_NODE_ID, RUN_NODE_ID

        edges = _reference_edges()
        src, tgt, etype, props = edges[0]
        assert src == SESSION_NODE_ID
        assert tgt == RUN_NODE_ID
        assert etype == "HAS_RUN"
        assert props == {"seq": 1}

    def test_has_step_edge(self):
        from tests.conftest import _reference_edges, RUN_NODE_ID, PROMPT_NODE_ID

        edges = _reference_edges()
        src, tgt, etype, props = edges[1]
        assert src == RUN_NODE_ID
        assert tgt == PROMPT_NODE_ID
        assert etype == "HAS_STEP"
        assert props == {"seq": 0}

    def test_triggered_edge(self):
        from tests.conftest import _reference_edges, PROMPT_NODE_ID, TOOL_NODE_ID

        edges = _reference_edges()
        src, tgt, etype, props = edges[2]
        assert src == PROMPT_NODE_ID
        assert tgt == TOOL_NODE_ID
        assert etype == "TRIGGERED"
        assert props == {"seq": 1}


# ---------------------------------------------------------------------------
# TestSeedReferenceGraph
# ---------------------------------------------------------------------------
class TestSeedReferenceGraph:
    """seed_reference_graph fixture must upsert all nodes/edges and flush."""

    async def test_seed_reference_graph_populates_store(self, store, seed_reference_graph):
        """After seeding, all 4 nodes should be retrievable from the store."""
        from tests.conftest import (
            SESSION_NODE_ID,
            RUN_NODE_ID,
            PROMPT_NODE_ID,
            TOOL_NODE_ID,
        )

        for nid in [SESSION_NODE_ID, RUN_NODE_ID, PROMPT_NODE_ID, TOOL_NODE_ID]:
            node = await store.get_node(nid)
            assert node is not None, f"Node {nid} not found after seeding"

    async def test_seed_reference_graph_populates_edges(self, store, seed_reference_graph):
        """After seeding, all 3 edges should be retrievable from the store."""
        from tests.conftest import (
            SESSION_NODE_ID,
            RUN_NODE_ID,
            PROMPT_NODE_ID,
            TOOL_NODE_ID,
        )

        edge1 = await store.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge1 is not None
        edge2 = await store.get_edge(RUN_NODE_ID, PROMPT_NODE_ID, "HAS_STEP")
        assert edge2 is not None
        edge3 = await store.get_edge(PROMPT_NODE_ID, TOOL_NODE_ID, "TRIGGERED")
        assert edge3 is not None

    async def test_seed_reference_graph_flushes_to_duckdb(self, store, seed_reference_graph):
        """After seeding, buffers should be empty (flushed to DuckDB)."""
        assert len(store._node_buffer) == 0
        assert len(store._edge_buffer) == 0
