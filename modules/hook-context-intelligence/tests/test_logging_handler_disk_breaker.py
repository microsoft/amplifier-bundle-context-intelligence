"""Tests for the disk-pressure circuit breaker and its (deliberately quiet) alert.

When the disk fills, ``_touch_last_event_at`` / metadata writes / the JSONL
append all fail with ENOSPC. Three requirements drive this behaviour:

1. Do NOT hammer a full filesystem on every event (and do not try to log the
   failure to a log file that also cannot be written). Open a breaker: skip disk
   writes for a growing cooldown, then let one event PROBE for recovery.
2. Tell the user ONCE, quietly. The alert must ride ``HookResult.user_message``
   (which the orchestrator surfaces in the UI) because ``logger.*`` output at
   this moment cannot reach disk -- but it is at most ONE ``warning`` per
   degraded episode plus at most one all-clear, never an error-level banner
   repeating on a timer. What is lost is observability data, not the user's
   session, and the default (local-only) configuration is the common case.
3. Record the episode DURABLY where operators already look: a
   ``context-intelligence:disk-pressure`` event in the session's own
   events.jsonl, written on recovery -- the first moment the disk is known
   writable again.

Verifies:
- ENOSPC opens the breaker and returns exactly one warning-level user_message.
- No "PERMANENT DATA LOSS" / shouty wording in any alert (see PR #105 review).
- While the breaker is open, disk writes are skipped (no per-event hammering).
- The alert fires ONCE PER EPISODE -- silent thereafter even long past any
  rate-limit window -- and re-arms only after a recovery.
- A successful probe closes the breaker, emits a recovery message, and writes
  the durable disk-pressure record; normal writes resume.
- A degraded blip we never warned about produces no orphan "recovered" notice.
- Backoff grows (capped) across repeated failures.
- A non-ENOSPC OSError does NOT open the breaker and surfaces no user_message.
- Event dispatch (network fan-out) still happens while the disk is degraded.
- THE #101 SEAM: ENOSPC raised by the REAL ``_atomic_write_text`` (merged in
  #101, which cleans up its temp file and re-raises) is classified by this
  breaker. Every other test here stubs ``_write_session_to_disk``; this one does
  not, because that stub is exactly what would hide a broken seam.
"""

from __future__ import annotations

import errno
import json
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


def _read_events(resolver: _FakeResolver, sid: str = "s1") -> list[dict]:
    f = resolver.session_dir(sid) / "events.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines()]


class TestDiskBreaker:
    async def test_enospc_opens_breaker_and_alerts_user(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        result = await handler("tool:call", _evt())

        assert result.action == "continue"
        # Tell the user -- but as a warning, not an error-level banner.
        assert result.user_message is not None
        assert result.user_message_level == "warning"
        assert result.user_message_source == "context-intelligence"
        assert "disk full" in result.user_message.lower()
        # Breaker is now open.
        assert handler._disk_backoff_seconds == mod._DISK_BACKOFF_INITIAL_SECONDS

    async def test_no_alert_claims_permanent_data_loss(self, tmp_path, monkeypatch) -> None:
        """PR #105 review (Salil): the default config IS the no-destination path.

        An error-level "PERMANENT DATA LOSS" banner on the ordinary local-only
        setup overstates the harm (this is telemetry, not the user's work) and
        trains people to ignore the channel. Lock the wording down.
        """
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        assert handler._dispatchers == []  # the DEFAULT configuration
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        result = await handler("tool:call", _evt())

        assert result.user_message_level == "warning"
        assert "PERMANENT DATA LOSS" not in result.user_message
        assert result.user_message == result.user_message.replace("DISK FULL", "")  # no shouting
        # Still honest about what happened, without overclaiming.
        assert "not recoverable" in result.user_message
        assert "observability data only" in result.user_message

    async def test_disk_full_but_delivered_says_so(self, tmp_path, monkeypatch) -> None:
        # Disk full BUT a dispatcher accepted the event -> not lost, just a stale
        # local log. The message must distinguish this from the lost case.
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
        assert "still reaching the configured server" in result.user_message
        assert "not recoverable" not in result.user_message

    async def test_disk_full_and_queue_dropped_reports_unrecoverable(
        self, tmp_path, monkeypatch
    ) -> None:
        # Disk full AND the delivery queue is full (enqueue returns False) ->
        # the event reached no sink. Still a warning, but honest about the loss.
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        class _FullDispatcher:
            def enqueue(self, event, data):
                return False  # queue full, dropped

        handler._dispatchers = [_FullDispatcher()]  # type: ignore[list-item]

        result = await handler("tool:call", _evt())

        assert result.user_message_level == "warning"
        assert "not recoverable" in result.user_message

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


class TestAlertFiresOncePerEpisode:
    """The alert is episode-scoped, NOT rate-limited on a timer.

    The original PR re-fired the alert every _LOG_RATE_LIMIT_SECONDS for as long
    as the disk stayed full. On the default configuration that is a repeating
    banner the user can do nothing further about after the first one.
    """

    async def test_second_degraded_event_is_silent(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        first = await handler("tool:call", _evt())
        assert first.user_message is not None

        clock.t += mod._DISK_BACKOFF_INITIAL_SECONDS + 0.1
        second = await handler("tool:call", _evt(ts="t2"))
        assert second.user_message is None

    async def test_still_silent_long_past_the_rate_limit_window(
        self, tmp_path, monkeypatch
    ) -> None:
        """The load-bearing assertion: elapsed time alone must NOT re-arm it.

        This is what distinguishes "once per episode" from the timer-based
        rate limit it replaced -- and it is the assertion that goes RED if
        anyone reintroduces a _LOG_RATE_LIMIT_SECONDS gate here.
        """
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)

        assert (await handler("tool:call", _evt())).user_message is not None

        # Ten full rate-limit windows of a still-full disk: still one alert.
        for i in range(10):
            clock.t += mod._LOG_RATE_LIMIT_SECONDS * 2
            later = await handler("tool:call", _evt(ts=f"t{i}"))
            assert later.user_message is None, (
                f"alert re-fired after {i + 1} rate-limit window(s); it must be "
                "once per degraded episode, not once per timer window"
            )

    async def test_alert_re_arms_after_a_recovery(self, tmp_path, monkeypatch) -> None:
        """A NEW episode, after a real recovery, does warn again."""
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)
        assert (await handler("tool:call", _evt())).user_message is not None

        # Recover.
        monkeypatch.setattr(handler, "_write_session_to_disk", lambda *a, **k: None)
        clock.t += mod._DISK_BACKOFF_INITIAL_SECONDS + 0.1
        assert (await handler("tool:call", _evt(ts="t2"))).user_message_level == "info"

        # Fill up again -> a genuinely new episode -> warn again.
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)
        clock.t += 1.0
        again = await handler("tool:call", _evt(ts="t3"))
        assert again.user_message is not None
        assert again.user_message_level == "warning"


