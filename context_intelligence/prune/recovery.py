"""Read-only exclusion of captures registered with durable recovery jobs.

Custom recovery state locations must be supplied by the host. No cursor reset,
delivery inference, schema migration or checkpoint mutation is performed here.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from .coordination import PruneError


def default_recovery_root() -> Path:
    return Path.home() / ".local/state/context-intelligence/recovery"


@contextmanager
def recovery_guard(source: Path, additional_roots: tuple[Path, ...] = ()) -> Iterator[None]:
    import fcntl

    with ExitStack() as stack:
        ledgers: set[Path] = set()

        def walk_error(error: OSError) -> None:
            raise PruneError("recovery-state-unreadable: cannot establish exclusion") from error

        for index, root in enumerate((default_recovery_root(), *additional_roots)):
            root = root.expanduser().absolute()
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
                        if not db.name.endswith(".v2.sqlite3"):
                            raise PruneError("recovery-state-unknown: legacy checkpoint present")
                        ledgers.add(db)
        for db in sorted(ledgers):
            lock_path = Path(str(db) + ".lock")
            try:
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
                stack.callback(os.close, fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = os.fstat(fd)
                current = lock_path.lstat()
                if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                    raise PruneError("recovery-state-changed: execution lock replaced")
                # mode=ro reads committed WAL too; do not use immutable=1,
                # which can silently ignore a live WAL. Runner lock is held.
                connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
                stack.callback(connection.close)
                connection.execute("PRAGMA query_only=ON")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(artifact)")}
                cursors = {row[1] for row in connection.execute("PRAGMA table_info(cursor)")}
                if (
                    not {"artifact_id", "path", "source_sha256", "state"} <= columns
                    or not {
                        "artifact_id",
                        "ordinal",
                        "byte_offset",
                    }
                    <= cursors
                ):
                    raise PruneError("recovery-state-unknown: unsupported V2 checkpoint")
                for (path,) in connection.execute("SELECT path FROM artifact"):
                    if not isinstance(path, str) or not Path(path).is_absolute():
                        raise PruneError("recovery-state-unknown: invalid source identity")
                    if Path(path).resolve() == source.resolve():
                        raise PruneError("skipped-ledger: source registered with a recovery job")
            except (OSError, sqlite3.Error) as exc:
                raise PruneError(
                    "recovery-state-busy-or-unreadable: retry after recovery settles"
                ) from exc
        yield
