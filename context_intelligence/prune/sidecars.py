"""Exact provider_payloads-v1 owner adapter, never a generic blob collector.

The opt-in owner contract permits references only from the exact events.jsonl
beside provider-payloads/. Recovery registration and writer exclusion are owned
by operations.py, not this adapter. Unknown descriptors pin the entire owner.
Unreferenced directory entries are deliberately never enumerated or reclaimed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .coordination import PruneError
from .policy import Policy, diagnostic_fields, parse_timestamp

_DIGEST = re.compile(r"[0-9a-f]{64}")
_REQUIRED = {"sha256", "bytes", "redacted", "truncated"}
_OPTIONAL = {"limit_chars"}


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


class Sidecars:
    """Disk-indexed all-reference closure for one declared event owner."""

    def __init__(
        self, source: Path, policy: Policy, *, number_type: type = int, now: datetime | None = None
    ):
        if source.name != "events.jsonl":
            raise PruneError("diagnostic owner requires exact events.jsonl source")
        self.source = source
        self.policy = policy
        self.number_type = number_type
        self.expired_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        self.directory = source.parent / "provider-payloads"
        self.temporary = tempfile.TemporaryDirectory(prefix="ci-sidecar-index-")
        self.index = Path(self.temporary.name) / "manifest.sqlite3"
        self.db = sqlite3.connect(self.index)
        self.db.execute("PRAGMA cache_size=-1024")
        self.db.execute(
            "CREATE TABLE asset (digest TEXT PRIMARY KEY, size INTEGER NOT NULL,"
            " pinned INTEGER NOT NULL, signature TEXT)"
        )
        self.db.execute(
            "CREATE TABLE record_map (ordinal INTEGER PRIMARY KEY,old_offset INTEGER,"
            "old_bytes INTEGER,old_sha256 TEXT,new_offset INTEGER,new_bytes INTEGER,new_sha256 TEXT)"
        )
        self.db.execute("CREATE TABLE format (name TEXT,version INTEGER)")
        self.db.execute("INSERT INTO format VALUES('ci-diagnostic-retention',1)")
        self.blocked = False
        self.directory_identity: tuple[int, int] | None = None
        self.manifest: Path | None = None
        self.planned_bytes = 0
        self.planned_files = 0
        # This adapter does not claim coverage over another canonical/recovery
        # consumer next to the events. Such owners require a separate inventory.
        self.protected = (
            "transcript.jsonl",
            "transcript.jsonl.backup",
            "recovery",
            "events.jsonl.bak",
            "events.jsonl.backup",
        )
        self.blocked = self.blocked or self.has_protected_consumers()

    def has_protected_consumers(self) -> bool:
        return any(os.path.lexists(self.source.parent / name) for name in self.protected)

    def check_consumers(self) -> None:
        if self.planned_files and self.has_protected_consumers():
            raise PruneError("diagnostic reference coverage changed")

    def close(self) -> None:
        self.db.close()
        self.temporary.cleanup()

    def _integer(self, value: Any) -> int | None:
        if type(value) is int:
            return value
        if type(value) is self.number_type and self.number_type is not int:
            try:
                return int(value)
            except (ValueError, OverflowError):
                return None
        return None

    def descriptor(self, value: Any) -> tuple[str, int] | None:
        if not isinstance(value, dict) or not _REQUIRED <= value.keys() <= _REQUIRED | _OPTIONAL:
            return None
        digest = value.get("sha256")
        size = self._integer(value.get("bytes"))
        if (
            not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
            or size is None
            or not 0 < size <= 64 * 1024**2
            or value.get("redacted") is not True
            or type(value.get("truncated")) is not bool
        ):
            return None
        if "limit_chars" in value:
            limit = self._integer(value["limit_chars"])
            if limit is None or limit < 1:
                return None
        return digest, size

    def observe(self, record: dict[str, Any]) -> None:
        data = record.get("data")
        if not isinstance(data, dict):
            return
        # Never overwrite unknown owner metadata or infer expiry from absence.
        expired = data.get("provider_payloads_expired", {})
        if not isinstance(expired, dict):
            self.blocked = True
        else:
            for key, marker in expired.items():
                if not self._tombstone_valid(key, marker):
                    self.blocked = True
        for key in ("raw", "raw_request", "raw_response"):
            if isinstance(data.get(key), dict) and "$blob_ref" in data[key]:
                self.blocked = True
        refs = data.get("provider_payloads")
        if refs is None:
            return
        if not isinstance(refs, dict):
            self.blocked = True
            return
        fields, _ = diagnostic_fields(record, self.policy)
        for key, value in refs.items():
            if isinstance(expired, dict) and key in expired:
                self.blocked = True
            descriptor = self.descriptor(value)
            if key not in ("raw", "raw_request", "raw_response") or descriptor is None:
                self.blocked = True
                continue
            digest, size = descriptor
            self.db.execute(
                "INSERT INTO asset VALUES(?,?,?,NULL) ON CONFLICT(digest) DO UPDATE SET"
                " pinned=max(pinned,excluded.pinned,asset.size != excluded.size)",
                (digest, size, int(key not in fields)),
            )

    def record(
        self, ordinal: int, old_offset: int, old: bytes, new_offset: int, new: bytes
    ) -> None:
        """Exact 1:1 physical-line identity map, including blanks and line endings."""
        self.db.execute(
            "INSERT INTO record_map VALUES(?,?,?,?,?,?,?)",
            (
                ordinal,
                old_offset,
                len(old),
                hashlib.sha256(old).hexdigest(),
                new_offset,
                len(new),
                hashlib.sha256(new).hexdigest(),
            ),
        )

    def _asset(self, digest: str, size: int) -> tuple[int, ...]:
        # Every ancestor is non-following; a dangling directory link is unsafe too.
        for directory in (self.directory, *self.directory.parents):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise PruneError("unsafe diagnostic sidecar directory")
        directory = self.directory.lstat()
        identity = (directory.st_dev, directory.st_ino)
        if self.directory_identity is None:
            self.directory_identity = identity
        if identity != self.directory_identity:
            raise PruneError("diagnostic sidecar directory changed")
        path = self.directory / f"{digest}.json"
        initial = path.lstat()
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or initial.st_uid != os.getuid()
            or initial.st_size != size
        ):
            raise PruneError("unsafe diagnostic sidecar identity")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if _signature(os.fstat(handle.fileno())) != _signature(initial):
                raise PruneError("diagnostic sidecar changed while opening")
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if (
                actual != digest
                or _signature(os.fstat(handle.fileno())) != _signature(initial)
                or _signature(path.lstat()) != _signature(initial)
            ):
                raise PruneError("diagnostic sidecar content changed")
        return _signature(initial)

    def qualify(self, poll: Callable[[], None] | None = None) -> None:
        if self.blocked or self.has_protected_consumers():
            self.db.execute("UPDATE asset SET pinned=1")
        for digest, size in self.db.execute("SELECT digest,size FROM asset WHERE pinned=0"):
            if poll is not None:
                poll()
            signature = self._asset(digest, size)
            self.db.execute(
                "UPDATE asset SET signature=? WHERE digest=?", (json.dumps(signature), digest)
            )
            self.planned_files += 1
            self.planned_bytes += size
        self.db.commit()

    def expires(self, value: Any) -> bool:
        descriptor = self.descriptor(value)
        if descriptor is None:
            return False
        row = self.db.execute(
            "SELECT pinned,signature FROM asset WHERE digest=?", (descriptor[0],)
        ).fetchone()
        return row is not None and row[0] == 0 and row[1] is not None

    def _tombstone_valid(self, key: str, marker: Any) -> bool:
        if (
            key not in ("raw", "raw_request", "raw_response")
            or not isinstance(marker, dict)
            or set(marker) != {"v", "sha256", "bytes", "expired_at", "reason"}
            or self._integer(marker.get("v")) != 1
            or marker.get("reason") != "retention"
            or not isinstance(marker.get("sha256"), str)
            or _DIGEST.fullmatch(marker["sha256"]) is None
            or parse_timestamp(marker.get("expired_at")) is None
        ):
            return False
        size = self._integer(marker.get("bytes"))
        return size is not None and 0 < size <= 64 * 1024**2

    def tombstone(self, value: Any) -> dict[str, Any]:
        descriptor = self.descriptor(value)
        if descriptor is None or not self.expires(value):
            raise PruneError("diagnostic reference is not qualified for expiry")
        return {
            "v": 1,
            "sha256": descriptor[0],
            "bytes": descriptor[1],
            "expired_at": self.expired_at,
            "reason": "retention",
        }

    def publish_manifest(self, destination: Path) -> str:
        """Immutable manifest first; no pixel, event, or exception text in it."""
        self.db.commit()
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output, self.index.open("rb") as source:
            while block := source.read(1024 * 1024):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        self.manifest = destination
        with destination.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()

    def unlink(self, *, check: Callable[[], None], outcome: Callable[[int], None]) -> None:
        """Metadata is already durable. A failure leaks bytes, never retries."""
        if self.manifest is None:
            raise PruneError("diagnostic manifest is not durable")
        log = self.manifest.with_suffix(".outcomes.jsonl")
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as journal:
            # The outcome filename itself must survive a crash before unlink.
            directory_fd = os.open(log.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(journal.fileno())
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            for digest, size, saved in self.db.execute(
                "SELECT digest,size,signature FROM asset WHERE pinned=0 AND signature IS NOT NULL"
            ):
                check()
                self.check_consumers()
                if self._asset(digest, size) != tuple(json.loads(saved)):
                    raise PruneError("diagnostic sidecar changed before unlink")
                journal.write(json.dumps({"digest": digest, "state": "unlink-intended"}) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
                os.unlink(self.directory / f"{digest}.json")
                outcome(size)  # actual effect, even if a later fsync/check fails
                directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                journal.write(json.dumps({"digest": digest, "state": "unlinked-durable"}) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
