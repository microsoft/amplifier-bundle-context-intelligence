"""Tests for DefaultHandler — Event node creation for unclaimed events.

session:resume is the primary test case: it's an app-level event that flows
through DefaultHandler rather than getting explicit SessionHandler treatment.
"""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.handlers.default import DefaultHandler
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
        import json

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
