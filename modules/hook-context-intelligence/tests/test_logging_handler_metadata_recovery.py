"""Regression tests for metadata.json corruption recovery.

Background: a long-running session's ``metadata.json`` is rewritten after every
event. The pre-fix code used a non-atomic ``Path.write_text`` (truncate then
write). When the disk filled mid-write (``ENOSPC``) the truncate succeeded but
the content write did not, leaving ``metadata.json`` permanently 0 bytes. Every
subsequent event then hit ``json.loads("")`` and raised
``JSONDecodeError: Expecting value: line 1 column 1 (char 0)`` -- caught, logged,
and repeated forever, because the read failed before the write that would have
repaired it and ``_ensure_metadata`` only (re)creates a *missing* file.

These tests verify the two-part fix:
1. reads tolerate a missing/empty/corrupt file and rebuild it (self-heal), and
2. writes are atomic (temp file + os.replace), so a failed write never
   truncates the existing file to zero bytes.
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
        self.working_dir: str = "/w"

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


def _meta_path(tmp_path: Path, session_id: str = "s1") -> Path:
    return tmp_path / "proj" / "sessions" / session_id / "context-intelligence" / "metadata.json"


# ---------------------------------------------------------------------------
# _read_metadata: tolerate missing / empty / corrupt
# ---------------------------------------------------------------------------
class TestReadMetadata:
    def test_missing_returns_none(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        assert LoggingHandler._read_metadata(tmp_path / "nope.json") is None

    def test_empty_returns_none(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        p = tmp_path / "metadata.json"
        p.write_text("")  # exactly the 0-byte ENOSPC artifact
        assert LoggingHandler._read_metadata(p) is None

    def test_corrupt_returns_none(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        p = tmp_path / "metadata.json"
        p.write_text("{not json")
        assert LoggingHandler._read_metadata(p) is None

    def test_non_object_returns_none(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        p = tmp_path / "metadata.json"
        p.write_text("[1, 2, 3]")
        assert LoggingHandler._read_metadata(p) is None

    def test_valid_returns_dict(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        p = tmp_path / "metadata.json"
        p.write_text(json.dumps({"a": 1}))
        assert LoggingHandler._read_metadata(p) == {"a": 1}


# ---------------------------------------------------------------------------
# _atomic_write_text: a failed write must not truncate the existing file
# ---------------------------------------------------------------------------
class TestAtomicWrite:
    def test_success_writes_content(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        p = tmp_path / "metadata.json"
        LoggingHandler._atomic_write_text(p, '{"ok":true}')
        assert json.loads(p.read_text()) == {"ok": True}

    def test_failure_leaves_original_intact(self, tmp_path: Path) -> None:
        """If the temp write fails (e.g. ENOSPC), the existing file is untouched.

        This is the core guarantee the old ``write_text`` violated: it would
        have left a 0-byte file. The atomic path must leave the last-good
        content in place instead.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        p = tmp_path / "metadata.json"
        good = json.dumps({"status": "running", "last_event_at": "t0"})
        p.write_text(good)

        real_write_text = Path.write_text

        def fail_on_tmp(self: Path, *args: object, **kwargs: object):
            # Simulate ENOSPC only for the temp file the atomic writer creates.
            if self.name.endswith(".tmp"):
                raise OSError("No space left on device")
            return real_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "write_text", fail_on_tmp):
            try:
                LoggingHandler._atomic_write_text(p, json.dumps({"status": "new"}))
            except OSError:
                pass  # the writer re-raises; caller (`_touch`) handles it

        # Original content survives; no truncation to zero bytes.
        assert p.read_text() == good
        assert p.stat().st_size > 0
        # No leftover temp files in the directory.
        assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# End-to-end self-heal through the handler event flow
# ---------------------------------------------------------------------------
class TestSelfHeal:
    async def test_touch_heals_empty_metadata_mid_session(self, tmp_path: Path) -> None:
        """The exact reported scenario: metadata.json goes 0-byte mid-session.

        A subsequent event must self-heal it (not raise, not warn) and restore a
        valid file whose last_event_at reflects that event.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
            logger,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "2026-01-15T10:00:00Z", "working_dir": "/w"},
        )

        meta_path = _meta_path(tmp_path)
        assert json.loads(meta_path.read_text())["last_event_at"] == "2026-01-15T10:00:00Z"

        # Corrupt exactly as ENOSPC did: truncate to zero bytes.
        meta_path.write_text("")
        assert meta_path.stat().st_size == 0

        with patch.object(logger, "warning") as warn:
            await handler(
                "tool:call",
                {"session_id": "s1", "timestamp": "2026-01-15T10:05:00Z", "tool_name": "read_file"},
            )

        # No warning was emitted -- the error loop is gone.
        warn.assert_not_called()

        healed = json.loads(meta_path.read_text())
        assert healed["last_event_at"] == "2026-01-15T10:05:00Z"
        assert healed["session_id"] == "s1"
        assert healed["status"] == "running"

    async def test_touch_heals_corrupt_metadata_mid_session(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
            logger,
        )

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "2026-01-15T10:00:00Z", "working_dir": "/w"},
        )

        _meta_path(tmp_path).write_text("{ truncated garbage")

        with patch.object(logger, "warning") as warn:
            await handler(
                "tool:call",
                {"session_id": "s1", "timestamp": "2026-01-15T10:06:00Z", "tool_name": "grep"},
            )

        warn.assert_not_called()
        assert (
            json.loads(_meta_path(tmp_path).read_text())["last_event_at"] == "2026-01-15T10:06:00Z"
        )

    async def test_ensure_metadata_recreates_empty_file_after_restart(self, tmp_path: Path) -> None:
        """A fresh handler (process restart) also heals a pre-existing 0-byte file.

        ``_ensure_metadata`` must treat empty/corrupt as "needs creation", not
        skip on ``exists()``.
        """
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            LoggingHandler,
        )

        # Pre-create the corrupt artifact before any handler sees the session.
        meta_path = _meta_path(tmp_path)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text("")

        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        await handler(
            "tool:call",
            {"session_id": "s1", "timestamp": "2026-01-15T11:00:00Z", "tool_name": "read_file"},
        )

        meta = json.loads(meta_path.read_text())
        assert meta["session_id"] == "s1"
        assert meta["last_event_at"] == "2026-01-15T11:00:00Z"
        assert meta["working_dir"] == "/w"
