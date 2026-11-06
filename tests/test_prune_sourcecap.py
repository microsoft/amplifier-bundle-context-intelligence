"""Read-only adapters for sourcecap authority and pathless V2 jobs (synthetic only)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_prune_ledger_resources import _mutation_stat

from context_intelligence.prune import Policy, PruneRequest, prune_file
from context_intelligence.prune.coordination import receipt_path
from context_intelligence.prune.recovery import _PATHLESS_ARTIFACT, _PATHLESS_CURSOR

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    monkeypatch.setattr(
        "context_intelligence.prune.recovery.default_recovery_root", lambda: tmp_path / "recovery"
    )


def source(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "event": "llm:request",
                "timestamp": "2026-01-01T00:00:00Z",
                "data": {"raw": "synthetic"},
            }
        )
        + "\n"
    )
    return path


def request(root):
    return PruneRequest(
        root,
        Policy(NOW - timedelta(days=7)),
        apply=True,
        writers_stopped=True,
        min_idle=timedelta(0),
    )


def registry(tmp_path, legacy=False):
    root = tmp_path / "recovery"
    root.mkdir(exist_ok=True)
    path = root / "recovery-source-enrollments.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE enrollment(capture_identity_sha256 TEXT PRIMARY KEY, source_handle TEXT NOT NULL)"
        )
        db.execute("INSERT INTO enrollment VALUES(?,?)", ("a" * 64, "nonsecret-handle"))
        if legacy:
            db.execute("CREATE TABLE legacy_enrollment(marker INTEGER PRIMARY KEY CHECK(marker=1))")
            db.execute("INSERT INTO legacy_enrollment VALUES(1)")
    return path


@pytest.mark.parametrize("legacy", [False, True])
def test_registry_classification_not_job_membership_and_no_mutation(tmp_path, legacy):
    path = source(tmp_path)
    db = registry(tmp_path, legacy)
    before, info = db.read_bytes(), db.stat()
    result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "pruned"
    assert db.read_bytes() == before
    assert db.stat().st_mtime_ns == info.st_mtime_ns
    assert not list(db.parent.glob("*.lock"))  # no imaginary registry flock


@pytest.mark.parametrize(
    "damage", ["corrupt", "columns", "linked", "hardlinked", "extra", "old-schema"]
)
def test_unsafe_registry_never_exempted_by_filename(tmp_path, damage):
    path = source(tmp_path)
    original = path.read_bytes()
    db = registry(tmp_path)
    if damage == "corrupt":
        db.write_bytes(b"not sqlite")
    elif damage in {"columns", "extra", "old-schema"}:
        with sqlite3.connect(db) as conn:
            if damage == "extra":
                conn.execute("CREATE TABLE unexpected(value TEXT)")
            else:
                conn.execute("DROP TABLE enrollment")
                conn.execute(
                    "CREATE TABLE enrollment("
                    + (
                        "capture_fingerprint TEXT,source_handle TEXT,source_stream_sha256 TEXT,source_sha256 TEXT,metadata_sha256 TEXT"
                        if damage == "old-schema"
                        else "capture_identity_sha256 TEXT,source_handle TEXT"
                    )
                    + ")"
                )
    elif damage == "linked":
        target = db.with_suffix(".target")
        db.rename(target)
        db.symlink_to(target)
    else:
        os.link(db, db.with_suffix(".hardlink"))
    result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "error"
    assert path.read_bytes() == original and not receipt_path(path).exists()


def ledger(tmp_path, path, state, *, identity=None, session="", workspace=""):
    root = tmp_path / "recovery"
    root.mkdir(exist_ok=True)
    db = root / "job.v2.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    columns = sorted(_PATHLESS_ARTIFACT)
    conn.execute("CREATE TABLE artifact(" + ",".join(c + " TEXT" for c in columns) + ")")
    conn.execute(
        "CREATE TABLE cursor(" + ",".join(c + " TEXT" for c in sorted(_PATHLESS_CURSOR)) + ")"
    )
    row = dict.fromkeys(columns, "")
    row.update(
        artifact_id=identity or hashlib.sha256(str(path.absolute()).encode()).hexdigest(),
        state=state,
        session_id=session,
        workspace=workspace,
    )
    conn.execute(
        "INSERT INTO artifact VALUES(" + ",".join("?" for _ in columns) + ")",
        tuple(row[c] for c in columns),
    )
    conn.execute(
        "INSERT INTO cursor(artifact_id,ordinal,byte_offset,state) VALUES(?,?,?,?)",
        (row["artifact_id"], "12", "345", "accepted"),
    )
    conn.commit()
    Path(str(db) + ".lock").touch(mode=0o600)
    return db, conn


@pytest.mark.parametrize(
    "state",
    ["eligible", "accepted", "filtered", "quarantined", "deferred", "blocked", "inventory-pending"],
)
def test_pathless_jobs_exclude_all_states_and_read_committed_WAL(tmp_path, state):
    path = source(tmp_path)
    before = path.read_bytes()
    db, writer = ledger(tmp_path, path, state)
    try:
        main_before, wal_size = db.read_bytes(), Path(str(db) + "-wal").stat().st_size
        rows = list(writer.execute("SELECT * FROM cursor"))
        result = prune_file(path, request(tmp_path), now=NOW)
        assert result.status == "skipped" and "skipped-ledger:" in (result.reason or "")
        assert path.read_bytes() == before and not receipt_path(path).exists()
        assert db.read_bytes() == main_before
        assert Path(str(db) + "-wal").stat().st_size == wal_size
        assert list(writer.execute("SELECT * FROM cursor")) == rows
    finally:
        writer.close()


def test_alias_identity_fallback_excludes_without_raw_event_read(tmp_path):
    path = tmp_path / "context-intelligence" / "events.jsonl"
    path.parent.mkdir()
    path.write_bytes(source(tmp_path).read_bytes())
    (path.parent / "metadata.json").write_text('{"session_id":"same","workspace":"project"}')
    _, writer = ledger(
        tmp_path, path, "accepted", identity="a" * 64, session="same", workspace="project"
    )
    try:
        result = prune_file(path, request(tmp_path), now=NOW)
        assert result.status == "skipped" and "native identity" in (result.reason or "")
        assert not receipt_path(path).exists()
    finally:
        writer.close()


def test_unrelated_pathless_job_allows_later_writer_guards(tmp_path):
    path = source(tmp_path)
    _, writer = ledger(tmp_path, tmp_path / "other.jsonl", "accepted")
    try:
        assert prune_file(path, request(tmp_path), now=NOW).status == "pruned"
    finally:
        writer.close()


def test_replaced_ledger_lock_refuses_before_receipt(tmp_path):
    path = source(tmp_path)
    before = path.read_bytes()
    db, writer = ledger(tmp_path, tmp_path / "other.jsonl", "accepted")
    try:

        def substitute():
            lock = Path(str(db) + ".lock")
            lock.rename(lock.with_suffix(".old"))
            lock.touch(mode=0o600)

        req = request(tmp_path)
        from dataclasses import replace

        req = replace(req, before_commit=substitute)
        result = prune_file(path, req, now=NOW)
        assert result.status == "error"
        assert path.read_bytes() == before and not receipt_path(path).exists()
    finally:
        writer.close()


def test_hybrid_path_and_pathless_schema_cannot_bypass_membership(tmp_path):
    path = source(tmp_path)
    before = path.read_bytes()
    _, writer = ledger(tmp_path, path, "accepted")
    try:
        writer.execute("ALTER TABLE artifact ADD COLUMN path TEXT")
        writer.execute("UPDATE artifact SET path=?", (str(tmp_path / "unrelated"),))
        writer.commit()
        result = prune_file(path, request(tmp_path), now=NOW)
        assert result.status == "error" and "ambiguous V2" in (result.reason or "")
        assert path.read_bytes() == before and not receipt_path(path).exists()
    finally:
        writer.close()


@pytest.mark.parametrize("registered", [False, True])
def test_supported_migration_retains_unavailable_attempts_column(tmp_path, registered):
    path = source(tmp_path)
    _, writer = ledger(tmp_path, path if registered else tmp_path / "other", "accepted")
    try:
        writer.execute("ALTER TABLE cursor ADD COLUMN unavailable_attempts INTEGER DEFAULT 0")
        writer.execute("ALTER TABLE cursor ADD COLUMN source_refusal_attempts INTEGER DEFAULT 0")
        writer.commit()
        result = prune_file(path, request(tmp_path), now=NOW)
        assert result.status == ("skipped" if registered else "pruned")
    finally:
        writer.close()


@pytest.mark.parametrize("kind", ["matching", "unrelated", "mismatched", "unknown-superset"])
def test_full_historical_schema_identity_and_exact_classification(tmp_path, kind):
    from test_prune_api import make_ledger

    path = source(tmp_path)
    before = path.read_bytes()
    registered = path if kind == "matching" else tmp_path / "other.jsonl"
    db, _ = make_ledger(tmp_path / "recovery", registered)
    with sqlite3.connect(db) as writer:
        if kind == "mismatched":
            identity = hashlib.sha256(str(path.absolute()).encode()).hexdigest()
            writer.execute(
                "UPDATE artifact SET artifact_id=?",
                (identity,),
            )
            writer.execute("UPDATE cursor SET artifact_id=?", (identity,))
        elif kind == "unknown-superset":
            writer.execute("ALTER TABLE artifact ADD COLUMN future_identity TEXT")
    db_before = db.read_bytes()
    result = prune_file(path, request(tmp_path), now=NOW)
    assert (
        result.status
        == {
            "matching": "skipped",
            "mismatched": "skipped",
            "unrelated": "pruned",
            "unknown-superset": "error",
        }[kind]
    )
    assert db.read_bytes() == db_before
    if kind != "unrelated":
        assert path.read_bytes() == before and not receipt_path(path).exists()


def test_pathless_inode_alias_excludes_without_native_metadata(tmp_path):
    path = source(tmp_path)
    before = path.read_bytes()
    _, writer = ledger(tmp_path, tmp_path / "alias", "accepted")
    try:
        writer.execute("UPDATE artifact SET source_inode=?", (str(path.stat().st_ino),))
        writer.commit()
        result = prune_file(path, request(tmp_path), now=NOW)
        assert result.status == "skipped" and "inode" in (result.reason or "")
        assert path.read_bytes() == before and not receipt_path(path).exists()
    finally:
        writer.close()


def _assert_unknown_membership(path, db, req, reason):
    """Unknown membership must refuse before publishing any retention receipt."""
    original, info = path.read_bytes(), path.stat()
    db_before, db_info = db.read_bytes(), db.stat()
    with closing(sqlite3.connect(db)) as reader:
        rows = {
            table: list(reader.execute(f"SELECT * FROM {table}"))
            for table in ("artifact", "cursor")
        }
        assert reader.execute("PRAGMA quick_check").fetchone() == ("ok",)
    result = prune_file(path, req, now=NOW)
    assert result.status == "error" and (result.reason or "").startswith(
        "recovery-state-unknown: " + reason
    ), result
    assert result.payloads == 0 and result.bytes_before == result.bytes_after
    assert path.read_bytes() == original and _mutation_stat(path.stat()) == _mutation_stat(info)
    assert db.read_bytes() == db_before and _mutation_stat(db.stat()) == _mutation_stat(db_info)
    with closing(sqlite3.connect(db)) as reader:
        assert {table: list(reader.execute(f"SELECT * FROM {table}")) for table in rows} == rows
    assert not receipt_path(path).exists()
    assert not list(path.parent.glob("*prune*"))


@pytest.mark.parametrize("historical", [False, True])
def test_orphan_pending_cursor_refuses_even_without_foreign_keys(tmp_path, historical):
    path = source(tmp_path)
    if historical:
        from test_prune_ledger_resources import _SCHEMA, _ledger

        db = _ledger(tmp_path / "recovery", 0, tmp_path / "other")
        with closing(sqlite3.connect(db)) as writer:
            # Historical producers need not have declared a foreign key.
            writer.execute("DROP TABLE cursor")
            writer.executescript(
                _SCHEMA[
                    _SCHEMA.index("CREATE TABLE cursor") : _SCHEMA.index("CREATE INDEX")
                ].replace(" REFERENCES artifact(artifact_id)", "")
            )
            writer.execute(
                "INSERT INTO cursor(artifact_id,destination,target_fingerprint,"
                "policy_fingerprint,state) VALUES(?, 'default', 'target', 'policy', 'pending')",
                ("a" * 64,),
            )
            writer.commit()
            assert not list(writer.execute("PRAGMA foreign_key_check"))
    else:
        db, writer = ledger(tmp_path, tmp_path / "other", "eligible")
        writer.execute("DELETE FROM artifact")
        writer.execute("UPDATE cursor SET state='pending'")
        writer.commit()
        writer.close()
    _assert_unknown_membership(path, db, request(tmp_path), "orphan recovery cursor")


@pytest.mark.parametrize("identity", [None, b"a" * 64, "", "a" * 63, "g" * 64, "A" * 64])
def test_noncanonical_source_identity_refuses_without_orphan_masking(tmp_path, identity):
    path = source(tmp_path)
    db, writer = ledger(tmp_path, tmp_path / "other", "eligible")
    writer.execute("UPDATE artifact SET artifact_id=?", (identity,))
    if identity is None:
        # NULL has no matching SQL identity; no orphan should mask artifact validation.
        writer.execute("DELETE FROM cursor")
    else:
        writer.execute("UPDATE cursor SET artifact_id=?", (identity,))
    writer.commit()
    writer.close()
    _assert_unknown_membership(path, db, request(tmp_path), "invalid source identity")
