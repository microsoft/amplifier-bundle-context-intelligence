"""Full end-to-end integration tests exercising the complete mount -> event -> cleanup cycle.

Tests the thin-forwarder path: LoggingHandler always active, no graph handlers.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from amplifier_core.events import ALL_EVENTS  # type: ignore[import-not-found]

from amplifier_module_hook_context_intelligence import mount, on_session_ready  # type: ignore[import-not-found]
from tests.helpers import make_lifecycle_coordinator

# Registration priority used by production code
LOGGING_PRIORITY = 100


# ---------------------------------------------------------------------------
# Mock coordinator helper — thin alias delegating to shared helper
# ---------------------------------------------------------------------------
def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    working_dir: str = "/home/user/test-project",
) -> MagicMock:
    return make_lifecycle_coordinator(
        contributed_events=contributed_events,
        working_dir=working_dir,
    )


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
        # on_session_ready() registers the LoggingHandler (two-phase lifecycle)
        await on_session_ready(coordinator)

        # Extract LoggingHandler from registrations by name (canonical identifier).
        # Priority alone is not unique — SkillFetcher also uses priority=100.
        # register() positional args: (event, handler) — index [1] is the handler callable.
        handler = None
        for call in coordinator.hooks.register.call_args_list:
            if call.kwargs.get("name") == "LoggingHandler":
                handler = call.args[1]
                break
        assert handler is not None, (
            "LoggingHandler not found in registrations (name='LoggingHandler' missing)"
        )

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
        await cleanup()

    async def test_logging_handler_registers_for_all_events(self) -> None:
        """LoggingHandler registers for ALL_EVENTS base after on_session_ready()."""
        coordinator = _make_coordinator()
        await mount(coordinator, config={})
        # on_session_ready() triggers event registration (two-phase lifecycle)
        await on_session_ready(coordinator)

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
        # on_session_ready() populates coordinator._unregister_fns with LoggingHandler entries.
        # Without this call the list is empty and the for-loops below are vacuous.
        await on_session_ready(coordinator)
        assert callable(cleanup)

        # coordinator._unregister_fns now has LoggingHandler unregister entries
        assert len(coordinator._unregister_fns) > 0, (
            "on_session_ready() must register LoggingHandler handlers so cleanup has something to tear down"
        )

        # Verify all unregister fns uncalled before cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_not_called()

        # Call cleanup
        await cleanup()

        # Verify all unregister fns called after cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()
