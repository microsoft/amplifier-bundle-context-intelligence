"""Tests for sustained-delivery-failure visibility (real-incident fix).

Covers:
  1. ``_degraded_since`` tracking -- set alongside ``_degraded_warned`` on the
     first TRANSIENT outcome, cleared on recovery (both the normal CLOSED path
     and the breaker half-open probe path).
  2. ``_maybe_escalate_sustained_failure`` -- no-ops while healthy or below
     threshold, rate-limited once past threshold, emits a loud ERROR plus a
     durable ``sustained_delivery_failure`` forwarding-diagnostics record
     naming overflow-dropped count, breaker-open state, and elapsed duration.
  3. Full ``_worker`` integration -- repeated retries against an always-down
     destination eventually escalate without any change to retry/backoff
     timing or breaker state.
  4. ``close()`` shutdown also writes a durable ``shutdown_undelivered``
     record under the same condition that already triggers its console
     WARNING (see test_shutdown.py for the pre-existing WARNING coverage).
  5. Structural zero-awaits/zero-I/O proof that ``enqueue()`` itself never
     reaches either new method (the comprehensive transitive gate lives in
     test_hot_path.py and is unmodified by this change).

Real incident this closes: a Context Intelligence server was down for ~2
days. The circuit breaker never opened (it only reacts to HARD/auth
failures -- a down server produces TRANSIENT network errors, which are
never HARD). The only signal was a single rate-limited INFO line per
session, invisible once each short-lived session's process exited. Nobody
noticed for two days.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _BREAKER_PROBE_INTERVAL,
    _DEGRADED_ESCALATION_SECONDS,
    _LOG_RATE_LIMIT_SECONDS,
    _TRANSIENT,
    _DestinationDispatcher,
)

LOGGER_PATH = "amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"


# ---------------------------------------------------------------------------
# Helpers (mirrors the conventions in test_shutdown.py / test_forwarding_diagnostics.py)
# ---------------------------------------------------------------------------


def _make_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _dispatcher(**overrides: Any) -> _DestinationDispatcher:
    defaults: dict[str, Any] = {
        "name": "test-dest",
        "url": "https://ci.example.com",
        "api_key": "test-key",
        "workspace": "ws",
        "dispatch_timeout": 10.0,
        "failure_threshold": 3,
        "queue_capacity": 256,
        "close_drain_timeout": 0.2,
        # 1ms backoff keeps iterations bounded so retries happen quickly in tests
        # (mirrors test_shutdown.py's DESIGN NOTE on why 0.001, not 0 or AsyncMock).
        "backoff_initial": 0.001,
        "backoff_max": 0.001,
        "backoff_jitter": False,
    }
    defaults.update(overrides)
    return _DestinationDispatcher(**defaults)


async def _always_transient(event: str, data: dict[str, Any]) -> str:
    """Stub _post: always TRANSIENT, never HARD (no _last_status/401 set)."""
    return _TRANSIENT


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _read_records(log_dir: Path) -> list[dict[str, Any]]:
    f = log_dir / f"forwarding-{_today_utc()}.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines()]


# ---------------------------------------------------------------------------
# TestDegradedSinceTracking
# ---------------------------------------------------------------------------


class TestDegradedSinceTracking:
    """_degraded_since is set alongside _degraded_warned=True and cleared on recovery."""

    async def test_degraded_since_none_before_any_failure(self) -> None:
        d = _dispatcher()
        assert d._degraded_since is None
        await d.close()

    async def test_degraded_since_set_on_first_transient(self) -> None:
        d = _dispatcher()
        d._post = _always_transient  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.sleep(0.05)

        assert d._degraded_warned is True
        assert d._degraded_since is not None
        await d.close()

    async def test_degraded_since_cleared_on_recovery(self) -> None:
        """One TRANSIENT (sets degraded_since) then a DELIVERED clears it."""
        d = _dispatcher()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = [_make_response(500), _make_response(200)]
        d._client = mock_client

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._degraded_warned is False
        assert d._degraded_since is None
        await d.close()

    async def test_degraded_since_cleared_on_breaker_probe_recovery(self) -> None:
        """Breaker half-open probe DELIVERED path also clears degraded_since."""
        d = _dispatcher()
        d._breaker_open = True
        # Force the probe to be immediately due *relative to* time.monotonic()'s
        # own (unspecified) reference point -- NOT an absolute 0.0. monotonic()'s
        # reference point is arbitrary (e.g. system boot); on a freshly-booted CI
        # runner it can itself be well under _BREAKER_PROBE_INTERVAL, in which case
        # `0.0` would NOT be "due" and the probe would never fire (this previously
        # passed only because dev machines have long uptimes).
        d._last_probe_ts = time.monotonic() - _BREAKER_PROBE_INTERVAL - 1.0
        d._degraded_warned = True
        d._degraded_since = time.monotonic() - 10.0

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = _make_response(200)
        d._client = mock_client

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._degraded_warned is False
        assert d._degraded_since is None
        await d.close()


# ---------------------------------------------------------------------------
# TestMaybeEscalateSustainedFailure (direct unit tests -- no worker needed)
# ---------------------------------------------------------------------------


class TestMaybeEscalateSustainedFailure:
    """Direct unit coverage of _maybe_escalate_sustained_failure's gating logic."""

    async def test_noop_when_healthy(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path)
        assert d._degraded_since is None

        with patch(LOGGER_PATH) as mock_logger:
            d._maybe_escalate_sustained_failure()

        assert mock_logger.error.call_count == 0
        assert _read_records(tmp_path) == []
        await d.close()

    async def test_noop_when_below_threshold(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._degraded_since = time.monotonic()  # just started -- far below threshold

        with patch(LOGGER_PATH) as mock_logger:
            d._maybe_escalate_sustained_failure()

        assert mock_logger.error.call_count == 0
        assert _read_records(tmp_path) == []
        await d.close()

    async def test_escalates_once_past_threshold(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._degraded_since = time.monotonic() - (_DEGRADED_ESCALATION_SECONDS + 1.0)
        d._overflow_dropped = 7
        d._breaker_open = False

        with patch(LOGGER_PATH) as mock_logger:
            d._maybe_escalate_sustained_failure()

        error_calls = mock_logger.error.call_args_list
        assert len(error_calls) == 1, f"expected exactly one ERROR, got: {error_calls}"
        rendered = str(error_calls[0])
        assert "failing to deliver" in rendered
        assert "test-dest" in rendered

        records = _read_records(tmp_path)
        assert len(records) == 1
        assert records[0]["kind"] == "sustained_delivery_failure"
        assert "overflow_dropped=7" in records[0]["detail"]
        assert "breaker_open=False" in records[0]["detail"]
        await d.close()

    async def test_includes_overflow_dropped_and_breaker_open_state(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._degraded_since = time.monotonic() - (_DEGRADED_ESCALATION_SECONDS + 1.0)
        d._overflow_dropped = 42
        d._breaker_open = True

        with patch(LOGGER_PATH) as mock_logger:
            d._maybe_escalate_sustained_failure()

        rendered = str(mock_logger.error.call_args_list[0])
        assert "42" in rendered
        assert "True" in rendered

        records = _read_records(tmp_path)
        assert records[0]["detail"] == (
            f"failing for {_DEGRADED_ESCALATION_SECONDS + 1.0:.0f}s "
            "(overflow_dropped=42, breaker_open=True)"
        )
        await d.close()

    async def test_rate_limited_repeat_calls_do_not_re_escalate(self, tmp_path: Path) -> None:
        """A second call immediately after the first must not log/write again."""
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._degraded_since = time.monotonic() - (_DEGRADED_ESCALATION_SECONDS + 1.0)

        with patch(LOGGER_PATH) as mock_logger:
            d._maybe_escalate_sustained_failure()
            d._maybe_escalate_sustained_failure()  # immediate repeat -- rate-limited

        assert mock_logger.error.call_count == 1
        assert len(_read_records(tmp_path)) == 1
        await d.close()

    async def test_re_escalates_after_rate_limit_window_elapses(self, tmp_path: Path) -> None:
        """Once the rate-limit window has passed, a still-degraded destination escalates again."""
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._degraded_since = time.monotonic() - (_DEGRADED_ESCALATION_SECONDS + 1.0)

        with patch(LOGGER_PATH) as mock_logger:
            d._maybe_escalate_sustained_failure()
            # Simulate the rate-limit window having elapsed.
            d._last_degraded_escalation_log -= _LOG_RATE_LIMIT_SECONDS + 1.0
            d._maybe_escalate_sustained_failure()

        assert mock_logger.error.call_count == 2
        assert len(_read_records(tmp_path)) == 2
        await d.close()


# ---------------------------------------------------------------------------
# TestWorkerIntegrationEscalation (full _worker loop, no time-source patching)
# ---------------------------------------------------------------------------


class TestWorkerIntegrationEscalation:
    """Drives the real _worker loop end-to-end; only _degraded_since is fast-forwarded
    (simulating elapsed wall-clock time) so the test doesn't sleep for real minutes.
    """

    async def test_worker_escalates_after_threshold_elapses_mid_retry(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._post = _always_transient  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "sess-1"})
            await asyncio.sleep(0.05)  # first TRANSIENT: sets _degraded_since

        assert d._degraded_since is not None

        # Fast-forward: simulate the degraded regime having run past threshold.
        d._degraded_since = time.monotonic() - (_DEGRADED_ESCALATION_SECONDS + 1.0)

        with patch(LOGGER_PATH) as mock_logger:
            # Worker keeps retrying the same event (1ms backoff) -- give it a few
            # cycles to hit the "already degraded" branch and escalate.
            await asyncio.sleep(0.05)

        error_calls = [
            c for c in mock_logger.error.call_args_list if "failing to deliver" in str(c)
        ]
        assert error_calls, f"expected an escalation ERROR, got: {mock_logger.error.call_args_list}"

        records = _read_records(tmp_path)
        kinds = {r["kind"] for r in records}
        assert "sustained_delivery_failure" in kinds

        await d.close()

    async def test_escalation_never_touches_breaker_or_backoff_semantics(
        self, tmp_path: Path
    ) -> None:
        """Escalation is purely additive -- breaker stays closed, event still
        retries in place (never dropped/skipped) for a genuinely transient
        (non-auth) outage, exactly as before this change.
        """
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._post = _always_transient  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "sess-1"})
            await asyncio.sleep(0.05)

        d._degraded_since = time.monotonic() - (_DEGRADED_ESCALATION_SECONDS + 1.0)

        with patch(LOGGER_PATH):
            await asyncio.sleep(0.05)

        # Breaker semantics unchanged: never opens for non-auth TRANSIENT outcomes.
        assert d._breaker_open is False
        # The event is still in-flight/retrying -- never silently dropped.
        assert d._current is not None
        assert d._current[0] == "e1"

        await d.close()


