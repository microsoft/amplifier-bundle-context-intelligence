"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService

# ---------------------------------------------------------------------------
# Neo4j test connection constants (shared across test modules)
# ---------------------------------------------------------------------------
NEO4J_URI = "neo4j://localhost:7690"
NEO4J_AUTH = None
NEO4J_DATABASE = "neo4j"

# ---------------------------------------------------------------------------
# Reference IDs – mirror the make_node_id format used by handlers
# ---------------------------------------------------------------------------
SESSION_ID = "55c8841a-test"
SESSION_NODE_ID = "55c8841a-test"
RUN_NODE_ID = "55c8841a-test__execution_start__1737972000000"
PROMPT_NODE_ID = "55c8841a-test__prompt_submit__1737972001000"
TOOL_NODE_ID = "55c8841a-test__tool_pre__1737972002000"


# ---------------------------------------------------------------------------
# Reference graph helpers (public API used by tests)
# ---------------------------------------------------------------------------
def reference_nodes() -> list[tuple[str, set[str], dict[str, Any]]]:
    """Return the 4 canonical node tuples for the reference session graph."""
    return [
        (
            SESSION_NODE_ID,
            {"Session", "Root"},
            {
                "session_id": SESSION_ID,
                "status": "running",
                "started_at": "2026-01-15T10:00:00Z",
            },
        ),
        (
            RUN_NODE_ID,
            {"OrchestratorRun"},
            {
                "session_id": SESSION_ID,
                "run_number": 1,
                "status": "running",
                "started_at": "2026-01-15T10:00:00Z",
            },
        ),
        (
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "session_id": SESSION_ID,
                "iteration": 0,
                "prompt_text": "Help me refactor the authentication module",
                "prompt_preview": "Help me refactor the authentication module",
                "occurred_at": "2026-01-15T10:00:01Z",
            },
        ),
        (
            TOOL_NODE_ID,
            {"ToolExecution"},
            {
                "session_id": SESSION_ID,
                "tool_name": "read_file",
                "tool_call_id": "call_001",
                "status": "success",
            },
        ),
    ]


def reference_edges() -> list[tuple[str, str, str, dict[str, Any]]]:
    """Return the 3 canonical edge tuples for the reference session graph."""
    return [
        (SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1}),
        (RUN_NODE_ID, PROMPT_NODE_ID, "HAS_STEP", {"seq": 0}),
        (PROMPT_NODE_ID, TOOL_NODE_ID, "TRIGGERED", {"seq": 1}),
    ]


# Private aliases preserved for backward compatibility with existing tests
# that import _reference_nodes / _reference_edges directly.
_reference_nodes = reference_nodes
_reference_edges = reference_edges


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService wired to an in-memory DuckDB store.

    Uses explicit config so the factory never tries to import file_store
    during DuckDB-focused tests.
    """
    return HookStateService(
        raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
    )


@pytest.fixture
async def seed_reference_graph(store: Any) -> None:
    """Upsert all reference nodes and edges into *store*, then flush."""
    for node_id, labels, props in reference_nodes():
        await store.upsert_node(node_id, labels, props)
    for src, tgt, etype, props in reference_edges():
        await store.upsert_edge(src, tgt, etype, props)
    await store.flush()
