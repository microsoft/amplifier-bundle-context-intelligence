"""Full end-to-end integration tests exercising the complete mount -> event -> cleanup cycle.

Tests both logging-only and logging+graph paths through the real mount() entry point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence import mount

# Registration priorities used by production code
LOGGING_PRIORITY = 100
GRAPH_PRIORITY = 90


# ---------------------------------------------------------------------------
# Mock coordinator helper
# ---------------------------------------------------------------------------
def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    working_dir: str = "/home/user/test-project",
) -> MagicMock:
    """Build a mock coordinator for integration tests.

    - get_capability returns None for 'observability.events',
      working_dir string for 'session.working_dir'
    - hooks.register returns tracked unregister MagicMock fns
    """
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

    def _get_capability(name: str) -> Any:
        if name == "session.working_dir":
            return working_dir
        if name == "observability.events":
            return None
        return None

    coordinator.get_capability = MagicMock(side_effect=_get_capability)

    return coordinator


# ---------------------------------------------------------------------------
# TestLoggingOnlyIntegration
# ---------------------------------------------------------------------------
class TestLoggingOnlyIntegration:
    """Full lifecycle: mount -> fire events -> verify files -> cleanup."""

    async def test_session_lifecycle_writes_files(self, tmp_path: Path) -> None:
        events = ["session:start", "tool:pre", "session:end"]
        coordinator = _make_coordinator(
            contributed_events=[events],
            working_dir="/home/user/test-project",
        )
        config = {"base_path": str(tmp_path), "project_slug": "test-project"}
        cleanup = await mount(coordinator, config=config)
        assert callable(cleanup)

        # Extract LoggingHandler from registrations (find by priority or name).
        # register() positional args: (event, handler) — index [1] is the handler callable.
        handler = None
        for call in coordinator.hooks.register.call_args_list:
            if (
                call.kwargs.get("priority") == LOGGING_PRIORITY
                or call.kwargs.get("name") == "LoggingHandler"
            ):
                handler = call.args[1]
                break
        assert handler is not None, "LoggingHandler not found in registrations"

        # Simulate session:start -> tool:pre -> session:end
        session_id = "int-sess-001"
        await handler(
            "session:start",
            {
                "session_id": session_id,
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/home/user/test-project",
            },
        )
        await handler(
            "tool:pre",
            {
                "session_id": session_id,
                "timestamp": "2026-01-15T10:00:01Z",
                "tool_name": "read_file",
            },
        )
        await handler(
            "session:end",
            {
                "session_id": session_id,
                "timestamp": "2026-01-15T10:00:02Z",
                "status": "completed",
            },
        )

        # Verify session directory exists at tmp_path/test-project/sessions/int-sess-001/context-intelligence/
        session_dir = tmp_path / "test-project" / "sessions" / session_id / "context-intelligence"
        assert session_dir.exists(), f"Session dir not found: {session_dir}"

        # Verify events.jsonl has 3 lines with correct event names in order
        events_file = session_dir / "events.jsonl"
        assert events_file.exists()
        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 3
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["event"] == "session:start"
        assert parsed[1]["event"] == "tool:pre"
        assert parsed[2]["event"] == "session:end"

        # Verify metadata.json has status='completed' and ended_at timestamp
        meta_file = session_dir / "metadata.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta["status"] == "completed"
        assert "ended_at" in meta
        assert meta["ended_at"] != ""

        # cleanup() does not raise
        cleanup()


# ---------------------------------------------------------------------------
# TestLoggingPlusGraphIntegration
# ---------------------------------------------------------------------------
class TestLoggingPlusGraphIntegration:
    """Both logging and graph paths mount successfully."""

    async def test_both_paths_mount_successfully(self) -> None:
        events = ["session:start", "session:end", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        config = {
            "enable_graph": True,
            "graph_store": {
                "type": "neo4j",
                "config": {"uri": "bolt://localhost:7687", "username": "neo4j", "password": "test"},
            },
        }
        mock_store = MagicMock()
        mock_store.close = AsyncMock()
        with patch(
            "amplifier_module_hook_context_intelligence.graph_data_hook.Neo4jGraphStore",
            return_value=mock_store,
        ):
            result = await mount(coordinator, config=config)
        assert callable(result)

        # Count registrations by priority
        logging_regs = [
            c
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("priority") == LOGGING_PRIORITY
        ]
        graph_regs = [
            c
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("priority") == GRAPH_PRIORITY
        ]

        # Logging registrations at priority=100 == 3 (all events)
        assert len(logging_regs) == 3
        # Graph registrations at priority=90 >= 3
        assert len(graph_regs) >= 3


# ---------------------------------------------------------------------------
# TestGraphNotCreatedWithoutStore
# ---------------------------------------------------------------------------
class TestGraphNotCreatedWithoutStore:
    """enable_graph=True without graph_store does NOT create graph handlers."""

    async def test_enable_graph_without_stores_is_logging_only(self) -> None:
        events = ["session:start", "session:end", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        config = {"enable_graph": True}  # No graph_store key
        await mount(coordinator, config=config)

        # Graph regs at GRAPH_PRIORITY == 0
        graph_regs = [
            c
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("priority") == GRAPH_PRIORITY
        ]
        assert len(graph_regs) == 0


# ---------------------------------------------------------------------------
# TestCleanupIntegration
# ---------------------------------------------------------------------------
class TestCleanupIntegration:
    """Cleanup calls all unregister functions."""

    async def test_cleanup_unregisters_all(self) -> None:
        events = ["session:start", "session:end", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        cleanup = await mount(coordinator, config={})
        assert callable(cleanup)

        # Verify all unregister fns uncalled before cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_not_called()

        # Call cleanup
        cleanup()

        # Verify all unregister fns called after cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()
