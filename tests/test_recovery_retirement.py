"""Explicit abandonment releases one retention barrier, never old delivery evidence."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import socket
import sqlite3
from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_prune_ledger_resources import _SCHEMA, BODY, NOW, _ledger, _request

from context_intelligence import config
from context_intelligence.prune import prune_file
from context_intelligence.prune.coordination import PruneError, capture_lock, receipt_path
from context_intelligence.recovery_retirement import RetirementError, retire_job, retired_job


@pytest.fixture
def owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setattr(config, "DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "recovery")
    return tmp_path


def _rows(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as db:
        return {name: list(db.execute(f"SELECT * FROM {name}")) for name in ("artifact", "cursor")}


@pytest.mark.parametrize("pathless", [False, True])
def test_retirement_preserves_outcomes_and_releases_only_recovery_barrier(
    owned: Path, pathless: bool
) -> None:
    source = owned / "events.jsonl"
    source.write_bytes(BODY + b'{"event":"llm:response","data":{"raw":"keep response"}}\n')
    original = source.read_bytes()
    transcript = owned / "transcript.jsonl"
    transcript.write_bytes(b"retained native transcript\n")
    ledger = _ledger(owned / "recovery", 0, source)
    if pathless:
        # Exact source-capability schema shape, including retained failed/unknown outcomes.
        with closing(sqlite3.connect(ledger)) as db:
            rows = _rows(ledger)
            db.execute("DROP TABLE cursor")
            db.execute("DROP TABLE artifact")
            db.executescript(
                _SCHEMA.replace(" path TEXT NOT NULL UNIQUE,", "").replace(
                    "working_dir TEXT NOT NULL DEFAULT '', ", ""
                )
            )
            keys = [r[1] for r in db.execute("PRAGMA table_info(artifact)")]
            values = dict(
                zip(
                    [
                        "artifact_id",
                        "path",
                        "state",
                        "reason",
                        "session_id",
                        "workspace",
                        "working_dir",
                        "parent_id",
                        "record_count",
                        "source_stream_sha256",
                        "source_sha256",
                        "metadata_sha256",
                        "source_size",
                        "source_mtime_ns",
                        "source_inode",
                        "metadata_size",
                        "metadata_mtime_ns",
                        "metadata_inode",
                    ],
                    rows["artifact"][0],
                )
            )
            db.execute(
                f"INSERT INTO artifact VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )
            db.execute("INSERT INTO cursor VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows["cursor"][0])
            db.execute("ALTER TABLE cursor ADD COLUMN source_refusal_attempts INTEGER DEFAULT 0")
            db.commit()
    with closing(sqlite3.connect(ledger)) as db:
        db.execute("UPDATE cursor SET state='deferred',error='delivery outcome UNKNOWN',attempts=3")
        db.commit()
    before = _rows(ledger)
    lock_identity = Path(str(ledger) + ".lock").stat().st_ino
    result = prune_file(source, _request(owned), now=NOW)
    assert result.reason is not None
    assert result.reason.startswith("skipped-ledger:")

    receipt = retire_job(ledger, reason="Operator abandoned this catch-up; old launcher retired")
    assert receipt["disposition"] == "abandoned"
    assert _rows(ledger) == before
    assert Path(str(ledger) + ".lock").stat().st_ino == lock_identity
    assert retire_job(ledger, reason="Repeated operator command") == receipt
    assert source.read_bytes() == original  # Retirement itself does not prune anything.
    result = prune_file(source, _request(owned), now=NOW)
    assert result.status == "pruned"
    records = [json.loads(line) for line in source.read_bytes().splitlines()]
    assert "raw" not in records[0]["data"]
    assert records[1]["data"]["raw"] == "keep response"
    assert transcript.read_bytes() == b"retained native transcript\n"
    assert _rows(ledger) == before
    assert receipt_path(source).is_file()


def test_other_job_still_protects_same_capture(owned: Path) -> None:
    source = owned / "events.jsonl"
    source.write_bytes(BODY)
    first = _ledger(owned / "recovery", 0, source)
    second = _ledger(owned / "recovery", 1, source)
    retire_job(first, reason="Only the first job was abandoned")
    result = prune_file(source, _request(owned), now=NOW)
    assert result.reason is not None
    assert result.reason.startswith("skipped-ledger:")
    assert source.read_bytes() == BODY and not receipt_path(source).exists()
    with closing(sqlite3.connect(second)) as db:
        assert retired_job(db) is None


def test_running_job_cannot_be_retired(owned: Path) -> None:
    ledger = _ledger(owned / "recovery", 0, owned / "events.jsonl")
    before = _rows(ledger)
    with Path(str(ledger) + ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RetirementError, match="running"):
            retire_job(ledger, reason="Do not retire a running job")
    assert _rows(ledger) == before
    with closing(sqlite3.connect(ledger)) as db:
        assert retired_job(db) is None


@pytest.mark.parametrize("change", ["copy", "wrong-job", "invalid-row", "unknown-schema"])
def test_invalid_retirement_never_bypasses_pruning(owned: Path, change: str) -> None:
    source = owned / "events.jsonl"
    source.write_bytes(BODY)
    ledger = _ledger(owned / "recovery", 0, source)
    retire_job(ledger, reason="Explicit abandonment")
    if change == "copy":
        copy = ledger.with_suffix(".copy")
        shutil.copy2(ledger, copy)
        os.replace(copy, ledger)
    else:
        with closing(sqlite3.connect(ledger)) as db:
            if change == "wrong-job":
                db.execute("UPDATE recovery_job_retirement SET job_id='some-other-job'")
            elif change == "invalid-row":
                db.execute("DELETE FROM recovery_job_retirement")
            else:
                db.execute("ALTER TABLE cursor ADD COLUMN unexpected TEXT")
            db.commit()
    result = prune_file(source, _request(owned), now=NOW)
    assert result.reason is not None
    assert result.status == "error" and result.reason.startswith("recovery-state-unknown:")
    assert source.read_bytes() == BODY and not receipt_path(source).exists()


def test_absent_or_unsafe_job_is_not_created_or_retired(owned: Path) -> None:
    missing = owned / "missing.v2.sqlite3"
    with pytest.raises(FileNotFoundError):
        retire_job(missing, reason="Absent is not abandoned")
    assert not missing.exists()
    ledger = _ledger(owned / "recovery", 0, owned / "events.jsonl")
    link = owned / "alias.v2.sqlite3"
    link.symlink_to(ledger)
    with pytest.raises(PruneError, match="unsafe"):
        retire_job(link, reason="Not an owned plain ledger")
    assert _rows(ledger)


@pytest.mark.parametrize("phase", ["BEGIN", "COMMIT"])
def test_retirement_sqlite_contention_rolls_back_and_retries(
    owned: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from context_intelligence import recovery_retirement

    ledger = _ledger(owned / "recovery", 0, owned / "events.jsonl")
    before = _rows(ledger)
    trace: list[str] = []

    def connect(*args, **kwargs) -> sqlite3.Connection:
        db = sqlite3.connect(*args, **kwargs)
        db.set_trace_callback(trace.append)
        return db

    with closing(sqlite3.connect(ledger, timeout=0)) as blocker:
        # A reader in WAL mode cannot block COMMIT. Use a real rollback journal,
        # keeping SQLite's normal synchronous/durability settings intact.
        assert blocker.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        if phase == "BEGIN":
            blocker.execute("BEGIN IMMEDIATE")
        else:
            blocker.execute("BEGIN")
            blocker.execute("SELECT * FROM artifact").fetchall()  # Keep SHARED until release.
        try:
            with monkeypatch.context() as patcher:
                # Scope the connection factory to retirement, not sqlite3 globally.
                patcher.setattr(recovery_retirement, "sqlite3", SimpleNamespace(connect=connect))
                with pytest.raises(sqlite3.OperationalError, match="locked") as failure:
                    retire_job(ledger, reason="Only committed abandonment may release retention")
            assert failure.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
            assert "BEGIN IMMEDIATE" in trace
            inserted = [
                sql for sql in trace if sql.startswith("INSERT INTO recovery_job_retirement")
            ]
            if phase == "BEGIN":
                assert trace[-1] == "BEGIN IMMEDIATE"
                assert not inserted and "COMMIT" not in trace
            else:
                assert len(inserted) == 1
                assert (
                    trace.index("BEGIN IMMEDIATE")
                    < trace.index(inserted[0])
                    < trace.index("COMMIT")
                )
                assert trace[-1] == "COMMIT"
            # Fresh connections see neither a marker nor any outcome changes,
            # even while the reader/writer blocker is still held.
            assert _rows(ledger) == before
            with closing(sqlite3.connect(ledger)) as db:
                assert retired_job(db) is None
                assert not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='recovery_job_retirement'"
                ).fetchall()
            with Path(str(ledger) + ".lock").open("rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            blocker.rollback()

    receipt = retire_job(ledger, reason="Retry after the actual SQLite blocker releases")
    assert receipt["disposition"] == "abandoned"
    assert _rows(ledger) == before
    with closing(sqlite3.connect(ledger)) as db:
        assert retired_job(db) == receipt


@pytest.mark.parametrize("target_kind", ["ledger", "lock"])
def test_inserted_marker_rolls_back_if_retirement_identity_changes(
    owned: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    from context_intelligence import recovery_retirement

    ledger = _ledger(owned / "recovery", 0, owned / "events.jsonl")
    with closing(sqlite3.connect(ledger)) as db:
        assert db.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    before = _rows(ledger)
    lock_path = Path(str(ledger) + ".lock")
    target = ledger if target_kind == "ledger" else lock_path
    initial_identity = target.stat().st_ino
    replacement = owned / "replacement"
    displaced = owned / "displaced"
    shutil.copy2(target, replacement)
    inserted: list[tuple] = []
    trace: list[str] = []

    class SwapAfterInsert(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.startswith("INSERT INTO recovery_job_retirement"):
                # Execute the real INSERT first; do not count private path checks
                # or mutate the filesystem from a pre-execution trace callback.
                assert self.in_transaction
                inserted.append(
                    super().execute("SELECT disposition FROM recovery_job_retirement").fetchone()
                )
                os.replace(target, displaced)
                os.replace(replacement, target)
            return cursor

    def connect(*args, **kwargs) -> sqlite3.Connection:
        db = sqlite3.connect(*args, factory=SwapAfterInsert, **kwargs)
        db.set_trace_callback(trace.append)
        return db

    with monkeypatch.context() as patcher:
        patcher.setattr(recovery_retirement, "sqlite3", SimpleNamespace(connect=connect))
        with pytest.raises(
            RetirementError, match="recovery job identity changed during retirement"
        ):
            retire_job(ledger, reason="Do not commit against a replaced ledger or runner lock")
    assert inserted == [("abandoned",)]
    assert "BEGIN IMMEDIATE" in trace and "COMMIT" not in trace
    assert target.stat().st_ino != initial_identity
    assert displaced.stat().st_ino == initial_identity
    for path in (ledger, displaced) if target_kind == "ledger" else (ledger,):
        assert _rows(path) == before
        with closing(sqlite3.connect(path)) as db:
            assert retired_job(db) is None
            assert not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='recovery_job_retirement'"
            ).fetchall()
    # The old lock handle must close too when its pathname has been replaced.
    for path in (lock_path, displaced) if target_kind == "lock" else (lock_path,):
        with path.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    receipt = retire_job(ledger, reason="Retry using the current identities")
    assert receipt["disposition"] == "abandoned"
    assert _rows(ledger) == before


def test_retirement_does_not_authorize_writers_stopped(owned: Path) -> None:
    from context_intelligence.prune import PruneRequest

    ledger = _ledger(owned / "recovery", 0, owned / "events.jsonl")
    retire_job(ledger, reason="Abandoned")
    with pytest.raises(ValueError, match="writers_stopped or inactive_only"):
        PruneRequest(owned, apply=True)


@pytest.mark.parametrize("target_kind", ["ledger", "lock"])
def test_retired_ledger_identity_is_rechecked_before_prune_receipt(
    owned: Path, target_kind: str
) -> None:
    source = owned / "events.jsonl"
    source.write_bytes(BODY)
    ledger = _ledger(owned / "recovery", 0, source)
    retire_job(ledger, reason="Abandoned, not exempt from identity checks")
    before = _rows(ledger)
    with closing(sqlite3.connect(ledger)) as db:
        marker_before = db.execute("SELECT * FROM recovery_job_retirement").fetchall()
    target = ledger if target_kind == "ledger" else Path(str(ledger) + ".lock")
    original_inode = target.stat().st_ino
    replacement = owned / "replacement"
    shutil.copy2(target, replacement)
    changed = False

    def replace_during_prune() -> None:
        nonlocal changed
        # Wait for an actual staged rewrite, rather than counting invocations of
        # before_commit (also used to poll during the scan).
        if not changed and list(source.parent.glob(".events-prune-*")):
            assert source.read_bytes() == BODY
            assert not receipt_path(source).exists()
            os.replace(replacement, target)
            changed = True

    result = prune_file(source, _request(owned, before_commit=replace_during_prune), now=NOW)
    assert changed and target.stat().st_ino != original_inode
    assert result.status == "error"
    assert result.reason == "recovery-state-changed: checked state identity replaced"
    assert source.read_bytes() == BODY and not receipt_path(source).exists()
    assert _rows(ledger) == before
    with closing(sqlite3.connect(ledger)) as db:
        # A copied marker is no longer valid for the replacement inode, but the
        # pruner must not rewrite its stored abandonment/outcome evidence.
        assert db.execute("SELECT * FROM recovery_job_retirement").fetchall() == marker_before
    for pattern in (".events-prune-*", ".prune-receipt-*", "*.prune-attempt-*", "*.bak"):
        assert not list(owned.rglob(pattern))
    with Path(str(ledger) + ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with capture_lock(source, exclusive=True):
        pass


@pytest.mark.parametrize(
    ("guard", "status", "reason"),
    [
        ("eligible", "pruned", None),
        ("maintenance-lock", "skipped", "capture-maintenance-busy: skip and retry later"),
        ("source-flock", "error", "file is locked by another process"),
        ("young-event", "unchanged", None),
        ("recent-mtime", "skipped", "recently modified (min-idle safety guard)"),
        (
            "replay-receipt",
            "skipped",
            "pruned-capture: raw retention is irreversible; replay refused",
        ),
    ],
)
def test_retirement_preserves_remaining_prune_guards(
    owned: Path, guard: str, status: str, reason: str | None
) -> None:
    source = owned / "events.jsonl"
    body = BODY
    if guard == "young-event":
        record = json.loads(body)
        record["timestamp"] = NOW.isoformat()
        body = (json.dumps(record) + "\n").encode()
    source.write_bytes(body)
    old = (NOW - timedelta(days=30)).timestamp()
    os.utime(source, (old, old))
    ledger = _ledger(owned / "recovery", 0, source)
    receipt = retire_job(ledger, reason="Release this job's barrier, not every prune guard")
    before = _rows(ledger)
    request = _request(owned)
    fence = receipt_path(source)
    if guard == "recent-mtime":
        os.utime(source, (NOW.timestamp(), NOW.timestamp()))
        request = replace(request, min_idle=timedelta(hours=1))
    if guard == "replay-receipt":
        fence.write_bytes(b'{"state":"prepared","synthetic":"retain existing fence"}\n')
    fence_before = fence.read_bytes() if fence.exists() else None

    with ExitStack() as locks:
        if guard == "maintenance-lock":
            locks.enter_context(capture_lock(source))
        if guard == "source-flock":
            held = locks.enter_context(source.open("rb"))
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = prune_file(source, request, now=NOW)
    assert (result.status, result.reason) == (status, reason)
    assert result.payloads == (1 if guard == "eligible" else 0)
    if guard == "eligible":
        expected = json.loads(body)
        del expected["data"]["raw"]
        assert json.loads(source.read_bytes()) == expected
        assert (
            json.loads(fence.read_bytes())["source"]["sha256"] == hashlib.sha256(body).hexdigest()
        )
    else:
        assert source.read_bytes() == body
        assert (fence.read_bytes() if fence.exists() else None) == fence_before
    assert _rows(ledger) == before
    with closing(sqlite3.connect(ledger)) as db:
        assert retired_job(db) == receipt
    for pattern in (".events-prune-*", ".prune-receipt-*", "*.prune-attempt-*", "*.bak"):
        assert not list(owned.rglob(pattern))
    with capture_lock(source, exclusive=True):
        pass


def test_retirement_preserves_inactive_execution_lock_until_release(owned: Path) -> None:
    # Minimal real ownership fixture, matching test_prune_inactive.py's protocol.
    workspace = owned / "workspace"
    workspace.mkdir()
    sid = "synthetic-retired-root"
    session = owned / "projects" / str(workspace).replace("/", "-") / "sessions" / sid
    source = session / "context-intelligence/events.jsonl"
    source.parent.mkdir(parents=True)
    source.write_bytes(BODY)
    state = owned / "execution-state"
    owner_dir = state / "v1" / hashlib.sha256(str(workspace).encode()).hexdigest() / sid
    owner_dir.mkdir(parents=True)
    lock_path = owner_dir / "session.lock"
    lock_path.touch(mode=0o600)
    original_inode = lock_path.stat().st_ino
    old = NOW - timedelta(days=30)
    owner = owner_dir / "owner.json"
    owner.write_text(
        json.dumps(
            {
                "app": "amplifier-cli",
                "hostname": socket.gethostname(),
                "session_id": sid,
                "workspace": str(workspace),
                "coordination_root": str(state),
                "active": False,
                "released_at": old.isoformat(),
            }
        )
    )
    metadata = source.with_name("metadata.json")
    metadata.write_text(
        json.dumps(
            {
                "session_id": sid,
                "parent_id": "",
                "status": "completed",
                "working_dir": str(workspace),
                "ended_at": old.isoformat(),
                "last_event_at": old.isoformat(),
            }
        )
    )
    native = session / "metadata.json"
    native.write_text(json.dumps({"session_id": sid}))
    preserved = {path: path.read_bytes() for path in (owner, metadata, native)}
    for path in (source, *preserved):
        os.utime(path, (old.timestamp(), old.timestamp()))
    ledger = _ledger(owned / "recovery", 0, source)
    receipt = retire_job(ledger, reason="Abandonment does not fence an active execution")
    before = _rows(ledger)
    request = replace(_request(owned), writers_stopped=False, inactive_only=True, state_root=state)

    with lock_path.open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = prune_file(source, request, now=NOW)
        assert (result.status, result.reason) == ("skipped", "execution lock held; session is live")
        assert source.read_bytes() == BODY and not receipt_path(source).exists()
        assert _rows(ledger) == before
        with closing(sqlite3.connect(ledger)) as db:
            assert retired_job(db) == receipt
    # The exact same capture/request must become eligible after the real flock
    # releases; a legacy/metadata refusal must not masquerade as lock coverage.
    result = prune_file(source, request, now=NOW)
    assert (result.status, result.reason, result.payloads) == ("pruned", None, 1)
    expected = json.loads(BODY)
    del expected["data"]["raw"]
    assert json.loads(source.read_bytes()) == expected
    fence = json.loads(receipt_path(source).read_bytes())
    assert fence["effective_policy"]["inactive_only"] is True
    assert fence["effective_policy"]["writers_stopped"] is False
    assert _rows(ledger) == before
    with closing(sqlite3.connect(ledger)) as db:
        assert retired_job(db) == receipt
    assert {path: path.read_bytes() for path in preserved} == preserved
    assert lock_path.stat().st_ino == original_inode
    with lock_path.open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for pattern in (".events-prune-*", ".prune-receipt-*", "*.prune-attempt-*", "*.bak"):
        assert not list(owned.rglob(pattern))
