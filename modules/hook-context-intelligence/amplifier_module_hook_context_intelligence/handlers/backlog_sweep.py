"""Bounded backlog sweep — the self-healing catch-up pass.

Reads each candidate session's ``events.jsonl`` from its delivery watermark
forward, rebuilds the wire payload, POSTs it, and advances the watermark over the
**contiguous prefix only**. Converts what was permanent data loss (events the live
dispatcher dropped on queue overflow, or left queued at shutdown) into delivery
latency: they arrive on a later session instead of never.

Three properties are load-bearing and each was proven against a real server
before this shipped:

* **Duplicate-free.** Safe because the server writes with ``MERGE`` on a
  deterministic ``node_id``, NOT because of the ``idempotency_key`` header —
  that is an in-memory 7-day LRU which ``?replay=true`` bypasses entirely and a
  restart clears. Re-sending is therefore safe but *not free*, which is why the
  sweep is bounded rather than exhaustive.
* **Order-independent.** The server MERGEs both endpoints of every edge and
  converges placeholder nodes, so concurrent in-flight POSTs cannot corrupt the
  graph. That is what licenses ``concurrency > 1``.
* **Never blocks exit.** This is a cancellable task; whatever the watermark says
  at cancellation is the state, and the next session resumes from it.

The sweep lives in the hook and imports nothing from the upload tool: the
dependency arrow is tool -> hook, so the reverse would be a circular import. It
does not need to — ``build_payload`` already lives here.

**Interaction with the live path, when running continuously.** The two never
fight over the same records, and nothing has to coordinate them. While the live
dispatcher keeps up it advances that session's watermark itself, so the sweep
sees the session as caught up and skips it entirely. The moment the live path
drops or fails a record it FREEZES the watermark there — it can no longer
honestly advance — and the sweep resumes from exactly that point. So the sweep
is idle on healthy sessions and is the only thing that can recover an unhealthy
one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from ..fanout import destination_is_active, normalize_match_key
from ..upload import build_payload
from .delivery_watermark import DeliveryWatermark, SweepLock

if TYPE_CHECKING:  # pragma: no cover - typing only
    from context_intelligence.auth import AuthStrategy

    from ..config_resolver import Destination

logger = logging.getLogger(__name__)

#: Defaults. Conservative on purpose: a launch must never re-POST a week of
#: history, and the server is single-process with no rate limiting of its own,
#: which makes THIS the rate limiter.
DEFAULT_SWEEP_MAX_EVENTS = 5000
DEFAULT_SWEEP_MAX_AGE_HOURS = 48.0
DEFAULT_SWEEP_MAX_SESSIONS = 20
DEFAULT_SWEEP_CONCURRENCY = 1

#: Below this residual backlog, a sweep that made progress stays quiet.
DEFAULT_QUIET_BACKLOG_THRESHOLD = 500


@dataclass(frozen=True)
class SweepBounds:
    max_events: int = DEFAULT_SWEEP_MAX_EVENTS
    max_age_hours: float = DEFAULT_SWEEP_MAX_AGE_HOURS
    max_sessions: int = DEFAULT_SWEEP_MAX_SESSIONS
    concurrency: int = DEFAULT_SWEEP_CONCURRENCY
    quiet_backlog_threshold: int = DEFAULT_QUIET_BACKLOG_THRESHOLD


@dataclass
class SweepReport:
    """Everything the loudness rules need to decide console behaviour.

    Deliberately reports the TREND (`events_delivered`, `made_progress`) as well
    as the LEVEL (`backlog_events_remaining`). Gating console output on the level
    alone is the current bug: it is why a healthy destination warned on 215 of
    215 shutdowns.
    """

    destination: str = ""
    sessions_considered: int = 0
    sessions_swept: int = 0
    sessions_skipped_age: int = 0
    sessions_skipped_caught_up: int = 0
    sessions_skipped_locked: int = 0
    #: In-window sessions this destination is NOT allowed to receive. Routine and
    #: expected -- one project directory can hold sessions from several working
    #: directories, and each one's own include/exclude decides where it may go.
    sessions_skipped_filtered: int = 0
    #: In-window sessions whose working_dir could not be established, so their
    #: routing could not be PROVEN. Never swept. Surfaced, never silent.
    sessions_blocked_unprovable: int = 0
    events_delivered: int = 0
    events_failed: int = 0
    bytes_advanced: int = 0
    backlog_bytes_remaining: int = 0
    stranded_sessions: int = 0
    oldest_stranded_age_hours: float = 0.0
    last_error: str | None = None
    duration_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def made_progress(self) -> bool:
        return self.events_delivered > 0

    @property
    def had_work(self) -> bool:
        return self.sessions_swept > 0 or self.backlog_bytes_remaining > 0

    def summary(self) -> str:
        return (
            f"{self.destination}: swept {self.sessions_swept}/{self.sessions_considered}"
            f" session(s), delivered {self.events_delivered} event(s),"
            f" {self.events_failed} failed, {self.duration_seconds:.1f}s"
        )


def read_session_metadata(session_dir: Path) -> dict[str, Any] | None:
    """Read ``metadata.json`` — the sweep's cheap index.

    This is why the age bound is keyed on ``metadata.last_event_at`` and not on
    the log: triage costs ~450 bytes per session instead of opening a file that
    can run to gigabytes, so start-of-session cost scales with the number of
    sessions rather than with the size of history.
    """
    try:
        return json.loads((session_dir / "metadata.json").read_text())
    except (OSError, ValueError):
        return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def select_candidates(
    project_dir: Path,
    bounds: SweepBounds,
    *,
    now: datetime | None = None,
) -> tuple[list[tuple[Path, dict[str, Any]]], int, float]:
    """Return ``(in-window sessions oldest-first, stranded count, oldest age h)``.

    Project-scoped by construction — the caller passes one project directory, so
    the sweep can never wander into another project's sessions. Oldest-first so a
    backlog drains in the order it accumulated.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=bounds.max_age_hours)
    sessions_root = project_dir / "sessions"
    if not sessions_root.is_dir():
        return [], 0, 0.0

    in_window: list[tuple[datetime, Path, dict[str, Any]]] = []
    stranded = 0
    oldest_age = 0.0

    try:
        entries = list(sessions_root.iterdir())
    except OSError:
        return [], 0, 0.0

    for entry in entries:
        session_dir = entry / "context-intelligence"
        if not (session_dir / "events.jsonl").exists():
            continue
        metadata = read_session_metadata(session_dir) or {}
        last = _parse_timestamp(metadata.get("last_event_at")) or _parse_timestamp(
            metadata.get("started_at")
        )
        if last is None:
            continue
        if last < cutoff:
            stranded += 1
            oldest_age = max(oldest_age, (now - last).total_seconds() / 3600.0)
            continue
        in_window.append((last, session_dir, metadata))

    in_window.sort(key=lambda item: item[0])
    selected = [(sd, md) for _, sd, md in in_window[: bounds.max_sessions]]
    return selected, stranded, oldest_age


