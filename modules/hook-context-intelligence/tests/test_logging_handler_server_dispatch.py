"""Tests for LoggingHandler server dispatch behavior.

After the _DestinationDispatcher refactor, dispatch behavior (circuit-breaker, persistent
client, queue, close) lives in _DestinationDispatcher and is tested in test_dispatcher.py.

This file retains:
- JSONL-writing behavior (always-on, D10)
- Idempotency key re-export (still re-exported from upload via logging_handler)
- LoggingHandler.close() which now gathers dispatcher closes
- set_dispatchers() / fan-out assertions (also covered in test_logging_handler_fanout.py)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    LoggingHandler,
    _DestinationDispatcher,
    _compute_idempotency_key,
)


# ---------------------------------------------------------------------------
# _FakeResolver
# ---------------------------------------------------------------------------
class _FakeResolver:
    """Minimal resolver adapter for LoggingHandler tests."""

    def __init__(
        self,
        base_path: Path,
        project_slug: str,
        workspace: str | None = None,
        parent_id: str = "",
        resolve_instance_id: str = "",
    ) -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.workspace = workspace
        self.parent_id = parent_id
        self.resolve_instance_id = resolve_instance_id
        self.working_dir: str = ""

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


# ---------------------------------------------------------------------------
# TestJSONLAlwaysWritten
# ---------------------------------------------------------------------------
class TestJSONLAlwaysWritten:
    """JSONL is written regardless of dispatcher configuration (D10)."""

    async def test_no_dispatch_without_dispatchers(self, tmp_path: Path) -> None:
        """With zero dispatchers, no HTTP dispatch occurs (no create_task calls)."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.asyncio.create_task"
        ) as mock_create_task:
            await handler(
                "session:start",
                {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
            )

        mock_create_task.assert_not_called()

    async def test_jsonl_still_written_without_dispatchers(self, tmp_path: Path) -> None:
        """JSONL is written even when no dispatchers are installed."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "t0", "working_dir": "/w"},
        )

        jsonl_path = tmp_path / "proj" / "sessions" / "s1" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists()
        record = json.loads(jsonl_path.read_text().strip())
        assert record["event"] == "session:start"

    async def test_jsonl_written_with_dispatchers(self, tmp_path: Path) -> None:
        """JSONL is written even when dispatchers are installed."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        mock_d = MagicMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_d])

        await handler(
            "session:start",
            {"session_id": "s2", "timestamp": "t0", "working_dir": "/w"},
        )

        jsonl_path = tmp_path / "proj" / "sessions" / "s2" / "context-intelligence" / "events.jsonl"
        assert jsonl_path.exists()
        record = json.loads(jsonl_path.read_text().strip())
        assert record["event"] == "session:start"


# ---------------------------------------------------------------------------
# TestIdempotencyKey
# ---------------------------------------------------------------------------
class TestIdempotencyKey:
    """Hook HTTP payloads carry deterministic idempotency keys."""

    def test_same_payload_produces_same_key(self) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-03-17T10:00:00.123456+00:00",
            "tool_call_id": "call-1",
            "payload": {"b": 2, "a": 1},
        }

        key_a = _compute_idempotency_key("tool:pre", "ws", data)
        key_b = _compute_idempotency_key("tool:pre", "ws", data)

        assert key_a == key_b

    def test_different_payload_produces_different_key(self) -> None:
        base = {
            "session_id": "s1",
            "timestamp": "2026-03-17T10:00:00.123456+00:00",
        }

        key_a = _compute_idempotency_key("tool:pre", "ws", {**base, "tool_call_id": "call-1"})
        key_b = _compute_idempotency_key("tool:pre", "ws", {**base, "tool_call_id": "call-2"})

        assert key_a != key_b


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() gathers close calls on all dispatchers."""

    async def test_close_safe_with_no_dispatchers(self, tmp_path: Path) -> None:
        """close() is a no-op when no dispatchers are installed."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler.close()  # must not raise

    async def test_close_calls_each_dispatcher_close(self, tmp_path: Path) -> None:
        """close() calls close() on each installed dispatcher."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        mock_a = AsyncMock(spec=_DestinationDispatcher)
        mock_b = AsyncMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_a, mock_b])

        await handler.close()

        mock_a.close.assert_awaited_once()
        mock_b.close.assert_awaited_once()

    async def test_close_tolerates_dispatcher_close_exception(self, tmp_path: Path) -> None:
        """close() continues even if a dispatcher raises during close (return_exceptions=True)."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        mock_a = AsyncMock(spec=_DestinationDispatcher)
        mock_a.close.side_effect = RuntimeError("boom")
        mock_b = AsyncMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_a, mock_b])

        await handler.close()  # must not raise

        mock_b.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestFanOut (complementary to test_logging_handler_fanout.py)
# ---------------------------------------------------------------------------
class TestFanOut:
    """set_dispatchers + enqueue fan-out behavior."""

    async def test_enqueue_called_on_all_dispatchers(self, tmp_path: Path) -> None:
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        mock_a = MagicMock(spec=_DestinationDispatcher)
        mock_b = MagicMock(spec=_DestinationDispatcher)
        await handler.set_dispatchers([mock_a, mock_b])

        await handler(
            "session:start",
            {"session_id": "s3", "timestamp": "t0", "working_dir": "/w"},
        )

        mock_a.enqueue.assert_called_once()
        mock_b.enqueue.assert_called_once()

    async def test_no_enqueue_without_dispatchers(self, tmp_path: Path) -> None:
        """No enqueue calls when dispatcher list is empty."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        # No dispatchers installed
        await handler(
            "session:start",
            {"session_id": "s4", "timestamp": "t0", "working_dir": "/w"},
        )
        # Should complete without error; no enqueue calls happen

    async def test_no_cleanup_when_no_dispatchers(self, tmp_path: Path) -> None:
        """_finalize_metadata works when no dispatchers installed; metadata is still written."""
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        session_dir = tmp_path / "proj" / "sessions" / "s5" / "context-intelligence"
        session_dir.mkdir(parents=True)

        # Should not raise
        handler._finalize_metadata(session_dir, {"status": "completed", "timestamp": "t1"})

        meta_path = session_dir / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["status"] == "completed"
