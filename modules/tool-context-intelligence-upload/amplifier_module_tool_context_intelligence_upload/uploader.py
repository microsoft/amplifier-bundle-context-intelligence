"""Core HTTP replay loop for context-intelligence upload.

Provides UploadResult, _count_lines, and run_upload for replaying
session events.jsonl files to the Context Intelligence ingestion endpoint.

CLI context: this module runs as a CLI tool, so user-facing warnings are written
to stderr via ``print(..., file=sys.stderr)`` rather than the ``logging`` module.
"""

from __future__ import annotations

import json
import random
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        error: str | None = None,
        failed_at: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.sessions_uploaded = sessions_uploaded
        self.events_uploaded = events_uploaded
        self.events_skipped = events_skipped
        # Not included in to_dict() (GATE 2) -- inspect this attribute directly.
        self.events_unmapped = events_unmapped
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
    """Count the number of lines in *file_path*."""
    count = 0
    with file_path.open(encoding="utf-8") as fh:
        for _ in fh:
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
    total_events_skipped = 0
    total_events_unmapped = 0
    total_sessions_uploaded = 0

    # NOTE (issue #338): auth headers are fetched PER attempt inside the loop
    # (not baked into the client here), so a long run that crosses the Entra
    # token-expiry boundary transparently picks up a refreshed bearer token.
    with httpx.Client(timeout=timeout) as client:
        for session_dir, metadata in sessions:
            session_id: str = metadata["session_id"]
            events_file = session_dir / "events.jsonl"

            if not events_file.exists():
                print(
                    f"WARNING: events.jsonl not found for session {session_id!r} "
                    f"(path: {events_file}), skipping.",
                    file=sys.stderr,
                )
                continue

            events_total = _count_lines(events_file)
            tracker.start_session(session_id, events_total)

            event_index = 0

            with events_file.open(encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue

                    # Parse the line via parse_fn — skip malformed lines with a warning
                    try:
                        parsed = parse_fn(line, session_dir, metadata)
                    except json.JSONDecodeError as exc:
                        print(
                            f"WARNING: malformed JSON in {events_file} "
                            f"at line {event_index}: {exc}",
                            file=sys.stderr,
                        )
                        total_events_skipped += 1
                        tracker.event_sent()
                        event_index += 1
                        continue
                    except MalformedRecordError as exc:
                        print(
                            f"WARNING: skipping malformed record in {events_file} "
                            f"at line {event_index}: {exc}",
                            file=sys.stderr,
                        )
                        total_events_skipped += 1
                        tracker.event_sent()
                        event_index += 1
                        continue
                    except SkipLine as exc:
                        print(
                            f"WARNING: skipping line in {events_file} "
                            f"at line {event_index}: {exc.reason}",
                            file=sys.stderr,
                        )
                        if exc.category == "unmapped":
                            total_events_unmapped += 1
                        else:
                            total_events_skipped += 1
                        tracker.event_sent()
                        event_index += 1
                        continue

                    if parsed is None:
                        continue

                    event, workspace, data = parsed
                    payload = build_payload(event, workspace, data)

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
                                events_skipped=total_events_skipped,
                                events_unmapped=total_events_unmapped,
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
                                print(
                                    f"WARNING: transient network error uploading session "
                                    f"{session_id!r} event {event_index} "
                                    f"(attempt {retry_index + 1}/{max_retries + 1}): {exc}; "
                                    f"retrying in {delay:.1f}s",
                                    file=sys.stderr,
                                )
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
                                events_skipped=total_events_skipped,
                                events_unmapped=total_events_unmapped,
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
                            print(
                                f"WARNING: HTTP {status_code} uploading session "
                                f"{session_id!r} event {event_index} "
                                f"(attempt {retry_index + 1}/{max_retries + 1}); "
                                f"retrying in {delay:.1f}s",
                                file=sys.stderr,
                            )
                            time.sleep(delay)
                            retry_index += 1
                            continue

                        # Permanent failure, or transient budget exhausted — fail loud.
                        body = response.text[:200].strip() if response.text else ""
                        error_msg = f"HTTP {status_code} from {endpoint}" + (
                            f": {body}" if body else ""
                        )
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
                            events_skipped=total_events_skipped,
                            events_unmapped=total_events_unmapped,
                            error=error_msg,
                            failed_at={
                                "session_id": session_id,
                                "event_index": event_index,
                                "http_status": status_code,
                            },
                        )

                    tracker.event_sent()
                    total_events_uploaded += 1
                    event_index += 1
                    if event_delay_s > 0:
                        time.sleep(event_delay_s)

            tracker.session_completed()
            total_sessions_uploaded += 1

    tracker.mark_completed()
    return UploadResult(
        success=True,
        sessions_uploaded=total_sessions_uploaded,
        events_uploaded=total_events_uploaded,
        events_skipped=total_events_skipped,
        events_unmapped=total_events_unmapped,
    )
