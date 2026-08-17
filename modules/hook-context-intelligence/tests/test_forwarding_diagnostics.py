"""Tests for the durable forwarding-diagnostics sink.

Covers the module-level best-effort writer (``_write_forwarding_record``) and
the ``_DestinationDispatcher`` integration: console warnings now include the
destination URL and a qualified verdict, and every auth-failure / give-up /
permanent-reject / auth-token-unavailable event also writes a durable JSONL
record via the sink. See ``forwarding-diagnostics-design.md`` for the design.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _TRANSIENT,
    _DestinationDispatcher,
    _write_forwarding_record,
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


# ---------------------------------------------------------------------------
# TestWriteForwardingRecord (module-level writer)
# ---------------------------------------------------------------------------


class TestWriteForwardingRecord:
    """Unit tests for the module-level best-effort JSONL writer."""

    def test_none_dir_is_noop(self, tmp_path: Path) -> None:
        """log_dir=None disables the sink entirely -- no directory, no file."""
        _write_forwarding_record(None, {"kind": "x"})
        assert list(tmp_path.iterdir()) == []

    def test_writes_jsonl_line(self, tmp_path: Path) -> None:
        """A single record is written as one canonical-JSON line."""
        record = {"kind": "auth_failure", "detail": "x"}
        _write_forwarding_record(tmp_path, record)

        f = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        assert f.exists()
        lines = f.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == record

    def test_appends_multiple_records(self, tmp_path: Path) -> None:
        """Repeated calls append -- one file per UTC day, one line per record."""
        _write_forwarding_record(tmp_path, {"a": 1})
        _write_forwarding_record(tmp_path, {"a": 2})

        lines = (tmp_path / f"forwarding-{_today_utc()}.jsonl").read_text().splitlines()
        assert len(lines) == 2
        assert [json.loads(line) for line in lines] == [{"a": 1}, {"a": 2}]

    def test_creates_missing_directory(self, tmp_path: Path) -> None:
        """log_dir need not pre-exist -- mkdir(parents=True) creates it."""
        nested = tmp_path / "a" / "b" / "c"
        _write_forwarding_record(nested, {"a": 1})

        assert (nested / f"forwarding-{_today_utc()}.jsonl").exists()

    def test_write_failure_never_raises(self, tmp_path: Path) -> None:
        """A log_dir that can't be created/written to is swallowed, not raised."""
        blocker = tmp_path / "blocker"
        blocker.write_text("this is a file, not a directory")

        # mkdir(parents=True, exist_ok=True) on a path that is already a FILE
        # raises (FileExistsError / NotADirectoryError) -- must not propagate.
        _write_forwarding_record(blocker, {"a": 1})  # should not raise


# ---------------------------------------------------------------------------
# TestDispatcherForwardingSink (integration with _DestinationDispatcher)
# ---------------------------------------------------------------------------


