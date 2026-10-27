"""Real SQLite resource regressions; all captures, HOME and ledgers are synthetic."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import resource
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
BODY = (
    b'{"event":"llm:request","timestamp":"2026-01-01T00:00:00Z",'
    b'"data":{"raw":{"messages":["synthetic"]},"usage":{"tokens":4}}}\n'
)

# Exact historical V2 table definitions emitted by this repo's RecoveryCheckpoint,
# not a permissive all-TEXT shape synthesized from the prune reader's column sets.
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


def _ledger(root: Path, index: int, source: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"job-{index:03d}.v2.sqlite3"
    identity = hashlib.sha256(str(source.absolute()).encode()).hexdigest()
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(_SCHEMA)
        db.execute(
            "INSERT INTO artifact(artifact_id,path,state) VALUES(?,?,'eligible')",
            (identity, str(source.absolute())),
        )
        db.execute(
            """INSERT INTO cursor(
                 artifact_id,destination,target_fingerprint,policy_fingerprint,
                 ordinal,byte_offset,state,source_order_key
               ) VALUES(?,'synthetic','target','policy',12,345,'accepted',?)""",
            (identity, identity),
        )
        db.commit()
    Path(str(path) + ".lock").touch(mode=0o600)
    return path


def _request(root: Path, **kwargs):
    from context_intelligence.prune import Policy, PruneRequest

    return PruneRequest(
        root,
        Policy(NOW - timedelta(days=14)),
        apply=True,
        writers_stopped=True,
        min_idle=timedelta(0),
        **kwargs,
    )


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def _resource_child(home: Path, case: str) -> None:
    # This entry runs in a fresh interpreter, not a fork inheriting pytest's FDs,
    # SQLite handles or an already-imported config with the operator's HOME.
    from context_intelligence.prune import prune_file
    from context_intelligence.prune import recovery
    from context_intelligence.prune.coordination import capture_lock, receipt_path

    assert Path.home() == home
    assert Path(recovery.__file__).resolve() == REPO / "context_intelligence/prune/recovery.py"
    state = home / "recovery"
    ledgers = sorted(state.glob("*.v2.sqlite3"))
    assert len(ledgers) == 130
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
    assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] == 64

    held = None
    visited: list[str] = []
    peaks: list[int] = []
    real_exclude = recovery._exclude_registered

    def observe(connection: sqlite3.Connection, source: Path) -> None:
        # Outbound observation only: execute the real membership/schema queries.
        visited.append(Path(connection.execute("PRAGMA database_list").fetchone()[2]).name)
        peaks.append(_fd_count())
        real_exclude(connection, source)

    if case == "busy":
        held = Path(str(ledgers[-1]) + ".lock").open("rb")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        baseline = _fd_count()
        after: list[int] = []
        with (
            patch.object(recovery, "default_recovery_root", lambda: state),
            patch.object(recovery, "_exclude_registered", observe),
        ):
            for iteration in range(4):
                source = home / f"source-{iteration if case == 'success' else 0}" / "events.jsonl"
                visited.clear()
                result = prune_file(source, _request(home), now=NOW)
                expected = {"success": "pruned", "member": "skipped"}.get(case, "error")
                assert result.status == expected, result
                count = 129 if case in {"busy", "corrupt"} else 130
                assert visited == [path.name for path in ledgers[:count]], visited
                if case == "success":
                    assert json.loads(source.read_bytes())["data"] == {"usage": {"tokens": 4}}
                    assert receipt_path(source).is_file()
                else:
                    prefix = "skipped-ledger:" if case == "member" else "recovery-state-"
                    assert (result.reason or "").startswith(prefix), result
                    assert source.read_bytes() == BODY
                    assert not receipt_path(source).exists()
                assert not list(home.rglob(".events-prune-*"))
                assert not list(home.rglob(".prune-receipt-*"))
                after.append(_fd_count())
                assert after[-1] == baseline, (baseline, after)
                # Also proves the capture lock is closed on every early exit.
                with capture_lock(source, exclusive=True):
                    pass
        assert max(peaks) <= baseline + 8, (baseline, max(peaks))
    finally:
        if held is not None:
            held.close()

    # No earlier job lock can be retained on success OR a late failure.
    for db in ledgers:
        with Path(str(db) + ".lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print(json.dumps({"case": case, "ledgers": 130, "limit": 64, "fds": after, "peak": max(peaks)}))


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="Linux FD-limit regression")
@pytest.mark.parametrize("case", ["success", "member", "busy", "corrupt"])
def test_130_real_ledgers_under_64_fds(tmp_path: Path, case: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    for iteration in range(4):
        source = home / f"source-{iteration}" / "events.jsonl"
        source.parent.mkdir()
        source.write_bytes(BODY)
    ledgers = [
        _ledger(
            home / "recovery",
            index,
            home / "source-0/events.jsonl"
            if case == "member" and index == 129
            else home / f"unrelated-{index}/events.jsonl",
        )
        for index in range(130)
    ]
    if case == "corrupt":
        ledgers[-1].write_bytes(b"synthetic: not a SQLite database")
    original = {db: (db.read_bytes(), db.stat().st_mtime_ns) for db in ledgers}
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPLIFIER_")}
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / ".local/state"))
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    # subprocess.run owns/reaps only its own PID, including timeout cleanup.
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--resource-child", str(home), case],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert report["ledgers"] == 130 and report["limit"] == 64
    assert len(set(report["fds"])) == 1
    print("ledger-resource-proof:", json.dumps(report, sort_keys=True))
    for db, before in original.items():
        assert (db.read_bytes(), db.stat().st_mtime_ns) == before


@pytest.mark.parametrize("replace", ["database", "lock"])
def test_released_ledger_identity_is_still_checked_before_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace: str
) -> None:
    from context_intelligence.prune import prune_file, recovery
    from context_intelligence.prune.coordination import receipt_path

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    source = tmp_path / "events.jsonl"
    source.write_bytes(BODY)
    db = _ledger(tmp_path / "recovery", 0, tmp_path / "unrelated/events.jsonl")
    target = db if replace == "database" else Path(str(db) + ".lock")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(target.read_bytes())
    changed = False

    def swap_after_membership() -> None:
        nonlocal changed
        if not changed:
            # Both handles have been released, but their checked identities must
            # remain authoritative until the public transaction has finished.
            os.replace(replacement, target)
            changed = True

    result = prune_file(source, _request(tmp_path, before_commit=swap_after_membership), now=NOW)
    assert changed
    assert result.status == "error" and "recovery-state-changed:" in (result.reason or "")
    assert source.read_bytes() == BODY and not receipt_path(source).exists()
    assert not list(tmp_path.glob(".events-prune-*"))
    assert not list(tmp_path.glob(".prune-receipt-*"))


if __name__ == "__main__":
    assert sys.argv[1] == "--resource-child"
    _resource_child(Path(sys.argv[2]), sys.argv[3])
