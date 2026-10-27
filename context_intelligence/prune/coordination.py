"""Shared per-capture maintenance protocol for prune, upload and recovery.

All flock acquisitions are nonblocking. Never unlink a lock; its inode must
remain stable. This protocol is independent of the live session writer fence.
Pruning takes capture maintenance -> recovery runner -> Foundation execution.
Recovery takes its runner lock before a shared capture lock. Every acquisition
is nonblocking: either contender refuses/releases rather than waiting in a cycle.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class PruneError(Exception):
    """Explicit maintenance refusal or precommit transaction failure."""


class CaptureBusy(PruneError):
    """A cooperating reader or maintenance transaction owns this capture."""


class PrunedCapture(PruneError):
    """Pruned or interrupted-pruning source: cannot replay as original."""


def receipt_path(path: Path) -> Path:
    return path.with_name(path.name + ".prune-receipt.json")


def assert_replayable(path: Path) -> None:
    # lexists includes malformed files, directories and dangling symlinks.
    if os.path.lexists(receipt_path(path)):
        raise PrunedCapture("pruned-capture: raw retention is irreversible; replay refused")


@contextmanager
def capture_lock(path: Path, *, exclusive: bool = False) -> Iterator[None]:
    import fcntl

    path = path.expanduser().absolute()
    lock = path.with_name(path.name + ".maintenance.lock")
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise PruneError("symlink or non-directory capture ancestor")
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as exc:
        # A failed fence is a maintenance refusal, not evidence that the source
        # is absent/non-capture. Keep OS details in the cause, not CLI output.
        raise PruneError("cannot open capture maintenance lock") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise PruneError("unsafe capture maintenance lock")
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CaptureBusy("capture-maintenance-busy: skip and retry later") from exc
        current = lock.lstat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise PruneError("capture maintenance lock identity changed")
        yield
    finally:
        os.close(fd)


@contextmanager
def replay_guard(path: Path) -> Iterator[None]:
    """Acquire BEFORE opening source; hold through consumption/delivery."""
    with capture_lock(path):
        assert_replayable(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            pass  # Missing sources still participate in the stable lock/marker protocol.
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PruneError("unsafe capture source: require regular, single-link file")
        yield


@contextmanager
def open_capture(path: Path, *, binary: bool = False, errors: str = "strict") -> Iterator[Any]:
    """No-follow source open, with the replay fence held for the full read."""
    with replay_guard(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PruneError("unsafe capture source: require regular, single-link file")
            if binary:
                handle = os.fdopen(fd, "rb")
            else:
                handle = os.fdopen(fd, "r", encoding="utf-8", errors=errors)
        except BaseException:
            os.close(fd)
            raise
        with handle:
            yield handle