class TestDispatcherForwardingSink:
    """Auth-failure console warning + durable-record integration."""

    async def test_auth_failure_writes_durable_record_and_console_warning(
        self, tmp_path: Path
    ) -> None:
        """A 401 with failure_threshold=1 writes a durable record AND warns loud."""
        d = _dispatcher(forwarding_log_dir=tmp_path, failure_threshold=1)
        d._client = _mock_client([_make_response(401), _make_response(200)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH) as mock_logger:
            d.enqueue("e1", {"session_id": "sess-123"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # --- durable record ---
        log_file = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        assert log_file.exists(), "expected a forwarding-<today>.jsonl file"
        records = [json.loads(line) for line in log_file.read_text().splitlines()]
        assert records, "expected at least one durable record"
        record = records[0]
        assert record["url"] == "https://ci.example.com"
        assert record["kind"] == "auth_failure"
        assert record["http_status"] == 401
        assert record["destination"] == "test-dest"
        assert record["session_id"] == "sess-123"

        # --- console warning: URL present + qualified verdict wording ---
        warning_calls = [
            c for c in mock_logger.warning.call_args_list if "still rejecting auth" in str(c)
        ]
        assert warning_calls, (
            f"expected an auth-failure console warning, got: {mock_logger.warning.call_args_list}"
        )
        rendered = str(warning_calls[0])
        assert "https://ci.example.com" in rendered, f"URL missing from warning: {rendered!r}"
        assert "misrouted URL" in rendered, (
            f"qualified-verdict wording missing from warning: {rendered!r}"
        )

        await d.close()

    async def test_sink_disabled_when_dir_empty(self, tmp_path: Path) -> None:
        """forwarding_log_dir='' disables the sink -- no file is ever written."""
        d = _dispatcher(forwarding_log_dir="", failure_threshold=1)
        assert d._forwarding_log_dir is None

        d._client = _mock_client([_make_response(401), _make_response(200)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "sess-1"})
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert list(tmp_path.glob("forwarding-*.jsonl")) == [], (
            "sink must not write any file when forwarding_log_dir is empty"
        )
        await d.close()

    async def test_sink_write_failure_does_not_break_dispatch(self, tmp_path: Path) -> None:
        """A sink write failure (uncreatable log dir) never breaks the 401 flow."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")  # mkdir() against this path will raise

        d = _dispatcher(forwarding_log_dir=blocker, failure_threshold=1)
        d._client = _mock_client([_make_response(401), _make_response(200)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        d.enqueue("e1", {"session_id": "sess-1"})
        # Dispatch must complete despite the sink write failing internally.
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert d._queue.qsize() == 0
        await d.close()

    async def test_repeated_401s_skip_per_event_without_opening_breaker_short_run(
        self, tmp_path: Path
    ) -> None:
        """10 rapid 401s then recovery: each 401 is skipped (no tight retry loop
        on a single event), but the destination-level breaker does NOT open --
        the wall-clock floor (_BREAKER_MIN_OPEN_SECONDS) is not met by a rapid,
        mocked-sleep sequence. This replaces the old per-event
        give-up/counter-reset behavior (see test_circuit_breaker.py for the
        breaker's own open/close/probe coverage, including the wall-clock gate
        and the auth-token-production-failure path).
        """
        d = _dispatcher(forwarding_log_dir=tmp_path, failure_threshold=1)
        d._client = _mock_client([_make_response(401)] * 10 + [_make_response(200)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            for i in range(10):
                d.enqueue(f"e{i}", {"session_id": f"sess-{i}"})
            d.enqueue("e-final", {"session_id": "sess-final"})
            await asyncio.wait_for(d._queue.join(), timeout=5.0)

        assert d._breaker_open is False, (
            "breaker must not open on a rapid (near-zero-wall-clock) sequence"
        )
        log_file = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        assert log_file.exists()
        records = [json.loads(line) for line in log_file.read_text().splitlines()]
        kinds = {r["kind"] for r in records}
        assert "breaker_open" not in kinds, f"breaker must not have opened: {kinds}"


class TestAuthTokenUnavailableDurableRecord:
    """Issue #431 D2: the durable auth_token_unavailable record must carry the
    exception TYPE and MESSAGE (not a byte-identical constant), and distinct
    failures must not be collapsed by the console rate-limit.

    Drives the auth-strategy header-production failure path directly: a mocked
    _strategy.headers() that raises is exactly what an expired `az login` /
    broken credential chain produces at runtime (static ApiKeyAuth never raises,
    so this path is entra-only).
    """

    async def test_record_carries_exception_type_and_message(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._client = MagicMock()  # headers() raises before any request is issued
        d._strategy = MagicMock()
        d._strategy.headers.side_effect = RuntimeError("token expired: refresh needed")

        with patch(LOGGER_PATH):
            result = await d._post("evt:x", {"session_id": "sess-9"})

        assert result == _TRANSIENT  # auth failure stays on the retry path
        log_file = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        records = [json.loads(line) for line in log_file.read_text().splitlines()]
        rec = next(r for r in records if r["kind"] == "auth_token_unavailable")
        # http_status None distinguishes this from a real HTTP 401 auth_failure.
        assert rec["http_status"] is None
        assert "RuntimeError" in rec["detail"], f"exception type missing: {rec['detail']!r}"
        assert "token expired: refresh needed" in rec["detail"], (
            f"exception message missing: {rec['detail']!r}"
        )

    async def test_distinct_failures_each_recorded_not_collapsed(self, tmp_path: Path) -> None:
        """Two distinct auth faults in quick succession (within the console
        rate-limit window) must each produce their own durable record -- the
        durable write is no longer gated by the rate-limit branch."""
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._client = MagicMock()
        d._strategy = MagicMock()
        d._strategy.headers.side_effect = [
            RuntimeError("expired token"),
            ValueError("wrong audience"),
        ]

        with patch(LOGGER_PATH):
            await d._post("e1", {"session_id": "s1"})
            await d._post("e2", {"session_id": "s1"})

        log_file = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        records = [json.loads(line) for line in log_file.read_text().splitlines()]
        auth_recs = [r for r in records if r["kind"] == "auth_token_unavailable"]
        assert len(auth_recs) == 2, f"expected 2 distinct durable records, got {len(auth_recs)}"
        blob = " || ".join(r["detail"] for r in auth_recs)
        assert "RuntimeError" in blob and "expired token" in blob
        assert "ValueError" in blob and "wrong audience" in blob


class TestDeliveryHeartbeat:
    """Issue #431 D3: a destination that delivers successfully must emit a
    positive `delivery_ok` liveness record once per session, so an EMPTY
    forwarding log unambiguously means "no delivery happened" rather than
    "possibly a silent outage". The sink otherwise only ever writes on problems.
    """

    async def test_first_successful_delivery_writes_heartbeat(self, tmp_path: Path) -> None:
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._client = _mock_client([_make_response(200)])
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "sess-1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        log_file = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        assert log_file.exists(), "a healthy delivery must still write a liveness record"
        records = [json.loads(line) for line in log_file.read_text().splitlines()]
        heartbeats = [r for r in records if r["kind"] == "delivery_ok"]
        assert len(heartbeats) == 1, f"expected exactly one delivery_ok record, got {heartbeats}"
        assert heartbeats[0]["destination"] == "test-dest"
        await d.close()

    async def test_heartbeat_emitted_only_once_per_session(self, tmp_path: Path) -> None:
        """Multiple successful deliveries in one session produce exactly ONE
        heartbeat -- the happy path is not flooded with liveness records."""
        d = _dispatcher(forwarding_log_dir=tmp_path)
        d._client = _mock_client([_make_response(200)] * 5)
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            for i in range(5):
                d.enqueue(f"e{i}", {"session_id": "sess-1"})
            await asyncio.wait_for(d._queue.join(), timeout=3.0)

        log_file = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        records = [json.loads(line) for line in log_file.read_text().splitlines()]
        heartbeats = [r for r in records if r["kind"] == "delivery_ok"]
        assert len(heartbeats) == 1, (
            f"heartbeat must fire at most once per session, got {len(heartbeats)}"
        )
        await d.close()

    async def test_no_delivery_no_heartbeat(self, tmp_path: Path) -> None:
        """A destination that never delivers writes no delivery_ok record -- the
        empty/heartbeat-less case is what makes the signal meaningful."""
        d = _dispatcher(forwarding_log_dir=tmp_path, failure_threshold=1)
        d._client = _mock_client([_make_response(403)])  # permanent skip, never delivered
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        with patch(LOGGER_PATH):
            d.enqueue("e1", {"session_id": "sess-1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)

        log_file = tmp_path / f"forwarding-{_today_utc()}.jsonl"
        records = (
            [json.loads(line) for line in log_file.read_text().splitlines()]
            if log_file.exists()
            else []
        )
        assert not [r for r in records if r["kind"] == "delivery_ok"], (
            "no successful delivery must mean no delivery_ok heartbeat"
        )
        await d.close()
        await d.close()
