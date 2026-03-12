"""Integration tests against live Neo4j — verifies refactored event pipeline.

Requires: neo4j-test-env container running on port 7690 with NEO4J_AUTH=none.
Run: docker run -d --name neo4j-test-env -p 7690:7687 -e NEO4J_AUTH=none neo4j:5-community

These tests use NO mocks for the graph store. Real Neo4jGraphStore, real events,
real graph nodes verified in Neo4j after flush.

The coordinator IS mocked (we can't run a full Amplifier session in a unit test),
but the Neo4jGraphStore and all handler logic are real.

Skip pattern: the entire module is skipped with pytest.mark.skipif if Neo4j is
not reachable at startup — same defensive approach as test_neo4j_store.py.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_core.events import ALL_EVENTS

from amplifier_module_hook_context_intelligence.mount import MountFlow, MountState
from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore
from amplifier_module_hook_context_intelligence.utils import make_node_id
from tests.conftest import NEO4J_AUTH, NEO4J_DATABASE, NEO4J_URI

# ---------------------------------------------------------------------------
# Skip entire module if Neo4j is unavailable at import time
# ---------------------------------------------------------------------------
_neo4j_available = True
try:
    import neo4j

    _check_driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    _check_driver.verify_connectivity()
    _check_driver.close()
except Exception:
    _neo4j_available = False

pytestmark = pytest.mark.skipif(not _neo4j_available, reason="Neo4j not available at startup")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _unique_session_id() -> str:
    """Generate a unique session ID for each test to prevent cross-test interference."""
    return f"integ-{uuid.uuid4().hex[:8]}"


def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    capability_events: list[str] | None = None,
) -> MagicMock:
    """Build a mock coordinator — same factory pattern as other test files."""
    coordinator = MagicMock()
    coordinator.config = {}
    unregister_fns: list[MagicMock] = []

    def _register_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        unreg = MagicMock()
        unregister_fns.append(unreg)
        return unreg

    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(side_effect=_register_side_effect)
    coordinator._unregister_fns = unregister_fns

    if contributed_events is None:
        contributed_events = []
    coordinator.collect_contributions = AsyncMock(return_value=contributed_events)

    if capability_events is not None:
        coordinator.get_capability = MagicMock(return_value=lambda: capability_events)
    else:
        coordinator.get_capability = MagicMock(return_value=None)

    return coordinator


# ---------------------------------------------------------------------------
# Fixture: real Neo4jGraphStore, cleans up integ-* nodes after each test
# ---------------------------------------------------------------------------
@pytest.fixture
async def neo4j_store():
    """Create a real Neo4jGraphStore for integration tests.

    Uses unique session IDs per test (integ-*) so tests don't interfere
    with each other or with production data. Cleans up integ-* nodes after
    each test to keep the shared container tidy.
    """
    store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
    try:
        yield store
    finally:
        # Remove all integ-* nodes written by this test (best-effort)
        try:
            async with store._driver.session(database=store._database) as session:
                await session.run("MATCH (n) WHERE n.node_id STARTS WITH 'integ-' DETACH DELETE n")
        except Exception:
            pass
        await store.close()


# ---------------------------------------------------------------------------
# TestMountFlowWithRealStore
# ---------------------------------------------------------------------------
class TestMountFlowWithRealStore:
    """MountFlow reaches READY state when wired to a real Neo4jGraphStore."""

    async def test_full_mount_reaches_ready(self, neo4j_store: Neo4jGraphStore) -> None:
        """MountFlow with a real store transitions through all 5 states to READY."""
        events = {"session:start", "session:end", "tool:pre", "tool:post"}
        coordinator = _make_coordinator()
        flow = MountFlow(config={}, graph_store=neo4j_store)

        cleanup = await flow.run(coordinator, events)

        assert flow.state == MountState.READY
        assert callable(cleanup)
        # Each event in `events` got at least one registration
        registered = {c.args[0] for c in coordinator.hooks.register.call_args_list}
        assert events.issubset(registered)
        cleanup()

    async def test_all_events_base_produces_registrations(
        self, neo4j_store: Neo4jGraphStore
    ) -> None:
        """Using ALL_EVENTS as the event set produces handler registrations for every event type.

        Verifies that the refactored event pipeline (ALL_EVENTS base) correctly
        produces registrations and that every remaining event has a handler.
        """
        events = set(ALL_EVENTS)
        coordinator = _make_coordinator()
        flow = MountFlow(config={}, graph_store=neo4j_store)

        cleanup = await flow.run(coordinator, events)

        assert flow.state == MountState.READY
        # Every event in ALL_EVENTS must be covered
        assert coordinator.hooks.register.call_count > 0
        registered = {c.args[0] for c in coordinator.hooks.register.call_args_list}
        # Every remaining_event must have a registration
        assert registered == flow.remaining_events
        # ALL_EVENTS were not excluded by default config
        assert set(ALL_EVENTS).issubset(registered)
        cleanup()


# ---------------------------------------------------------------------------
# TestEventFiringProducesGraph
# ---------------------------------------------------------------------------
class TestEventFiringProducesGraph:
    """Fire real events through mounted handlers and verify graph nodes exist in Neo4j."""

    async def test_session_start_creates_session_node(self, neo4j_store: Neo4jGraphStore) -> None:
        """session:start event → real SessionHandler → Session node visible in Neo4j after flush."""
        session_id = _unique_session_id()
        events = {"session:start", "session:end"}
        coordinator = _make_coordinator()
        flow = MountFlow(config={}, graph_store=neo4j_store)
        cleanup = await flow.run(coordinator, events)

        # Find the registered handler for session:start
        handler = None
        for call in coordinator.hooks.register.call_args_list:
            if call.args[0] == "session:start":
                handler = call.args[1]
                break
        assert handler is not None, "session:start handler was not registered"

        # Fire the event through the real handler (wrapped with session guarantee)
        await handler(
            "session:start",
            {
                "session_id": session_id,
                "timestamp": "2026-03-12T10:00:00Z",
            },
        )

        # Flush buffered writes to Neo4j
        await neo4j_store.flush()

        # Verify the Session node exists in Neo4j
        # The session_id IS the node_id for Session nodes (set by SessionHandler)
        node = await neo4j_store.get_node(session_id)
        assert node is not None, f"Session node '{session_id}' not found in Neo4j after flush"
        assert "Session" in node["labels"], f"Expected 'Session' label, got: {node['labels']}"
        assert node["properties"]["status"] == "running"

        cleanup()

    async def test_tool_pre_creates_tool_execution_node(self, neo4j_store: Neo4jGraphStore) -> None:
        """tool:pre event → real ToolExecutionHandler → ToolExecution node in Neo4j after flush.

        Fires session:start first (the session guarantee wrapper requires the
        session to exist), then fires tool:pre and verifies both the Session
        node and the ToolExecution node are persisted to Neo4j.
        """
        session_id = _unique_session_id()
        tool_timestamp = "2026-03-12T10:00:01Z"
        events = {"session:start", "tool:pre", "tool:post"}
        coordinator = _make_coordinator()
        flow = MountFlow(config={}, graph_store=neo4j_store)
        cleanup = await flow.run(coordinator, events)

        # Build a lookup of event → registered handler
        handlers: dict[str, Any] = {}
        for call in coordinator.hooks.register.call_args_list:
            event_name = call.args[0]
            if event_name not in handlers:
                handlers[event_name] = call.args[1]

        # Fire session:start so the session node exists
        assert "session:start" in handlers, "session:start handler was not registered"
        await handlers["session:start"](
            "session:start",
            {
                "session_id": session_id,
                "timestamp": "2026-03-12T10:00:00Z",
            },
        )

        # Fire tool:pre
        assert "tool:pre" in handlers, "tool:pre handler was not registered"
        await handlers["tool:pre"](
            "tool:pre",
            {
                "session_id": session_id,
                "timestamp": tool_timestamp,
                "tool_name": "read_file",
                "tool_call_id": "call_integ_001",
            },
        )

        # Flush all buffered writes to Neo4j
        await neo4j_store.flush()

        # Verify Session node exists
        session_node = await neo4j_store.get_node(session_id)
        assert session_node is not None, f"Session node '{session_id}' not found in Neo4j"
        assert "Session" in session_node["labels"]

        # Verify ToolExecution node exists
        # make_node_id produces: {session_id}__tool_pre__{epoch_ms}
        te_id = make_node_id(session_id, "tool:pre", tool_timestamp)
        te_node = await neo4j_store.get_node(te_id)
        assert te_node is not None, f"ToolExecution node '{te_id}' not found in Neo4j"
        assert "ToolExecution" in te_node["labels"]
        assert te_node["properties"]["tool_name"] == "read_file"
        assert te_node["properties"]["session_id"] == session_id

        cleanup()
