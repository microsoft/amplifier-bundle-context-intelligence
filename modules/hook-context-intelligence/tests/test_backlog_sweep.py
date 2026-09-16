"""Backlog-sweep tests — the self-healing catch-up pass (design Increment 1).

Two families of failure are pinned here, and they are asymmetric on purpose:

* Sweeping too LITTLE is a bug the user never sees (their events silently stay
  undelivered), so the bound tests assert the sweep is reported LOUDLY when it
  declines to send something.
* Sweeping too MUCH is bounded waste, not corruption, because the server merges
  on a deterministic node_id.

So every ambiguity resolves toward re-sending, and every refusal to send must be
visible. The tests below encode that asymmetry rather than just "it works".
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import pytest

from amplifier_module_hook_context_intelligence.handlers.backlog_sweep import (
    BacklogSweeper,
    SweepBounds,
    iter_records_from,
    read_session_metadata,
    select_candidates,
)
from amplifier_module_hook_context_intelligence.handlers.delivery_watermark import (
    DeliveryWatermark,
)

DEST = "team-shared"
URL = "https://ci.example.com"


class _Auth:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc

    def headers(self) -> dict[str, str]:
        if self._exc:
            raise self._exc
        return {"Authorization": "Bearer k"}


def _make_session(
    project: Path,
    session_id: str,
    *,
    events: int = 5,
    age_hours: float = 1.0,
    workspace: str = "ws",
) -> Path:
    session_dir = project / "sessions" / session_id / "context-intelligence"
    session_dir.mkdir(parents=True)
    with (session_dir / "events.jsonl").open("w") as fh:
        for i in range(events):
            fh.write(
                json.dumps(
                    {
                        "event": f"e{i}",
                        "workspace": workspace,
                        "timestamp": "2026-09-14T00:00:00Z",
                        "data": {"session_id": session_id, "timestamp": "2026-09-14T00:00:00Z"},
                    }
                )
                + "\n"
            )
    stamp = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
    (session_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "workspace": workspace,
                "working_dir": "/work/x",
                "started_at": stamp,
                "last_event_at": stamp,
                "status": "completed",
            }
        )
    )
    return session_dir


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Outbound spy: records what OUR code sends. Never fabricates a boundary."""

    def __init__(self, outcomes: list[int] | None = None) -> None:
        self.posted: list[dict[str, Any]] = []
        self._outcomes = outcomes

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posted.append(kwargs.get("json", {}))
        if self._outcomes is None:
            return _FakeResponse(202)
        index = len(self.posted) - 1
        code = self._outcomes[index] if index < len(self._outcomes) else 202
        return _FakeResponse(code, "boom" if code >= 400 else "")

    async def __aenter__(self) -> "Self":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Spec:
    """Stand-in with the two fields fanout reads. Mirrors config_resolver.Destination."""

    def __init__(self, include: tuple[str, ...] = ("**",), exclude: tuple[str, ...] = ()) -> None:
        self.include = include
        self.exclude = exclude


def _sweeper(project: Path, **overrides: Any) -> BacklogSweeper:
    kwargs: dict[str, Any] = {
        "destination": DEST,
        "url": URL,
        "auth": _Auth(),
        "project_dir": project,
        # Default to "everything allowed" so the existing delivery tests keep
        # testing DELIVERY. Routing is exercised explicitly below.
        "spec": _Spec(include=("**",)),
        "bounds": SweepBounds(),
    }
    kwargs.update(overrides)
    return BacklogSweeper(**kwargs)


# ---------------------------------------------------------------------------
# Candidate selection + bounds
# ---------------------------------------------------------------------------


