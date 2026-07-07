"""Tests for the v2 minimal circuit breaker on _DestinationDispatcher.

See ``forwarding-diagnostics-design.md`` Part 2 for the design. These tests pin
the council's tester-breaker findings: the detector must be RATE-over-a-window
(not a streak), gated by a minimum sample count AND a minimum sustained
wall-clock duration, with 403 and auth-token-production failures classified
correctly (403 never feeds the breaker; auth-token-production failure DOES).

Constants are monkeypatched per-test (via the ``logging_handler`` module
object) to keep the suite fast -- production defaults
(_BREAKER_MIN_OPEN_SECONDS=30.0, _BREAKER_PROBE_INTERVAL=300.0) would make
these tests slow or require sleeping for real.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_hook_context_intelligence.handlers import logging_handler
from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    LoggingHandler,
    _DestinationDispatcher,
)

LOGGER_PATH = "amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _mock_client(side_effects: list[Any]) -> AsyncMock:
    """AsyncMock httpx client whose .post() returns responses in sequence."""
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = side_effects
    return client


def _dispatcher(**overrides: Any) -> _DestinationDispatcher:
    defaults: dict[str, Any] = dict(
        name="test-dest",
        url="https://ci.example.com",
        api_key="test-key",
        workspace="ws",
        dispatch_timeout=10.0,
        failure_threshold=1,
        queue_capacity=256,
        close_drain_timeout=2.0,
        backoff_initial=1.0,
        backoff_max=30.0,
        backoff_jitter=False,
    )
    defaults.update(overrides)
    return _DestinationDispatcher(**defaults)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _drain(d: _DestinationDispatcher, events: list[str], timeout: float = 5.0) -> None:
    for i, name in enumerate(events):
        d.enqueue(name, {"session_id": f"s-{name}-{i}"})
    await asyncio.wait_for(d._queue.join(), timeout=timeout)


class _FakeClock:
    """Deterministic stand-in for the ``time`` module inside ``logging_handler``.

    ``logging_handler`` only ever calls ``time.monotonic()`` (verified by
    grepping the module) so this is the entire surface that needs faking.
    It is installed via ``monkeypatch.setattr(logging_handler, "time", ...)``,
    which rebinds ONLY the ``time`` name in ``logging_handler``'s own module
    namespace -- it does not touch the real stdlib ``time`` module object,
    so asyncio's internal clock (which holds its own independent reference
    to the genuine module) is completely unaffected. Advancing this clock
    therefore cannot desynchronize ``asyncio.wait_for`` / queue timeouts.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _FakeResolver:
    """Minimal resolver for LoggingHandler (mirrors test_logging_handler_fanout.py)."""

    def __init__(self, base_path: Path, project_slug: str = "proj") -> None:
        self.base_path = base_path
        self.project_slug = project_slug
        self.workspace: str | None = "ws"
        self.parent_id: str = ""
        self.resolve_instance_id: str = ""
        self.working_dir: str = ""

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"


# ---------------------------------------------------------------------------
# 1. Opens on RATE, not streak
# ---------------------------------------------------------------------------


