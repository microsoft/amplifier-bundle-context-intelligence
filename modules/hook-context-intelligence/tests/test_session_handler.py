"""Tests for SessionHandler — session lifecycle graph mutations."""

from __future__ import annotations

import dataclasses

import pytest

from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.services import HookStateService, SessionCursors


class TestSessionIdGuard:
    async def test_missing_session_id_returns_continue(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        result = await handler("session:start", {"timestamp": "2026-01-01T00:00:00Z"})
        assert result.action == "continue"


class TestSessionStart:
    async def test_root_session(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
                "metadata": {"key": "val"},
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        assert node["labels"] == {"Session", "Root"}
        assert node["properties"]["started_at"] == "2026-01-01T00:00:00Z"
        assert node["properties"]["status"] == "running"
        assert node["properties"]["metadata"] == {"key": "val"}

    async def test_root_session_no_subsession_edge(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        edge = await services.graph.get_edge("s1", "", "SUBSESSION_OF")
        assert edge is None

    async def test_subsession(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "child",
                "parent_id": "parent",
                "timestamp": "2026-01-01T00:00:00Z",
                "metadata": {"m": 1},
            },
        )
        node = await services.graph.get_node("child")
        assert node is not None
        assert node["labels"] == {"Session", "Subsession"}
        assert node["properties"]["started_at"] == "2026-01-01T00:00:00Z"
        assert node["properties"]["status"] == "running"
        assert node["properties"]["metadata"] == {"m": 1}

    async def test_subsession_edge(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "child",
                "parent_id": "parent",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        edge = await services.graph.get_edge("child", "parent", "SUBSESSION_OF")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == "2026-01-01T00:00:00Z"

    async def test_missing_metadata_defaults_to_empty_dict(
        self, services: HookStateService
    ) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        assert node["properties"]["metadata"] == {}


class TestSessionStartParentIdEdgeCases:
    @pytest.mark.parametrize("parent_id", [None, "", "   ", "\t", "\n"])
    async def test_falsy_parent_id_produces_root(
        self, services: HookStateService, parent_id: str | None
    ) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "parent_id": parent_id,
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        assert "Root" in node["labels"]
        assert "Subsession" not in node["labels"]

    async def test_missing_parent_id_key_produces_root(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        assert "Root" in node["labels"]
        assert "Subsession" not in node["labels"]


class TestSessionFork:
    async def test_fork_labels(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:fork",
            {
                "session_id": "f1",
                "parent": "p1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        node = await services.graph.get_node("f1")
        assert node is not None
        assert node["labels"] == {"Session", "Subsession", "ForkedSession"}
        assert node["properties"]["status"] == "running"

    async def test_fork_edge(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:fork",
            {
                "session_id": "f1",
                "parent": "p1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        edge = await services.graph.get_edge("f1", "p1", "SUBSESSION_OF")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == "2026-01-01T00:00:00Z"

    async def test_fork_missing_parent_degrades_to_root(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:fork",
            {
                "session_id": "f1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        node = await services.graph.get_node("f1")
        assert node is not None
        assert node["labels"] == {"Session", "Root", "ForkedSession"}


class TestSessionEnd:
    async def test_end_merges_properties(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        await handler(
            "session:end",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T01:00:00Z",
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        assert node["properties"]["ended_at"] == "2026-01-01T01:00:00Z"
        assert node["properties"]["status"] == "completed"

    async def test_end_preserves_existing_labels(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        await handler(
            "session:end",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T01:00:00Z",
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        # membership checks: end upsert merges into labels created by start
        assert "Session" in node["labels"]
        assert "Root" in node["labels"]

    async def test_end_without_prior_start(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:end",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T01:00:00Z",
            },
        )
        node = await services.graph.get_node("s1")
        assert node is not None


class TestSessionResumeNotClaimed:
    """session:resume is NOT handled by SessionHandler — it flows to DefaultHandler.

    Verify that SessionHandler ignores session:resume (no graph mutations).
    See test_default_handler.py for the DefaultHandler tests.
    """

    async def test_session_handler_does_not_claim_resume(self) -> None:
        assert "session:resume" not in SessionHandler.handled_events


class TestSessionCursors:
    """Tests for SessionCursors dataclass and HookStateService cursor management."""

    def test_defaults(self) -> None:
        cursors = SessionCursors()
        assert cursors.current_run_id is None
        assert cursors.current_step_id is None
        assert cursors.run_counter == 0
        assert cursors.step_counter == 0
        assert cursors.prompt_preview == ""
        assert cursors.parallel_groups == {}
        assert cursors.tool_call_map == {}

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(SessionCursors)

    def test_lazy_init(self, services: HookStateService) -> None:
        """get_cursors creates a SessionCursors on first access."""
        cursors = services.get_cursors("sess-1")
        assert isinstance(cursors, SessionCursors)

    def test_same_instance_returned(self, services: HookStateService) -> None:
        """get_cursors returns the same object for the same session_id."""
        a = services.get_cursors("sess-1")
        b = services.get_cursors("sess-1")
        assert a is b

    def test_different_sessions_get_different_cursors(self, services: HookStateService) -> None:
        a = services.get_cursors("sess-1")
        b = services.get_cursors("sess-2")
        assert a is not b

    def test_remove_cursors(self, services: HookStateService) -> None:
        """remove_cursors deletes the cursor entry for a session."""
        services.get_cursors("sess-1")
        services.remove_cursors("sess-1")
        # After removal, a new call should create a fresh instance
        new = services.get_cursors("sess-1")
        assert new.run_counter == 0  # fresh defaults

    def test_remove_nonexistent_is_safe(self, services: HookStateService) -> None:
        """remove_cursors does not raise for a nonexistent session_id."""
        services.remove_cursors("does-not-exist")  # should not raise

    async def test_session_end_removes_cursors(self, services: HookStateService) -> None:
        """session:end event calls remove_cursors for the ended session."""
        handler = SessionHandler(services)
        # Prime cursors and mutate to prove they existed
        cursors = services.get_cursors("s1")
        cursors.run_counter = 5

        await handler(
            "session:end",
            {"session_id": "s1", "timestamp": "2026-01-01T01:00:00Z"},
        )

        # After session:end, cursors should have been removed;
        # a fresh get_cursors returns default values
        fresh = services.get_cursors("s1")
        assert fresh.run_counter == 0
