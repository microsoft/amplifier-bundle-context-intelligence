"""Sweep SCHEDULING tests — the half that unit tests missed the first time.

The sweep mechanism was correct and every mechanism test was green, yet in a real
DTU run it delivered ZERO events. The defect was entirely in *when* it got to
run: ``cleanup()`` cancelled the task at session teardown, and a short session
ends before the first several-hundred-millisecond POST completes.

So these tests pin the two scheduling properties that failure exposed, neither of
which is observable from the sweeper in isolation:

* teardown grants a BOUNDED grace before cancelling (and honours 0.0), and
* the sweep REPEATS during the session instead of running once.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence import (
    _describe_sweep,
    await_sweeps_idle,
    schedule_backlog_sweeps,
)
from amplifier_module_hook_context_intelligence.config_resolver import HookConfigResolver
from amplifier_module_hook_context_intelligence.handlers.backlog_sweep import SweepReport


class _Resolver:
    """Only the attributes the scheduler reads."""

    def __init__(self, tmp_path: Any, **overrides: Any) -> None:
        self.sweep_enabled = True
        self.sweep_max_events = 5000
        self.sweep_max_age_hours = 48.0
        self.sweep_max_sessions = 20
        self.sweep_concurrency = 1
        self.sweep_interval_seconds = 0.0
        self.sweep_close_grace_seconds = 2.0
        self.dispatch_timeout = 10.0
        self.base_path = tmp_path
        self.project_slug = "-proj"
        for key, value in overrides.items():
            setattr(self, key, value)


class _Spec:
    """Minimal destination spec. The scheduler now REQUIRES one per destination:
    a sweeper that cannot evaluate include/exclude is not started at all."""

    include = ("**",)
    exclude = ()


#: Keyed by dispatcher name, as apply_active_dispatchers passes them.
SPECS = {"main": _Spec()}


class _Dispatcher:
    """Mutable name so a spec-less destination can be simulated."""

    def __init__(self) -> None:
        self.name = "main"
        self.url = "https://ci.example.com"
        self.auth_strategy = type("A", (), {"headers": lambda self: {}})()


class TestConfigDefaults:
    def test_grace_and_interval_defaults(self) -> None:
        resolver = HookConfigResolver({}, None)
        assert resolver.sweep_close_grace_seconds == 2.0
        assert resolver.sweep_interval_seconds == 60.0

    def test_grace_can_be_disabled_with_zero(self) -> None:
        """0.0 restores the original cancel-immediately behaviour."""
        assert (
            HookConfigResolver({"sweep_close_grace_seconds": 0}, None).sweep_close_grace_seconds
            == 0.0
        )

    def test_garbage_falls_back_to_default(self) -> None:
        resolver = HookConfigResolver({"sweep_close_grace_seconds": "nonsense"}, None)
        assert resolver.sweep_close_grace_seconds == 2.0


@pytest.mark.asyncio
class TestSchedulingLoop:
    async def test_disabled_sweep_schedules_nothing(self, tmp_path: Any) -> None:
        assert (
            schedule_backlog_sweeps(
                _Resolver(tmp_path, sweep_enabled=False), [_Dispatcher()], SPECS
            )
            == []
        )

    async def test_single_pass_when_interval_is_zero(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs = 0

        async def fake_run(self: Any) -> SweepReport:
            nonlocal runs
            runs += 1
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            fake_run,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=0.0), [_Dispatcher()], SPECS
        )
        await asyncio.gather(*(h.task for h in handles))
        assert runs == 1

    async def test_repeats_on_the_interval(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE fix for the DTU failure: one pass per session was not enough."""
        runs = 0

        async def fake_run(self: Any) -> SweepReport:
            nonlocal runs
            runs += 1
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            fake_run,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=0.01), [_Dispatcher()], SPECS
        )
        await asyncio.sleep(0.08)
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*(h.task for h in handles), return_exceptions=True)
        assert runs >= 3, f"continuous catch-up ran only {runs} time(s)"

    async def test_a_failing_pass_does_not_kill_the_loop(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs = 0

        async def fake_run(self: Any) -> SweepReport:
            nonlocal runs
            runs += 1
            if runs == 1:
                raise RuntimeError("transient")
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            fake_run,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=0.01), [_Dispatcher()], SPECS
        )
        await asyncio.sleep(0.06)
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*(h.task for h in handles), return_exceptions=True)
        assert runs >= 2, "the loop died on the first failure"

    async def test_cancellation_is_honoured(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_run(self: Any) -> SweepReport:
            await asyncio.sleep(10)
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            fake_run,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=1.0), [_Dispatcher()], SPECS
        )
        await asyncio.sleep(0)
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*(h.task for h in handles), return_exceptions=True)
        assert all(h.task.cancelled() or h.task.done() for h in handles)