class TestSelectCandidates:
    def test_in_window_session_is_selected(self, tmp_path: Path) -> None:
        _make_session(tmp_path, "s1", age_hours=1.0)
        selected, stranded, oldest = select_candidates(tmp_path, SweepBounds())
        assert len(selected) == 1
        assert (stranded, oldest) == (0, 0.0)

    def test_out_of_window_session_is_stranded_and_reported(self, tmp_path: Path) -> None:
        """The age bound must never become a SILENT drop."""
        _make_session(tmp_path, "old", age_hours=24 * 7)
        selected, stranded, oldest = select_candidates(tmp_path, SweepBounds(max_age_hours=48.0))
        assert selected == []
        assert stranded == 1
        assert oldest > 48, "the stranded age must be reported so the bound is audible"

    def test_max_sessions_caps_the_pass(self, tmp_path: Path) -> None:
        for i in range(5):
            _make_session(tmp_path, f"s{i}", age_hours=1.0)
        selected, _, _ = select_candidates(tmp_path, SweepBounds(max_sessions=2))
        assert len(selected) == 2

    def test_oldest_first(self, tmp_path: Path) -> None:
        """A backlog drains in the order it accumulated."""
        _make_session(tmp_path, "newer", age_hours=1.0)
        _make_session(tmp_path, "older", age_hours=10.0)
        selected, _, _ = select_candidates(tmp_path, SweepBounds())
        assert [s.parent.name for s, _ in selected] == ["older", "newer"]

    def test_session_without_metadata_is_skipped(self, tmp_path: Path) -> None:
        session_dir = _make_session(tmp_path, "s1")
        (session_dir / "metadata.json").unlink()
        selected, _, _ = select_candidates(tmp_path, SweepBounds())
        assert selected == []

    def test_naive_timestamp_is_treated_as_utc(self, tmp_path: Path) -> None:
        session_dir = _make_session(tmp_path, "s1")
        (session_dir / "metadata.json").write_text(
            json.dumps({"last_event_at": datetime.now(UTC).replace(tzinfo=None).isoformat()})
        )
        selected, _, _ = select_candidates(tmp_path, SweepBounds())
        assert len(selected) == 1

    def test_missing_sessions_dir_is_not_an_error(self, tmp_path: Path) -> None:
        assert select_candidates(tmp_path, SweepBounds()) == ([], 0, 0.0)

    def test_unreadable_metadata_returns_none(self, tmp_path: Path) -> None:
        session_dir = _make_session(tmp_path, "s1")
        (session_dir / "metadata.json").write_text("{nope")
        assert read_session_metadata(session_dir) is None


class TestIterRecords:
    def test_offsets_are_byte_exact(self, tmp_path: Path) -> None:
        session_dir = _make_session(tmp_path, "s1", events=3)
        records = list(iter_records_from(session_dir / "events.jsonl", 0))
        assert len(records) == 3
        assert records[0][0] == 0
        assert records[-1][1] == (session_dir / "events.jsonl").stat().st_size

    def test_resume_from_offset_skips_what_came_before(self, tmp_path: Path) -> None:
        session_dir = _make_session(tmp_path, "s1", events=3)
        events_path = session_dir / "events.jsonl"
        first_end = next(iter_records_from(events_path, 0))[1]
        assert len(list(iter_records_from(events_path, first_end))) == 2

    def test_partial_final_line_is_not_yielded(self, tmp_path: Path) -> None:
        """A live session may be mid-write; advancing past a half record loses it."""
        session_dir = _make_session(tmp_path, "s1", events=2)
        with (session_dir / "events.jsonl").open("a") as fh:
            fh.write('{"event": "half"')
        assert len(list(iter_records_from(session_dir / "events.jsonl", 0))) == 2


