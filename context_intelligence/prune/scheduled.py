"""Configured, bounded local retention runs. No scheduler or Click dependency.

A schedule is opt-in. The runner never asserts stopped writers, prunes responses,
creates raw backups, clears replay fences, or relaxes recovery/writer exclusion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import tempfile
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .coordination import PruneError, receipt_path
from .inactive import state_root
from .operations import PruneRequest, PruneSummary, iter_prune_tree
from .policy import parse_duration
from .storage import FileResult


ERROR_DETAIL_LIMIT = 32
ERROR_DETAIL_BYTES_LIMIT = 8192
_STATUSES = frozenset(("unchanged", "pruned", "would-prune", "skipped", "error"))


class ScheduleError(PruneError):
    """Sanitized configuration/management refusal."""


class JobBusy(ScheduleError):
    """Another run or management operation owns this installation."""


class RunInterrupted(ScheduleError):
    """Deadline or scoped process signal stopped this run."""


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        raise ScheduleError("XDG_CONFIG_HOME_requires_absolute_path")
    return base / "context-intelligence/local-prune/config.json"


def checked_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ScheduleError("symlink_path_refused")
    return path


def checked_config_path(path: Path, *, unit_paths: tuple[Path, ...] = ()) -> Path:
    """Keep policy, evidence, lock and selected unit files in distinct roles."""
    path = checked_path(path)
    managed = (
        path,
        *(
            path.parent / name
            for name in (
                "installation.json",
                "last-run.json",
                "last-success.json",
                "admission.lock",
                "runs",
            )
        ),
        *unit_paths,
    )
    # Check links before canonical comparison, never normalize links to acceptance.
    checked = tuple(checked_path(item) for item in managed)
    canonical = tuple(item.resolve(strict=False) for item in checked)
    for index, item in enumerate(canonical):
        for other in canonical[:index]:
            if item == other or item in other.parents or other in item.parents:
                raise ScheduleError("managed_configuration_path_overlap")
    return path


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def read_json(path: Path) -> dict[str, Any]:
    path = checked_path(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_size > 65536
    ):
        raise ScheduleError("unsafe_configuration_file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
            raise ScheduleError("configuration_changed")
        raw = stream.read(65537)
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(value, dict) or len(raw) > 65536:
            raise ValueError
    except (ValueError, RecursionError) as exc:
        raise ScheduleError("invalid_configuration_json") from exc
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path = checked_path(path)
    if os.path.lexists(path):
        read_json(path)  # Reject foreign, linked, public, or malformed destinations.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".local-prune-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _run_directory(config_path: Path, run_id: str) -> Path:
    """Reserve a private, never-reused evidence namespace before traversal."""
    runs = checked_path(config_path.parent / "runs")
    runs.mkdir(mode=0o700, exist_ok=True)
    info = runs.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ScheduleError("unsafe_run_receipt_directory")
    directory = runs / run_id
    directory.mkdir(mode=0o700)  # A collision refuses; never reuse or replace evidence.
    _sync_directory(runs)
    _sync_directory(config_path.parent)
    return directory


def _immutable_json(path: Path, value: dict[str, Any]) -> None:
    """Publish a fully synced receipt exclusively, without a partial final file.

    Immutability is our no-overwrite protocol, not protection against the owner
    changing its own files. A failed sync may leave a published receipt. A hard
    kill between link/unlink can leave two links, refused by read_json; retain
    both as unconfirmed evidence, never automatically repair or replay them.
    """
    path = checked_path(path)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    if len(raw.encode("ascii")) > 65536:
        raise ScheduleError("run_receipt_size_limit")
    fd, name = tempfile.mkstemp(prefix=".local-prune-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)  # Exclusive even for dangling links.
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(path.parent)


@contextmanager
def admission(config_path: Path) -> Iterator[None]:
    """One stable lock for both management and execution; never unlink it."""
    try:
        import fcntl
    except ImportError as exc:
        raise ScheduleError("local_scheduling_requires_posix_locks") from exc
    config_path = checked_config_path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = config_path.parent / "admission.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ScheduleError("unsafe_admission_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise JobBusy("job_or_management_busy") from exc
        current = lock.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ScheduleError("admission_lock_changed")
        yield
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ScheduleConfig:
    root: Path = field(default_factory=lambda: Path.home() / ".amplifier/projects")
    state_root: Path = field(default_factory=state_root)
    recovery_roots: tuple[Path, ...] = ()
    at: str = "02:00"
    older_than: str = "60d"
    min_inactive: str = "7d"
    min_idle: str = "1h"
    max_runtime: int = 3600
    dry_run: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ScheduleError("unsupported_configuration_version")
        if type(self.dry_run) is not bool:
            raise ScheduleError("dry_run_requires_boolean")
        if (
            not isinstance(self.at, str)
            or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", self.at) is None
        ):
            raise ScheduleError("schedule_requires_HH_MM_local_time")
        try:
            age, inactive, idle = (
                parse_duration(s) for s in (self.older_than, self.min_inactive, self.min_idle)
            )
        except (TypeError, ValueError) as exc:
            raise ScheduleError("invalid_retention_duration") from exc
        if age <= timedelta(0) or inactive < max(timedelta(days=1), idle):
            raise ScheduleError("inactivity_requires_at_least_one_day_and_min_idle")
        if type(self.max_runtime) is not int or not 1 <= self.max_runtime <= 86400:
            raise ScheduleError("max_runtime_requires_1_to_86400_seconds")
        for path in (self.root, self.state_root, *self.recovery_roots):
            if not isinstance(path, Path) or not path.is_absolute():
                raise ScheduleError("configuration_paths_must_be_absolute")
            checked_path(path)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update(
            root=str(self.root),
            state_root=str(self.state_root),
            recovery_roots=[str(p) for p in self.recovery_roots],
        )
        return value

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, path: Path) -> ScheduleConfig:
        value = read_json(path)
        if set(value) != set(cls.__dataclass_fields__):
            raise ScheduleError("configuration_keys_must_match_version_one")
        if (
            not isinstance(value["recovery_roots"], list)
            or not all(isinstance(p, str) for p in value["recovery_roots"])
            or not isinstance(value["root"], str)
            or not isinstance(value["state_root"], str)
        ):
            raise ScheduleError("invalid_configuration_paths")
        value["root"] = Path(value["root"])
        value["state_root"] = Path(value["state_root"])
        value["recovery_roots"] = tuple(Path(p) for p in value["recovery_roots"])
        return cls(**value)


def _reason(reason: str | None) -> str:
    """Fixed allowlist only: no source bytes, JSON values, paths, or causes."""
    reason = reason or ""
    for fragment, code in (
        ("skipped-ledger:", "recovery_registered"),
        ("live", "writer_lock_held"),
        ("capture-maintenance-busy:", "capture_busy"),
        ("repeat fence", "fence_unreadable"),
        ("legacy/unknown", "legacy_or_unknown"),
        ("recognized root", "unsupported_path"),
        ("inactivity", "recent_activity"),
        ("released", "owner_not_released"),
        ("clean release", "owner_not_released"),
        ("completed root", "not_completed_root"),
        ("marked running", "running"),
        ("identity", "identity_mismatch"),
        ("transfer", "transfer_fenced"),
        ("invalid JSON", "malformed_event"),
        ("recovery", "recovery_unavailable"),
    ):
        if fragment in reason:
            return code
    return "safety_refusal"


def _report_duration(value: str) -> str:
    """Keep normal policy spellings; normalize oversized valid spellings only.

    Policy/config identity is unchanged. Report the exact effective timedelta,
    not a truncated string or a new rounded float, within the reader byte limit.
    """
    if len(value) <= 64:
        return value
    duration = parse_duration(value)
    seconds = duration.days * 86400 + duration.seconds
    fractional = f".{duration.microseconds:06d}".rstrip("0") if duration.microseconds else ""
    return f"{seconds}{fractional}s"


@dataclass
class _Attribution:
    """Bounded projections only; never retain a FileResult or exception message."""

    status_by_reason: dict[str, Counter[str]] = field(default_factory=dict)
    error_details: list[dict[str, Any]] = field(default_factory=list)
    error_count: int = 0
    detail_bytes: int = 2  # Include the JSON array brackets and separators.
    held_known_bytes: int = 0
    held_known_files: int = 0
    held_unknown_files: int = 0

    def add(self, result: FileResult, ordinal: int) -> None:
        status = result.status if result.status in _STATUSES else "unknown"
        code = _reason(result.reason) if result.reason or status in ("skipped", "error") else "none"
        self.status_by_reason.setdefault(status, Counter())[code] += 1
        if status in ("skipped", "error"):
            # No body reads, no link following, no estimate of removable payloads.
            # A traversal-level error can refer to a directory, not a source file.
            try:
                info = checked_path(result.path).lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ScheduleError("held_size_unknown")
            except (OSError, PruneError, ValueError):
                self.held_unknown_files += 1
            else:
                self.held_known_files += 1
                self.held_known_bytes += info.st_size
        if status != "error" and not (status == "pruned" and result.reason):
            return
        self.error_count += 1  # Same predicate as PruneSummary.errors.
        if len(self.error_details) >= ERROR_DETAIL_LIMIT:
            return
        row = {
            "ordinal": ordinal,
            "path_sha256": hashlib.sha256(os.fsencode(result.path)).hexdigest(),
            "status": status,
            "code": code,
            # Current FileResult does not preserve exception type, phase or errno.
            # Do not guess them by extracting values from potentially private text.
            "phase": None,
            "errno": None,
        }
        size = len(json.dumps(row, sort_keys=True, ensure_ascii=True).encode("ascii"))
        size += 2 if self.error_details else 0
        if self.detail_bytes + size <= ERROR_DETAIL_BYTES_LIMIT:
            self.error_details.append(row)
            self.detail_bytes += size

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_by_reason": {
                status: dict(codes) for status, codes in self.status_by_reason.items()
            },
            "error_details": self.error_details,
            "error_details_limit": ERROR_DETAIL_LIMIT,
            "error_details_bytes_limit": ERROR_DETAIL_BYTES_LIMIT,
            "error_details_omitted": self.error_count - len(self.error_details),
            "held_source_bytes": {
                "basis": "lstat_regular_single_link_source_file_size_not_reclaimable_bytes",
                "known_bytes": self.held_known_bytes,
                "known_files": self.held_known_files,
                "unknown_files": self.held_unknown_files,
                "coverage": "observed_skipped_and_error_results_only",
                "reclaimable_bytes": None,
                "sidecar_bytes": None,
            },
        }


def run_scheduled(
    config_path: Path, *, dry_run: bool | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Run once, emitting only a bounded allowlisted result; never auto-retry.

    A prior 'running' result after a hard kill remains an interrupted/unknown
    observation, not a successful or resumable transaction. The next invocation
    is independently authorized and rechecks every capture from current state.
    """
    try:
        config_path = checked_config_path(config_path)
        with admission(config_path):
            config = ScheduleConfig.load(config_path)
            installation = config_path.parent / "installation.json"
            if os.path.lexists(installation):
                installed = read_json(installation)
                if (
                    installed.get("format") != "context-intelligence-local-prune-installation"
                    or installed.get("version") != 1
                    or installed.get("config") != str(config_path)
                    or installed.get("state", "installed") != "installed"
                    or installed.get("config_sha256") != config.sha256
                ):
                    raise ScheduleError("installation_not_ready_or_configuration_changed")
            now = now or datetime.now(timezone.utc)
            if now.utcoffset() is None:
                raise ScheduleError("now_requires_timezone")
            min_inactive = parse_duration(config.min_inactive)
            # Refuse unrepresentable inactivity before publishing a report or scanning.
            _ = now - min_inactive
            preview = config.dry_run if dry_run is None else dry_run
            if type(preview) is not bool:
                raise ScheduleError("dry_run_requires_boolean")
            request = PruneRequest.from_options(
                config.root,
                older_than=config.older_than,
                now=now,
                apply=not preview,
                inactive_only=True,
                writers_stopped=False,
                backup=False,
                repeat_pruned=True,
                min_idle=config.min_idle,
                min_inactive=min_inactive,
                state_root=config.state_root,
                recovery_roots=config.recovery_roots,
            )
            deadline = time.monotonic() + config.max_runtime
            stopped = False

            def check() -> None:
                if stopped or time.monotonic() >= deadline:
                    raise RunInterrupted("deadline_reached")

            def interrupted(signum: int, frame: Any) -> None:
                nonlocal stopped
                stopped = True

            # The dataclass is immutable; construct the checked request once more
            # rather than mutating public state behind the caller's back.
            from dataclasses import replace

            request = replace(request, before_commit=check)
            report: dict[str, Any] = {
                "format": "context-intelligence-local-prune-run",
                "version": 1,
                "run_id": str(uuid.uuid4()),
                "config_sha256": config.sha256,
                "started_at": now.astimezone().isoformat(),
                "state": "running",
                "exit_code": 1,
                "cutoff": request.policy.cutoff.isoformat() if request.policy.cutoff else None,
                "dry_run": preview,
                "effective_policy": {
                    "older_than": _report_duration(config.older_than),
                    "min_inactive": _report_duration(config.min_inactive),
                    "min_idle": _report_duration(config.min_idle),
                    "repeat_pruned": True,
                    "inactive_only": True,
                    "writers_stopped": False,
                    "backup": False,
                    "include_responses": False,
                },
            }
            last = config_path.parent / "last-run.json"
            directory = _run_directory(config_path, report["run_id"])
            report["run_receipt"] = f"runs/{report['run_id']}/result.json"
            _immutable_json(directory / "started.json", report)
            atomic_json(last, report)
            summary = PruneSummary()
            reasons: Counter[str] = Counter()
            attribution = _Attribution()
            fenced = 0
            unconfirmed = 0
            old_handlers: dict[int, Any] = {}
            try:
                for sig in (
                    (signal.SIGTERM, signal.SIGINT)
                    if threading.current_thread() is threading.main_thread()
                    else ()
                ):
                    old_handlers[sig] = signal.signal(sig, interrupted)
                for result in iter_prune_tree(request, now=now):
                    summary.add(result)
                    attribution.add(result, summary.files)
                    # Presence is enough for observability, not repeat authority.
                    has_fence = os.path.lexists(receipt_path(result.path))
                    fenced += int(has_fence)
                    unconfirmed += int(
                        result.prior_attempt_unconfirmed
                        or (has_fence and result.status != "pruned")
                    )
                    if result.status in ("skipped", "error") or result.reason:
                        reasons[_reason(result.reason)] += 1
                    check()
                report.update(
                    state="dry_run" if preview else "completed", exit_code=summary.exit_code
                )
                if summary.exit_code:
                    report["state"] = "partial"
            except RunInterrupted:
                report.update(state="interrupted", exit_code=1, error_code="run_interrupted")
            except (OSError, PruneError, ValueError):
                report.update(state="failed", exit_code=1, error_code="run_failed")
            finally:
                report.update(
                    summary=summary.to_dict(),
                    **attribution.to_dict(),
                    reasons=dict(reasons),
                    fenced_captures=fenced,
                    prior_attempts_unconfirmed=unconfirmed,
                    finished_at=datetime.now().astimezone().isoformat(),
                )
                try:
                    _immutable_json(directory / "result.json", report)
                    atomic_json(last, report)
                    if report["exit_code"] == 0:
                        atomic_json(
                            config_path.parent / "last-success.json",
                            {
                                key: report[key]
                                for key in ("run_id", "finished_at", "state", "config_sha256")
                            },
                        )
                finally:
                    for sig, handler in old_handlers.items():
                        signal.signal(sig, handler)
            return report
    except JobBusy:
        # Do not overwrite the current owner's last result on contention.
        return {"state": "skipped_busy", "exit_code": 0, "error_code": "job_or_management_busy"}
    except (OSError, PruneError, ValueError, OverflowError):
        return {"state": "failed", "exit_code": 1, "error_code": "configuration_or_state_refused"}