# ---------------------------------------------------------------------------
# TestShutdownDurableRecord
# ---------------------------------------------------------------------------


class TestShutdownDurableRecord:
    """close() writes a durable shutdown_undelivered record under the same
    condition that already triggers its (pre-existing) console WARNING.
    """

    async def test_close_writes_shutdown_undelivered_record_when_degraded(
        self, tmp_path: Path
    ) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path, close_drain_timeout=0.2)
        d._post = _always_transient  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "sess-1"})
            await asyncio.sleep(0.05)

        with patch(LOGGER_PATH):
            await d.close()

        records = _read_records(tmp_path)
        shutdown_records = [r for r in records if r["kind"] == "shutdown_undelivered"]
        assert len(shutdown_records) == 1, f"expected one shutdown record, got: {records}"
        detail = shutdown_records[0]["detail"]
        assert "overflow_dropped=" in detail
        assert "breaker_open=" in detail
        assert "degraded_seconds=" in detail

    async def test_close_clean_shutdown_writes_no_durable_record(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path, close_drain_timeout=2.0)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = _make_response(200)
        d._client = mock_client

        d.enqueue("e1", {"session_id": "sess-1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        records_before_close = _read_records(tmp_path)

        with patch(LOGGER_PATH):
            await d.close()

        # close() on a clean shutdown must add NO durable record. The one record
        # present is the D3 `delivery_ok` heartbeat, written by the worker on the
        # first successful delivery (not by close()); it must not be joined by a
        # shutdown_undelivered record on a clean drain.
        records_after_close = _read_records(tmp_path)
        assert records_after_close == records_before_close, (
            "clean close() must not add any durable record"
        )
        assert [r for r in records_after_close if r["kind"] == "shutdown_undelivered"] == []
        assert [r["kind"] for r in records_after_close] == ["delivery_ok"]

    async def test_close_shutdown_record_includes_degraded_seconds_zero_when_never_degraded(
        self, tmp_path: Path
    ) -> None:
        """total > 0 but never degraded (e.g. pure overflow-dropped) -> degraded_seconds=0."""
        d = _dispatcher(forwarding_log_dir=tmp_path, close_drain_timeout=0.2)
        d._overflow_dropped = 3
        assert d._degraded_since is None
        # close()'s shutdown-reporting block only runs when a worker task was
        # ever started (`if self._worker_task is not None:`) -- simulate one
        # having run and finished, without ever entering a degraded regime
        # (a pure overflow-drop scenario: queue filled and drained clean).
        d._worker_task = asyncio.create_task(asyncio.sleep(0))
        await asyncio.sleep(0)  # let the stub task finish before close() sees it

        with patch(LOGGER_PATH):
            await d.close()

        records = _read_records(tmp_path)
        shutdown_records = [r for r in records if r["kind"] == "shutdown_undelivered"]
        assert len(shutdown_records) == 1
        assert "degraded_seconds=0" in shutdown_records[0]["detail"]


# ---------------------------------------------------------------------------
# TestHotPathUnaffected -- zero-awaits/zero-I/O proof for THIS change
#
# The comprehensive, structural gate is test_hot_path.py's transitive AST
# allowlist test (unmodified by this change -- neither enqueue() nor
# _ensure_worker() gained any new call). This adds a narrow, self-contained
# proof scoped to exactly what this change could have broken: that neither
# new method (both of which perform logging + best-effort file I/O) is
# reachable from enqueue()'s own source, and that enqueue() still contains
# no `await` at all.
# ---------------------------------------------------------------------------


class TestHotPathUnaffected:
    def test_enqueue_contains_no_await(self) -> None:
        """AST-based (not naive substring) check: the enqueue() docstring itself
        legitimately says "zero awaits" (the word "awaits" contains "await" as
        a substring), so this parses the real syntax tree and looks for actual
        ``ast.Await`` expression nodes rather than matching text.
        """
        source = textwrap.dedent(inspect.getsource(_DestinationDispatcher.enqueue))
        tree = ast.parse(source)
        awaits = [node for node in ast.walk(tree) if isinstance(node, ast.Await)]
        assert not awaits, f"enqueue() must remain synchronous -- zero awaits, found: {awaits}"

    def test_enqueue_does_not_reference_new_escalation_method(self) -> None:
        source = inspect.getsource(_DestinationDispatcher.enqueue)
        assert "_maybe_escalate_sustained_failure" not in source

    def test_enqueue_does_not_reference_forwarding_record_write(self) -> None:
        source = inspect.getsource(_DestinationDispatcher.enqueue)
        assert "_record_forwarding_issue" not in source

    def test_ensure_worker_does_not_reference_new_escalation_method(self) -> None:
        """enqueue() calls _ensure_worker() on every invocation (see
        test_hot_path.py) -- confirm the new method isn't reachable there either.
        """
        source = inspect.getsource(_DestinationDispatcher._ensure_worker)
        assert "_maybe_escalate_sustained_failure" not in source
        assert "_record_forwarding_issue" not in source