class TestOpensOnRate:
    async def test_opens_on_rate_18_of_20(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """18 hard (401) + 2 delivered (200), interleaved -- ratio 0.9 over a full
        20-sample window -- opens, with exactly ONE breaker_open WARNING and ONE
        breaker_open durable record."""
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        d = _dispatcher(forwarding_log_dir=tmp_path)
        statuses = [401, 401, 200] + [401] * 9 + [200] + [401] * 7
        assert len(statuses) == 20
        assert statuses.count(401) == 18
        assert statuses.count(200) == 2
        d._client = _mock_client([_make_response(s) for s in statuses])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            await _drain(d, [f"e{i}" for i in range(20)])

            open_warnings = [
                c for c in mock_logger.warning.call_args_list if "forwarding paused" in str(c)
            ]
            assert len(open_warnings) == 1, (
                f"expected exactly 1 breaker_open warning, got {len(open_warnings)}: "
                f"{mock_logger.warning.call_args_list}"
            )

        assert d._breaker_open is True

        records = [
            json.loads(line)
            for line in (tmp_path / f"forwarding-{_today_utc()}.jsonl").read_text().splitlines()
        ]
        open_records = [r for r in records if r["kind"] == "breaker_open"]
        assert len(open_records) == 1, f"expected exactly 1 breaker_open record, got {open_records}"
        await d.close()


# ---------------------------------------------------------------------------
# 2. Does NOT open on 50/50 flapping
# ---------------------------------------------------------------------------


class TestFlappingDoesNotOpen:
    async def test_50_50_flapping_never_opens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alternating 401/200 -- ratio stays 0.5 < 0.9 -- never opens."""
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        d = _dispatcher()
        statuses = [401, 200] * 20
        d._client = _mock_client([_make_response(s) for s in statuses])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            await _drain(d, [f"e{i}" for i in range(40)])

        assert d._breaker_open is False
        await d.close()


# ---------------------------------------------------------------------------
# 3. 403-only stream never opens (per-event skip, not breaker-eligible)
# ---------------------------------------------------------------------------


class Test403NeverOpens:
    async def test_403_only_never_feeds_breaker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        d = _dispatcher()
        d._client = _mock_client([_make_response(403)] * 30)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            await _drain(d, [f"e{i}" for i in range(30)])

        assert d._breaker_open is False
        assert len(d._breaker_window) == 0, "403 must never enter the breaker window"
        assert d._queue.qsize() == 0, "each 403 is a per-event skip; all events processed"
        await d.close()


# ---------------------------------------------------------------------------
# 4. Wall-clock gate
# ---------------------------------------------------------------------------


class TestWallClockGate:
    async def test_does_not_open_before_floor_then_opens_after(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """10 hard failures processed within a sub-threshold wall-clock window
        (rate + samples satisfied) must NOT open the breaker; once real elapsed
        time crosses the (monkeypatched, small) floor, the next hard failure
        opens it."""
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.2)
        d = _dispatcher()
        d._client = _mock_client([_make_response(401)] * 11)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            await _drain(d, [f"e{i}" for i in range(10)])
            assert d._breaker_open is False, (
                "must not open before the wall-clock floor elapses, even though"
                " rate and sample count are already satisfied"
            )

            # Let real wall-clock time cross the (monkeypatched) floor.
            await asyncio.sleep(0.3)

            await _drain(d, ["e-late"])

        assert d._breaker_open is True, "elapsed time past the floor must allow opening"
        await d.close()


# ---------------------------------------------------------------------------
# 5. Auth-token-production failure feeds the breaker as HARD
# ---------------------------------------------------------------------------


class TestAuthTokenProductionFailureIsHard:
    async def test_persistent_token_production_failure_opens_breaker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A persistent local inability to mint a Bearer (headers() always
        raises) must feed the breaker as HARD -- not be buried as an ordinary
        transient that retries forever in silence."""
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        d = _dispatcher()

        def _raise_auth_error() -> dict[str, str]:
            raise RuntimeError("token production failed")

        d._strategy.headers = _raise_auth_error  # type: ignore[method-assign]
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            await _drain(d, [f"e{i}" for i in range(12)])

        assert d._breaker_open is True
        await d.close()


# ---------------------------------------------------------------------------
# 6. Pure transient (5xx) never opens, keeps retrying
# ---------------------------------------------------------------------------


class TestPureTransientNeverOpens:
    async def test_pure_5xx_stream_never_opens_and_keeps_retrying(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        d = _dispatcher()
        d._client = _mock_client([_make_response(503)] * 30 + [_make_response(200)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            await _drain(d, ["e1"])  # same event retried 30x, then delivered

        assert d._client.post.call_count == 31, (
            "must have retried the transient 30 times then delivered"
        )
        assert d._breaker_open is False
        assert True not in d._breaker_window, "transient (5xx) must never enter the breaker window"
        await d.close()


# ---------------------------------------------------------------------------
# 7. Slow re-probe auto-recovers (no restart needed)
# ---------------------------------------------------------------------------


class TestAutoRecovery:
    async def test_successful_probe_closes_breaker_and_resumes_delivery(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        monkeypatch.setattr(logging_handler, "_BREAKER_PROBE_INTERVAL", 0.0)
        d = _dispatcher(forwarding_log_dir=tmp_path)
        responses = [_make_response(401)] * 10 + [_make_response(200), _make_response(200)]
        d._client = _mock_client(responses)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            await _drain(d, [f"e{i}" for i in range(10)])
            assert d._breaker_open is True, "breaker must be open before the probe"

            # Probe interval monkeypatched to 0 -- immediately due.
            await _drain(d, ["e-probe"])
            assert d._breaker_open is False, "a successful probe must close the breaker"

            recon_calls = [
                c
                for c in mock_logger.info.call_args_list
                if "Reconnected to" in str(c) and "resuming delivery" in str(c)
            ]
            assert len(recon_calls) == 1, (
                f"expected exactly one reconnect/breaker_close INFO, got {recon_calls}"
            )

            # Subsequent event dispatches normally -- breaker stays CLOSED.
            await _drain(d, ["e-after"])

        assert d._client.post.call_count == 12  # 10 opening + 1 probe + 1 normal after
        records = [
            json.loads(line)
            for line in (tmp_path / f"forwarding-{_today_utc()}.jsonl").read_text().splitlines()
        ]
        assert any(r["kind"] == "breaker_close" for r in records), "expected a breaker_close record"
        await d.close()


# ---------------------------------------------------------------------------
# 8. While OPEN and not probe-due: no dispatch, console stays quiet
# ---------------------------------------------------------------------------


class TestOpenSuppressesDispatchWhenNotProbeDue:
    async def test_no_dispatch_and_no_repeated_warnings_while_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        # Leave _BREAKER_PROBE_INTERVAL at its large default (300s) -- never due.
        d = _dispatcher()
        d._client = _mock_client([_make_response(401)] * 10)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            await _drain(d, [f"e{i}" for i in range(10)])
            assert d._breaker_open is True

            calls_before = d._client.post.call_count
            warnings_before = len(mock_logger.warning.call_args_list)

            await _drain(d, [f"e-suppressed-{i}" for i in range(5)])

            assert d._client.post.call_count == calls_before, (
                "no dispatch attempt while OPEN and not probe-due"
            )
            assert len(mock_logger.warning.call_args_list) == warnings_before, (
                "no additional console warnings while OPEN and quiet"
            )

        await d.close()


# ---------------------------------------------------------------------------
# 9. Best-effort safety: sink/console failures never break the breaker or flow
# ---------------------------------------------------------------------------


class TestBestEffortSafety:
    async def test_breaker_opens_despite_sink_write_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")  # mkdir() against this raises

        d = _dispatcher(forwarding_log_dir=blocker)
        d._client = _mock_client([_make_response(401)] * 10)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            await _drain(d, [f"e{i}" for i in range(10)])

        assert d._breaker_open is True, "breaker must open even when the durable sink write fails"
        assert d._queue.qsize() == 0, "dispatch flow must still complete"
        await d.close()

    async def test_state_consistent_when_console_warning_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        d = _dispatcher()
        d._client = _mock_client([_make_response(401)] * 10)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            mock_logger.warning.side_effect = RuntimeError("console broken")
            await _drain(d, [f"e{i}" for i in range(10)])

        # _breaker_open is set True BEFORE the raising logger.warning() call in
        # _maybe_open_breaker, so it is already correct when the exception
        # propagates; the worker's outer unclassified-exception handler catches
        # it, drops that one poisoned event, and keeps draining.
        assert d._breaker_open is True
        assert d._queue.qsize() == 0, "worker must keep draining despite the raising logger"

        # Worker must still be alive and functioning post-exception.
        await _drain(d, ["e-after"], timeout=2.0)
        assert d._queue.qsize() == 0
        await d.close()


# ---------------------------------------------------------------------------
# 10. events.jsonl durability is independent of breaker state
# ---------------------------------------------------------------------------


class TestEventsJsonlDurabilityIndependentOfBreaker:
    async def test_event_still_written_when_dispatcher_breaker_is_open(
        self, tmp_path: Path
    ) -> None:
        """LoggingHandler.__call__ writes events.jsonl to disk BEFORE fanning out
        to dispatchers -- this must hold even when every installed dispatcher's
        circuit breaker is OPEN."""
        handler = LoggingHandler(_FakeResolver(tmp_path))

        mock_d = MagicMock(spec=_DestinationDispatcher)
        mock_d._breaker_open = True  # simulate an OPEN destination
        await handler.set_dispatchers([mock_d])

        await handler(
            "session:start",
            {"session_id": "s-open-breaker", "timestamp": "t0", "working_dir": "/w"},
        )

        jsonl_path = (
            tmp_path
            / "proj"
            / "sessions"
            / "s-open-breaker"
            / "context-intelligence"
            / "events.jsonl"
        )
        assert jsonl_path.exists(), "events.jsonl must be written even when the dispatcher is OPEN"
        record = json.loads(jsonl_path.read_text().strip())
        assert record["event"] == "session:start"
        # enqueue is still called -- the breaker gate lives in the worker, not
        # in enqueue/fan-out.
        mock_d.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# 11. D3 regression: real production floor, rate-over-window at the wall clock
# ---------------------------------------------------------------------------
#
# Every test above monkeypatches _BREAKER_MIN_OPEN_SECONDS to 0.0 (or a tiny
# value) to keep the suite fast -- which means none of them exercise the
# wall-clock sustain gate against the REAL production floor (30.0s) combined
# with an interleaved (non-streak) failing stream. That combination is
# exactly where D3 lived:
#
#   _breaker_record_delivered() used to set ``self._first_hard_ts = None``
#   UNCONDITIONALLY on every DELIVERED outcome. ``_first_hard_ts`` is the
#   start of the sustained-failure clock that ``_maybe_open_breaker``'s
#   wall-clock gate checks. A destination that is ~90% failing but whose
#   successes arrive more often than every 30s would have its sustain clock
#   wiped by every success -- so it could NEVER accumulate a continuous 30s
#   failing run, and the breaker would never open in production. This
#   directly contradicts the design's promise: "rate-over-a-window (not a
#   streak); a lone 200 no longer resets a half-broken destination."
#
# This test pins the fix by holding the floor at its real value and mocking
# the clock instead (so time can be advanced deterministically without a
# real 30-second sleep).


class TestD3RateOverWindowAtRealFloor:
    async def test_interleaved_90pct_failing_opens_only_after_real_30s_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interleaved ~90%-failing stream ([401]*9 + [200], repeated) against
        the REAL production ``_BREAKER_MIN_OPEN_SECONDS`` (30.0, NOT
        monkeypatched) with a mocked clock.

        Math (period 10, window maxlen 20 -- see ``_BREAKER_WINDOW`` /
        ``_BREAKER_MIN_SAMPLES`` / ``_BREAKER_HARD_RATIO`` asserted below):

        - The window enters the failing regime (ratio >= 0.9, samples >= 10)
          at event #10 (9 hard + 1 delivered => ratio exactly 0.9). Because
          20 is an exact multiple of the 10-event period, ANY full window
          thereafter contains exactly 18 hard / 2 delivered -- ratio pinned
          at 0.9 forever. The regime, once entered, never exits.
        - Clock advances 1.0s per event, so the regime is entered at
          t=10s. The sustain gate requires (now - first_hard_ts) >= 30s, so
          it must NOT open until t=40s, and only a HARD outcome re-checks
          the gate (delivered outcomes never call ``_maybe_open_breaker``) --
          the next hard outcome after t=40s is event #41 (t=41s, delta=31s).

        Pre-fix trace (why this test FAILS on the buggy code): the old
        ``_breaker_record_delivered`` set ``_first_hard_ts = None``
        unconditionally on every delivered outcome -- i.e. every 10th event
        (t=10, 20, 30, 40, ...). Each reset is immediately followed by a
        fresh ``_first_hard_ts`` on the very next hard outcome (1s later),
        so the clock is alive for at most ~9 continuous seconds before being
        wiped again -- it can never reach the 30s floor. The breaker would
        stay CLOSED forever on this exact stream, even after hundreds of
        events -- confirmed by running this scenario against the pre-fix
        ``_breaker_record_delivered`` (unconditional ``self._first_hard_ts =
        None``): ``d._breaker_open`` remained ``False`` through 60 events.
        Post-fix, it opens at event #41 as computed above.
        """
        # Real production values -- deliberately NOT monkeypatched to 0.
        assert logging_handler._BREAKER_MIN_OPEN_SECONDS == 30.0
        assert logging_handler._BREAKER_WINDOW == 20
        assert logging_handler._BREAKER_MIN_SAMPLES == 10
        assert logging_handler._BREAKER_HARD_RATIO == 0.9

        clock = _FakeClock(start=0.0)
        monkeypatch.setattr(logging_handler, "time", clock)

        d = _dispatcher()
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        cycle = [401] * 9 + [200]  # 9 hard, 1 delivered -- period 10
        total_events = 41
        statuses = (cycle * ((total_events // len(cycle)) + 1))[:total_events]
        d._client = _mock_client([_make_response(s) for s in statuses])

        with patch(LOGGER_PATH):
            # Events 1..39: clock reaches t=39s (delta=29s since regime
            # entry at t=10s) -- must NOT open, despite the sustained ~90%
            # hard ratio and successes arriving every ~10s.
            for i in range(39):
                clock.advance(1.0)
                await _drain(d, [f"e{i}"])

            assert d._breaker_open is False, (
                "must not open before 30s of sustained failing-regime time"
                " has elapsed, even with the real production floor and"
                " interleaved successes every ~10s"
            )

            # Events 40-41: t=40s (delivered -- no open check performed) then
            # t=41s (hard -- delta=31s since regime entry >= 30s => opens).
            for i in range(39, 41):
                clock.advance(1.0)
                await _drain(d, [f"e{i}"])

        assert d._breaker_open is True, (
            "a real 30s sustained failing-regime must open the breaker even"
            " though ~10%% of the stream is interleaved successes"
        )
        await d.close()


# ---------------------------------------------------------------------------
# 12. D4: sink-write-failure console warning is rate-limited
# ---------------------------------------------------------------------------
#
# _write_forwarding_record swallowing every sink-write failure to DEBUG meant
# a full disk / bad perms on the configured forwarding_log_dir silently killed
# the durable forwarding-diagnostics file operators are told to consult -- no
# operator-visible signal at all. D4 surfaces a rate-limited console WARNING
# (via the module logger, never a second sink write) when the sink write
# itself raises. This must hold across MANY invocations of
# _record_forwarding_issue -- not just one -- so the test drives the
# destination through breaker_open -> breaker_close (probe) -> breaker_open
# again, three separate call sites that each attempt (and fail) a durable
# write against the same persistently-broken directory.


class TestSinkWriteFailureConsoleWarning:
    """D4: a persistently broken forwarding-diagnostics sink must surface a
    rate-limited console WARNING instead of silently swallowing every write
    failure to DEBUG (which previously masked a full disk / bad perms killing
    the very diagnostics file operators are told to consult).

    Drives ``_record_forwarding_issue`` through THREE separate call sites --
    breaker_open, breaker_close (successful probe), breaker_open again -- all
    against the same persistently-failing ``forwarding_log_dir``, and asserts
    exactly ONE console WARNING is emitted despite three write failures (the
    new ``_last_sink_fail_log`` rate-limit gate holds across invocations
    regardless of ``kind``).
    """

    async def test_sink_failure_warning_rate_limited_across_repeated_failures(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(logging_handler, "_BREAKER_MIN_OPEN_SECONDS", 0.0)
        monkeypatch.setattr(logging_handler, "_BREAKER_PROBE_INTERVAL", 0.0)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")  # mkdir() against this raises

        d = _dispatcher(forwarding_log_dir=blocker)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        # 10 hard failures -> breaker opens (_record_forwarding_issue #1: "breaker_open").
        # 1 probe success -> breaker closes (_record_forwarding_issue #2: "breaker_close").
        # 10 more hard failures -> breaker reopens (_record_forwarding_issue #3: "breaker_open").
        responses = [_make_response(401)] * 10 + [_make_response(200)] + [_make_response(401)] * 10
        d._client = _mock_client(responses)

        with patch(LOGGER_PATH) as mock_logger:
            await _drain(d, [f"e-open-{i}" for i in range(10)])
            assert d._breaker_open is True, "breaker must open on the first hard-failure run"

            await _drain(d, ["e-probe"])
            assert d._breaker_open is False, "successful probe must close the breaker"

            await _drain(d, [f"e-reopen-{i}" for i in range(10)])
            assert d._breaker_open is True, "breaker must reopen on the second hard-failure run"

            sink_fail_warnings = [
                c for c in mock_logger.warning.call_args_list if "sink write failed" in str(c)
            ]
            assert len(sink_fail_warnings) == 1, (
                "expected exactly 1 rate-limited sink-failure warning despite 3 separate"
                f" _record_forwarding_issue invocations against the same persistently-broken"
                f" sink, got {len(sink_fail_warnings)}: {mock_logger.warning.call_args_list}"
            )
            assert str(blocker) in str(sink_fail_warnings[0]), (
                "sink-failure warning must name the configured forwarding_log_dir"
            )

        assert d._queue.qsize() == 0, "dispatch flow must complete despite the sink failures"
        await d.close()
