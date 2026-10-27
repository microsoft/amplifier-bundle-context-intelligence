"""Level 3 public API: one safety/orchestration path for hosts and wrappers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .coordination import PruneError, assert_replayable, capture_lock
from .inactive import InactiveInventory
from .policy import Policy, parse_duration, parse_timestamp
from .recovery import recovery_guard
from .storage import FileResult, discover_events, prune_file as _transaction


@dataclass(frozen=True)
class PruneRequest:
    root: Path
    policy: Policy = Policy()
    apply: bool = False
    writers_stopped: bool = False
    inactive_only: bool = False
    backup: bool = False
    min_idle: timedelta = timedelta(hours=1)
    recovery_roots: tuple[Path, ...] = ()
    state_root: Path | None = None
    min_inactive: timedelta | None = None
    repeat_pruned: bool = False
    before_commit: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        for name in ("apply", "writers_stopped", "inactive_only", "backup", "repeat_pruned"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.apply and not (self.writers_stopped or self.inactive_only):
            raise ValueError("apply requires writers_stopped or inactive_only")
        if self.backup and not self.apply:
            raise ValueError("backup requires apply")
        if self.min_idle < timedelta(0):
            raise ValueError("min_idle must not be negative")
        if self.min_inactive is not None and self.min_inactive < timedelta(days=1):
            raise ValueError("min_inactive must be at least one day")
        if self.repeat_pruned and (not self.inactive_only or self.writers_stopped):
            raise ValueError("repeat pruning requires inactive_only without writers_stopped")

    @classmethod
    def from_options(
        cls,
        root: Path,
        *,
        older_than: str | None = None,
        before: str | None = None,
        include_responses: bool = False,
        min_idle: str = "1h",
        now: datetime | None = None,
        **options: Any,
    ) -> PruneRequest:
        if older_than is not None and before is not None:
            raise ValueError("choose older_than or before, not both")
        now = now or datetime.now(timezone.utc)
        cutoff = now - parse_duration(older_than) if older_than is not None else None
        if before is not None:
            cutoff = parse_timestamp(before)
            if cutoff is None:
                raise ValueError("before requires a timezone-aware ISO8601 timestamp")
        return cls(
            root, Policy(cutoff, include_responses), min_idle=parse_duration(min_idle), **options
        )


@dataclass
class PruneSummary:
    files: int = 0
    changed_files: int = 0
    skipped_files: int = 0
    errors: int = 0
    payloads: int = 0
    kept_undated: int = 0
    bytes_saved: int = 0

    def add(self, result: FileResult) -> None:
        self.files += 1
        changed = result.status in ("pruned", "would-prune")
        self.changed_files += int(changed)
        self.skipped_files += int(result.status == "skipped")
        self.errors += int(
            result.status == "error" or (result.status == "pruned" and bool(result.reason))
        )
        self.payloads += result.payloads if changed else 0
        self.kept_undated += result.missing_timestamps
        self.bytes_saved += result.bytes_saved if changed else 0

    @property
    def exit_code(self) -> int:
        return int(bool(self.errors or self.kept_undated))

    def to_dict(self) -> dict[str, Any]:
        return {"type": "summary", **asdict(self)}


def result_dict(result: FileResult) -> dict[str, Any]:
    row = asdict(result)
    row.update(
        type="file",
        path=str(result.path),
        backup=str(result.backup) if result.backup else None,
        bytes_saved=result.bytes_saved if result.status in ("pruned", "would-prune") else 0,
    )
    return row


def prune_file(path: Path, request: PruneRequest, *, now: datetime | None = None) -> FileResult:
    """Apply the same mandatory guards for Python, CLI and future tool callers."""
    # Standalone calls always discover fresh ownership paths. Only our traversal
    # can supply a shared advisory index; it is not public pruning authority.
    return _prune_file(path, request, now=now)


def _prune_file(
    path: Path,
    request: PruneRequest,
    *,
    now: datetime | None = None,
    inventory: InactiveInventory | None = None,
) -> FileResult:
    path = path.expanduser().absolute()
    now = now or datetime.now(timezone.utc)
    # Final guard checks may fail after storage has returned a committed result.
    result: FileResult | None = None
    try:
        # Fence new source scans before inventorying ledgers. recovery_guard also
        # checks job EX: an earlier scan can have dropped capture SH while its
        # registration transaction is still pending under that job lock.
        with (
            capture_lock(path, exclusive=True),
            recovery_guard(path, request.recovery_roots) as check_recovery,
        ):

            def check_commit() -> None:
                if request.before_commit is not None:
                    request.before_commit()
                check_recovery()

            if not request.repeat_pruned:
                assert_replayable(path)
            if request.inactive_only:
                if inventory is None:
                    inventory = InactiveInventory.load(request.state_root)
                result = inventory.prune(
                    path,
                    request.policy,
                    apply=request.apply,
                    backup=request.backup,
                    now=now,
                    min_idle=request.min_idle,
                    min_inactive=request.min_inactive,
                    repeat_pruned=request.repeat_pruned,
                    before_commit=check_commit,
                )
            else:
                result = _transaction(
                    path,
                    request.policy,
                    apply=request.apply,
                    backup=request.backup,
                    now=now,
                    min_idle=request.min_idle,
                    before_commit=check_commit,
                    during_scan=check_commit,
                )
        return result
    except PruneError as exc:
        if result is not None and result.status == "pruned":
            warning = "replacement completed, but postcommit guard cleanup failed"
            result.reason = f"{result.reason}; {warning}" if result.reason else warning
            return result
        reason = str(exc)
        safety_skip = reason.startswith(
            ("skipped-ledger:", "pruned-capture:", "capture-maintenance-busy:")
        )
        return FileResult(path, status="skipped" if safety_skip else "error", reason=reason)
    except OSError as exc:
        if result is not None and result.status == "pruned":
            warning = "replacement completed, but postcommit guard cleanup failed"
            result.reason = f"{result.reason}; {warning}" if result.reason else warning
            return result
        return FileResult(path, status="error", reason=str(exc))


def iter_prune_tree(request: PruneRequest, *, now: datetime | None = None) -> Iterator[FileResult]:
    now = now or datetime.now(timezone.utc)
    inventory: InactiveInventory | None = None
    try:
        for path in discover_events(request.root):
            if request.before_commit is not None:
                request.before_commit()
            if request.inactive_only and inventory is None:
                # Index paths once, lazily: empty trees need no ownership state.
                # Eligibility, execution locks, owner bytes and metadata are
                # still revalidated by inventory.prune for every capture.
                inventory = InactiveInventory.load(request.state_root)
            yield _prune_file(path, request, now=now, inventory=inventory)
    except (PruneError, OSError) as exc:
        yield FileResult(request.root, status="error", reason=str(exc))
