"""Streaming, per-file transactional pruning. No Amplifier runtime dependency.

Only an allowlisted raw field is removed. Non-target lines are copied byte for
byte. Callers must stop log writers before applying: advisory locks and identity
checks cannot make replacement safe against a writer holding an open descriptor.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator


from .coordination import PruneError, assert_replayable, receipt_path
from .policy import Policy, prune_record


@dataclass
class FileResult:
    path: Path
    status: str = "unchanged"
    lines: int = 0
    payloads: int = 0
    missing_timestamps: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    backup: Path | None = None
    reason: str | None = None

    @property
    def bytes_saved(self) -> int:
        return self.bytes_before - self.bytes_after


def discover_events(root: Path) -> Iterator[Path]:
    """Find exact events.jsonl basenames without following symlink entries."""
    root = root.expanduser().absolute()
    if root.is_symlink():
        raise PruneError(f"refusing symlink root: {root}")
    if root.is_file():
        if root.name != "events.jsonl":
            raise PruneError("a file argument must be named events.jsonl")
        yield root
        return
    if not root.is_dir():
        raise PruneError(f"not a directory or events.jsonl file: {root}")

    def onerror(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        if "events.jsonl" in files:
            path = Path(directory) / "events.jsonl"
            # Yield symlink files too: prune_file reports the safety skip.
            yield path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


class _JSONNumber(str):
    """A validated JSON numeric token, retained without binary-float rounding."""


def _encode_json(value: Any) -> str:
    if isinstance(value, _JSONNumber):
        return str(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=True) + ":" + _encode_json(item)
                for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(_encode_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
    )


def _assert_unchanged(path: Path, source: BinaryIO, initial: os.stat_result) -> None:
    if _signature(path.lstat()) != _signature(initial) or _signature(
        os.fstat(source.fileno())
    ) != _signature(initial):
        raise PruneError("file changed during scan; original not replaced")


@contextmanager
def _lock(source: BinaryIO, apply: bool) -> Iterator[None]:
    if not apply:
        yield
        return
    try:
        import fcntl
    except ImportError as exc:
        raise PruneError("apply requires POSIX advisory file locking") from exc
    try:
        fcntl.flock(source.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise PruneError("file is locked by another process") from exc
    try:
        yield
    finally:
        fcntl.flock(source.fileno(), fcntl.LOCK_UN)


def _stream(
    source: BinaryIO,
    output: BinaryIO | None,
    policy: Policy,
    result: FileResult,
) -> str:
    digest = hashlib.sha256()
    for number, line in enumerate(source, 1):
        digest.update(line)
        result.lines = number
        new_line = line
        if line.strip():
            try:
                record = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                    parse_float=_JSONNumber,
                    parse_int=_JSONNumber,
                )
                if not isinstance(record, dict):
                    raise ValueError("event must be a JSON object")
            except (ValueError, RecursionError) as exc:
                # Do not expose raw JSON/prompt text in error messages.
                raise PruneError(
                    f"invalid JSON event at line {number}; file left unchanged"
                ) from exc
            # Explicit native and hooks-logging V1 adapters, not arbitrary future schemas.
            if "schema" in record:
                schema = record["schema"]
                if (
                    not isinstance(schema, dict)
                    or schema.get("name") != "amplifier.log"
                    or schema.get("ver") != "1.0.0"
                ):
                    raise PruneError(
                        f"unsupported event schema at line {number}; file left unchanged"
                    )
            replacement, undated = prune_record(record, policy)
            result.missing_timestamps += int(undated)
            if replacement is not record:
                ending = (
                    b"\r\n" if line.endswith(b"\r\n") else (b"\n" if line.endswith(b"\n") else b"")
                )
                try:
                    new_line = _encode_json(replacement).encode("utf-8") + ending
                except RecursionError as exc:
                    raise PruneError(
                        f"event nesting too deep at line {number}; file left unchanged"
                    ) from exc
                result.payloads += 1
        result.bytes_after += len(new_line)
        if output is not None:
            output.write(new_line)
    return digest.hexdigest()


def _reject_constant(value: str) -> Any:
    raise ValueError("nonstandard JSON numeric constant")


def _make_backup(path: Path, source: BinaryIO, expected_hash: str, initial: os.stat_result) -> Path:
    backup = path.with_name(path.name + ".bak")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(backup, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            source.seek(0)
            digest = hashlib.sha256()
            while block := source.read(1024 * 1024):
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != expected_hash:
            raise PruneError("source changed while backing up; original not replaced")
        _assert_unchanged(path, source, initial)
    except BaseException:
        backup.unlink()
        raise
    return backup


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _capture_schema(path: Path) -> tuple[Path, tuple[int, ...]] | None:
    """Validate explicit native versions without requiring metadata for legacy logs."""
    metadata = path.with_name("metadata.json")
    if not metadata.exists():
        return None
    info = metadata.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
        raise PruneError("unsupported or unsafe capture metadata")
    try:
        value = json.loads(metadata.read_bytes(), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise PruneError("invalid capture metadata") from exc
    if not isinstance(value, dict):
        raise PruneError("invalid capture metadata")
    if "format" in value or "version" in value:
        if value.get("format") != "context-intelligence" or value.get("version") != "1.0.0":
            raise PruneError("unsupported capture format/version")
    return metadata, _signature(info)


def _publish_receipt(path: Path, body: dict[str, Any]) -> None:
    """Publish the deny-replay fence durably BEFORE the source replacement.

    link() publishes without overwriting even a concurrently created marker.
    Every present marker blocks replay, including a prepared-but-uncommitted one.
    """
    fd, name = tempfile.mkstemp(prefix=".prune-receipt-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(body, output, ensure_ascii=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, receipt_path(path), follow_symlinks=False)
        temporary.unlink()
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def prune_file(
    path: Path,
    policy: Policy,
    *,
    apply: bool = False,
    backup: bool = False,
    min_idle: timedelta = timedelta(hours=1),
    now: datetime | None = None,
    before_commit: Callable[[], None] | None = None,
) -> FileResult:
    """Inspect or atomically replace one log, raising PruneError on unsafe input.

    Malformed JSON aborts the whole file. A subtree is NOT one transaction;
    earlier successfully replaced files stay replaced if a later file fails.
    Backups are optional because keeping full raw copies defeats space recovery.
    """
    if min_idle < timedelta(0):
        raise ValueError("min_idle must not be negative")
    path = Path(path).expanduser().absolute()
    assert_replayable(path)
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    initial = path.lstat()
    result = FileResult(path, bytes_before=initial.st_size, bytes_after=initial.st_size)
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        result.status, result.reason = "skipped", "not a regular, single-link file"
        return result
    if min_idle > timedelta(0) and now.timestamp() - initial.st_mtime < min_idle.total_seconds():
        result.status, result.reason = "skipped", "recently modified (min-idle safety guard)"
        return result
    if apply and hasattr(os, "geteuid") and initial.st_uid != os.geteuid():
        raise PruneError("refusing to replace a file owned by another user")
    capture_schema = _capture_schema(path)

    def check_schema() -> None:
        if capture_schema is not None:
            metadata, signature = capture_schema
            if _signature(metadata.lstat()) != signature:
                raise PruneError("capture metadata changed; original not replaced")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    temporary: Path | None = None
    try:
        with os.fdopen(fd, "rb") as source, _lock(source, apply):
            _assert_unchanged(path, source, initial)
            check_schema()
            if before_commit is not None:
                before_commit()
            result.bytes_after = 0
            if apply:
                temp_fd, temp_name = tempfile.mkstemp(prefix=".events-prune-", dir=path.parent)
                temporary = Path(temp_name)
                with os.fdopen(temp_fd, "wb") as output:
                    digest = _stream(source, output, policy, result)
                    output.flush()
                    if hasattr(os, "fchown"):
                        os.fchown(output.fileno(), initial.st_uid, initial.st_gid)
                    # copystat includes mode, timestamps and xattrs where supported.
                    shutil.copystat(path, temporary, follow_symlinks=False)
                    os.utime(temporary, ns=(initial.st_atime_ns, initial.st_mtime_ns))
                    os.fsync(output.fileno())
            else:
                digest = _stream(source, None, policy, result)
            _assert_unchanged(path, source, initial)
            if before_commit is not None:
                before_commit()
            check_schema()
            if not result.payloads:
                return result
            result.status = "would-prune"
            if not apply:
                return result
            if backup:
                result.backup = _make_backup(path, source, digest, initial)
            # Re-read before commit: detect same-size edits as well as appends.
            source.seek(0)
            if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                raise PruneError("source content changed; original not replaced")
            _assert_unchanged(path, source, initial)
            if before_commit is not None:
                before_commit()
            assert temporary is not None
            with temporary.open("rb") as prepared:
                after_hash = hashlib.file_digest(prepared, "sha256").hexdigest()
            _publish_receipt(
                path,
                {
                    "format": "context-intelligence-prune",
                    "version": 1,
                    "state": "prepared",
                    "created_at": now.isoformat(),
                    "source": {"sha256": digest, "bytes": initial.st_size, "inode": initial.st_ino},
                    "expected": {"sha256": after_hash, "bytes": result.bytes_after},
                    "cutoff": policy.cutoff.isoformat() if policy.cutoff else None,
                    "include_responses": policy.include_responses,
                    "payloads": result.payloads,
                    "lines": result.lines,
                    "notice": "Raw content may be permanently lost. Every present receipt blocks replay. "
                    "Prepared is not proof that source replacement completed.",
                },
            )
            # Receipt durability is necessary, but does not waive final source checks.
            _assert_unchanged(path, source, initial)
            check_schema()
            if before_commit is not None:
                before_commit()
            os.replace(temporary, path)
            temporary = None
            result.status = "pruned"
            try:
                _sync_directory(path.parent)
            except OSError:
                # Replacement happened. Do not misreport an uncommitted failure.
                result.reason = "replacement completed, but directory fsync failed"
            return result
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