@pytest.mark.asyncio
class TestTeardownGrace:
    async def test_grace_lets_an_in_flight_pass_finish(self) -> None:
        """A bounded wait, then cancel -- the shape cleanup() implements."""
        finished = False

        async def work() -> None:
            nonlocal finished
            await asyncio.sleep(0.02)
            finished = True

        task = asyncio.create_task(work())
        await asyncio.wait([task], timeout=1.0)
        task.cancel()
        assert finished is True

    async def test_grace_is_bounded_and_does_not_wait_forever(self) -> None:
        task = asyncio.create_task(asyncio.sleep(10))
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.wait([task], timeout=0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert (loop.time() - started) < 1.0, "teardown exceeded its declared budget"


class TestLoudnessGate:
    def _report(self, **kwargs: Any) -> SweepReport:
        report = SweepReport(destination="main")
        for key, value in kwargs.items():
            setattr(report, key, value)
        return report

    def test_quiet_when_caught_up(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            _describe_sweep(self._report(sessions_swept=1, events_delivered=5))
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_loud_when_no_progress_against_a_backlog(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            _describe_sweep(
                self._report(
                    sessions_swept=1,
                    events_delivered=0,
                    backlog_bytes_remaining=9000,
                    last_error="HTTP 401",
                )
            )
        assert any("NOT shrinking" in r.getMessage() for r in caplog.records)

    def test_loud_about_stranded_sessions(self, caplog: pytest.LogCaptureFixture) -> None:
        """The age bound must be audible or it is a silent drop."""
        with caplog.at_level("WARNING"):
            _describe_sweep(self._report(stranded_sessions=2, oldest_stranded_age_hours=168.0))
        message = " ".join(r.getMessage() for r in caplog.records)
        assert "older than the sweep window" in message
        assert "context-intelligence-upload" in message

    def test_stranded_warning_can_be_suppressed_after_the_first_pass(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A continuous loop must not repeat a backlog-shaped warning every tick."""
        with caplog.at_level("WARNING"):
            _describe_sweep(
                self._report(stranded_sessions=2, oldest_stranded_age_hours=168.0),
                suppress_stranded=True,
            )
        assert not [r for r in caplog.records if r.levelname == "WARNING"]


@pytest.mark.asyncio
class TestExitIsNotSlowedWhenIdle:
    """The grace must be spent ONLY on a sweep that is actually delivering.

    REGRESSION. The sweep task is an infinite loop, so it never completes --
    waiting on the TASK at teardown burned the entire budget on every exit,
    including the overwhelmingly common case of nothing to sweep. Measured at a
    flat 2.003s added to every session exit, which is precisely the cost the
    original "never slows exit" rule existed to prevent. Teardown now waits for
    the sweep to be IDLE, not for the task to end.
    """

    async def _cost(self, handles: list[Any], grace: float) -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await await_sweeps_idle(handles, grace)
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*(h.task for h in handles), return_exceptions=True)
        return loop.time() - started

    async def test_idle_sweep_costs_nothing_at_exit(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def instant(self: Any) -> SweepReport:
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            instant,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=60.0), [_Dispatcher()], SPECS
        )
        await asyncio.sleep(0.05)  # first pass done; task now idle between passes
        assert await self._cost(handles, grace=2.0) < 0.2, (
            "an idle sweep spent the teardown budget -- every exit pays for nothing"
        )

    async def test_in_flight_sweep_may_use_the_budget_but_not_exceed_it(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def slow(self: Any) -> SweepReport:
            await asyncio.sleep(10)
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            slow,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=60.0), [_Dispatcher()], SPECS
        )
        await asyncio.sleep(0.05)
        cost = await self._cost(handles, grace=0.3)
        assert 0.2 < cost < 1.0, f"budget not honoured: {cost:.3f}s"

    async def test_zero_grace_never_waits(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def slow(self: Any) -> SweepReport:
            await asyncio.sleep(10)
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            slow,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=60.0), [_Dispatcher()], SPECS
        )
        await asyncio.sleep(0.05)
        assert await self._cost(handles, grace=0.0) < 0.2

    async def test_budget_is_shared_across_destinations_not_per_destination(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three slow destinations must not cost three graces."""

        async def slow(self: Any) -> SweepReport:
            await asyncio.sleep(10)
            return SweepReport(destination="main")

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.handlers.backlog_sweep.BacklogSweeper.run",
            slow,
        )
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=60.0),
            [_Dispatcher(), _Dispatcher(), _Dispatcher()],
        )
        await asyncio.sleep(0.05)
        cost = await self._cost(handles, grace=0.3)
        assert cost < 1.0, f"budget multiplied by destination count: {cost:.3f}s"


@pytest.mark.asyncio
class TestSweeperRequiresARoutingSpec:
    """No include/exclude, no sweeper. Refusing is recoverable; leaking is not."""

    async def test_destination_without_a_spec_gets_no_sweeper(self, tmp_path: Any) -> None:
        handles = schedule_backlog_sweeps(_Resolver(tmp_path), [_Dispatcher()], {})
        assert handles == []

    async def test_only_specced_destinations_are_swept(self, tmp_path: Any) -> None:
        other = _Dispatcher()
        other.name = "no-spec"
        handles = schedule_backlog_sweeps(
            _Resolver(tmp_path, sweep_interval_seconds=0.0), [_Dispatcher(), other], SPECS
        )
        assert len(handles) == 1
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*(h.task for h in handles), return_exceptions=True)
