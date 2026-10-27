"""Read-only exclusion of captures registered with durable recovery jobs.

Custom recovery state locations must be supplied by the host. No cursor reset,
delivery inference, schema migration or checkpoint mutation is performed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .coordination import PruneError


def default_recovery_root() -> Path:
    from context_intelligence.config import global_recovery_state_root

    return global_recovery_state_root()


_REGISTRY = "recovery-source-enrollments.sqlite3"
_PATHLESS_ARTIFACT = {
    "artifact_id",
    "state",
    "reason",
    "session_id",
    "workspace",
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
}
_PATHLESS_CURSOR = {
    "artifact_id",
    "destination",
    "target_fingerprint",
    "policy_fingerprint",
    "ordinal",
    "byte_offset",
    "state",
    "retry_at",
    "attempts",
    "error",
    "source_order_key",
}


def _safe_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise PruneError("recovery-state-unsafe: require owner regular single-link state")
    return info


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise PruneError("recovery-state-unknown: invalid SQLite state")
    except BaseException:
        connection.close()
        raise
    return connection


def _validate_registry(path: Path) -> None:
    """Classify anti-remint authority, NOT delivery or job membership.

    Do not instantiate the producer's mutating registry class or inspect any
    capability/handle rows. Unknown and obsolete content-keyed schemas refuse.
    SQLite readers may maintain WAL shared-memory read marks; main DB is read-only.
    """
    initial = _safe_file(path)
    connection = _open_readonly(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns = list(connection.execute("PRAGMA table_info(enrollment)"))
        shape = [(row[1], row[2].upper(), row[3], row[5]) for row in columns]
        if tables not in ({"enrollment"}, {"enrollment", "legacy_enrollment"}) or shape != [
            ("capture_identity_sha256", "TEXT", 0, 1),
            ("source_handle", "TEXT", 1, 0),
        ]:
            raise PruneError("recovery-state-unknown: unsupported enrollment authority")
        if "legacy_enrollment" in tables:
            columns = list(connection.execute("PRAGMA table_info(legacy_enrollment)"))
            if [(r[1], r[2].upper(), r[5]) for r in columns] != [("marker", "INTEGER", 1)]:
                raise PruneError("recovery-state-unknown: unsupported legacy enrollment authority")
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='legacy_enrollment' AND type='table'"
            ).fetchone()[0]
            if "check(marker=1)" not in "".join(sql.lower().split()):
                raise PruneError("recovery-state-unknown: unsupported legacy enrollment constraint")
        if _identity(initial) != _identity(_safe_file(path)):
            raise PruneError("recovery-state-changed: enrollment authority replaced")
    finally:
        connection.close()


def _exclude_registered(connection: sqlite3.Connection, source: Path) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(artifact)")}
    cursors = {row[1] for row in connection.execute("PRAGMA table_info(cursor)")}
    supported_cursors = (
        _PATHLESS_CURSOR,
        _PATHLESS_CURSOR | {"source_refusal_attempts"},
        _PATHLESS_CURSOR | {"unavailable_attempts"},
        _PATHLESS_CURSOR | {"source_refusal_attempts", "unavailable_attempts"},
    )
    historical = columns == _PATHLESS_ARTIFACT | {"path", "working_dir"}
    if columns != _PATHLESS_ARTIFACT and not historical:
        raise PruneError("recovery-state-unknown: ambiguous V2 checkpoint schema")
    if cursors not in supported_cursors:
        raise PruneError("recovery-state-unknown: unsupported V2 checkpoint")
    # Both supported producers use absolute(), not resolve(), for this ID.
    artifact_id = hashlib.sha256(str(source.absolute()).encode("utf-8")).hexdigest()
    if connection.execute("SELECT 1 FROM artifact WHERE artifact_id=?", (artifact_id,)).fetchone():
        raise PruneError("skipped-ledger: source registered with a recovery job")
    if historical:
        for stored_id, path in connection.execute("SELECT artifact_id,path FROM artifact"):
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise PruneError("recovery-state-unknown: invalid source identity")
            if Path(path).resolve() == source.resolve():
                raise PruneError("skipped-ledger: source registered with a recovery job")
            expected = hashlib.sha256(str(Path(path).absolute()).encode("utf-8")).hexdigest()
            if stored_id != expected:
                raise PruneError("recovery-state-unknown: inconsistent historical source identity")
    # The stored schema has no device ID, so inode matching is deliberately
    # conservative: an unrelated same-number inode may over-skip, never permit.
    if connection.execute(
        "SELECT 1 FROM artifact WHERE CAST(source_inode AS TEXT)=?",
        (str(_safe_file(source).st_ino),),
    ).fetchone():
        raise PruneError("skipped-ledger: source inode registered with a recovery job")
    # A ledger made through an alternate path spelling must not escape exclusion.
    # Conservatively exclude same native identity as well; no event bodies read.
    if source.parent.name == "context-intelligence":
        metadata_path = source.parent / "metadata.json"
        _safe_file(metadata_path)
        if metadata_path.stat().st_size > 1024 * 1024:
            raise PruneError("recovery-state-unknown: oversized capture metadata")
        fd = os.open(metadata_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise PruneError("recovery-state-unsafe: nonregular capture metadata")
            try:
                metadata = json.loads(handle.read(1024 * 1024 + 1))
            except (ValueError, UnicodeError) as exc:
                raise PruneError("recovery-state-unknown: invalid capture metadata") from exc
        if not isinstance(metadata, dict) or not all(
            isinstance(metadata.get(key), str) and metadata[key]
            for key in ("session_id", "workspace")
        ):
            raise PruneError("recovery-state-unknown: missing native capture identity")
        if connection.execute(
            "SELECT 1 FROM artifact WHERE session_id=? AND workspace=?",
            (metadata["session_id"], metadata["workspace"]),
        ).fetchone():
            raise PruneError("skipped-ledger: native identity registered with a recovery job")


@contextmanager
def recovery_guard(
    source: Path, additional_roots: tuple[Path, ...] = ()
) -> Iterator[Callable[[], None]]:
    import fcntl

    with ExitStack():
        ledgers: set[Path] = set()
        seen: set[Path] = set()
        checked_identities: list[tuple[Path, tuple[int, int]]] = []

        def walk_error(error: OSError) -> None:
            raise PruneError("recovery-state-unreadable: cannot establish exclusion") from error

        for index, root in enumerate((default_recovery_root(), *additional_roots)):
            root = root.expanduser().absolute()
            if ".." in root.parts or ".." in source.parts:
                raise PruneError("recovery-state-unsafe: ambiguous path spelling")
            if root in seen:
                continue
            seen.add(root)
            if any(parent.is_symlink() for parent in (root, *root.parents)):
                raise PruneError("recovery-state-unsafe: linked state directory")
            if not root.exists():
                if index:
                    raise PruneError("recovery-state-missing: explicit root does not exist")
                continue
            if not root.is_dir():
                raise PruneError("recovery-state-unsafe: cannot establish exclusion")
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
                if any((Path(directory) / name).is_symlink() for name in dirs):
                    raise PruneError("recovery-state-unsafe: linked state directory")
                for name in files:
                    if name.endswith((".sqlite3", ".sqlite3.lock")):
                        db = Path(directory) / name.removesuffix(".lock")
                        if name == _REGISTRY:
                            try:
                                _validate_registry(db)
                            except (OSError, sqlite3.Error) as exc:
                                raise PruneError(
                                    "recovery-state-unreadable: enrollment authority"
                                ) from exc
                            continue
                        if not db.name.endswith(".v2.sqlite3"):
                            raise PruneError("recovery-state-unknown: legacy checkpoint present")
                        ledgers.add(db)
        for db in sorted(ledgers):
            lock_path = Path(str(db) + ".lock")
            try:
                # A prior scan may have dropped capture SH before publishing its
                # SQLite transaction. Job EX must therefore succeed before the
                # membership read. It fences that pending transaction, not only
                # the source read. All acquisitions remain nonblocking.
                with ExitStack() as ledger:
                    info = lock_path.lstat()
                    db_info = db.lstat()
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_nlink != 1
                        or not stat.S_ISREG(db_info.st_mode)
                        or db_info.st_nlink != 1
                        or db_info.st_uid != os.getuid()
                    ):
                        raise PruneError("recovery-state-unsafe")
                    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    ledger.callback(os.close, fd)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = os.fstat(fd)
                    current = lock_path.lstat()
                    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                        raise PruneError("recovery-state-changed: execution lock replaced")
                    checked_identities.extend(
                        ((lock_path, _identity(held)), (db, _identity(db_info)))
                    )
                    # mode=ro reads committed WAL too; immutable=1 would not.
                    connection = _open_readonly(db)
                    ledger.callback(connection.close)
                    _exclude_registered(connection, source)
                # Close SQLite before releasing job EX. A later cooperating
                # runner cannot scan/register this source while the caller still
                # holds capture EX. Keep only identities, not O(job-count) FDs.
            except (OSError, sqlite3.Error) as exc:
                raise PruneError(
                    "recovery-state-busy-or-unreadable: retry after recovery settles"
                ) from exc

        def recheck() -> None:
            for path, identity in checked_identities:
                if _identity(_safe_file(path)) != identity:
                    raise PruneError("recovery-state-changed: checked state identity replaced")

        recheck()
        yield recheck
        recheck()
