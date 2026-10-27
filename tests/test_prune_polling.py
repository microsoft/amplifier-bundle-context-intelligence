"""Cheap stream polls and full commit fences, using real SQLite/filesystem guards."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import pytest

from context_intelligence import config
from context_intelligence.prune import inactive, operations, recovery, storage
from context_intelligence.prune.coordination import PrunedCapture, receipt_path, replay_guard
from context_intelligence.prune.policy import Policy
from context_intelligence.prune.scheduled import RunInterrupted

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=30)
BODY = (
    b'{"event":"llm:request","timestamp":"2026-09-20T00:00:00Z",'
    b'"data":{"raw":"SYNTHETIC","usage":{"tokens":3}}}\n'
)

# Historical V2 schema, matching test_prune_ledger_resources.py's producer fixture.
_SCHEMA = """
CREATE TABLE artifact (
  artifact_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '', workspace TEXT NOT NULL DEFAULT '',
  working_dir TEXT NOT NULL DEFAULT '', parent_id TEXT NOT NULL DEFAULT '',
  record_count INTEGER NOT NULL DEFAULT 0,
  source_stream_sha256 TEXT NOT NULL DEFAULT '',
  source_sha256 TEXT NOT NULL DEFAULT '', metadata_sha256 TEXT NOT NULL DEFAULT '',
  source_size INTEGER NOT NULL DEFAULT 0, source_mtime_ns INTEGER NOT NULL DEFAULT 0,
  source_inode INTEGER NOT NULL DEFAULT 0, metadata_size INTEGER NOT NULL DEFAULT 0,
  metadata_mtime_ns INTEGER NOT NULL DEFAULT 0,
  metadata_inode INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE cursor (
  artifact_id TEXT NOT NULL REFERENCES artifact(artifact_id),
  destination TEXT NOT NULL, target_fingerprint TEXT NOT NULL,
  policy_fingerprint TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
  byte_offset INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
  retry_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '', source_order_key TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (artifact_id, destination)
);
CREATE INDEX cursor_artifact_target_state ON cursor(artifact_id,target_fingerprint,state);
CREATE INDEX artifact_session ON artifact(session_id);
CREATE INDEX artifact_parent ON artifact(parent_id);
CREATE INDEX artifact_state ON artifact(state);
CREATE INDEX cursor_target_pending_order
  ON cursor(target_fingerprint,state,source_order_key,artifact_id);
CREATE INDEX cursor_target_state_retry ON cursor(target_fingerprint,state,retry_at);
"""


@pytest.fixture(autouse=True)
def isolated_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    for name, path in {
        "HOME": home,
        "AMPLIFIER_HOME": home / "amplifier",
        "XDG_STATE_HOME": home / "state",
        "AMPLIFIER_SESSION_STATE_HOME": home / "sessions",
    }.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setattr(config, "DEFAULT_RECOVERY_STATE_ROOT", home / "absent-global")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: home / "absent-cli")


@dataclass
class Capture:
    root: Path
    source: Path
    state: Path
    ledger_root: Path
    databases: list[Path]
    tracked: set[Path]

    def request(self, mode: str, runtime: Callable[[], None], *, apply: bool = True):
        return operations.PruneRequest(
            self.root,
            Policy(NOW - timedelta(days=14)),
            apply=apply,
            writers_stopped=mode == "direct",
            inactive_only=mode == "inactive",
            state_root=self.state,
            recovery_roots=(self.ledger_root,),
            min_idle=timedelta(0),
            before_commit=runtime,
        )


def _capture(root: Path, records: int, ledgers: int) -> Capture:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    sid = "synthetic-session"
    slug = str(workspace).replace("/", "-")
    session = root / "projects" / slug / "sessions" / sid
    ci = session / "context-intelligence"
    ci.mkdir(parents=True)
    source = ci / "events.jsonl"
    source.write_bytes(BODY * records)
    state = root / "state"
    owner = state / "v1" / hashlib.sha256(str(workspace).encode()).hexdigest() / sid
    owner.mkdir(parents=True)
    (owner / "session.lock").touch(mode=0o600)
    for path, value in {
        owner / "owner.json": {
            "app": "amplifier-cli",
            "hostname": socket.gethostname(),
            "session_id": sid,
            "workspace": str(workspace),
            "coordination_root": str(state),
            "active": False,
            "released_at": OLD.isoformat(),
        },
        ci / "metadata.json": {
            "session_id": sid,
            "workspace": str(workspace),
            "working_dir": str(workspace),
            "parent_id": "",
            "status": "completed",
            "ended_at": OLD.isoformat(),
            "last_event_at": OLD.isoformat(),
        },
        session / "metadata.json": {"session_id": sid},
    }.items():
        path.write_text(json.dumps(value), encoding="utf-8")
        os.utime(path, (OLD.timestamp(), OLD.timestamp()))
    os.utime(source, (OLD.timestamp(), OLD.timestamp()))
    ledger_root = root / "recovery"
    ledger_root.mkdir()
    databases = []
    tracked = set()
    for number in range(ledgers):
        database = ledger_root / f"job-{number:03d}.v2.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()
        lock = Path(str(database) + ".lock")
        lock.touch(mode=0o600)
        databases.append(database)
        tracked.update((database, lock))
    return Capture(root, source, state, ledger_root, databases, tracked)


class Observation:
    def __init__(self, capture: Capture):
        self.capture = capture
        self.scanning = False
        self.ledger_lstats = 0
        self.scan_ledger_lstats = 0
        self.runtime_calls = 0
        self.scan_runtime_calls = 0
        self.transformed = 0

    def runtime(self) -> None:
        self.runtime_calls += 1
        self.scan_runtime_calls += int(self.scanning)

    def run(
        self,
        mode: str,
        *,
        apply: bool = True,
        transform_fault: Callable[[], None] | None = None,
        receipt_fault: Callable[[], None] | None = None,
        runtime_check: Callable[[], None] | None = None,
        direct_inventory: bool = False,
    ) -> storage.FileResult:
        real_lstat = Path.lstat
        real_stream = storage._stream
        real_transform = storage.prune_record
        real_publish = storage._publish_receipt

        def lstat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
            if path in self.capture.tracked:
                self.ledger_lstats += 1
                self.scan_ledger_lstats += int(self.scanning)
            return real_lstat(path, *args, **kwargs)

        def stream(*args: Any, **kwargs: Any) -> str:
            self.scanning = True
            try:
                return real_stream(*args, **kwargs)
            finally:
                self.scanning = False

        def transform(record: Any, policy: Policy) -> Any:
            result = real_transform(record, policy)
            self.transformed += 1
            if self.transformed == 2 and transform_fault is not None:
                transform_fault()
            return result

        def publish(*args: Any, **kwargs: Any) -> None:
            real_publish(*args, **kwargs)
            if receipt_fault is not None:
                receipt_fault()

        def runtime() -> None:
            self.runtime()
            if runtime_check is not None:
                runtime_check()

        with (
            patch.object(Path, "lstat", lstat),
            patch.object(storage, "_stream", stream),
            patch.object(storage, "prune_record", transform),
            patch.object(storage, "_publish_receipt", publish),
        ):
            if direct_inventory:
                result = inactive.InactiveInventory.load(self.capture.state).prune(
                    self.capture.source,
                    Policy(NOW - timedelta(days=14)),
                    apply=apply,
                    now=NOW,
                    min_idle=timedelta(0),
                    before_commit=runtime,
                )
            else:
                result = operations.prune_file(
                    self.capture.source, self.capture.request(mode, runtime, apply=apply), now=NOW
                )
        assert not list(self.capture.source.parent.glob(".events-prune-*"))
        assert not list(self.capture.source.parent.glob(".prune-receipt-*"))
        return result

    def counts(self) -> dict[str, int]:
        return {
            "ledger_lstats": self.ledger_lstats,
            "scan_ledger_lstats": self.scan_ledger_lstats,
            "runtime_calls": self.runtime_calls,
            "scan_runtime_calls": self.scan_runtime_calls,
            "transformed": self.transformed,
        }


def _replace_same_bytes(target: Path) -> Callable[[], None]:
    replacement = target.with_name(target.name + ".replacement")
    replacement.write_bytes(target.read_bytes())
    assert replacement.stat().st_ino != target.stat().st_ino

    def replace() -> None:
        os.replace(replacement, target)

    return replace


@pytest.mark.parametrize("mode", ["direct", "inactive"])
@pytest.mark.parametrize("ledgers", [1, 3])
def test_scan_polling_does_not_recheck_ledgers(tmp_path: Path, mode: str, ledgers: int):
    # Reconnecting full recovery checks to each poll makes these counts grow with N.
    observations = []
    for records in (1, 12):
        capture = _capture(tmp_path / f"n-{records}", records, ledgers)
        observation = Observation(capture)
        result = observation.run(mode)
        assert result.status == "pruned"
        assert result.payloads == records
        rows = [json.loads(line) for line in capture.source.read_bytes().splitlines()]
        assert len(rows) == records
        assert all("raw" not in row["data"] for row in rows)
        assert all(row["data"]["usage"] == {"tokens": 3} for row in rows)
        assert receipt_path(capture.source).exists()
        observations.append(observation)
        print(json.dumps(dict(mode=mode, ledgers=ledgers, records=records, **observation.counts())))
    for observation, records in zip(observations, (1, 12)):
        assert observation.scan_runtime_calls == 2 * records
        assert observation.scan_ledger_lstats == 0
        assert 0 < observation.ledger_lstats <= 32 * ledgers
    assert observations[0].ledger_lstats == observations[1].ledger_lstats


@pytest.mark.parametrize("mode", ["direct", "inactive"])
@pytest.mark.parametrize("replaced", ["database", "lock"])
def test_scan_ledger_replacement_refuses_before_commit(tmp_path: Path, mode: str, replaced: str):
    capture = _capture(tmp_path, 4, 3)
    target = capture.databases[-1]
    if replaced == "lock":
        target = Path(str(target) + ".lock")
    original = capture.source.read_bytes()
    observation = Observation(capture)
    result = observation.run(mode, transform_fault=_replace_same_bytes(target))
    assert result.status == "error"
    assert result.reason and "recovery-state-changed:" in result.reason
    assert capture.source.read_bytes() == original
    assert not receipt_path(capture.source).exists()


@pytest.mark.parametrize("mode", ["direct", "inactive"])
def test_post_receipt_ledger_replacement_keeps_replay_fence(tmp_path: Path, mode: str):
    capture = _capture(tmp_path, 4, 3)
    original = capture.source.read_bytes()
    observation = Observation(capture)
    result = observation.run(mode, receipt_fault=_replace_same_bytes(capture.databases[-1]))
    assert result.status == "error"
    assert result.reason and "recovery-state-changed:" in result.reason
    assert capture.source.read_bytes() == original
    assert receipt_path(capture.source).exists()
    with pytest.raises(PrunedCapture):
        with replay_guard(capture.source):
            pytest.fail("published receipt must deny replay")


@pytest.mark.parametrize("mode", ["direct", "inactive"])
@pytest.mark.parametrize("stop", ["deadline", "SIGTERM"])
def test_runtime_interrupt_remains_responsive_mid_scan(tmp_path: Path, mode: str, stop: str):
    capture = _capture(tmp_path, 4, 1)
    original = capture.source.read_bytes()
    observation = Observation(capture)
    clock = [time.monotonic()]
    deadline = clock[0] + 1
    stopped = False
    previous = signal.getsignal(signal.SIGTERM)

    def handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    def interrupt() -> None:
        if stop == "deadline":
            clock[0] = deadline + 1
        else:
            signal.raise_signal(signal.SIGTERM)

    def check_runtime() -> None:
        if stopped or clock[0] >= deadline:
            raise RunInterrupted("deadline_reached")

    try:
        signal.signal(signal.SIGTERM, handle_signal)
        result = observation.run(mode, transform_fault=interrupt, runtime_check=check_runtime)
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert result.status == "error"
    assert result.reason == "deadline_reached"
    assert observation.transformed == 2
    assert capture.source.read_bytes() == original
    assert not receipt_path(capture.source).exists()
    assert signal.getsignal(signal.SIGTERM) == previous


def test_direct_inventory_preserves_legacy_scan_callback(tmp_path: Path):
    capture = _capture(tmp_path, 4, 1)
    # Blank lines still need both runtime polls, even without a record transform.
    capture.source.write_bytes(BODY * 2 + b"\n" + BODY * 2)
    os.utime(capture.source, (OLD.timestamp(), OLD.timestamp()))
    observation = Observation(capture)
    result = observation.run("inactive", direct_inventory=True)
    assert result.status == "pruned"
    assert result.payloads == 4
    assert observation.scan_runtime_calls == 10
    assert observation.runtime_calls > observation.scan_runtime_calls
    assert capture.source.read_bytes().splitlines()[2] == b""
    assert receipt_path(capture.source).exists()


@pytest.mark.parametrize("mode", ["direct", "inactive"])
def test_preview_keeps_runtime_polls_without_ledger_scan_checks(tmp_path: Path, mode: str):
    capture = _capture(tmp_path, 4, 1)
    original = capture.source.read_bytes()
    observation = Observation(capture)
    result = observation.run(mode, apply=False)
    assert result.status == "would-prune"
    assert result.payloads == 4
    assert observation.scan_ledger_lstats == 0
    assert observation.scan_runtime_calls == 8
    assert observation.ledger_lstats > 0
    assert capture.source.read_bytes() == original
    assert not receipt_path(capture.source).exists()
