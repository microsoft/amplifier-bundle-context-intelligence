"""Tests for DefaultHandler — Event node creation for unclaimed events.

session:resume is the primary test case: it's an app-level event that flows
through DefaultHandler rather than getting explicit SessionHandler treatment.
"""

from __future__ import annotations

import json

from amplifier_module_hook_context_intelligence.handlers.default import DefaultHandler
from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id


class TestDefaultHandlerCreatesEventNodes:
    """DefaultHandler creates :Event:{DerivedLabel} nodes + HAS_EVENT edges."""

    async def test_creates_event_node_with_derived_label(self, services: HookStateService) -> None:
        handler = DefaultHandler(services)
        await handler(
            "session:resume",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T02:00:00Z",
            },
        )
        event_id = make_node_id("s1", "session:resume", "2026-01-01T02:00:00Z")
        node = await services.graph.get_node(event_id)
        assert node is not None
        assert node["labels"] == {"Event", "SessionResume"}
        assert node["properties"]["occurred_at"] == "2026-01-01T02:00:00Z"
        assert node["properties"]["event_name"] == "session:resume"

    async def test_creates_has_event_edge(self, services: HookStateService) -> None:
        handler = DefaultHandler(services)
        await handler(
            "session:resume",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T02:00:00Z",
            },
        )
        event_id = make_node_id("s1", "session:resume", "2026-01-01T02:00:00Z")
        edge = await services.graph.get_edge("s1", event_id, "HAS_EVENT")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == "2026-01-01T02:00:00Z"

    async def test_skips_event_without_session_id(self, services: HookStateService) -> None:
        handler = DefaultHandler(services)
        result = await handler(
            "session:resume",
            {"timestamp": "2026-01-01T02:00:00Z"},
        )
        assert result.action == "continue"
        # No nodes should have been created
        node = await services.graph.get_node("s1")
        assert node is None

    async def test_does_not_mutate_session_node(self, services: HookStateService) -> None:
        """DefaultHandler only creates Event nodes — it does NOT add labels
        to the Session node. The :ResumedSession label is gone by design;
        resume is discoverable via (Session)-[:HAS_EVENT]->(:Event:SessionResume).
        """
        handler = DefaultHandler(services)
        # Pre-create a Session node to verify it's untouched
        await services.graph.upsert_node("s1", {"Session", "Root"}, {"status": "running"})
        await handler(
            "session:resume",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T02:00:00Z",
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        assert node["labels"] == {"Session", "Root"}  # unchanged

    async def test_works_with_arbitrary_unclaimed_event(self, services: HookStateService) -> None:
        """DefaultHandler is generic — works for any event name."""
        handler = DefaultHandler(services)
        await handler(
            "custom:my_event",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T03:00:00Z",
            },
        )
        event_id = make_node_id("s1", "custom:my_event", "2026-01-01T03:00:00Z")
        node = await services.graph.get_node(event_id)
        assert node is not None
        assert node["labels"] == {"Event", "CustomMyEvent"}
        assert node["properties"]["event_name"] == "custom:my_event"


class TestDefaultHandlerDataProperty:
    """DefaultHandler stores full event payload in the 'data' property."""

    async def test_stores_data_property(self, services: HookStateService) -> None:
        """Event node has 'data' property containing the full JSON payload."""
        handler = DefaultHandler(services)
        await handler(
            "session:resume",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T02:00:00Z",
                "custom_info": "extra-value",
            },
        )
        event_id = make_node_id("s1", "session:resume", "2026-01-01T02:00:00Z")
        node = await services.graph.get_node(event_id)
        assert node is not None
        data = json.loads(node["properties"]["data"])
        assert data["session_id"] == "s1"
        assert data["custom_info"] == "extra-value"


