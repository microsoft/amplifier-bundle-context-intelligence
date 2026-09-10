"""Core HTTP replay loop for context-intelligence upload.

Provides UploadResult, _count_lines, and run_upload for replaying
session events.jsonl files to the Context Intelligence ingestion endpoint.

CLI context: this module runs as a CLI tool.  Per-event/per-attempt problems
(malformed records, unreadable session files, transient-error retries) are
NOT printed here -- at up to ~95k events per run, one ``print`` per skip or
retry sprays into the middle of the live progress block the CLI renders
(``progress.py::TwoLevelProgressRenderer``) and corrupts it.  Instead they
are accumulated into counters on :class:`UploadResult` and surfaced ONCE, in
the completion/failure block the CLI prints after the run ends.
"""

from __future__ import annotations

import heapq
import json
import random
import socket
import ssl
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
from amplifier_module_hook_context_intelligence.upload import build_payload

from .logging_hook_format import SkipLine

if TYPE_CHECKING:
    from context_intelligence.auth import AuthStrategy

    from amplifier_module_tool_context_intelligence_upload.progress import ProgressTracker

    from .formats import ParseFn


class UploadResult:
    """Result of a run_upload call."""

    def __init__(
        self,
        success: bool,
        sessions_uploaded: int,
        events_uploaded: int,
        events_skipped: int = 0,
        events_unmapped: int = 0,
        events_malformed: int = 0,
        events_unreadable: int = 0,
        retries: int = 0,
        error: str | None = None,
        failed_at: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.sessions_uploaded = sessions_uploaded
        self.events_uploaded = events_uploaded
        # events_skipped is the combined total (events_malformed + events_unreadable);
        # kept as its own field for backward compatibility with callers that only
        # care about the aggregate. The completion block renders the breakdown.
        self.events_skipped = events_skipped
        # Not included in to_dict() (GATE 2) -- inspect this attribute directly.
        self.events_unmapped = events_unmapped
        self.events_malformed = events_malformed
        self.events_unreadable = events_unreadable
        self.retries = retries
        self.error = error
        self.failed_at = failed_at

    def to_dict(self) -> dict[str, Any]:
        """Return a dict representation of this result.

        Returns::

            {
                "status": "completed" | "failed",
                "sessions_uploaded": int,
                "events_uploaded": int,
                "error": str,   # only present when error is not None
            }

        Note: ``failed_at`` is not included in the serialized output —
        inspect ``result.failed_at`` directly.
        """
        d: dict[str, Any] = {
            "status": "completed" if self.success else "failed",
            "sessions_uploaded": self.sessions_uploaded,
            "events_uploaded": self.events_uploaded,
        }
        if self.error is not None:
            d["error"] = self.error
        return d


def _workspace_from_path(session_dir: Path) -> str:
    """Derive workspace from the project slug in the session directory path.

    Used as a fallback when an ``events.jsonl`` record does not carry a
    ``workspace`` field.  Sessions captured before workspace was added to the
    on-disk format lack the field entirely; the project slug is the value
    ``ConfigResolver`` would have resolved at live-capture time.

    Path structure:
        .../.amplifier/projects/{project_slug}/sessions/{id}/context-intelligence/

    Walks the path parts looking for the ``projects`` segment and returns the
    immediately following part as the workspace.  Returns an empty string when
    the structure cannot be determined.
    """
    parts = session_dir.parts
    for i, part in enumerate(parts):
        if part == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def _count_lines(file_path: Path) -> int:
    """Count the number of non-blank lines in *file_path*."""
    count = 0
    with file_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            if raw_line.strip():
                count += 1
    return count


# ---------------------------------------------------------------------------
# Retry / backoff (issue #338 — bounded exponential backoff on transient errors)
# ---------------------------------------------------------------------------
#: Backoff schedule mirrors the live-forwarding hook's proven values
#: (hook-context-intelligence logging_handler: initial 1.0s, cap 30.0s, jitter).
#: Kept as module constants rather than run_upload parameters — there is no
#: second caller that needs to tune them, so widening the signature would be
#: speculative generality.
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
_BACKOFF_JITTER = True
#: Default number of *additional* retries after the first attempt.  Total
#: attempts per event = max_retries + 1 (default 5 -> 6 attempts).
_DEFAULT_MAX_RETRIES = 5


def _is_fatal_transport_error(exc: BaseException) -> bool:
    """True if a transport-level error is PERMANENT (never succeeds on retry).

    ``httpx.ConnectError`` covers both genuinely transient faults (connection
    reset, refused) and permanent ones — DNS resolution failure (``socket.gaierror``,
    e.g. a mistyped ``--server-url``) and TLS/certificate failure (``ssl.SSLError``,
    e.g. an untrusted cert).  Retrying a permanent transport fault just burns the
    whole backoff budget on a guaranteed-dead destination, so we classify those as
    fail-fast.  The underlying OS error is wrapped by httpx, so walk the
    cause/context chain to find it.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (socket.gaierror, ssl.SSLError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _is_transient_status(status_code: int) -> bool:
    """Return True if *status_code* is a transient failure worth retrying.

    Transient = HTTP 429 (throttled) or any 5xx (server-side).  Everything else
    non-2xx is permanent.

    NOTE — deliberately ADAPTED from the live-forwarding hook's classifier
    (``_classify_http_outcome`` treats 401 as transient): for the batch
    uploader, per-request token refresh (see :func:`run_upload`) already handles
    token expiry, so a 401 that survives a fresh token is a *real* auth fault
    that retrying cannot fix.  401 is therefore PERMANENT here — fail fast, loud.
    """
    return status_code == 429 or status_code >= 500


def _backoff_delay(retry_index: int) -> float:
    """Exponential backoff for *retry_index* (0 = first retry), capped + jittered."""
    delay = min(_BACKOFF_INITIAL_S * (2**retry_index), _BACKOFF_MAX_S)
    if _BACKOFF_JITTER:
        # Full-ish jitter in [50%, 100%] of the computed delay — spreads retries
        # without ever collapsing the backoff to ~0.
        delay = delay * (0.5 + random.random() * 0.5)
    return delay


def _retry_after_or_backoff(response: httpx.Response, retry_index: int) -> float:
    """Honor a numeric ``Retry-After`` header (clamped to the cap); else backoff.

    APIM commonly answers 429/503 with ``Retry-After``.  Ignoring it either
    under-waits (keeps getting throttled) or over-waits.  Only the delta-seconds
    form is honored; the HTTP-date form falls back to exponential backoff.
    """
    try:
        raw = response.headers.get("Retry-After")
    except (AttributeError, TypeError):  # defensive: non-httpx mock response
        raw = None
    if raw:
        try:
            secs = float(raw)
        except (ValueError, TypeError):
            secs = -1.0
        if secs >= 0:
            return min(secs, _BACKOFF_MAX_S)
    return _backoff_delay(retry_index)


# ---------------------------------------------------------------------------
# Time-consistent global feed (faithful replay ordering) -- merges every
# session's events.jsonl into ONE globally timestamp-ordered stream so a
# spawned sub-session drains before its parent resumes, reproducing the
# live capture timing that is provably race-free server-side. This is
# purely a client-side FEED ORDER + PACING concern: no new server calls, no
# /status polling, no drain barrier -- the only network call remains the
# existing POST {server_url}/events, and the per-event parse/POST/retry
# body below (run_upload) is untouched.
# ---------------------------------------------------------------------------


class _MergedEvent(NamedTuple):
    """One line pulled from the global, timestamp-ordered merge of every session.

    Carries everything the existing per-event body needs (session_dir,
    metadata, session_id, working_dir, raw_line) plus the line's own
    best-effort-parsed timestamp (used for pacing) and its position within
    its OWN session's file (used for the deterministic tie-break).
    """

    session_dir: Path
    metadata: dict[str, Any]
    session_id: str
    working_dir: str
    raw_line: str
    line_index: int
    timestamp: float | None


def _extract_timestamp(line: str) -> float | None:
    """Best-effort parse of *line*'s top-level ``timestamp`` field.

    Returns a POSIX-seconds float, or ``None`` if the line is not valid
    JSON, is not a JSON object, has no ``timestamp`` field, or the field
    isn't a parseable ISO-8601 string. This NEVER raises -- a malformed or
    timestamp-less line still flows through to the EXISTING parse_fn
    error-handling downstream, unaffected by this best-effort peek. Used
    only for global ordering (:func:`_iter_merged_events`) and pacing.
    """
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    raw_ts = record.get("timestamp")
    if not isinstance(raw_ts, str) or not raw_ts.strip():
        return None
    try:
        return datetime.fromisoformat(raw_ts).timestamp()
    except ValueError:
        return None


def _iter_session_lines(session_dir: Path, metadata: dict[str, Any]) -> Iterator[_MergedEvent]:
    """Yield one :class:`_MergedEvent` per non-blank line of *session_dir*'s ``events.jsonl``.

    Lines are yielded in file order. Blank/whitespace-only lines are
    skipped entirely -- matching the original per-session loop's
    ``if not line: continue`` (never parsed, sent, or counted). Callers
    are expected to have already verified ``events.jsonl`` exists.
    """
    session_id: str = metadata["session_id"]
    working_dir: str = metadata.get("working_dir", "")
    events_file = session_dir / "events.jsonl"
    line_index = 0
    with events_file.open(encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            yield _MergedEvent(
                session_dir=session_dir,
                metadata=metadata,
                session_id=session_id,
                working_dir=working_dir,
                raw_line=line,
                line_index=line_index,
                timestamp=_extract_timestamp(line),
            )
            line_index += 1


def _iter_merged_events(sessions: list[tuple[Path, dict[str, Any]]]) -> Iterator[_MergedEvent]:
    """Merge every session's events.jsonl into ONE globally time-ordered stream.

    Each session's own file is already chronological (real captured
    timestamps), so a k-way ``heapq.merge`` keyed on
    ``(timestamp, session_id, line_index)`` reproduces the TRUE global
    emission order across sessions -- in particular, a spawned
    sub-session's events interleave BEFORE its parent's later-timestamped
    resume, exactly as they occurred live.

    Entries with a missing/unparseable timestamp sort as ``float("inf")``
    -- stably AFTER every entry with a known timestamp, and never a crash
    -- tie-broken by ``session_id`` then position within that session's
    own file. For the common case where every session in *sessions* lacks
    timestamps entirely (e.g. older test fixtures), this tie-break
    reproduces the original parent-first, in-list-order feed exactly
    (session_id sorts the same way the caller's list already does).

    This orders whole LINES; it does not re-sort the interior of a single
    file if that file itself mixed timestamped and non-timestamped lines
    out of chronological order -- per the module's contract (and every
    real context-intelligence-native events.jsonl, which stamps every
    line), each session's file is already chronological, so this does not
    arise in practice.

    Sessions with no events.jsonl at all contribute nothing here -- the
    "unreadable session" bookkeeping happens in :func:`run_upload`, before
    this merge is ever constructed.
    """
    iterables = [
        _iter_session_lines(session_dir, metadata)
        for session_dir, metadata in sessions
        if (session_dir / "events.jsonl").exists()
    ]
    yield from heapq.merge(
        *iterables,
        key=lambda item: (
            item.timestamp if item.timestamp is not None else float("inf"),
            item.session_id,
            item.line_index,
        ),
    )


def run_upload(
    sessions: list[tuple[Path, dict[str, Any]]],
    server_url: str,
    api_key: str,
    tracker: ProgressTracker,
    event_delay_s: float = 0.0,
    *,
    auth_strategy: AuthStrategy | None = None,
    replay: bool = True,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    timeout_s: float | None = None,
    parse_fn: ParseFn | None = None,
    max_gap_s: float = 2.0,
) -> UploadResult:
    """Replay all events from *sessions* to the server.

    Parameters
    ----------
    sessions:
        Ordered list of ``(session_dir, metadata)`` tuples.
    server_url:
        Base URL of the Context Intelligence ingestion server.
    api_key:
        API key used in the ``Authorization: Bearer`` header (static mode).
        Ignored when *auth_strategy* is provided.
    tracker:
        A :class:`ProgressTracker` instance that is updated after every event.
    event_delay_s:
        Seconds to sleep between each successful event POST.  Defaults to
        ``0.0`` (no delay).  Set to a positive value (e.g. ``0.05``) to
        throttle the upload rate and reduce Neo4j write pressure on the server.
    auth_strategy:
        Optional :class:`~context_intelligence.auth.AuthStrategy` that produces
        the ``Authorization`` header.  When ``None``, an ``ApiKeyAuth`` is
        derived from *api_key* for backward compatibility.
    replay:
        When ``True`` (the default), every POST is sent with ``?replay=true`` so
        the server bypasses its in-memory idempotency cache.  This is the safe
        default for re-uploading historical session data.  Set to ``False`` to
        re-enable the server's 7-day deduplication cache (the old behaviour);
        only do this for live, in-progress sessions where duplicate suppression
        is intentional.

    Returns
    -------
    UploadResult
        Success result after all sessions complete, or failure result if any
        HTTP error occurs.
    """
    if parse_fn is None:
        from .formats import ci_parse_line

        parse_fn = ci_parse_line

    from .formats import MalformedRecordError

    if auth_strategy is None:
        from context_intelligence.auth import ApiKeyAuth

        auth_strategy = ApiKeyAuth(api_key)

    endpoint = f"{server_url}/events"
    # timeout_s (when set via --timeout) tunes the read/write timeout — the dial an
    # operator reaches for on a slow/variable link with large event payloads.  connect
    # stays short (a slow *connect* is a real problem, not a payload-size one).
    rw_timeout = timeout_s if timeout_s is not None else 30.0
    timeout = httpx.Timeout(connect=5.0, read=rw_timeout, write=rw_timeout, pool=5.0)
    query_params: dict[str, str] | None = {"replay": "true"} if replay else None
    # max_retries is the number of ADDITIONAL attempts after the first; guard
    # against negatives so there is always at least one POST (never a silent skip).
    max_retries = max(0, max_retries)

    total_events_uploaded = 0
    total_events_unmapped = 0
    total_events_malformed = 0
    total_events_unreadable = 0
    total_retries = 0
    total_sessions_uploaded = 0

    # --- Pre-pass: resolve per-session totals up front (faithful replay ---
    # ordering). A fully-interleaved global feed can no longer discover "no
    # events.jsonl" or "existing file, zero non-blank lines" lazily as it
    # visits each session in turn (the old nested loop did both inline) --
    # both must be resolved before the merged replay begins, since the
    # merge only ever sees sessions that HAVE at least one non-blank line.
    session_totals: dict[str, int] = {}
    zero_event_session_ids: list[str] = []
    for session_dir, metadata in sessions:
        session_id: str = metadata["session_id"]
        events_file = session_dir / "events.jsonl"
        if not events_file.exists():
            # Session-level "unreadable" -- we can't count its events (the file
            # doesn't exist to count lines in), so it contributes 1 to the
            # unreadable bucket rather than a per-event count.
            total_events_unreadable += 1
            continue
        total = _count_lines(events_file)
        session_totals[session_id] = total
        if total == 0:
            zero_event_session_ids.append(session_id)

    # A session whose events.jsonl EXISTS but has zero non-blank lines will
    # never appear in the merged stream below (there is nothing to
    # interleave) -- start/complete it immediately, exactly as the original
    # per-session loop did (start_session(id, 0) immediately followed by
    # session_completed()).
    for session_id in zero_event_session_ids:
        tracker.start_session(session_id, 0)
        tracker.session_completed()
        total_sessions_uploaded += 1

    # --- Tracker adaptation note (interleaved replay) ---------------------
    # ProgressTracker's on-disk schema has a single "current session" slot
    # (current_session_id / current_session_events_total /
    # current_session_events_sent) -- it was designed for one-session-at-a-
    # time replay. A globally interleaved feed can revisit a session (e.g.
    # the parent) after a DIFFERENT session's (the child's) events have run
    # in between. Minimal adaptation: call tracker.start_session() on every
    # ACTIVE-SESSION TRANSITION (not just each session's first-ever
    # appearance), so current_session_id / current_session_events_total
    # always describe whichever session is actually being sent right now --
    # this keeps the live "now:" folder label and the persisted JSON
    # honest. GLOBAL progress (sessions_completed, overall events sent,
    # percent, elapsed, ETA -- everything the live 2-line bar renders) is
    # computed by TwoLevelProgressRenderer from its OWN overall counters,
    # entirely independent of "current session" bookkeeping, so it is
    # unaffected and remains fully correct. The one accepted, documented
    # limitation: current_session_events_sent resets to 0 on each
    # transition back into a session, so it reflects only the current
    # unbroken segment's sent-count, not that session's running total
    # across multiple non-contiguous segments -- a consequence of the
    # schema's single-current-session design, not of this change's logic.
    active_session_id: str | None = None
    lines_seen_counts: dict[str, int] = {}
    event_index_counts: dict[str, int] = {}
    prev_event_timestamp: float | None = None
    is_first_emitted_event = True

    def _maybe_complete_session(sid: str) -> None:
        """Fire tracker.session_completed() the moment *sid*'s LAST non-blank

        line (per the pre-pass count in session_totals) has been processed
        -- matching the original per-session loop's unconditional
        post-for-loop session_completed() call, now triggered by a count
        instead of a loop boundary (since sessions are interleaved).
        """
        nonlocal total_sessions_uploaded
        if lines_seen_counts.get(sid, 0) >= session_totals.get(sid, 0):
            tracker.session_completed()
            total_sessions_uploaded += 1

    # NOTE (issue #338): auth headers are fetched PER attempt inside the loop
    # (not baked into the client here), so a long run that crosses the Entra
    # token-expiry boundary transparently picks up a refreshed bearer token.
    with httpx.Client(timeout=timeout) as client:
        for merged in _iter_merged_events(sessions):
            session_dir = merged.session_dir
            metadata = merged.metadata
            session_id = merged.session_id
            working_dir = merged.working_dir
            line = merged.raw_line

            if session_id != active_session_id:
                tracker.start_session(session_id, session_totals.get(session_id, 0))
                active_session_id = session_id

            lines_seen_counts[session_id] = lines_seen_counts.get(session_id, 0) + 1
            event_index = event_index_counts.get(session_id, 0)

            # --- PACING: real inter-event gap between this line and the ---
            # previously emitted line in the GLOBAL merged stream, capped at
            # max_gap_s and floored by event_delay_s. This is what lets a
            # spawned sub-session's events drain before its parent resumes
            # (a seconds-scale live hand-off gap), without any server call
            # of any kind -- purely a client-side sleep derived from the
            # events' own timestamps. Skipped for the very first emitted
            # event of the whole run (nothing to measure a gap against yet),
            # matching the original event_delay_s behaviour of never
            # delaying before the first send. NOTE: gated on "is this the
            # first event ever", NOT "do we have a previous known
            # timestamp" -- a run with NO timestamps anywhere must still
            # honor the event_delay_s floor from the second event onward.
            if not is_first_emitted_event:
                if prev_event_timestamp is not None and merged.timestamp is not None:
                    gap = max(0.0, merged.timestamp - prev_event_timestamp)
                else:
                    # Unparseable/missing timestamp on either side -- never
                    # crash, treat as no measurable gap; event_delay_s (if
                    # set) still floors it.
                    gap = 0.0
                sleep_s = min(max_gap_s, gap)
                sleep_s = max(sleep_s, event_delay_s)
                if sleep_s > 0:
                    time.sleep(sleep_s)
            is_first_emitted_event = False
            if merged.timestamp is not None:
                prev_event_timestamp = merged.timestamp

            # Parse the line via parse_fn — accumulate a counter rather than
            # printing per-event (see module docstring): exc is unused for
            # display now, but kept named for clarity of which branch fired.
            try:
                parsed = parse_fn(line, session_dir, metadata)
            except json.JSONDecodeError:
                total_events_malformed += 1
                tracker.event_sent()
                event_index_counts[session_id] = event_index + 1
                _maybe_complete_session(session_id)
                continue
            except MalformedRecordError:
                total_events_malformed += 1
                tracker.event_sent()
                event_index_counts[session_id] = event_index + 1
                _maybe_complete_session(session_id)
                continue
            except SkipLine as exc:
                if exc.category == "unmapped":
                    total_events_unmapped += 1
                else:
                    total_events_malformed += 1
                tracker.event_sent()
                event_index_counts[session_id] = event_index + 1
                _maybe_complete_session(session_id)
                continue

            if parsed is None:
                # No event_index increment here -- matches the original
                # body's behaviour verbatim (a parse_fn that returns None
                # for a non-blank line is never exercised by the default
                # ci_parse_line, which only returns None for blank lines
                # already filtered out upstream). The line still counts
                # towards this session's completion trigger, mirroring
                # _count_lines' definition of "total" (every non-blank
                # line), so a session can never fail to complete.
                _maybe_complete_session(session_id)
                continue

            event, workspace, data = parsed
            payload = build_payload(event, workspace, data, working_dir=working_dir)

            # --- POST with bounded retry + exponential backoff (issue #338) ---
            # Transient failures (connection errors, timeouts, 5xx, 429) are
            # retried up to *max_retries* times; permanent failures (4xx other
            # than 429, 3xx) and an exhausted retry budget fail loud exactly as
            # before.  tracker.event_sent()/mark_failed() fire ONCE per event on
            # the terminal outcome — never per attempt (so progress.json never
            # flips to 'failed' mid-retry, and sent-counts never over-count).
            retry_index = 0
            while True:
                # Fetch the auth header for THIS attempt so a retry that crosses
                # the token-expiry margin transparently gets a refreshed token.
                # headers() can raise (unusable API key -> ValueError; credential
                # failure -> azure error) — neither is an httpx.HTTPError, so guard
                # it explicitly and fail loud rather than crash the run mid-batch.
                try:
                    request_headers = auth_strategy.headers()
                except Exception as exc:  # noqa: BLE001 - auth failure must fail loud, not crash
                    error_msg = f"auth header error: {exc}"
                    tracker.mark_failed(
                        session_id=session_id,
                        event_index=event_index,
                        http_status=0,
                        error=error_msg,
                    )
                    return UploadResult(
                        success=False,
                        sessions_uploaded=total_sessions_uploaded,
                        events_uploaded=total_events_uploaded,
                        events_skipped=total_events_malformed + total_events_unreadable,
                        events_unmapped=total_events_unmapped,
                        events_malformed=total_events_malformed,
                        events_unreadable=total_events_unreadable,
                        retries=total_retries,
                        error=error_msg,
                        failed_at={
                            "session_id": session_id,
                            "event_index": event_index,
                            "http_status": 0,
                        },
                    )

                try:
                    response = client.post(
                        endpoint,
                        json=payload,
                        params=query_params,
                        headers=request_headers,
                    )
                except httpx.HTTPError as exc:
                    # Transport-level error.  Genuinely transient ones (connection
                    # reset, timeout) are retried; PERMANENT ones (DNS resolution,
                    # TLS/cert failure) never succeed on retry, so fail them fast
                    # instead of burning the whole backoff budget on a dead host.
                    if not _is_fatal_transport_error(exc) and retry_index < max_retries:
                        delay = _backoff_delay(retry_index)
                        total_retries += 1
                        time.sleep(delay)
                        retry_index += 1
                        continue
                    tracker.mark_failed(
                        session_id=session_id,
                        event_index=event_index,
                        http_status=0,
                        error=str(exc),
                    )
                    return UploadResult(
                        success=False,
                        sessions_uploaded=total_sessions_uploaded,
                        events_uploaded=total_events_uploaded,
                        events_skipped=total_events_malformed + total_events_unreadable,
                        events_unmapped=total_events_unmapped,
                        events_malformed=total_events_malformed,
                        events_unreadable=total_events_unreadable,
                        retries=total_retries,
                        error=str(exc),
                        failed_at={
                            "session_id": session_id,
                            "event_index": event_index,
                            "http_status": 0,
                        },
                    )

                status_code = response.status_code
                if 200 <= status_code < 300:
                    break  # delivered

                # Non-2xx: retry only transient statuses, and only while budget remains.
                if _is_transient_status(status_code) and retry_index < max_retries:
                    delay = _retry_after_or_backoff(response, retry_index)
                    total_retries += 1
                    time.sleep(delay)
                    retry_index += 1
                    continue

                # Permanent failure, or transient budget exhausted — fail loud.
                body = response.text[:200].strip() if response.text else ""
                error_msg = f"HTTP {status_code} from {endpoint}" + (f": {body}" if body else "")
                tracker.mark_failed(
                    session_id=session_id,
                    event_index=event_index,
                    http_status=status_code,
                    error=error_msg,
                )
                return UploadResult(
                    success=False,
                    sessions_uploaded=total_sessions_uploaded,
                    events_uploaded=total_events_uploaded,
                    events_skipped=total_events_malformed + total_events_unreadable,
                    events_unmapped=total_events_unmapped,
                    events_malformed=total_events_malformed,
                    events_unreadable=total_events_unreadable,
                    retries=total_retries,
                    error=error_msg,
                    failed_at={
                        "session_id": session_id,
                        "event_index": event_index,
                        "http_status": status_code,
                    },
                )

            tracker.event_sent()
            total_events_uploaded += 1
            event_index_counts[session_id] = event_index + 1
            _maybe_complete_session(session_id)

    tracker.mark_completed()
    return UploadResult(
        success=True,
        sessions_uploaded=total_sessions_uploaded,
        events_uploaded=total_events_uploaded,
        events_skipped=total_events_malformed + total_events_unreadable,
        events_unmapped=total_events_unmapped,
        events_malformed=total_events_malformed,
        events_unreadable=total_events_unreadable,
        retries=total_retries,
    )
