"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

import os
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService

# ---------------------------------------------------------------------------
# Neo4j test connection constants (shared across test modules)
# ---------------------------------------------------------------------------
NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j://localhost:7690")
_neo4j_user = os.environ.get("NEO4J_USER")
_neo4j_pass = os.environ.get("NEO4J_PASSWORD")
NEO4J_AUTH = (_neo4j_user, _neo4j_pass) if _neo4j_user and _neo4j_pass else None
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# ---------------------------------------------------------------------------
# Reference IDs – mirror the make_node_id format used by handlers
# ---------------------------------------------------------------------------
SESSION_ID = "55c8841a-test"
SESSION_NODE_ID = "55c8841a-test"
RUN_NODE_ID = "55c8841a-test__execution_start__1737972000000"
PROMPT_NODE_ID = "55c8841a-test__prompt_submit__1737972001000"
TOOL_NODE_ID = "55c8841a-test__tool_pre__1737972002000__call_001"

# New reference IDs for expanded graph (all 8 edge types)
ASSISTANT_STEP_NODE_ID = "55c8841a-test__provider_request__1737972003000"
TOOL_NODE_2_ID = "55c8841a-test__tool_pre__1737972004000__call_002"
DELEGATION_TE_NODE_ID = "55c8841a-test__tool_pre__1737972005000__call_003"
CHILD_SESSION_ID = "child-55c8841a-test"
CHILD_SESSION_NODE_ID = "child-55c8841a-test"
EVENT_NODE_ID = "55c8841a-test__event__1737972006000"

# Shared reference timestamp for all edge occurred_at values
REF_TIMESTAMP = "2026-01-15T10:00:05Z"


# ---------------------------------------------------------------------------
# Reference graph helpers (public API used by tests)
# ---------------------------------------------------------------------------
def reference_nodes() -> list[tuple[str, set[str], dict[str, Any]]]:
    """Return the 9 canonical node tuples for the reference session graph."""
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
        (
            ASSISTANT_STEP_NODE_ID,
            {"Step", "AssistantStep"},
            {
                "session_id": SESSION_ID,
                "iteration": 1,
                "occurred_at": REF_TIMESTAMP,
            },
        ),
        (
            TOOL_NODE_2_ID,
            {"ToolExecution"},
            {
                "session_id": SESSION_ID,
                "tool_name": "write_file",
                "tool_call_id": "call_002",
                "status": "success",
            },
        ),
        (
            DELEGATION_TE_NODE_ID,
            {"ToolExecution", "Delegation"},
            {
                "session_id": SESSION_ID,
                "tool_name": "delegate",
                "tool_call_id": "call_003",
                "child_session_id": CHILD_SESSION_ID,
            },
        ),
        (
            CHILD_SESSION_NODE_ID,
            {"Session", "Subsession"},
            {
                "session_id": CHILD_SESSION_ID,
                "status": "running",
                "started_at": REF_TIMESTAMP,
            },
        ),
        (
            EVENT_NODE_ID,
            {"Event"},
            {
                "session_id": SESSION_ID,
                "event_type": "context:compaction",
                "occurred_at": REF_TIMESTAMP,
            },
        ),
    ]


def reference_edges() -> list[tuple[str, str, str, dict[str, Any]]]:
    """Return the 8 canonical edge tuples covering all 8 edge types, all with occurred_at."""
    return [
        (SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1, "occurred_at": REF_TIMESTAMP}),
        (RUN_NODE_ID, PROMPT_NODE_ID, "HAS_STEP", {"seq": 0, "occurred_at": REF_TIMESTAMP}),
        (PROMPT_NODE_ID, TOOL_NODE_ID, "TRIGGERED", {"seq": 1, "occurred_at": REF_TIMESTAMP}),
        (PROMPT_NODE_ID, ASSISTANT_STEP_NODE_ID, "NEXT", {"occurred_at": REF_TIMESTAMP}),
        (TOOL_NODE_2_ID, TOOL_NODE_ID, "PARALLEL_WITH", {"occurred_at": REF_TIMESTAMP}),
        (DELEGATION_TE_NODE_ID, CHILD_SESSION_NODE_ID, "SPAWNED", {"occurred_at": REF_TIMESTAMP}),
        (
            CHILD_SESSION_NODE_ID,
            SESSION_NODE_ID,
            "SUBSESSION_OF",
            {"occurred_at": REF_TIMESTAMP},
        ),
        (SESSION_NODE_ID, EVENT_NODE_ID, "HAS_EVENT", {"occurred_at": REF_TIMESTAMP}),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService using default GraphState (no external store)."""
    return HookStateService(raw_config={})
