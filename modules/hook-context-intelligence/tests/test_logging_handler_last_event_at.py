"""Tests for LoggingHandler.last_event_at — V1 hook fix.

Verifies:
1. First event writes last_event_at to metadata.json
2. Subsequent events update last_event_at to the latest timestamp
3. Failure in _touch_last_event_at never blocks event capture (JSONL append)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


class _FakeResolver:
    """Minimal resolver adapter for testing LoggingHandler in isolation."""

    def __init__(
        self, base_path: Path, project_slug: str, workspace: str = "test-workspace"
    ) -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.workspace = workspace
        self.working_dir: str = ""

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


# ---------------------------------------------------------------------------
# TestLastEventAt
# ---------------------------------------------------------------------------
class TestLastEventAt:
    """LoggingHandler writes and updates last_event_at in metadata.json."""

    async def test_first_event_writes_last_event_at(self, tmp_path: Path) -> None:
        """After the first event, metadata.json must contain last_event_at."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )

        meta_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
        meta = json.loads(meta_path.read_text())
        assert "last_event_at" in meta
        assert meta["last_event_at"] == "2026-01-15T10:00:00Z"

    async def test_subsequent_events_update_last_event_at(self, tmp_path: Path) -> None:
        """After two events, last_event_at must equal the second event's timestamp."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:00Z",
                "working_dir": "/w",
            },
        )
        await handler(
            "tool:call",
            {
                "session_id": "s1",
                "timestamp": "2026-01-15T10:00:05Z",
                "tool_name": "read_file",
            },
        )

        meta_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "metadata.json"
        meta = json.loads(meta_path.read_text())
        # last_event_at must reflect the second event, not the first
        assert meta["last_event_at"] == "2026-01-15T10:00:05Z"

    async def test_last_event_at_failure_does_not_block_event_capture(self, tmp_path: Path) -> None:
        """Failure in _touch_last_event_at must not prevent events.jsonl capture."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )
        from amplifier_core.models import HookResult

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        # Patch _touch_last_event_at to raise an OSError — simulates metadata
        # write failure (e.g. disk full, permissions error).
        with patch.object(handler, "_touch_last_event_at", side_effect=OSError("meta full")):
            result = await handler(
                "tool:call",
                {
                    "session_id": "s1",
                    "timestamp": "2026-01-15T10:00:01Z",
                    "tool_name": "read_file",
                },
            )

        # Hook must still return continue (event capture not blocked)
        assert isinstance(result, HookResult)
        assert result.action == "continue"

        # The event line must be present in events.jsonl despite the meta failure
        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists(), "events.jsonl must exist even when metadata write fails"
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "tool:call"