class TestDefaultHandlerRunAwareness:
    """DefaultHandler attaches HAS_EVENT to OrchestratorRun when one is active,
    falls back to Session when no active run.
    """

    async def _seed_active_run(self, services: HookStateService, session_id: str = "s1") -> str:
        """Create Session + prompt:submit + execution:start so current_run_id is set.

        Returns the run node ID.
        """
        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": session_id, "timestamp": "2026-03-06T00:00:00Z"},
        )
        run_handler = OrchestratorRunHandler(services)
        await run_handler(
            "prompt:submit",
            {"session_id": session_id, "timestamp": "2026-03-06T01:00:00Z", "prompt": "Hello"},
        )
        await run_handler(
            "execution:start",
            {"session_id": session_id, "timestamp": "2026-03-06T02:00:00Z"},
        )
        cursors = services.get_cursors(session_id)
        assert cursors.current_run_id is not None
        return cursors.current_run_id

    async def test_event_during_active_run_attaches_to_run(
        self, services: HookStateService
    ) -> None:
        """When current_run_id exists, HAS_EVENT goes from OrchestratorRun to Event."""
        run_id = await self._seed_active_run(services)
        handler = DefaultHandler(services)
        await handler(
            "artifact:read",
            {"session_id": "s1", "timestamp": "2026-03-06T02:30:00Z"},
        )
        event_id = make_node_id("s1", "artifact:read", "2026-03-06T02:30:00Z")

        # HAS_EVENT should come from the run, not the session
        edge_from_run = await services.graph.get_edge(run_id, event_id, "HAS_EVENT")
        assert edge_from_run is not None, "HAS_EVENT edge from run is missing"

        # HAS_EVENT from session should NOT exist
        edge_from_session = await services.graph.get_edge("s1", event_id, "HAS_EVENT")
        assert edge_from_session is None, (
            "HAS_EVENT from session should not exist when run is active"
        )

    async def test_event_without_active_run_attaches_to_session(
        self, services: HookStateService
    ) -> None:
        """When no current_run_id, HAS_EVENT goes from Session (existing behavior)."""
        handler = DefaultHandler(services)
        await handler(
            "session:resume",
            {"session_id": "s1", "timestamp": "2026-01-01T02:00:00Z"},
        )
        event_id = make_node_id("s1", "session:resume", "2026-01-01T02:00:00Z")

        edge = await services.graph.get_edge("s1", event_id, "HAS_EVENT")
        assert edge is not None, "HAS_EVENT edge from session is missing"

    async def test_event_after_run_completes_attaches_to_session(
        self, services: HookStateService
    ) -> None:
        """After orchestrator:complete clears current_run_id, events go back to Session."""
        await self._seed_active_run(services)
        run_handler = OrchestratorRunHandler(services)
        await run_handler(
            "orchestrator:complete",
            {
                "session_id": "s1",
                "timestamp": "2026-03-06T03:00:00Z",
                "status": "success",
                "turn_count": 1,
            },
        )

        # current_run_id should be cleared
        cursors = services.get_cursors("s1")
        assert cursors.current_run_id is None

        handler = DefaultHandler(services)
        await handler(
            "prompt:complete",
            {"session_id": "s1", "timestamp": "2026-03-06T03:01:00Z"},
        )
        event_id = make_node_id("s1", "prompt:complete", "2026-03-06T03:01:00Z")

        # Should attach to session, not the (now-closed) run
        edge_from_session = await services.graph.get_edge("s1", event_id, "HAS_EVENT")
        assert edge_from_session is not None

    async def test_run_aware_event_node_still_has_correct_labels(
        self, services: HookStateService
    ) -> None:
        """Event node labels and properties are unchanged by run-awareness."""
        await self._seed_active_run(services)
        handler = DefaultHandler(services)
        await handler(
            "artifact:read",
            {"session_id": "s1", "timestamp": "2026-03-06T02:30:00Z"},
        )
        event_id = make_node_id("s1", "artifact:read", "2026-03-06T02:30:00Z")
        node = await services.graph.get_node(event_id)
        assert node is not None
        assert node["labels"] == {"Event", "ArtifactRead"}
        assert node["properties"]["event_name"] == "artifact:read"
