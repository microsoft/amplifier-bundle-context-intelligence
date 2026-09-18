"""Delivery-watermark tests — the resume cursor (design Increment 0).

The watermark is only ever READ by a later session's backlog sweep, so a wrong
value here is invisible in this process and shows up as either wasted
re-delivery (harmless) or a SKIPPED EVENT (silent data loss). These tests pin
the difference.

The load-bearing one is ``test_guard_reset_survives_save``. See its docstring.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.handlers.delivery_watermark import (
    DeliveryWatermark,
    SweepLock,
    sanitize_destination,
)
from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DestinationDispatcher,
    _SessionWatermarkState,
    _WatermarkKey,
)

DEST = "team-shared"
URL = "https://ci.example.com"


def _session(tmp_path: Path, *, lines: int = 10) -> Path:
    session_dir = tmp_path / "sessions" / "s1" / "context-intelligence"
    session_dir.mkdir(parents=True)
    with (session_dir / "events.jsonl").open("w") as fh:
        for i in range(lines):
            fh.write(json.dumps({"event": f"e{i}", "workspace": "ws", "data": {}}) + "\n")
    return session_dir


def _dispatcher(**overrides: object) -> _DestinationDispatcher:
    defaults: dict[str, object] = dict(
        name=DEST,
        url=URL,
        api_key="k",
        workspace="ws",
        dispatch_timeout=10.0,
        failure_threshold=3,
        queue_capacity=256,
        close_drain_timeout=2.0,
        backoff_jitter=False,
    )
    defaults.update(overrides)
    return _DestinationDispatcher(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# File mechanics
# ---------------------------------------------------------------------------


class TestWatermarkFile:
    def test_absent_watermark_reads_as_zero(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        wm, notes = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert wm.offset == 0
        assert notes == []

    def test_round_trip(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        wm, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        wm.offset = 120
        wm.delivered_lines = 3
        wm.save(session_dir)

        again, notes = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert (again.offset, again.delivered_lines, notes) == (120, 3, [])

    def test_save_is_monotonic(self, tmp_path: Path) -> None:
        """A stale writer must never move the cursor backwards.

        Offsets stay INSIDE the log on purpose: an offset past EOF would trip
        the truncation guard on load and reset to 0, which would make this test
        pass or fail for the wrong reason.
        """
        session_dir = _session(tmp_path)
        size = (session_dir / "events.jsonl").stat().st_size
        ahead = DeliveryWatermark(destination=DEST, destination_url=URL, offset=size)
        ahead.save(session_dir)

        behind = DeliveryWatermark(destination=DEST, destination_url=URL, offset=5)
        behind.save(session_dir)

        loaded, notes = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert loaded.offset == size
        assert notes == []

    def test_transient_reset_flag_is_never_persisted(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        wm = DeliveryWatermark(destination=DEST, destination_url=URL, offset=1)
        wm.reset_by_guard = True
        wm.save(session_dir)
        raw = json.loads(DeliveryWatermark.path_for(session_dir, DEST).read_text())
        assert "reset_by_guard" not in raw
        assert wm.reset_by_guard is False, "flag must be cleared once the write lands"

    def test_destination_name_is_sanitized_for_the_filename(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        wm = DeliveryWatermark(destination="team/prod:eu", destination_url=URL, offset=7)
        wm.save(session_dir)
        path = DeliveryWatermark.path_for(session_dir, "team/prod:eu")
        assert path.name == "team_prod_eu.json"
        # The RAW name is preserved inside, so a post-sanitization collision is
        # detectable rather than silent.
        assert json.loads(path.read_text())["destination"] == "team/prod:eu"
        assert sanitize_destination("") == "_"


# ---------------------------------------------------------------------------
# Guards — every one must fail toward RE-SENDING, never toward SKIPPING
# ---------------------------------------------------------------------------


class TestGuards:
    def test_corrupt_watermark_resets_to_zero(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        DeliveryWatermark.dir_for(session_dir).mkdir(parents=True, exist_ok=True)
        DeliveryWatermark.path_for(session_dir, DEST).write_text("{not json")

        wm, notes = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert wm.offset == 0
        assert any("unreadable" in n for n in notes)

    def test_non_object_watermark_resets_to_zero(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        DeliveryWatermark.dir_for(session_dir).mkdir(parents=True, exist_ok=True)
        DeliveryWatermark.path_for(session_dir, DEST).write_text("[1, 2, 3]")

        wm, notes = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert wm.offset == 0
        assert any("unreadable" in n for n in notes)

    def test_truncated_log_resets_to_zero(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        DeliveryWatermark(destination=DEST, destination_url=URL, offset=10_000).save(session_dir)
        (session_dir / "events.jsonl").write_text('{"event":"x","workspace":"w","data":{}}\n')

        wm, notes = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert wm.offset == 0
        assert any("shrank" in n for n in notes)

    def test_guard_reset_survives_save(self, tmp_path: Path) -> None:
        """REGRESSION: a guard rewind must actually reach disk.

        Two individually-correct rules collide here. Monotonicity says "never
        move the cursor backwards"; the truncation guard says "rewind, the log
        was replaced". Without an explicit override, `save()` re-reads the stale
        on-disk offset, sees it is larger, and silently restores it -- so the
        rewind never lands and the watermark points past the end of a truncated
        log FOREVER, skipping every event in it.

        That is the one genuinely lossy failure mode in this design, it is
        invisible to inspection, and it was caught only by executing the path.
        """
        session_dir = _session(tmp_path)
        DeliveryWatermark(destination=DEST, destination_url=URL, offset=10_000).save(session_dir)
        (session_dir / "events.jsonl").write_text('{"event":"x","workspace":"w","data":{}}\n')

        rewound, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert rewound.offset == 0
        rewound.save(session_dir)

        reloaded, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert reloaded.offset == 0, (
            "monotonicity clobbered the truncation rewind: the watermark still points "
            "past the end of the log, so every event in it would be skipped"
        )

    def test_changed_destination_url_resets_to_zero(self, tmp_path: Path) -> None:
        """Same name, different sink: claiming 'already delivered' would lose data."""
        session_dir = _session(tmp_path)
        DeliveryWatermark(destination=DEST, destination_url=URL, offset=120).save(session_dir)

        wm, notes = DeliveryWatermark.load(
            session_dir, DEST, destination_url="https://elsewhere.example.com"
        )
        assert wm.offset == 0
        assert any("url changed" in n for n in notes)

    def test_same_url_does_not_reset(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        DeliveryWatermark(destination=DEST, destination_url=URL, offset=120).save(session_dir)
        wm, notes = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert (wm.offset, notes) == (120, [])


class TestBacklogReporting:
    def test_backlog_bytes_and_caught_up(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        size = (session_dir / "events.jsonl").stat().st_size
        wm = DeliveryWatermark(destination=DEST, destination_url=URL)
        assert wm.backlog_bytes(session_dir) == size
        assert not wm.caught_up(session_dir)
        wm.offset = size
        assert wm.backlog_bytes(session_dir) == 0
        assert wm.caught_up(session_dir)


class TestSweepLock:
    def test_second_holder_is_refused_and_first_releases(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        with SweepLock(session_dir, DEST) as first, SweepLock(session_dir, DEST) as second:
            assert first.held is True
            assert second.held is False
        with SweepLock(session_dir, DEST) as third:
            assert third.held is True

    def test_stale_lock_is_broken(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        with SweepLock(session_dir, DEST) as held:
            assert held.held
            # A lock whose owner died: the file outlives the process.
            fresh = SweepLock(session_dir, DEST, stale_after=-1.0)
            with fresh as broken:
                assert broken.held is True, "a stale lock must not block the sweep forever"


# ---------------------------------------------------------------------------
# Live-path accounting: contiguous prefix, and freeze-on-undelivered
# ---------------------------------------------------------------------------


class TestContiguousPrefixAccounting:
    def test_commit_advances_the_prefix(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        d = _dispatcher()
        for offset in (10, 20, 30):
            d._wm_commit(_WatermarkKey(session_dir=str(session_dir), end_offset=offset))
        state = d._wm_state[str(session_dir)]
        assert (state.committed, state.frozen, state.dirty) == (30, False, True)

    def test_commit_never_moves_backwards(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        d = _dispatcher()
        d._wm_commit(_WatermarkKey(session_dir=str(session_dir), end_offset=99))
        d._wm_commit(_WatermarkKey(session_dir=str(session_dir), end_offset=5))
        assert d._wm_state[str(session_dir)].committed == 99

    def test_a_dropped_event_freezes_the_prefix(self, tmp_path: Path) -> None:
        """THE reason the live path's delivered set is not a contiguous prefix.

        Drops happen at ENQUEUE time when the queue is full, so a dropped record
        can sit between delivered records either side of it. Once anything goes
        undelivered the cursor must stop there, or the sweep would skip it.
        """
        session_dir = _session(tmp_path)
        d = _dispatcher()
        key = str(session_dir)
        d._wm_commit(_WatermarkKey(session_dir=key, end_offset=10))
        d._wm_freeze(_WatermarkKey(session_dir=key, end_offset=20))
        d._wm_commit(_WatermarkKey(session_dir=key, end_offset=30))

        state = d._wm_state[key]
        assert state.frozen is True
        assert state.committed == 10, "the prefix advanced past an undelivered record"

    def test_none_key_is_a_noop(self, tmp_path: Path) -> None:
        d = _dispatcher()
        d._wm_commit(None)
        d._wm_freeze(None)
        assert d._wm_state == {}

    def test_sessions_are_tracked_independently(self, tmp_path: Path) -> None:
        """One dispatcher carries a session AND its sub-sessions."""
        a = _session(tmp_path / "a")
        b = _session(tmp_path / "b")
        d = _dispatcher()
        d._wm_commit(_WatermarkKey(session_dir=str(a), end_offset=50))
        d._wm_freeze(_WatermarkKey(session_dir=str(b), end_offset=50))
        assert d._wm_state[str(a)].frozen is False
        assert d._wm_state[str(b)].frozen is True


class TestWatermarkFlush:
    def test_forced_flush_persists_committed_prefix(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        d = _dispatcher()
        d._wm_commit(_WatermarkKey(session_dir=str(session_dir), end_offset=64))
        d._wm_flush(force=True)

        wm, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert wm.offset == 64
        assert d._wm_state[str(session_dir)].dirty is False

    def test_flush_is_debounced(self, tmp_path: Path) -> None:
        """The watermark helps the NEXT session; it must not fsync per event."""
        session_dir = _session(tmp_path)
        d = _dispatcher()
        d._wm_commit(_WatermarkKey(session_dir=str(session_dir), end_offset=64))
        d._wm_flush()  # immediately after construction -> inside the debounce window
        assert not DeliveryWatermark.path_for(session_dir, DEST).exists()

    def test_flush_never_raises_when_the_directory_is_gone(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        d = _dispatcher()
        d._wm_commit(_WatermarkKey(session_dir=str(session_dir / "vanished"), end_offset=64))
        d._wm_flush(force=True)  # must not raise

    def test_flush_with_no_state_is_a_noop(self) -> None:
        _dispatcher()._wm_flush(force=True)


# ---------------------------------------------------------------------------
# End to end through the real enqueue/worker path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestThroughTheDispatcher:
    async def test_delivered_events_advance_the_watermark(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        d = _dispatcher()

        async def ok(event: str, data: dict[str, Any]) -> str:
            return "delivered"

        d._post = ok  # type: ignore[method-assign]
        for offset in (10, 20, 30):
            assert d.enqueue(
                "e",
                {"session_id": "s1"},
                wm_key=_WatermarkKey(session_dir=str(session_dir), end_offset=offset),
            )
        await asyncio.wait_for(d._queue.join(), timeout=5)
        d._wm_flush(force=True)

        wm, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert wm.offset == 30

    async def test_overflow_drop_freezes_the_watermark(self, tmp_path: Path) -> None:
        session_dir = _session(tmp_path)
        d = _dispatcher(queue_capacity=1)
        key = str(session_dir)

        # Fill the queue, then overflow. No worker is started, so nothing drains.
        assert d.enqueue(
            "e1", {"session_id": "s1"}, wm_key=_WatermarkKey(session_dir=key, end_offset=10)
        )
        assert not d.enqueue(
            "e2", {"session_id": "s1"}, wm_key=_WatermarkKey(session_dir=key, end_offset=20)
        )

        assert d._overflow_dropped == 1
        assert d._wm_state[key].frozen is True

        d._wm_commit(_WatermarkKey(session_dir=key, end_offset=30))
        assert d._wm_state[key].committed == 0, "a drop must pin the cursor where it fell"

    async def test_enqueue_without_a_key_tracks_nothing(self, tmp_path: Path) -> None:
        """Back-compat: the 2-arg call must stay valid and inert."""
        d = _dispatcher()

        async def ok(event: str, data: dict[str, Any]) -> str:
            return "delivered"

        d._post = ok  # type: ignore[method-assign]
        assert d.enqueue("e", {"session_id": "s1"})
        await asyncio.wait_for(d._queue.join(), timeout=5)
        assert d._wm_state == {}


def test_session_watermark_state_defaults() -> None:
    state = _SessionWatermarkState()
    assert (state.committed, state.frozen, state.dirty) == (0, False, False)
