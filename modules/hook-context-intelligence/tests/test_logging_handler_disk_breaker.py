"""Tests for the disk-pressure circuit breaker and user-visible fail-loud.

When the disk fills, ``_touch_last_event_at`` / metadata writes / the JSONL
append all fail with ENOSPC. Two requirements drive this behaviour:

1. Do NOT hammer a full filesystem on every event (and do not try to log the
   failure to a log file that also cannot be written). Open a breaker: skip disk
   writes for a growing cooldown, then let one event PROBE for recovery.
2. FAIL LOUD to the *user*, not the log file. The disk-full alert must ride
   ``HookResult.user_message`` (which the orchestrator surfaces in the UI),
   because ``logger.*`` output at this moment cannot reach disk.

Verifies:
- ENOSPC opens the breaker and returns an error-level user_message.
- While the breaker is open, disk writes are skipped (no per-event hammering).
- The alert is rate-limited (not emitted on every event).
- A successful probe after cooldown closes the breaker and emits a recovery
  message; normal writes resume.
- Backoff grows (capped) across repeated failures.
- A non-ENOSPC OSError does NOT open the breaker and surfaces no user_message.
- Event dispatch (network fan-out) still happens while the disk is degraded.
"""

from __future__ import annotations

import errno
from pathlib import Path

import amplifier_module_hook_context_intelligence.handlers.logging_handler as mod
from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler


class _FakeResolver:
    def __init__(self, base_path: Path, project_slug: str, workspace: str = "ws") -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.workspace = workspace
        self.working_dir: str = ""

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


def _enospc(*_a, **_k):
    raise OSError(errno.ENOSPC, "No space left on device")


class _Clock:
    """Monotonic clock stub the test advances explicitly."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _evt(sid: str = "s1", ts: str = "2026-01-15T10:00:00Z", **extra):
    return {"session_id": sid, "timestamp": ts, **extra}


class TestDiskBreaker:
    async def test_enospc_opens_breaker_and_alerts_user(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        result = await handler("tool:call", _evt())

        assert result.action == "continue"
        # Fail loud, to the USER, at error level — not just a log line. With no
        # destination configured, disk-full means the data is gone for good.
        assert result.user_message is not None
        assert "PERMANENT DATA LOSS" in result.user_message
        assert result.user_message_level == "error"
        assert result.user_message_source == "context-intelligence"
        # Breaker is now open.
        assert handler._disk_backoff_seconds == mod._DISK_BACKOFF_INITIAL_SECONDS

    async def test_no_destination_disk_full_is_permanent_loss(self, tmp_path, monkeypatch) -> None:
        # The user's scenario: NO dispatchers configured. A full disk then means
        # events reach no sink at all — the alert must say so, at error level.
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        assert handler._dispatchers == []  # no destination
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        result = await handler("tool:call", _evt())

        assert result.user_message_level == "error"
        assert "PERMANENT DATA LOSS" in result.user_message
        assert "disk is full" in result.user_message.lower()

    async def test_disk_full_but_delivered_is_warning_not_loss(self, tmp_path, monkeypatch) -> None:
        # Disk full BUT a dispatcher accepted the event -> not lost, just a stale
        # local log. Must be a warning, and must NOT claim permanent loss.
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        class _OkDispatcher:
            def enqueue(self, event, data):
                return True  # accepted for delivery

        handler._dispatchers = [_OkDispatcher()]  # type: ignore[list-item]

        result = await handler("tool:call", _evt())

        assert result.user_message_level == "warning"
        assert "PERMANENT DATA LOSS" not in result.user_message
        assert "still being sent to the server" in result.user_message

    async def test_disk_full_and_queue_dropped_is_permanent_loss(
        self, tmp_path, monkeypatch
    ) -> None:
        # Disk full AND the delivery queue is full (enqueue returns False) ->
        # the event reached no sink -> permanent loss, at error level.
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        class _FullDispatcher:
            def enqueue(self, event, data):
                return False  # queue full, dropped

        handler._dispatchers = [_FullDispatcher()]  # type: ignore[list-item]

        result = await handler("tool:call", _evt())

        assert result.user_message_level == "error"
        assert "PERMANENT DATA LOSS" in result.user_message

    async def test_open_breaker_skips_disk_writes(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        calls = {"n": 0}

        def _counting_enospc(*_a, **_k):
            calls["n"] += 1
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(handler, "_write_session_to_disk", _counting_enospc)

        await handler("tool:call", _evt())  # trips breaker (1 attempt)
        assert calls["n"] == 1

        # Next events, still inside the cooldown window: must NOT attempt a write.
        clock.t += 1.0
        await handler("tool:call", _evt(ts="t2"))
        clock.t += 1.0
        await handler("tool:call", _evt(ts="t3"))
        assert calls["n"] == 1, "disk write was retried during the cooldown window"

    async def test_alert_is_rate_limited(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        first = await handler("tool:call", _evt())
        assert first.user_message is not None

        # Advance past the cooldown but NOT past the log rate-limit window, then
        # fail again — the breaker re-opens but the user alert is suppressed.
        clock.t += mod._DISK_BACKOFF_INITIAL_SECONDS + 0.1
        second = await handler("tool:call", _evt(ts="t2"))
        assert second.user_message is None

    async def test_probe_recovers_and_notifies(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)
        await handler("tool:call", _evt())  # breaker open
        assert handler._disk_backoff_seconds > 0.0

        # Disk frees up; advance past cooldown; the probe write now succeeds.
        monkeypatch.setattr(handler, "_write_session_to_disk", lambda *a, **k: None)
        clock.t += mod._DISK_BACKOFF_INITIAL_SECONDS + 0.1
        result = await handler("tool:call", _evt(ts="t2"))

        assert handler._disk_backoff_seconds == 0.0  # breaker closed
        assert result.user_message is not None
        assert result.user_message_level == "info"
        assert "recovered" in result.user_message.lower()

    async def test_backoff_grows_and_caps(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        seen = []
        for _ in range(20):
            await handler("tool:call", _evt(ts="t"))
            seen.append(handler._disk_backoff_seconds)
            clock.t = handler._disk_retry_at + 0.001  # jump to just past each cooldown
        assert seen[0] == mod._DISK_BACKOFF_INITIAL_SECONDS
        assert seen[1] > seen[0]  # doubled
        assert max(seen) == mod._DISK_BACKOFF_MAX_SECONDS  # capped, never unbounded

    async def test_non_enospc_oserror_does_not_open_breaker(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        def _eacces(*_a, **_k):
            raise OSError(errno.EACCES, "permission denied")

        monkeypatch.setattr(handler, "_write_session_to_disk", _eacces)
        result = await handler("tool:call", _evt())

        assert result.user_message is None  # not a disk-full condition
        assert handler._disk_backoff_seconds == 0.0  # breaker stays closed

    async def test_dispatch_still_runs_while_disk_degraded(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        enqueued = []

        class _Dispatcher:
            def enqueue(self, event, data):
                enqueued.append(event)
                return True

        handler._dispatchers = [_Dispatcher()]  # type: ignore[list-item]

        await handler("tool:call", _evt())
        assert enqueued == ["tool:call"], "network dispatch must not depend on disk health"
