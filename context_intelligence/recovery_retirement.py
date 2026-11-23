"""Explicit abandonment of one local recovery job, without changing its outcomes.

Callers must retire older/uncoordinated launchers first. This disposition releases
only that job's pruning exclusion; it is neither delivery nor stopped-writer proof.
"""

from __future__ import annotations

import fcntl
import math
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class RetirementError(RuntimeError):
    """The requested abandonment cannot be established safely."""


_TABLE = "recovery_job_retirement"
_COLUMNS = (
    "singleton",
    "version",
    "job_id",
    "ledger_path",
    "ledger_device",
    "ledger_inode",
    "disposition",
    "recorded_at",
    "reason",
)


def retired_job(connection: sqlite3.Connection) -> dict | None:
    """Read an exact ledger-bound disposition. Malformed/copied markers refuse."""
    table = connection.execute("SELECT type FROM sqlite_master WHERE name=?", (_TABLE,)).fetchone()
    if table is None:
        return None
    if (
        table[0] != "table"
        or tuple(row[1] for row in connection.execute(f"PRAGMA table_info({_TABLE})")) != _COLUMNS
    ):
        raise RetirementError("invalid recovery job retirement schema")
    rows = connection.execute(f"SELECT * FROM {_TABLE}").fetchmany(2)
    if len(rows) != 1:
        raise RetirementError("invalid recovery job retirement record")
    record = dict(zip(_COLUMNS, rows[0]))
    path = Path(connection.execute("PRAGMA database_list").fetchone()[2]).absolute()
    info = path.lstat()
    if (
        record["singleton"] != 1
        or record["version"] != 1
        or not path.name.endswith(".v2.sqlite3")
        or record["job_id"] != path.name.removesuffix(".v2.sqlite3")
        or record["ledger_path"] != str(path)
        or record["ledger_device"] != info.st_dev
        or record["ledger_inode"] != info.st_ino
        or record["disposition"] != "abandoned"
        or not isinstance(record["recorded_at"], (int, float))
        or not math.isfinite(record["recorded_at"])
        or record["recorded_at"] <= 0
        or not isinstance(record["reason"], str)
        or not 1 <= len(record["reason"].strip()) <= 500
    ):
        raise RetirementError("recovery job retirement identity is invalid")
    return record


def retire_job(path: Path, *, reason: str) -> dict:
    """Retire an existing supported job under its unchanged runner lock.

    Artifact/cursor rows, unknown delivery outcomes and separate production or
    enrollment authorities are preserved. Repetition returns the first receipt.
    """
    from context_intelligence.prune.recovery import _safe_file, _validate_checkpoint_schema

    path = path.expanduser().absolute()
    if (
        not path.name.endswith(".v2.sqlite3")
        or path.name == ".v2.sqlite3"
        or ".." in path.parts
        or any(parent.is_symlink() for parent in path.parents)
    ):
        raise RetirementError("require an existing unlinked V2 recovery job path")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise RetirementError("require an abandonment reason of 1 to 500 characters")
    lock_path = Path(str(path) + ".lock")
    original = _safe_file(path)
    _safe_file(lock_path)
    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RetirementError("recovery job is running; stop it before retirement") from exc
        held = os.fstat(fd)

        def recheck() -> None:
            current = _safe_file(path)
            lock = _safe_file(lock_path)
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino) or (
                lock.st_dev,
                lock.st_ino,
            ) != (held.st_dev, held.st_ino):
                raise RetirementError("recovery job identity changed during retirement")

        recheck()
        with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)) as db:
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RetirementError("invalid recovery checkpoint")
            db.execute("BEGIN IMMEDIATE")
            _validate_checkpoint_schema(db)
            existing = retired_job(db)
            if existing is not None:
                return existing
            db.execute(
                f"CREATE TABLE {_TABLE} ("
                "singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL, "
                "job_id TEXT NOT NULL, ledger_path TEXT NOT NULL, ledger_device INTEGER NOT NULL, "
                "ledger_inode INTEGER NOT NULL, disposition TEXT NOT NULL, "
                "recorded_at REAL NOT NULL, reason TEXT NOT NULL)"
            )
            db.execute(
                f"INSERT INTO {_TABLE} VALUES (1,1,?,?,?,?,?,?,?)",
                (
                    path.name.removesuffix(".v2.sqlite3"),
                    str(path),
                    original.st_dev,
                    original.st_ino,
                    "abandoned",
                    time.time(),
                    reason.strip(),
                ),
            )
            recheck()
            db.commit()
            result = retired_job(db)
            assert result is not None
            return result
    finally:
        os.close(fd)