def iter_records_from(events_path: Path, offset: int):
    """Yield ``(start, end, text)`` for complete lines from *offset* forward.

    Binary mode so offsets are true byte positions. A trailing PARTIAL line is
    never yielded: a live session may be mid-write, and advancing the watermark
    past a half-written record would skip that event permanently.
    """
    with events_path.open("rb") as fh:
        fh.seek(offset)
        position = offset
        for raw in fh:
            if not raw.endswith(b"\n"):
                return
            start, position = position, position + len(raw)
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                yield start, position, text


class BacklogSweeper:
    """Sweeps one destination's backlog for one project."""

    def __init__(
        self,
        *,
        destination: str,
        url: str,
        auth: AuthStrategy,
        project_dir: Path,
        spec: Destination,
        bounds: SweepBounds | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._destination = destination
        #: The destination's OWN include/exclude. Required, not optional: without
        #: it this class cannot answer "may this session's events go here?", and
        #: a sweeper that cannot answer that must not run.
        self._spec = spec
        self._url = url.rstrip("/")
        self._endpoint = f"{self._url}/events"
        self._auth = auth
        self._project_dir = project_dir
        self._bounds = bounds or SweepBounds()
        self._timeout = timeout

    # -- one event ------------------------------------------------------

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> tuple[bool, str]:
        """POST one event. Returns ``(delivered, detail)``. Never raises.

        Deliberately does NOT set ``?replay=true``. The manual upload CLI does,
        because it re-imports cold archives that the server's dedup cache has
        long forgotten. This sweep chases a live tail, so leaving the cache
        enabled lets a recently-delivered duplicate short-circuit cheaply
        instead of doing a full append + drain + MERGE.
        """
        try:
            headers: dict[str, str] = dict(self._auth.headers())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a token fault must not kill the sweep
            # Mirrors the dispatcher: a local token-production failure is a
            # deterministic HARD auth fault, reported, never retried blindly.
            return False, f"auth failure: {type(exc).__name__}: {exc}"
        headers.setdefault("Content-Type", "application/json")
        try:
            response = await client.post(
                self._endpoint, json=payload, headers=headers, timeout=self._timeout
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - network faults are expected here
            return False, f"{type(exc).__name__}: {exc}"
        if response.status_code in (200, 201, 202):
            return True, ""
        return False, f"HTTP {response.status_code}: {response.text[:200]}"

    # -- routing: may this session's events go to this destination? -------

    def _routing_verdict(self, session_dir: Path, metadata: dict[str, Any]) -> tuple[bool, str]:
        """Re-evaluate THIS session's include/exclude for THIS destination.

        Load-bearing, and the reason it exists is worth stating plainly: the
        sweep walks a PROJECT directory, but a project directory can hold
        sessions from several different working directories -- ``project_slug``
        falls back to ``"default"`` when the capability is unavailable, so
        unrelated trees collide there routinely. Without this check the sweep
        would forward every session it finds to whatever destinations the
        CURRENT session happens to match, which silently reroutes one session's
        events using another session's permissions. That is a leak, not an
        inefficiency: an event delivered somewhere it was excluded from cannot be
        recalled.

        So routing INVERTS this module's usual failure bias. Every delivery guard
        elsewhere fails toward re-sending, because a duplicate is absorbed by the
        server's MERGE while a skip loses data. Here the opposite holds: a
        wrongly-sent event is unrecoverable, a wrongly-skipped one is picked up
        by the next sweep. **When routing cannot be proven, it is refused.**

        The matcher is the live path's own (``fanout``), never a second
        implementation of the rules.
        """
        working_dir = metadata.get("working_dir")
        if not isinstance(working_dir, str) or not working_dir.strip():
            return False, "working_dir missing from metadata.json -- routing unprovable"
        try:
            match_key = normalize_match_key(working_dir)
        except ValueError as exc:
            return False, f"working_dir unusable ({exc}) -- routing unprovable"
        if not destination_is_active(self._spec, match_key):
            return False, f"excluded by this destination's include/exclude for {working_dir}"
        return True, ""

    # -- one session ----------------------------------------------------

    async def _sweep_session(
        self,
        session_dir: Path,
        metadata: dict[str, Any],
        client: httpx.AsyncClient,
        budget: int,
        report: SweepReport,
    ) -> int:
        watermark, notes = DeliveryWatermark.load(
            session_dir, self._destination, destination_url=self._url
        )
        for note in notes:
            logger.warning("%s watermark: %s (%s)", self._destination, note, session_dir)
            report.notes.append(note)

        events_path = session_dir / "events.jsonl"
        if watermark.caught_up(session_dir):
            report.sessions_skipped_caught_up += 1
            return 0

        working_dir = str(metadata.get("working_dir") or "")
        fallback_workspace = str(metadata.get("workspace") or "")

        window: list[tuple[int, int, dict[str, Any]]] = []
        try:
            for start, end, text in iter_records_from(events_path, watermark.offset):
                if len(window) >= budget:
                    break
                try:
                    record = json.loads(text)
                    if not isinstance(record, dict):
                        raise TypeError("record is not an object")
                    data = record.get("data", {})
                    if not isinstance(data, dict):
                        raise TypeError("record 'data' is not an object")
                except (ValueError, TypeError) as exc:
                    # Do NOT advance past it: a partially-written line completes
                    # on the next flush, and skipping it would lose the event.
                    report.notes.append(f"malformed record at byte {start}: {exc}")
                    break
                window.append(
                    (
                        start,
                        end,
                        build_payload(
                            str(record.get("event", "")),
                            str(record.get("workspace") or fallback_workspace),
                            data,
                            working_dir=working_dir,
                        ),
                    )
                )
        except OSError as exc:
            report.notes.append(f"unreadable events.jsonl at {session_dir}: {exc}")
            return 0

        if not window:
            return 0

        # Deliver in CHUNKS, committing the watermark after each one.
        #
        # A single gather over the whole window looks tidier and is wrong: this
        # task is cancelled at session teardown, and a cancellation mid-gather
        # threw away EVERY delivery the pass had made, because the watermark was
        # only written at the end. Measured in a DTU: a sweep that genuinely
        # re-sent events left its cursor at 0 and the work had to be redone.
        # Chunking bounds that loss to one chunk.
        chunk = max(1, self._bounds.concurrency)
        prefix_end = watermark.offset
        delivered_count = 0
        failed = 0
        first_error: str | None = None
        stop = False

        def _persist(outcome: str) -> None:
            advanced_now = prefix_end - watermark.offset
            watermark.offset = prefix_end
            watermark.delivered_lines += delivered_count
            watermark.last_attempt_at = datetime.now(UTC).isoformat()
            watermark.last_outcome = outcome
            watermark.last_error = first_error
            if delivered_count:
                watermark.last_delivered_at = watermark.last_attempt_at
                watermark.consecutive_sweeps_without_progress = 0
            elif outcome == "no_progress":
                watermark.consecutive_sweeps_without_progress += 1
            try:
                watermark.save(session_dir)
            except OSError as exc:
                report.notes.append(f"watermark write failed at {session_dir}: {exc}")
            return advanced_now

        try:
            for start_index in range(0, len(window), chunk):
                batch = window[start_index : start_index + chunk]
                results = await asyncio.gather(
                    *(self._post(client, payload) for _s, _e, payload in batch)
                )
                for (_start, end, _payload), (delivered, detail) in zip(batch, results):
                    if not delivered:
                        failed += 1
                        first_error = first_error or detail
                        stop = True
                        break
                    # CONTIGUOUS PREFIX ONLY: the cursor may only pass a record
                    # once every record before it has been accepted.
                    prefix_end = end
                    delivered_count += 1
                if stop:
                    break
            advanced = _persist("delivered" if delivered_count else "no_progress")
        except asyncio.CancelledError:
            # Teardown. Keep what we actually achieved rather than redoing it.
            _persist("cancelled" if delivered_count else "no_progress")
            raise

        report.events_delivered += delivered_count
        report.events_failed += failed
        report.bytes_advanced += advanced
        report.backlog_bytes_remaining += watermark.backlog_bytes(session_dir)
        if first_error and not report.last_error:
            report.last_error = first_error
        return delivered_count

    # -- the pass -------------------------------------------------------

    async def run(self) -> SweepReport:
        started = time.monotonic()
        report = SweepReport(destination=self._destination)

        sessions, stranded, oldest_age = select_candidates(self._project_dir, self._bounds)
        report.sessions_considered = len(sessions)
        report.stranded_sessions = stranded
        report.sessions_skipped_age = stranded
        report.oldest_stranded_age_hours = oldest_age

        budget = self._bounds.max_events
        try:
            async with httpx.AsyncClient() as client:
                for session_dir, metadata in sessions:
                    if budget <= 0:
                        break
                    # ROUTING GATE -- before the watermark is read, before a lock
                    # is taken, before a single byte is sent. A session this
                    # destination may not receive is not "skipped later", it is
                    # never touched.
                    allowed, why = self._routing_verdict(session_dir, metadata)
                    if not allowed:
                        if "unprovable" in why:
                            report.sessions_blocked_unprovable += 1
                            report.notes.append(f"BLOCKED {session_dir}: {why}")
                            logger.warning(
                                "%s: refusing to sweep %s -- %s",
                                self._destination,
                                session_dir,
                                why,
                            )
                        else:
                            report.sessions_skipped_filtered += 1
                            logger.debug(
                                "%s: not routed this session -- %s", self._destination, why
                            )
                        continue
                    watermark, _ = DeliveryWatermark.load(
                        session_dir, self._destination, destination_url=self._url
                    )
                    if watermark.caught_up(session_dir):
                        report.sessions_skipped_caught_up += 1
                        continue
                    with SweepLock(session_dir, self._destination) as lock:
                        if not lock.held:
                            report.sessions_skipped_locked += 1
                            continue
                        delivered = await self._sweep_session(
                            session_dir, metadata, client, budget, report
                        )
                    report.sessions_swept += 1
                    budget -= delivered
        except asyncio.CancelledError:
            # Exit must never be delayed. Whatever landed on disk is the state.
            report.duration_seconds = time.monotonic() - started
            logger.debug("%s backlog sweep cancelled: %s", self._destination, report.summary())
            raise
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a session
            report.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("%s backlog sweep failed", self._destination, exc_info=True)

        report.duration_seconds = time.monotonic() - started
        return report
