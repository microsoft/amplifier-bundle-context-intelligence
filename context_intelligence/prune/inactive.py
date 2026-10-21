"""Prune only old, released ROOT sessions while holding their execution lock.

This is not a heuristic "nothing has written lately" override. Existing stable
Foundation ownership locks fence cooperating CLI/Unified/TUI root execution.
Legacy sessions, children, foreign hosts, ambiguous owners and live locks are
skipped. No owner or checkpoint records are written, and no lock is created.
Uncoordinated external writers remain outside this protocol.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .policy import Policy, parse_timestamp
from .storage import FileResult, PruneError, _signature, prune_file


class InactiveSkip(PruneError):
    """No mutation: this candidate cannot be fenced as an inactive root."""


def state_root() -> Path:
    if configured := os.environ.get("AMPLIFIER_SESSION_STATE_HOME"):
        return Path(configured).expanduser().absolute()
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return (base / "amplifier/sessions").expanduser().absolute()


def _regular(path: Path, limit: int | None = None) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise InactiveSkip("unsafe or foreign-owned session/coordination file")
    if limit is not None and info.st_size > limit:
        raise InactiveSkip("oversized session metadata; ownership uncertain")
    return info


def _json(path: Path, limit: int = 131072) -> tuple[dict[str, Any], os.stat_result]:
    info = _regular(path, limit)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        if _signature(os.fstat(source.fileno())) != _signature(info):
            raise InactiveSkip("metadata changed while opening")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise InactiveSkip("oversized session metadata")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise InactiveSkip("missing/corrupt session metadata") from exc
    if not isinstance(data, dict):
        raise InactiveSkip("invalid session metadata shape")
    return data, info


def _directories(path: Path) -> None:
    for part in (path, *path.parents):
        if not stat.S_ISDIR(part.lstat().st_mode):
            raise InactiveSkip("symlink or non-directory ancestor")


@dataclass
class InactiveInventory:
    root: Path
    owners: dict[str, list[Path]] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path | None = None) -> InactiveInventory:
        root = (root or state_root()).expanduser().absolute()
        result = cls(root)
        _directories(root)
        for path in root.glob("v1/*/*/owner.json"):
            # Index path identity only. Validate bytes and canonical address
            # again while holding the lock, not during this advisory inventory.
            result.owners.setdefault(path.parent.name, []).append(path)
        return result

    def prune(
        self,
        path: Path,
        policy: Policy,
        *,
        apply: bool = False,
        backup: bool = False,
        now: datetime | None = None,
        min_idle: timedelta = timedelta(days=14),
    ) -> FileResult:
        """Acquire a matching existing execution lock, or return a safety skip."""
        now = now or datetime.now(timezone.utc)
        path = path.expanduser().absolute()
        try:
            return self._prune(
                path,
                policy,
                apply=apply,
                backup=backup,
                now=now,
                min_idle=max(min_idle, timedelta(days=14)),
            )
        except (InactiveSkip, FileNotFoundError) as exc:
            return FileResult(path, status="skipped", reason=str(exc))

    def _prune(
        self,
        path: Path,
        policy: Policy,
        *,
        apply: bool,
        backup: bool,
        now: datetime,
        min_idle: timedelta,
    ) -> FileResult:
        import fcntl

        if path.name != "events.jsonl":
            raise InactiveSkip("not an events.jsonl file")
        session = path.parent.parent if path.parent.name == "context-intelligence" else path.parent
        if session.parent.name != "sessions" or not re.fullmatch(
            r"[A-Za-z0-9-]{1,128}", session.name
        ):
            raise InactiveSkip("not a recognized root-session path")
        owners = self.owners.get(session.name, [])
        if len(owners) != 1:
            raise InactiveSkip("no unique existing execution lock; legacy/unknown session")
        _directories(session)
        _directories(path.parent)
        owner_path = owners[0]
        _directories(owner_path.parent)
        lock_path = owner_path.with_name("session.lock")
        lock_info = _regular(lock_path)
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InactiveSkip("execution lock held; session is live") from exc
            if _signature(os.fstat(fd)) != _signature(lock_info):
                raise InactiveSkip("execution lock changed while opening")
            owner, owner_info = _json(owner_path)
            workspace = owner.get("workspace")
            if not isinstance(workspace, str) or not Path(workspace).is_absolute():
                raise InactiveSkip("invalid workspace ownership")
            canonical = str(Path(workspace).resolve())
            key = hashlib.sha256(workspace.encode()).hexdigest()
            slug = workspace.replace("/", "-").replace("\\", "-").replace(":", "")
            if not slug.startswith("-"):
                slug = "-" + slug
            if (
                canonical != workspace
                or owner_path.parent.parent.name != key
                or session.parent.parent.name != slug
                or owner.get("session_id") != session.name
                or owner.get("hostname") != socket.gethostname()
                or owner.get("coordination_root") != str(self.root.resolve())
            ):
                raise InactiveSkip("coordination identity does not match native session")
            if owner.get("app") not in ("amplifier-cli", "amplifier-unified", "amplifier-tui"):
                raise InactiveSkip("unrecognized execution-lock owner protocol")
            if owner.get("active") is not False:
                raise InactiveSkip("owner has not recorded a clean release")
            inactive_before = now - min_idle
            if policy.cutoff is not None:
                inactive_before = min(inactive_before, policy.cutoff)
            released = parse_timestamp(owner.get("released_at"))
            if released is None or released >= inactive_before:
                raise InactiveSkip("execution ownership not released for at least 14 days")
            ci_path = session / "context-intelligence/metadata.json"
            ci, ci_info = _json(ci_path)
            if (
                ci.get("session_id") != session.name
                or ci.get("status") != "completed"
                or "parent_id" not in ci
                or ci["parent_id"] not in ("", None)
                or ci.get("working_dir") != workspace
            ):
                raise InactiveSkip("not an unambiguous completed root session")
            for key_name in ("ended_at", "last_event_at"):
                value = parse_timestamp(ci.get(key_name))
                if value is None or value >= inactive_before:
                    raise InactiveSkip("session completion/activity is not at least 14 days old")
            stamps = {owner_path: owner_info, ci_path: ci_info}
            # Root-native metadata can contain large frozen configuration.
            native_path = session / "metadata.json"
            native, native_info = _json(native_path, limit=32 * 1024**2)
            if native.get("session_id") != session.name or native.get("parent_id") not in (
                None,
                "",
            ):
                raise InactiveSkip("native metadata conflicts with root identity")
            if native.get("status") in ("running", "in_progress", "working"):
                raise InactiveSkip("native session is marked running")
            stamps[native_path] = native_info
            for candidate in (path, session / "transcript.jsonl", session / "events.jsonl"):
                if candidate.exists():
                    stamps[candidate] = _regular(candidate)
            for info in stamps.values():
                if info.st_mtime >= inactive_before.timestamp():
                    raise InactiveSkip("session files changed within the inactivity window")
            if (session / "transfer-fence.json").exists():
                raise InactiveSkip("session has a transfer fence")

            def check() -> None:
                # The stable execution lock remains held THROUGH replacement.
                if _signature(lock_path.lstat()) != _signature(lock_info):
                    raise InactiveSkip("execution-lock identity changed")
                for candidate, info in stamps.items():
                    if _signature(candidate.lstat()) != _signature(info):
                        raise InactiveSkip("session metadata/history changed during pruning")
                if (session / "transfer-fence.json").exists():
                    raise InactiveSkip("session became transfer-fenced")

            check()
            return prune_file(
                path,
                policy,
                apply=apply,
                backup=backup,
                now=now,
                min_idle=min_idle,
                before_commit=check,
            )
        finally:
            os.close(fd)
