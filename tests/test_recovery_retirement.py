"""Explicit abandonment releases one retention barrier, never old delivery evidence."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_prune_ledger_resources import _SCHEMA, BODY, NOW, _ledger, _request

from context_intelligence import config
from context_intelligence.prune import prune_file
from context_intelligence.prune.coordination import PruneError, receipt_path
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
    assert prune_file(source, _request(owned), now=NOW).reason.startswith("skipped-ledger:")

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
    assert prune_file(source, _request(owned), now=NOW).reason.startswith("skipped-ledger:")
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


def test_interrupted_retirement_rolls_back_without_changing_rows(
    owned: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from context_intelligence.prune import recovery

    ledger = _ledger(owned / "recovery", 0, owned / "events.jsonl")
    before = _rows(ledger)
    original = recovery._safe_file
    count = 0

    def fail_last_identity_check(path: Path):
        nonlocal count
        count += 1
        if count == 5:  # final precommit path check, after the proposed row exists
            raise OSError("synthetic interrupted final check")
        return original(path)

    monkeypatch.setattr(recovery, "_safe_file", fail_last_identity_check)
    with pytest.raises(OSError, match="synthetic interrupted"):
        retire_job(ledger, reason="Only an atomic disposition may release retention")
    with closing(sqlite3.connect(ledger)) as db:
        assert retired_job(db) is None
    assert _rows(ledger) == before


def test_retirement_does_not_authorize_writers_stopped(owned: Path) -> None:
    from context_intelligence.prune import PruneRequest

    ledger = _ledger(owned / "recovery", 0, owned / "events.jsonl")
    retire_job(ledger, reason="Abandoned")
    with pytest.raises(ValueError, match="writers_stopped or inactive_only"):
        PruneRequest(owned, apply=True)