# ---------------------------------------------------------------------------
# The sweep itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSweep:
    async def test_delivers_backlog_and_advances_watermark(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = _make_session(tmp_path, "s1", events=4)
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(tmp_path).run()

        assert report.events_delivered == 4
        assert report.made_progress is True
        watermark, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert watermark.offset == (session_dir / "events.jsonl").stat().st_size
        assert watermark.caught_up(session_dir)

    async def test_payload_uses_working_dir_from_metadata(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """events.jsonl does not carry working_dir; metadata.json does."""
        _make_session(tmp_path, "s1", events=1)
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        await _sweeper(tmp_path).run()
        assert client.posted[0]["working_dir"] == "/work/x"
        assert client.posted[0]["idempotency_key"]

    async def test_does_not_set_replay_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opposite of the manual CLI: keep the server's cheap dedup short-circuit."""
        _make_session(tmp_path, "s1", events=1)
        seen: dict[str, Any] = {}

        class _Recorder(_FakeClient):
            async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                seen["url"] = url
                seen["params"] = kwargs.get("params")
                return await super().post(url, **kwargs)

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _Recorder(),
        )
        await _sweeper(tmp_path).run()
        assert seen["url"] == f"{URL}/events"
        assert not seen["params"]

    async def test_watermark_stops_at_the_first_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONTIGUOUS PREFIX: a later success must not carry the cursor over a gap."""
        session_dir = _make_session(tmp_path, "s1", events=4)
        events_path = session_dir / "events.jsonl"
        boundaries = [end for _s, end, _t in iter_records_from(events_path, 0)]
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _FakeClient([202, 202, 500, 202]),
        )
        report = await _sweeper(tmp_path).run()

        assert report.events_delivered == 2
        watermark, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert watermark.offset == boundaries[1], "cursor jumped a failed record"
        assert watermark.last_error and "500" in watermark.last_error

    async def test_total_failure_makes_no_progress_and_stays_loud(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = _make_session(tmp_path, "s1", events=3)
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _FakeClient([401, 401, 401]),
        )
        report = await _sweeper(tmp_path).run()

        assert report.events_delivered == 0
        assert report.made_progress is False
        assert report.last_error and "401" in report.last_error
        watermark, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert watermark.offset == 0, "watermark advanced through a total outage"
        assert watermark.consecutive_sweeps_without_progress == 1
        assert watermark.last_outcome == "no_progress"

    async def test_auth_failure_is_reported_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(tmp_path, "s1", events=2)
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _FakeClient(),
        )
        report = await _sweeper(tmp_path, auth=_Auth(ValueError("no token"))).run()
        assert report.events_delivered == 0
        assert report.last_error and "auth failure" in report.last_error

    async def test_max_events_budget_is_respected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(tmp_path, "s1", events=10)
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _FakeClient(),
        )
        report = await _sweeper(tmp_path, bounds=SweepBounds(max_events=3)).run()
        assert report.events_delivered == 3
        assert report.backlog_bytes_remaining > 0

    async def test_caught_up_session_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = _make_session(tmp_path, "s1", events=3)
        size = (session_dir / "events.jsonl").stat().st_size
        DeliveryWatermark(destination=DEST, destination_url=URL, offset=size).save(session_dir)
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(tmp_path).run()
        assert client.posted == []
        assert report.sessions_skipped_caught_up == 1

    async def test_malformed_record_halts_the_cursor_rather_than_skipping_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = _make_session(tmp_path, "s1", events=2)
        with (session_dir / "events.jsonl").open("a") as fh:
            fh.write("[1,2,3]\n")
            fh.write(json.dumps({"event": "after", "workspace": "ws", "data": {}}) + "\n")
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _FakeClient(),
        )
        report = await _sweeper(tmp_path).run()
        assert report.events_delivered == 2
        assert any("malformed" in n for n in report.notes)
        watermark, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert not watermark.caught_up(session_dir)

    async def test_empty_project_is_a_clean_noop(self, tmp_path: Path) -> None:
        report = await _sweeper(tmp_path).run()
        assert report.sessions_considered == 0
        assert report.had_work is False
        assert report.made_progress is False


@pytest.mark.asyncio
class TestCancellationSafety:
    """A cancelled sweep must KEEP the progress it actually made.

    Found in a DTU, not in a unit test: the watermark used to be written only
    after the whole window completed, so teardown cancellation threw away every
    delivery the pass had made and the work had to be redone from scratch next
    session. Delivery now commits in chunks, and cancellation persists the
    contiguous prefix reached so far.
    """

    async def test_cancelled_sweep_keeps_its_contiguous_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = _make_session(tmp_path, "s1", events=20)
        boundaries = [end for _s, end, _t in iter_records_from(session_dir / "events.jsonl", 0)]

        posted = 0

        class _SlowClient(_FakeClient):
            async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                nonlocal posted
                posted += 1
                if posted > 5:
                    # Stand in for teardown arriving mid-pass.
                    raise asyncio.CancelledError
                return await super().post(url, **kwargs)

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _SlowClient(),
        )
        with pytest.raises(asyncio.CancelledError):
            await _sweeper(tmp_path).run()

        watermark, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert watermark.offset == boundaries[4], (
            f"cancelled sweep lost its progress: offset {watermark.offset}, "
            f"expected {boundaries[4]} (5 delivered records)"
        )
        assert watermark.last_outcome == "cancelled"

    async def test_cancelled_before_any_delivery_records_no_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = _make_session(tmp_path, "s1", events=5)

        class _DeadClient(_FakeClient):
            async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                raise asyncio.CancelledError

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: _DeadClient(),
        )
        with pytest.raises(asyncio.CancelledError):
            await _sweeper(tmp_path).run()

        watermark, _ = DeliveryWatermark.load(session_dir, DEST, destination_url=URL)
        assert watermark.offset == 0
        assert watermark.last_outcome == "no_progress"


@pytest.mark.asyncio
class TestRoutingIsEnforced:
    """The sweep MUST obey each session's own include/exclude.

    A project directory can hold sessions from several working directories --
    `project_slug` falls back to "default" when the capability is unavailable, so
    unrelated trees collide there routinely. Forwarding every session it finds to
    whatever the CURRENT session matched would reroute one session's events using
    another session's permissions. An event delivered where it was excluded cannot
    be recalled, so this is a leak, not an inefficiency.

    Routing therefore INVERTS this module's usual failure bias: everything else
    fails toward re-sending, this fails toward NOT sending.
    """

    async def test_excluded_session_is_never_posted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(tmp_path, "secret", events=5)
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(tmp_path, spec=_Spec(include=("**",), exclude=("/work/**",))).run()

        assert client.posted == [], "events were sent to an EXCLUDED destination"
        assert report.events_delivered == 0
        assert report.sessions_skipped_filtered == 1

    async def test_included_session_is_swept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(tmp_path, "ok", events=5)
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(tmp_path, spec=_Spec(include=("/work/**",))).run()
        assert report.events_delivered == 5
        assert report.sessions_skipped_filtered == 0

    async def test_empty_include_matches_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty include is 'nowhere', never 'everywhere'."""
        _make_session(tmp_path, "s1", events=5)
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(tmp_path, spec=_Spec(include=())).run()
        assert client.posted == []
        assert report.sessions_skipped_filtered == 1

    async def test_exclude_wins_over_include(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_session(tmp_path, "s1", events=5)
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(
            tmp_path, spec=_Spec(include=("/work/**",), exclude=("/work/x/**",))
        ).run()
        assert client.posted == []
        assert report.sessions_skipped_filtered == 1

    async def test_mixed_project_dir_routes_each_session_by_its_own_working_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE leak scenario: two working dirs colliding in one project dir."""
        allowed = _make_session(tmp_path, "public", events=4)
        secret = _make_session(tmp_path, "client-x", events=4)
        for session_dir, wd in ((allowed, "/work/public"), (secret, "/work/client-x")):
            meta = json.loads((session_dir / "metadata.json").read_text())
            meta["working_dir"] = wd
            (session_dir / "metadata.json").write_text(json.dumps(meta))

        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(
            tmp_path, spec=_Spec(include=("**",), exclude=("/work/client-x/**",))
        ).run()

        assert report.events_delivered == 4, "the permitted session should still heal"
        assert report.sessions_skipped_filtered == 1
        secret_wm = secret / "delivery" / f"{DEST}.json"
        assert not secret_wm.exists(), "the excluded session was touched at all"

    async def test_missing_working_dir_is_blocked_not_assumed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail CLOSED: unprovable routing is refused, and said out loud."""
        session_dir = _make_session(tmp_path, "s1", events=5)
        meta = json.loads((session_dir / "metadata.json").read_text())
        del meta["working_dir"]
        (session_dir / "metadata.json").write_text(json.dumps(meta))

        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(tmp_path, spec=_Spec(include=("**",))).run()

        assert client.posted == [], "swept a session whose routing could not be proven"
        assert report.sessions_blocked_unprovable == 1
        assert any("BLOCKED" in n for n in report.notes)

    async def test_blank_working_dir_is_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = _make_session(tmp_path, "s1", events=5)
        meta = json.loads((session_dir / "metadata.json").read_text())
        meta["working_dir"] = "   "
        (session_dir / "metadata.json").write_text(json.dumps(meta))
        client = _FakeClient()
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.httpx.AsyncClient",
            lambda *a, **k: client,
        )
        report = await _sweeper(tmp_path, spec=_Spec(include=("**",))).run()
        assert client.posted == []
        assert report.sessions_blocked_unprovable == 1
