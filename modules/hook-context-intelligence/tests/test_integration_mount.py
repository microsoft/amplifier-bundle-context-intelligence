"""Full end-to-end integration tests exercising the complete mount -> event -> cleanup cycle.

Tests the thin-forwarder path: LoggingHandler always active, no graph handlers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.events import ALL_EVENTS

from amplifier_module_hook_context_intelligence import mount

# Registration priority used by production code
LOGGING_PRIORITY = 100


# ---------------------------------------------------------------------------
# Mock coordinator helper
# ---------------------------------------------------------------------------
def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    working_dir: str = "/home/user/test-project",
) -> MagicMock:
    """Build a mock coordinator for integration tests."""
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

        # Verify session directory exists
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

    async def test_logging_handler_registers_for_all_events(self) -> None:
        """LoggingHandler registers for ALL_EVENTS base."""
        coordinator = _make_coordinator()
        await mount(coordinator, config={})

        logging_regs = [
            c
            for c in coordinator.hooks.register.call_args_list
            if c.kwargs.get("priority") == LOGGING_PRIORITY
            or c.kwargs.get("name") == "LoggingHandler"
        ]
        assert len(logging_regs) >= len(ALL_EVENTS)


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
