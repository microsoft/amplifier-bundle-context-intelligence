"""Per-session, per-destination delivery watermark — the resume cursor.

Answers exactly one question: **how far through this session's ``events.jsonl``
has destination D been accepted?**

``events.jsonl`` is append-only, line-oriented, and never rewritten, so a byte
offset is the natural cursor: resuming is ``seek(offset)``, O(1), with no need to
parse or even read what came before. That matters at real corpus sizes — a single
observed ``events.jsonl`` on a developer workstation reached 2.47 GB.

State lives in ``<session_dir>/delivery/<destination>.json``, beside the log it
indexes, rather than in a central registry. That choice buys three things:
self-cleanup (the state dies with the session directory, so there is no orphan
registry and no reaping job), natural sharding (concurrent sessions write
different session directories, so the common case has zero contention), and
locality (everything about one session is in one place).

**Failure bias.** Every guard below resolves toward RE-SENDING, never toward
SKIPPING. Re-sending is absorbed by the server's ``MERGE`` on a deterministic
``node_id``; skipping is silent data loss. When in doubt, this module rewinds.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self

logger = logging.getLogger(__name__)

WATERMARK_FORMAT = "context-intelligence-delivery-watermark"
WATERMARK_VERSION = "1.0.0"

#: Destination names are operator-chosen ``settings.yaml`` dict keys, so they can
#: contain anything. Sanitize for use as a filename; the RAW name is kept inside
#: the file so a post-sanitization collision is detectable rather than silent.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

#: A sweep lock older than this is assumed to belong to a dead process. Generous
#: on purpose: breaking a live lock costs only duplicate work, but breaking it
#: too eagerly defeats the point of having one.
DEFAULT_LOCK_STALE_SECONDS = 600.0


def sanitize_destination(name: str) -> str:
    """Map a destination name to a safe filename component."""
    return _UNSAFE_NAME.sub("_", name) or "_"


@dataclass
class DeliveryWatermark:
    """The cursor for one (session, destination) pair."""

    destination: str
    destination_url: str = ""
    offset: int = 0
    delivered_lines: int = 0
    events_file_size_at_write: int = 0
    last_delivered_at: str = ""
    last_attempt_at: str = ""
    last_outcome: str = ""
    last_error: str | None = None
    consecutive_sweeps_without_progress: int = 0
    format: str = WATERMARK_FORMAT
    version: str = WATERMARK_VERSION

    #: Transient, never persisted. Set when a load-time guard rewound `offset`.
    #: Monotonicity on save MUST NOT apply in that case — see `save`.
    reset_by_guard: bool = False

    # -- locations ------------------------------------------------------

    @staticmethod
    def dir_for(session_dir: Path) -> Path:
        return session_dir / "delivery"

    @staticmethod
    def path_for(session_dir: Path, destination: str) -> Path:
        return DeliveryWatermark.dir_for(session_dir) / f"{sanitize_destination(destination)}.json"

    # -- load -----------------------------------------------------------

    @classmethod
    def load(
        cls,
        session_dir: Path,
        destination: str,
        *,
        destination_url: str = "",
    ) -> tuple[DeliveryWatermark, list[str]]:
        """Load the watermark, applying every guard.

        Returns ``(watermark, notes)``. ``notes`` names any guard that fired;
        the caller owns the logging policy so this stays pure and testable.
        A guard firing is never an exception — it is a rewind plus a note.
        """
        notes: list[str] = []
        path = cls.path_for(session_dir, destination)
        events = session_dir / "events.jsonl"
        try:
            size = events.stat().st_size
        except OSError:
            size = 0

        if not path.exists():
            return cls(destination=destination, destination_url=destination_url), notes

        try:
            raw = json.loads(path.read_text())
            if not isinstance(raw, dict):
                raise TypeError("watermark is not a JSON object")
            known = set(cls.__dataclass_fields__)
            wm = cls(**{k: v for k, v in raw.items() if k in known})
        except (OSError, ValueError, TypeError) as exc:
            # GUARD 1 — unreadable/corrupt. Bounded re-delivery beats a hard
            # failure in a best-effort telemetry path.
            notes.append(f"watermark unreadable ({exc}); resetting to 0")
            return cls(destination=destination, destination_url=destination_url), notes

        # GUARD 2 — truncation. If the log is now SMALLER than where we believe
        # we are, it was truncated or replaced and our offset is a lie.
        if wm.offset > size:
            notes.append(
                f"events.jsonl shrank ({size} < offset {wm.offset}); truncated or replaced"
                f" — resetting to 0"
            )
            wm.offset = 0
            wm.delivered_lines = 0
            wm.reset_by_guard = True

        # GUARD 3 — destination identity. Same name, different URL means a
        # different sink. Claiming "already delivered" against a server that
        # never saw these events is the one genuinely lossy mistake available.
        if destination_url and wm.destination_url and wm.destination_url != destination_url:
            notes.append(
                f"destination url changed ({wm.destination_url} -> {destination_url});"
                f" treating as a fresh destination"
            )
            wm.offset = 0
            wm.delivered_lines = 0
            wm.reset_by_guard = True

        wm.destination_url = destination_url or wm.destination_url
        return wm, notes

    # -- save -----------------------------------------------------------

    def save(self, session_dir: Path) -> None:
        """Persist atomically: temp file, fsync, ``os.replace``. Never torn.

        Monotonic by default: re-reads the on-disk value and refuses to move the
        offset backwards, so two racing processes can only ever advance it.

        **EXCEPT after a guard rewind**, which is the subtle part and was a real
        bug caught only by executing this path. A truncated log rewinds `offset`
        to 0 in memory — but the STALE ON-DISK value is still large, so plain
        monotonicity reads it back and silently restores it. The watermark would
        then point past the end of a truncated log forever and every event in it
        would be skipped: precisely the silent data loss this design exists to
        prevent. `reset_by_guard` is the override, and it is cleared once the
        rewind has actually landed on disk.
        """
        path = self.path_for(session_dir, self.destination)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists() and not self.reset_by_guard:
            try:
                on_disk = json.loads(path.read_text())
                if int(on_disk.get("offset", 0)) > self.offset:
                    self.offset = int(on_disk["offset"])
                    self.delivered_lines = max(
                        self.delivered_lines, int(on_disk.get("delivered_lines", 0))
                    )
            except (OSError, ValueError, TypeError):
                logger.debug("unreadable watermark during monotonic check at %s", path)

        try:
            self.events_file_size_at_write = (session_dir / "events.jsonl").stat().st_size
        except OSError:
            self.events_file_size_at_write = 0

        record = {k: v for k, v in asdict(self).items() if k != "reset_by_guard"}
        payload = json.dumps(record, indent=2, sort_keys=True)

        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".wm-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            self.reset_by_guard = False
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- reporting ------------------------------------------------------

    def backlog_bytes(self, session_dir: Path) -> int:
        try:
            size = (session_dir / "events.jsonl").stat().st_size
        except OSError:
            return 0
        return max(0, size - self.offset)

    def caught_up(self, session_dir: Path) -> bool:
        return self.backlog_bytes(session_dir) == 0


class SweepLock:
    """Advisory ``O_EXCL`` lock so two sessions don't sweep the same backlog.

    Deliberately NOT load-bearing for correctness: losing the race costs
    duplicate POSTs, which the server merges. It exists only to avoid wasted
    work, which is why it is never blocked on and a stale lock is simply broken.
    """

    def __init__(
        self,
        session_dir: Path,
        destination: str,
        stale_after: float = DEFAULT_LOCK_STALE_SECONDS,
    ) -> None:
        self.path = DeliveryWatermark.dir_for(session_dir) / (
            f"{sanitize_destination(destination)}.lock"
        )
        self.stale_after = stale_after
        self.held = False

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # At most two attempts: acquire, or break one stale lock and retry. A
        # loop rather than recursion so a pathological lock-churn race cannot
        # build a stack.
        for attempt in (1, 2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as fh:
                    json.dump({"pid": os.getpid(), "created_at": time.time()}, fh)
                self.held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if attempt == 1 and age > self.stale_after:
                    logger.debug("breaking stale sweep lock at %s (age %.0fs)", self.path, age)
                    self.path.unlink(missing_ok=True)
                    continue
                self.held = False
                return self
            except OSError:
                self.held = False
                return self
        return self

    def __exit__(self, *exc: object) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False