class TestRecovery:
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

    async def test_unwarned_blip_produces_no_orphan_all_clear(self, tmp_path, monkeypatch) -> None:
        """If we never told the user it broke, don't tell them it's fixed.

        An all-clear with no preceding warning is pure noise -- the user has no
        idea what recovered.
        """
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        # Force the degraded state WITHOUT ever emitting the alert (as would
        # happen for a blip absorbed inside an already-alerted episode).
        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)
        await handler("tool:call", _evt())
        handler._disk_alert_sent = False  # pretend we never surfaced it

        monkeypatch.setattr(handler, "_write_session_to_disk", lambda *a, **k: None)
        clock.t += mod._DISK_BACKOFF_INITIAL_SECONDS + 0.1
        result = await handler("tool:call", _evt(ts="t2"))

        assert handler._disk_backoff_seconds == 0.0  # still recovered internally
        assert result.user_message is None  # but said nothing about it


class TestDurableEpisodeRecord:
    """The episode is recorded to context-intelligence on disk, not only shown."""

    async def test_recovery_writes_disk_pressure_event(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        resolver = _FakeResolver(tmp_path, "proj")
        handler = LoggingHandler(resolver)

        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)
        await handler("tool:call", _evt())  # episode starts
        clock.t += 1.0
        await handler("tool:call", _evt(ts="t2"))  # a second event skipped

        # Recover for real (unstub) so the record actually lands on disk.
        monkeypatch.undo()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        clock.t += mod._DISK_BACKOFF_INITIAL_SECONDS + 0.1
        await handler("tool:call", _evt(ts="t3"))

        records = [
            e for e in _read_events(resolver) if e["event"] == "context-intelligence:disk-pressure"
        ]
        assert len(records) == 1, f"expected one durable episode record, got: {records}"
        payload = records[0]["data"]
        assert payload["events_not_written"] == 2
        assert payload["delivered_to_server"] is False
        assert payload["user_alerted"] is True
        assert payload["degraded_seconds"] > 0

    async def test_record_write_failure_never_breaks_the_event_path(
        self, tmp_path, monkeypatch
    ) -> None:
        """A diagnostics write must never raise into event handling."""
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        handler = LoggingHandler(_FakeResolver(tmp_path, "proj"))

        monkeypatch.setattr(handler, "_write_session_to_disk", _enospc)
        await handler("tool:call", _evt())

        monkeypatch.setattr(handler, "_write_session_to_disk", lambda *a, **k: None)
        monkeypatch.setattr(handler, "_append_event", _enospc)  # the record write itself fails
        clock.t += mod._DISK_BACKOFF_INITIAL_SECONDS + 0.1

        result = await handler("tool:call", _evt(ts="t2"))  # must not raise
        assert result.action == "continue"
        assert handler._disk_backoff_seconds == 0.0  # breaker still closed cleanly


class TestBreakerMechanics:
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


class TestAtomicWriteSeam:
    """THE #101 SEAM -- no _write_session_to_disk stub anywhere in this class.

    #105's ENOSPC classification depends on a contract #101 introduced:
    ``_atomic_write_text`` writes to a temp file, and on failure unlinks that
    temp file and RE-RAISES the OSError unchanged. If #101 ever swallowed that
    error (or wrapped it in a different exception type), the breaker would
    silently never open and every test that stubs ``_write_session_to_disk``
    would still pass. This drives the real code path instead.
    """

    async def test_enospc_from_real_atomic_write_opens_breaker(self, tmp_path, monkeypatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(mod.time, "monotonic", clock)
        resolver = _FakeResolver(tmp_path, "proj")
        handler = LoggingHandler(resolver)

        real_write_text = Path.write_text

        def _enospc_on_tmp(self: Path, *args, **kwargs):
            # Only the atomic writer's temp file "fills the disk" -- this is the
            # exact failure #101's helper is built to survive.
            if self.name.endswith(".tmp"):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _enospc_on_tmp)

        result = await handler("session:start", _evt())

        # The breaker classified an ENOSPC that came out of the REAL
        # _atomic_write_text, through _ensure_metadata, unstubbed.
        assert handler._disk_backoff_seconds == mod._DISK_BACKOFF_INITIAL_SECONDS
        assert result.user_message is not None
        assert result.user_message_level == "warning"

        # And #101's own guarantee still holds: no 0-byte metadata.json, and no
        # temp file left behind.
        meta = resolver.session_dir("s1") / "metadata.json"
        assert not meta.exists() or meta.stat().st_size > 0
        assert list(resolver.session_dir("s1").glob("*.tmp")) == []
