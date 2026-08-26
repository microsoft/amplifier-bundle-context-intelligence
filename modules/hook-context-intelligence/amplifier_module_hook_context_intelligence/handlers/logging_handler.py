"""LoggingHandler — always-on flat JSONL session file writer.

Zero dependency on graph infrastructure — no nodes, edges, cursors, or stores.
Writes per-session events.jsonl and metadata.json files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import deque
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from amplifier_module_hook_context_intelligence.upload import (
    _canonical_json,
    _compute_idempotency_key,  # noqa: F401 — re-exported for test imports
    build_payload,
)

from amplifier_core.models import HookResult

logger = logging.getLogger(__name__)

_OPTIONAL_METADATA_FIELDS = ("agent_name", "parallel_group_id", "recipe_name", "recipe_step")
_DEFAULT_DISPATCH_QUEUE_CAPACITY = 256
_DEFAULT_CLOSE_DRAIN_TIMEOUT = 10.0
_DEFAULT_BACKOFF_INITIAL = 1.0
_DEFAULT_BACKOFF_MAX = 30.0
_DEFAULT_BACKOFF_JITTER = True
#: Upper bound on the backoff exponent. ``2 ** n`` computed with a float base
#: raises ``OverflowError`` once ``n`` reaches ~1024, which a long-lived session
#: can hit after enough consecutive failures. The backoff is clamped to
#: ``backoff_max`` long before this exponent matters (2**64 already dwarfs any
#: sane ceiling), so bounding the exponent changes no delivered delay — it only
#: removes the overflow footgun that would otherwise crash the retry loop.
_BACKOFF_MAX_EXPONENT = 64
#: Hard deadline (seconds) for each previously-UNBOUNDED teardown step in
#: ``_DestinationDispatcher.close()``: joining the cancelled worker task and
#: closing the httpx client. Event delivery is documented best-effort and must
#: NEVER block process exit -- a half-closed (CLOSE-WAIT) connection once
#: wedged a host process for hours inside ``AsyncClient.aclose()`` during the
#: final flush at session end. On deadline: warn and abandon.
_CLOSE_HARD_TIMEOUT = 5.0
_METADATA_FORMAT = "context-intelligence"
_METADATA_VERSION = "1.0.0"
_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 3.0
_POOL_TIMEOUT = 0.5
#: Minimum seconds between repeated overflow or permanent-skip log warnings.
_LOG_RATE_LIMIT_SECONDS = 60.0

# ---------------------------------------------------------------------------
# Circuit breaker constants (v2 minimal breaker)
#
# See forwarding-diagnostics-design.md Part 2. Replaces the old per-event
# "give up after N consecutive 401s, reset, re-climb" behavior with a
# destination-level breaker: warn once, go quiet, auto-recover via a slow
# re-probe -- no restart needed. Deliberately minimal: no persistence, no
# stored state-machine enum, no config knobs (hardcoded defaults).
# ---------------------------------------------------------------------------
#: Sliding window (attempt outcomes) used to compute the hard-failure ratio.
#: True = hard (deterministic auth fault), False = delivered. Genuinely
#: transient/permanent outcomes are never pushed into this window.
_BREAKER_WINDOW: int = 20
#: Hard-failure ratio (over the window) at/above which the breaker opens.
_BREAKER_HARD_RATIO: float = 0.9
#: Minimum number of window samples required before the breaker can open --
#: prevents a handful of early failures (before the window is representative)
#: from tripping it.
_BREAKER_MIN_SAMPLES: int = 10
#: Minimum sustained wall-clock seconds of hard failure before opening. A
#: fast backoff curve can produce many attempts within a short token-rotation
#: window (e.g. ~12s); this floor stops the breaker from opening on that
#: alone -- it must be sustained, not just frequent.
_BREAKER_MIN_OPEN_SECONDS: float = 30.0
#: While OPEN, allow exactly one probe attempt at this cadence (seconds).
_BREAKER_PROBE_INTERVAL: float = 300.0

# ---------------------------------------------------------------------------
# Sustained-failure visibility escalation
#
# REAL INCIDENT: a Context Intelligence server was down/crash-looping for
# ~2 days. Every session on the host kept dispatching into the dead
# endpoint. The client behaved correctly throughout -- events stayed
# durable in events.jsonl, the per-destination queue stayed isolated -- but
# NOTHING escalated. Root cause of the silence: the circuit breaker above
# only opens on a HARD (deterministic auth) failure ratio (see
# _maybe_open_breaker); a down/unreachable server produces TRANSIENT
# (network-error / timeout) outcomes, which are explicitly never HARD (see
# _is_hard_outcome) and so never feed the breaker at all. A network outage
# therefore retries the SAME event forever with capped backoff, forever
# "DEGRADED", and the only signal was a single rate-limited INFO line per
# episode -- deliberately quiet by design (see the comment in _worker),
# because most DEGRADED episodes are transient blips that self-resolve in
# seconds. Nothing ever escalated that quiet signal once the episode
# stopped being transient and became a multi-day outage.
#
# This escalation is a SEPARATE, additive signal -- it does not touch
# breaker state, retry/backoff timing, or dispatch semantics. It only
# tracks wall-clock time since the CURRENT degraded regime began
# (_degraded_since, set/cleared alongside _degraded_warned) and, once that
# regime has run longer than this threshold, raises a rate-limited loud
# ERROR plus a durable forwarding-diagnostics record (see
# _maybe_escalate_sustained_failure) naming: events dropped so far, whether
# the (auth-only) breaker is open, and how long delivery has been failing.
# ---------------------------------------------------------------------------
#: Sustained wall-clock seconds a destination must stay continuously
#: DEGRADED before the routine per-episode INFO notice escalates to a loud,
#: durable signal. Deliberately much longer than one backoff cycle (a
#: single transient blip must never trip this) but short enough that an
#: operator learns about a real outage in minutes, not days.
_DEGRADED_ESCALATION_SECONDS: float = 300.0

# ---------------------------------------------------------------------------
# _post outcome constants (Task 4)
# ---------------------------------------------------------------------------
#: Returned by _post when the event was successfully delivered (HTTP < 400).
_DELIVERED: str = "delivered"
#: Returned by _post when delivery should be retried with backoff (network
#: errors, HTTP 5xx, HTTP 429, HTTP 401).
_TRANSIENT: str = "transient"
#: Returned by _post when the event must be skipped permanently (HTTP 4xx
#: other than 401/429). This is a RETRY-CLASSIFICATION bucket only -- it does
#: NOT imply a single cause. The cause varies by status and is asserted only
#: where actually known: 400/413/422 = malformed/unprocessable payload,
#: 403 = forbidden (credentials), 404/410 = endpoint not found/gone (routing
#: or deployment, not the payload). Any other 4xx is non-retryable but its
#: cause is NOT asserted (see the message-layer branches in _worker).
_PERMANENT: str = "permanent"


# ---------------------------------------------------------------------------
# HTTP outcome classification helper (Task 4)
# ---------------------------------------------------------------------------
def _classify_http_outcome(status_code: int) -> str:
    """Map an HTTP status code to a _post outcome constant.

    Classification table:
    - ``_DELIVERED``:  status < 300 (2xx success)
    - ``_PERMANENT``:  300 <= status < 400 (3xx redirect — deliberately not following;
      authenticated POST redirects risk bearer-token leakage to another host)
    - ``_TRANSIENT``:  401, 429, or any 5xx (retry forever w/ backoff)
    - ``_PERMANENT``:  403, 400, 413, 422, 404, 410, and any other 4xx (loud skip)

    This is a RETRY decision only — every status below is uniformly non-retryable.
    It does NOT imply they share one cause: 400/413/422 mean the payload was
    rejected, 403 means forbidden/credentials, 404/410 mean the endpoint itself
    is missing/gone (routing or deployment, not the payload). The message layer
    (see ``_worker``) asserts the cause per status instead of blaming the payload
    for all of them.
    """
    if status_code < 300:
        return _DELIVERED
    if status_code < 400:
        # 3xx redirect: misconfigured URL or HTTPS-enforce redirect.
        # Do NOT follow — silently following an authenticated POST redirect risks
        # leaking the bearer token to a different host.
        return _PERMANENT
    if status_code == 401 or status_code == 429 or status_code >= 500:
        return _TRANSIENT
    # 403, 400, 413, 422, 404, 410, and any other 4xx — all non-retryable, but
    # NOT all "malformed"; see the message-layer branches in _worker for the
    # per-status cause.
    return _PERMANENT


# ---------------------------------------------------------------------------
# Delivery-path task hygiene
# ---------------------------------------------------------------------------
def _retrieve_task_exception(task: asyncio.Task[Any], context: str = "task") -> None:
    """Done-callback: retrieve a delivery-path task's exception so asyncio never
    reports ``Task exception was never retrieved``.

    Teardown races with an already-closed event loop or client (e.g. httpx
    ``AsyncClient.aclose`` raising ``RuntimeError('Event loop is closed')``)
    are expected best-effort noise: logged at DEBUG and swallowed. Anything
    else is a real defect surfaced at WARNING -- but NEVER propagated; event
    delivery is best-effort and must not take the host process down.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    if isinstance(exc, RuntimeError) and "closed" in str(exc).lower():
        logger.debug("%s: teardown raced a closed event loop/client: %r", context, exc)
        return
    logger.warning("%s: unhandled exception retrieved: %r", context, exc)


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------
def _sanitize_value(value: Any) -> Any:
    """Sanitize a single value for JSON serialization."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _sanitize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, set):
        return sorted(_sanitize_value(v) for v in value)
    # Pydantic model
    if hasattr(value, "model_dump"):
        return _sanitize_value(value.model_dump())
    # Generic object with __dict__ (only if non-empty)
    obj_dict = getattr(value, "__dict__", None)
    if obj_dict:
        return _sanitize_value(obj_dict)
    # Fallback
    return str(value)


def _sanitize_for_json(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize a dict for JSON serialization. Never raises."""
    return {k: _sanitize_value(v) for k, v in data.items()}


# ---------------------------------------------------------------------------
# Forwarding-diagnostics sink (best-effort, durable JSONL)
# ---------------------------------------------------------------------------
def _write_forwarding_record(log_dir: Path | None, record: dict[str, Any]) -> bool:
    """Append *record* to the per-UTC-day forwarding-diagnostics JSONL file.

    Best-effort: a diagnostics write must NEVER raise into the dispatch path.
    A ``None`` *log_dir* disables the sink entirely (no directory, no file) --
    that is not a failure, so it returns ``True`` (nothing to warn about).

    Returns
    -------
    bool
        ``True`` on a successful write, or when the sink is disabled
        (``log_dir is None``). ``False`` ONLY when the write itself raised --
        the caller uses this to surface a rate-limited console warning
        without ever writing another diagnostics record about it.
    """
    if log_dir is None:
        return True
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with (log_dir / f"forwarding-{day}.jsonl").open("a") as f:
            f.write(_canonical_json(record) + "\n")
        return True
    except Exception:
        logger.debug("forwarding-diagnostics write failed", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# _DestinationDispatcher
# ---------------------------------------------------------------------------
class _DestinationDispatcher:
    """One context-intelligence destination: own client, queue, breaker, worker.

    Concurrent, non-blocking, drop-on-full PER destination. Failures are isolated;
    breaker opens this destination only.
    """

    def __init__(
        self,
        name: str,
        url: str,
        api_key: str,
        workspace: str | None,
        *,
        working_dir: str | None = None,
        dispatch_timeout: float,
        failure_threshold: int,
        queue_capacity: int,
        close_drain_timeout: float,
        # Back-compat default for direct construction only; _READ_TIMEOUT (3.0) is intentionally NOT tracking the resolver's 10.0 default. Do not "make them match" -- the resolver is the single source of truth for configured runs.
        read_timeout: float = _READ_TIMEOUT,
        # Back-compat default for direct construction only; the resolver
        # (dispatch_connect_timeout) is the single source of truth for configured runs.
        connect_timeout: float = _CONNECT_TIMEOUT,
        auth_mode: str = "static",
        auth_resource: str = "",
        backoff_initial: float = _DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = _DEFAULT_BACKOFF_MAX,
        backoff_jitter: bool = _DEFAULT_BACKOFF_JITTER,
        storage_path: str | Path = "",
        forwarding_log_dir: str | Path = "",
    ) -> None:
        from context_intelligence.auth import AuthStrategy, build_auth_strategy  # noqa: PLC0415

        self._name = name
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._workspace = workspace
        self._working_dir = working_dir
        self._dispatch_timeout = dispatch_timeout
        self._read_timeout = read_timeout
        self._connect_timeout = connect_timeout
        self._failure_threshold = failure_threshold
        self._close_drain_timeout = close_drain_timeout
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._backoff_jitter = backoff_jitter
        self._storage_path = storage_path
        self._forwarding_log_dir: Path | None = (
            Path(forwarding_log_dir).expanduser() if forwarding_log_dir else None
        )
        # Build the auth strategy ONCE at init; credential SDK handles token refresh internally.
        self._strategy: AuthStrategy = build_auth_strategy(
            auth_mode=auth_mode,
            api_key=api_key,
            auth_resource=auth_resource,
        )
        self._client: httpx.AsyncClient | None = None
        self._queue_capacity = max(1, queue_capacity)
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
            maxsize=self._queue_capacity
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0  # backoff driver only — never disables
        self._degraded_warned = False
        # Wall-clock start (time.monotonic()) of the CURRENT sustained-degraded
        # regime; None while healthy. Set/cleared in lockstep with
        # _degraded_warned (see _worker) — independent of the auth-only circuit
        # breaker above, so a network outage (never HARD, never opens the
        # breaker) still measures "how long has delivery been failing" for
        # _maybe_escalate_sustained_failure.
        self._degraded_since: float | None = None
        self._current: tuple[str, dict[str, Any]] | None = None  # in-flight held event
        self._overflow_dropped = 0
        self._auth_failures = 0
        self._last_status: int | None = None
        # Sentinel "never logged yet" value. MUST be -inf, not 0.0: these are
        # compared against time.monotonic(), whose reference point is platform-
        # defined (often process/boot start, not epoch) and can legitimately be
        # a small number of seconds on a freshly booted host or container. A
        # 0.0 sentinel would make `now - 0.0 >= _LOG_RATE_LIMIT_SECONDS` false
        # for the very first occurrence when now < 60s, silently swallowing the
        # first overflow/permanent/auth-escalation warning of the process's
        # lifetime. -inf guarantees the first check always passes regardless
        # of the monotonic clock's absolute baseline.
        self._last_overflow_log = float("-inf")
        self._last_permanent_log = float("-inf")
        self._last_auth_log = float("-inf")
        self._last_giveup_log = float("-inf")
        # Dedicated rate-limit gate for auth-strategy (headers()) production
        # failures -- see _post. Kept separate from _last_auth_log (which gates
        # the genuine-HTTP-401 escalation warning) so the two unrelated failure
        # modes don't share (and reset) each other's cooldown.
        self._last_headers_error_log = float("-inf")
        # Dedicated rate-limit gate for the forwarding-diagnostics SINK write
        # itself failing (e.g. full disk, bad perms on self._forwarding_log_dir).
        # Separate from every other _last_*_log sentinel above -- this one
        # gates a console-only warning about the diagnostics sink, not about
        # the destination's HTTP/auth behavior. See _record_forwarding_issue.
        self._last_sink_fail_log = float("-inf")
        # Dedicated rate-limit gate for the sustained-delivery-failure
        # escalation (see _maybe_escalate_sustained_failure) -- separate from
        # every other _last_*_log sentinel above so this new signal's cooldown
        # cannot be reset by an unrelated warning firing first.
        self._last_degraded_escalation_log = float("-inf")
        # --- circuit breaker state ---
        # OWNERSHIP: this destination's single worker task (_worker) is the
        # ONLY mutator of breaker state. No lock is needed -- there is exactly
        # one asyncio task per dispatcher processing events serially.
        # True = hard (deterministic auth) outcome, False = delivered.
        # Genuinely transient/permanent outcomes never enter this window.
        self._breaker_window: deque[bool] = deque(maxlen=_BREAKER_WINDOW)
        # Start of the CURRENT sustained failing-regime (window ratio >=
        # _BREAKER_HARD_RATIO with >= _BREAKER_MIN_SAMPLES samples), not the
        # timestamp of the last hard outcome. See _update_regime_clock().
        self._first_hard_ts: float | None = None
        self._breaker_open: bool = False
        self._last_probe_ts: float = 0.0
        # Set True by _post's auth-header-production failure path for the
        # CURRENT attempt only; cleared at the top of _post before every
        # attempt so a stale value can never be inherited by an unrelated
        # outcome.
        self._auth_token_failed: bool = False

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            # Retrieve any terminal exception so asyncio never reports
            # "Task exception was never retrieved" for the delivery path
            # (the worker supervisor catches Exception, but a defect outside
            # its try block -- or a teardown race -- must still be retrieved).
            self._worker_task.add_done_callback(
                partial(_retrieve_task_exception, context=f"{self._name} dispatch worker")
            )

    def enqueue(self, event: str, data: dict[str, Any]) -> None:
        """Enqueue an event for dispatch. HOT PATH — zero awaits, zero I/O.

        Drops on full queue (bumps _overflow_dropped counter). Never disables.

        Immutability contract: ``data`` MUST be treated as immutable from the
        moment it is enqueued. Neither ``_worker`` nor ``_post`` may mutate the
        dict in-place. This guarantees that ``_compute_idempotency_key`` produces
        the same value on every retry attempt — including the lost-ACK path where
        a ``RemoteProtocolError`` is raised after the server has already processed
        the event. Any mutation would silently change the idempotency key and
        defeat server-side dedup.
        """
        self._ensure_worker()
        try:
            self._queue.put_nowait((event, data))
        except asyncio.QueueFull:
            self._overflow_dropped += 1
            now = time.monotonic()
            if now - self._last_overflow_log >= _LOG_RATE_LIMIT_SECONDS:
                self._last_overflow_log = now
                logger.warning(
                    "%s buffer full — %d events dropped since last warning;"
                    " events are durable in events.jsonl."
                    " To manually upload run: context-intelligence-upload --path %s"
                    " (--server-url/--api-key come from flags or env/config; see --help)",
                    self._name,
                    self._overflow_dropped,
                    self._storage_path,
                )

    def _record_forwarding_issue(self, kind: str, detail: str) -> None:
        """Write a durable forwarding-diagnostics record for this destination.

        Best-effort via ``_write_forwarding_record`` \u2014 never raises into the
        caller. ``session_id`` is pulled from the in-flight event held in
        ``self._current``, when available.

        If the sink write itself fails (full disk, bad permissions on
        ``self._forwarding_log_dir``, ...), that failure is otherwise only
        ever logged at DEBUG \u2014 invisible by default, silently killing the
        very diagnostics file operators are told to consult. Surface a
        rate-limited console WARNING instead. This is console-only: it must
        NOT recurse into ``_write_forwarding_record``/``_record_forwarding_issue``
        (the sink is what's failing) and must NOT touch ``events.jsonl`` (that
        path belongs solely to ``LoggingHandler``).
        """
        session_id = ""
        if self._current is not None:
            _evt, payload = self._current
            if isinstance(payload, dict):
                session_id = str(payload.get("session_id", ""))
        wrote_ok = _write_forwarding_record(
            self._forwarding_log_dir,
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "destination": self._name,
                "url": self._url,
                "kind": kind,
                "http_status": self._last_status,
                "auth_failures": self._auth_failures,
                "session_id": session_id,
                "workspace": self._workspace or "",
                "detail": detail,
            },
        )
        if not wrote_ok:
            now = time.monotonic()
            if now - self._last_sink_fail_log >= _LOG_RATE_LIMIT_SECONDS:
                self._last_sink_fail_log = now
                logger.warning(
                    "%s forwarding-diagnostics sink write failed for %s \u2014"
                    " diagnostics records are being lost (see DEBUG log for the"
                    " stack trace); captured events remain durable in"
                    " events.jsonl.",
                    self._name,
                    self._forwarding_log_dir,
                )

    def _maybe_escalate_sustained_failure(self) -> None:
        """Escalate a SUSTAINED degraded regime to a loud, durable signal.

        Called ONLY from ``_worker`` (never ``enqueue()``) -- this performs
        logging plus best-effort file I/O via ``_record_forwarding_issue``
        and must never run on the hot path.

        RATIONALE (real incident): the circuit breaker (below) only opens on
        a HARD (deterministic auth) failure ratio. A down/unreachable server
        produces TRANSIENT network-error/timeout outcomes, which are never
        HARD (see ``_is_hard_outcome``) and so never feed the breaker at
        all -- the worker just retries the same event forever with capped
        backoff, "DEGRADED" but silent beyond one rate-limited INFO line per
        episode (deliberately quiet -- see the comment in ``_worker`` -- most
        DEGRADED episodes are transient blips). A production server was once
        down for ~2 days and nothing ever escalated past that quiet signal.

        This closes that gap without touching retry/backoff timing or
        breaker state: once ``self._degraded_since`` shows the CURRENT
        regime has run longer than ``_DEGRADED_ESCALATION_SECONDS``, emit a
        rate-limited (``_LOG_RATE_LIMIT_SECONDS``) loud ERROR plus a durable
        ``sustained_delivery_failure`` forwarding-diagnostics record naming
        the three facts an operator needs: events dropped so far, whether
        the (auth-only) breaker is open, and how long delivery has been
        failing. No-op while healthy (``self._degraded_since is None``).
        """
        if self._degraded_since is None:
            return
        now = time.monotonic()
        failing_seconds = now - self._degraded_since
        if failing_seconds < _DEGRADED_ESCALATION_SECONDS:
            return
        if now - self._last_degraded_escalation_log < _LOG_RATE_LIMIT_SECONDS:
            return
        self._last_degraded_escalation_log = now
        logger.error(
            "%s (%s) has been failing to deliver for %.0fs \u2014 %d event(s)"
            " dropped from a full queue so far, circuit breaker open=%s."
            " Events remain durable in events.jsonl; once %s recovers, replay"
            " any dropped backlog with: context-intelligence-upload --path %s"
            " (--server-url/--api-key come from flags or env/config; see --help).",
            self._name,
            self._url,
            failing_seconds,
            self._overflow_dropped,
            self._breaker_open,
            self._name,
            self._storage_path,
        )
        self._record_forwarding_issue(
            "sustained_delivery_failure",
            "failing for %.0fs (overflow_dropped=%d, breaker_open=%s)"
            % (failing_seconds, self._overflow_dropped, self._breaker_open),
        )

    # -- circuit breaker (v2 minimal) ---------------------------------------
    #
    # OWNERSHIP: every method below is called ONLY from the worker loop
    # (_worker), which is this destination's single asyncio task. No lock is
    # needed -- there is exactly one task mutating breaker state, and it
    # always does so serially between `await` points that matter.
    def _is_hard_outcome(self) -> bool:
        """True when the current ``_post`` outcome is a HARD (breaker-eligible)
        auth fault.

        HARD = a real HTTP 401 response, or a local auth-token-PRODUCTION
        failure (``self._auth_token_failed``, set by ``_post``'s ``headers()``
        exception path). Both are deterministic auth faults -- distinct from
        genuine network errors/timeouts/5xx/429, which must never feed the
        breaker, and distinct from 403 (authorization, often per-workspace),
        which stays a per-event skip and must not feed the breaker either.

        Only meaningful when the just-completed outcome was ``_TRANSIENT``.
        """
        return self._last_status == 401 or self._auth_token_failed

    def _update_regime_clock(self) -> None:
        """Track the start of the CURRENT sustained failing-regime.

        ``_first_hard_ts`` is the wall-clock timestamp the sustain gate in
        ``_maybe_open_breaker`` measures against. It must mark when the
        window ENTERED the failing regime -- ratio >= ``_BREAKER_HARD_RATIO``
        with >= ``_BREAKER_MIN_SAMPLES`` samples -- not the timestamp of the
        most recent hard outcome.

        Call this after every window append (hard or delivered) and
        recompute from the window's current state:

        - Currently in the failing regime: set the timestamp ONLY if it
          isn't already set (the regime just started; a hard outcome deep
          inside an ongoing regime must not push the clock forward).
        - Not in the failing regime: clear the timestamp. The regime ended
          (or never started), so there is no sustain clock running.

        This is what makes the design's "rate-over-a-window, not a streak"
        promise hold at the wall-clock gate too: a single DELIVERED outcome
        that doesn't drop the window ratio below threshold leaves the
        window (and thus this destination) still in the failing regime, so
        the sustain clock must keep running, not reset to None.
        """
        n = len(self._breaker_window)
        in_regime = n >= _BREAKER_MIN_SAMPLES and (
            sum(self._breaker_window) / n >= _BREAKER_HARD_RATIO
        )
        if in_regime:
            if self._first_hard_ts is None:
                self._first_hard_ts = time.monotonic()
        else:
            self._first_hard_ts = None

    def _breaker_record_hard(self) -> None:
        """Record a HARD outcome in the sliding window and maybe open."""
        self._breaker_window.append(True)
        self._update_regime_clock()
        self._maybe_open_breaker()

    def _breaker_record_delivered(self) -> None:
        """Record a DELIVERED outcome -- the breaker's recovery signal.

        Re-evaluates the sustained-failure regime clock (see
        ``_update_regime_clock``) rather than unconditionally clearing it --
        a lone success that leaves the window still >= the hard ratio must
        NOT reset the sustain clock, or an interleaved mostly-failing
        destination could never accumulate a continuous failing regime.

        If the breaker was OPEN, this is the successful half-open probe:
        close it, wipe the window (so the next failure streak starts
        clean), and emit exactly one recovery line (console + durable
        record). This closing path is unconditional on the ratio -- a
        successful probe always closes, regardless of window state.
        """
        self._breaker_window.append(False)
        self._update_regime_clock()
        if self._breaker_open:
            self._breaker_open = False
            self._breaker_window.clear()
            self._first_hard_ts = None
            logger.info(
                "Reconnected to %s (%s) \u2014 resuming delivery.",
                self._name,
                self._url,
            )
            self._record_forwarding_issue("breaker_close", "auto-recovered on probe success")

    def _maybe_open_breaker(self) -> None:
        """Open the breaker when the sustained hard-failure rate crosses threshold.

        Requires ALL of: enough samples in the window (``_BREAKER_MIN_SAMPLES``),
        a hard-failure ratio at/above ``_BREAKER_HARD_RATIO``, and sustained
        wall-clock time since the first hard failure of at least
        ``_BREAKER_MIN_OPEN_SECONDS``. Rate-over-a-window (not a streak) means a
        lone 200 no longer resets a half-broken destination; the wall-clock
        floor means a fast backoff curve can't trip it inside one token-rotation
        window.
        """
        if self._breaker_open:
            return
        n = len(self._breaker_window)
        if n < _BREAKER_MIN_SAMPLES:
            return
        hard_ratio = sum(self._breaker_window) / n
        if hard_ratio < _BREAKER_HARD_RATIO:
            return
        if (
            self._first_hard_ts is None
            or (time.monotonic() - self._first_hard_ts) < _BREAKER_MIN_OPEN_SECONDS
        ):
            return
        self._breaker_open = True
        self._last_probe_ts = time.monotonic()
        logger.warning(
            "%s (%s) forwarding paused after sustained auth failures (HTTP %s) \u2014 fix the"
            " credential/URL; delivery auto-resumes when it recovers (no restart needed)."
            " Events are safe in events.jsonl; replay the backlog with"
            " context-intelligence-upload.",
            self._name,
            self._url,
            self._last_status,
        )
        self._record_forwarding_issue(
            "breaker_open", "forwarding paused after sustained auth failures"
        )

    def _breaker_probe_due(self) -> bool:
        """True when OPEN and the slow-cadence probe interval has elapsed."""
        return (time.monotonic() - self._last_probe_ts) >= _BREAKER_PROBE_INTERVAL

    async def _worker(self) -> None:
        """Process events from the queue with retry-on-transient backoff.

        Holds the in-flight event in ``self._current`` during retries so the
        supervisor (Task 6) can inspect or reassert it. On ``_TRANSIENT``
        outcomes (network errors, HTTP 5xx/429/401) the SAME event is retried
        after a capped full-jitter backoff sleep; ``_consecutive_failures``
        grows the exponent each time. On ``_DELIVERED`` or ``_PERMANENT`` the
        counter resets, ``task_done()`` is called, ``_current`` is cleared, and
        the outer loop fetches the next event — order is preserved by
        construction (single worker, one in-flight event at a time).

        Unclassified exceptions (bare ``Exception``, ``TypeError``, …) are
        caught by the supervisor (Task 6): logged loudly, the poisoned in-flight
        event is dropped (``task_done()`` + clear ``_current``), and the worker
        re-enters the outer loop so subsequent events keep draining.

        ``asyncio.CancelledError`` is a ``BaseException`` (not ``Exception``) so
        it bypasses the unclassified handler. A separate handler calls
        ``task_done()`` before re-raising so that ``asyncio.Queue.join()`` in
        ``close()`` never hangs.
        """
        while True:
            event, payload_data = await self._queue.get()
            self._current = (event, payload_data)
            try:
                # --- circuit breaker gate -------------------------------
                # OWNERSHIP: only this worker task reads/mutates breaker
                # state (see the class-level note above the breaker
                # methods) -- no lock needed.
                if self._breaker_open:
                    if not self._breaker_probe_due():
                        # Already durable in events.jsonl (LoggingHandler
                        # wrote it before fan-out) -- do not dispatch while
                        # OPEN and not yet due for a probe.
                        self._queue.task_done()
                        self._current = None
                        continue
                    # Half-open PROBE: exactly one attempt, no inner retry.
                    self._last_probe_ts = time.monotonic()
                    outcome = await self._post(event, payload_data)
                    if outcome == _DELIVERED:
                        self._breaker_record_delivered()
                        self._degraded_warned = False
                        self._degraded_since = None
                    elif outcome == _TRANSIENT and self._is_hard_outcome():
                        self._breaker_record_hard()
                    # Genuinely transient/permanent while probing is
                    # inconclusive -- leave the breaker OPEN and advance;
                    # the event stays durable regardless.
                    self._consecutive_failures = 0
                    self._auth_failures = 0
                    self._queue.task_done()
                    self._current = None
                    continue

                # --- CLOSED: normal dispatch, retry-on-transient backoff
                while True:
                    outcome = await self._post(event, payload_data)
                    if outcome == _TRANSIENT:
                        self._consecutive_failures += 1
                        # Only a GENUINE, fresh 401 HTTP response is an auth failure.
                        # Timeouts/network errors clear _last_status in _post, so they
                        # can never be mistaken for a 401 here -- this is what stops a
                        # network blip from inflating the auth counter.
                        is_auth_401 = self._last_status == 401
                        if is_auth_401:
                            self._auth_failures += 1
                        if not self._degraded_warned:
                            # Cause-agnostic on purpose. At this point the worker only
                            # knows the outcome was _TRANSIENT, NOT why -- it could be a
                            # network blip OR an auth-header failure (expired `az login`).
                            # The old wording ("unreachable ... no action needed") asserted
                            # both a cause (network) and an action-verdict (none) it cannot
                            # actually know, and directly CONTRADICTED the actionable
                            # "run `az login`" warning that _post emits on the auth path.
                            # State only what is true here; the specific preceding warning
                            # (auth -> run az login; genuine 401 -> check credentials)
                            # carries any cause-specific guidance.
                            # Level: INFO, not WARNING. This is a TRANSIENT,
                            # non-actionable state change -- events stay durable in
                            # events.jsonl and delivery keeps retrying -- so it must NOT
                            # surface on the default WARNING stream a user session sees
                            # (log_level defaults to WARNING). Emitting it there is the
                            # exact noise/confusion we want to avoid. It stays in the
                            # durable/diagnostic log (visible at log_level=INFO) and
                            # pairs symmetrically with the INFO "Reconnected ... resuming
                            # delivery" recovery notice below. The LOUD, user-facing
                            # WARNINGs are reserved for actionable/terminal points:
                            # sustained auth rejection, the breaker opening ("forwarding
                            # paused"), and _PERMANENT rejects -- all below.
                            logger.info(
                                "%s delivery degraded, retrying with backoff — events"
                                " remain durable in events.jsonl.",
                                self._name,
                            )
                            self._degraded_warned = True
                            self._degraded_since = time.monotonic()
                        else:
                            logger.debug("server_dispatch_retry dest=%s", self._name)
                            # Independent of the routine per-episode INFO above:
                            # escalate loudly + durably once this regime has run
                            # past _DEGRADED_ESCALATION_SECONDS. See that
                            # constant's docstring and
                            # _maybe_escalate_sustained_failure for the
                            # real-incident rationale (a network outage never
                            # feeds the auth-only breaker and can otherwise
                            # retry silently forever).
                            self._maybe_escalate_sustained_failure()

                        is_hard = self._is_hard_outcome()
                        if is_hard:
                            # Feed the destination-level circuit breaker. This
                            # may transition CLOSED -> OPEN right here.
                            self._breaker_record_hard()
                            # Only warn about sustained per-event auth rejection
                            # while the breaker is still CLOSED -- once it opens,
                            # the single breaker_open line (in _maybe_open_breaker)
                            # is the only console output for this failure mode;
                            # go quiet.
                            if (
                                not self._breaker_open
                                and is_auth_401
                                and self._auth_failures >= self._failure_threshold
                            ):
                                now = time.monotonic()
                                if now - self._last_auth_log >= _LOG_RATE_LIMIT_SECONDS:
                                    self._last_auth_log = now
                                    logger.warning(
                                        "%s (%s) still rejecting auth (HTTP %s) after %d auth"
                                        " failures — check this destination's credentials AND"
                                        " that its URL targets the CI server (a 401 can also"
                                        " come from a misrouted URL such as an auth gateway).",
                                        self._name,
                                        self._url,
                                        self._last_status,
                                        self._auth_failures,
                                    )
                                    self._record_forwarding_issue(
                                        "auth_failure",
                                        "still rejecting auth after %d attempts"
                                        % self._auth_failures,
                                    )
                            # A deterministic hard failure (401, or a local
                            # auth-token-production failure) will never succeed
                            # on an immediate retry. Stop blocking the single
                            # worker -- and thus the whole queue -- on one doomed
                            # event: skip it (it stays durable in events.jsonl)
                            # and advance so delivery of later events continues.
                            # The destination-level circuit breaker (above) is
                            # what stops the sustained-failure hammering.
                            #
                            # _consecutive_failures resets (it only drives the
                            # backoff sleep for genuinely transient outcomes,
                            # which a hard failure never reaches). _auth_failures
                            # is DELIBERATELY NOT reset here: with same-event
                            # retries gone, a single doomed event can never
                            # reach the escalation threshold by itself anymore
                            # -- the counter must accumulate ACROSS consecutive
                            # hard-skip events instead, so the rate-limited
                            # "still rejecting auth" early-warning (below
                            # threshold, before the breaker opens) remains
                            # reachable for a destination that is 401'ing
                            # across many different events. It resets only on
                            # an actual DELIVERED/PERMANENT advance (bottom of
                            # the loop below), matching a real recovery.
                            self._consecutive_failures = 0
                            self._queue.task_done()
                            self._current = None
                            break
                        # Genuinely transient (network/5xx/429) -- never feeds
                        # the breaker; retry the same event with backoff.
                        await self._sleep_backoff()
                        continue  # retry the same event
                    # _DELIVERED or _PERMANENT — advance to next event
                    if outcome == _DELIVERED:
                        self._breaker_record_delivered()
                        if self._degraded_warned:
                            logger.info(
                                "Reconnected to %s — resuming delivery.",
                                self._name,
                            )
                            self._degraded_warned = False
                            self._degraded_since = None
                    elif outcome == _PERMANENT:
                        now = time.monotonic()
                        if now - self._last_permanent_log >= _LOG_RATE_LIMIT_SECONDS:
                            self._last_permanent_log = now
                            if self._last_status is not None and 300 <= self._last_status < 400:
                                logger.warning(
                                    "%s (%s) returned an unexpected redirect (HTTP %d)"
                                    " — destination URL likely misconfigured;"
                                    " not following redirects, event skipped.",
                                    self._name,
                                    self._url,
                                    self._last_status,
                                )
                                self._record_forwarding_issue(
                                    "permanent_reject",
                                    "unexpected redirect (HTTP %s)" % self._last_status,
                                )
                            elif self._last_status == 403:
                                logger.warning(
                                    "%s (%s) rejected event (HTTP 403) — check credentials.",
                                    self._name,
                                    self._url,
                                )
                                self._record_forwarding_issue(
                                    "permanent_reject", "rejected event (HTTP 403)"
                                )
                            elif self._last_status in (404, 410):
                                # Endpoint itself is missing/gone — a routing or deployment
                                # problem (e.g. an undeployed route behind Azure APIM), NOT a
                                # payload problem. Do not blame the payload for a 4xx that
                                # means "there is nothing here to receive it."
                                logger.warning(
                                    "%s (%s) rejected event (HTTP %d) — endpoint not found;"
                                    " verify the route is deployed and reachable behind the"
                                    " gateway (this is not a payload problem).",
                                    self._name,
                                    self._url,
                                    self._last_status,
                                )
                                self._record_forwarding_issue(
                                    "endpoint_not_found",
                                    "endpoint not found (HTTP %s)" % self._last_status,
                                )
                            elif self._last_status in (400, 413, 422):
                                # These are the ONLY statuses that genuinely mean the
                                # payload was rejected as malformed/unprocessable.
                                logger.warning(
                                    "%s (%s) rejected event (HTTP %d) — malformed event, skipped.",
                                    self._name,
                                    self._url,
                                    self._last_status,
                                )
                                self._record_forwarding_issue(
                                    "permanent_reject",
                                    "rejected event (HTTP %s) — malformed" % self._last_status,
                                )
                            else:
                                # Any other non-retryable 4xx (405, 409, 421, 451, …). The
                                # cause is NOT known here — do not assert "malformed"; that
                                # claim is only true for the enumerated payload-rejection
                                # set above. State only what is true: the server rejected it
                                # and it will not be retried.
                                logger.warning(
                                    "%s (%s) rejected event (HTTP %d) — skipped.",
                                    self._name,
                                    self._url,
                                    self._last_status,
                                )
                                self._record_forwarding_issue(
                                    "permanent_reject",
                                    "rejected event (HTTP %s)" % self._last_status,
                                )
                    self._consecutive_failures = 0
                    self._auth_failures = 0
                    self._queue.task_done()
                    self._current = None
                    break
            except asyncio.CancelledError:
                # CancelledError must propagate so close() can cancel the worker.
                # Pair task_done() with the queue.get() above before re-raising.
                self._queue.task_done()
                self._current = None
                raise
            except Exception:
                # Unclassified exception — log loudly, drop the poisoned event,
                # clear in-flight state, and re-enter the loop so the worker
                # keeps draining subsequent events (Task 6 / TB-01).
                logger.exception(
                    "worker_unclassified_exception: poisoned event dropped dest=%s event=%s",
                    self._name,
                    event,
                )
                # Reset failure counters: the poisoned event is being dropped, so
                # the next (unrelated) event must start from a clean slate — its
                # backoff must not inherit this event's failure count, and the
                # 401 auth-escalation gate must not carry over. Mirrors the reset
                # on the normal DELIVERED/PERMANENT advance path.
                self._consecutive_failures = 0
                self._auth_failures = 0
                self._queue.task_done()
                self._current = None
                # Outer while True continues — worker survives.

    async def _sleep_backoff(self) -> None:
        """Sleep for a capped exponential backoff interval.

        Computes ``cap = min(backoff_initial * 2^(consecutive_failures − 1),
        backoff_max)``.  The first retry (failures = 1) sleeps exactly
        ``backoff_initial`` (no jitter); subsequent retries double the cap up
        to ``backoff_max``.  With ``backoff_jitter=True``, the delay is chosen
        uniformly at random from ``[0, cap]`` (full-jitter pattern); without
        jitter the delay equals ``cap`` deterministically.

        Uses ``asyncio.sleep`` so the sleep is cancellable by ``close()``.
        """
        # Bound the exponent BEFORE exponentiating. ``2 ** n`` with a float base
        # raises OverflowError near n~1024, which a long-lived session can reach
        # after enough consecutive failures; that would crash the retry loop and
        # (via the worker supervisor) start silently dropping events. The result
        # is clamped to ``backoff_max`` long before the exponent matters, so this
        # changes no delivered delay — it only removes the overflow footgun.
        exponent = min(self._consecutive_failures - 1, _BACKOFF_MAX_EXPONENT)
        cap = min(
            self._backoff_initial * (2**exponent),
            self._backoff_max,
        )
        delay = random.uniform(0, cap) if self._backoff_jitter else cap
        await asyncio.sleep(delay)

    async def _post(self, event: str, data: dict[str, Any]) -> str:
        """POST one event to this destination and return a three-way outcome.

        Returns
        -------
        _DELIVERED
            HTTP status < 400, or a RuntimeError whose message contains "closed"
            (client torn down during session teardown — treated as done).
        _TRANSIENT
            Network-level httpx errors -- httpx.NetworkError (ConnectError, ReadError,
            WriteError, CloseError), httpx.TimeoutException (ConnectTimeout, ReadTimeout,
            WriteTimeout, PoolTimeout), or httpx.RemoteProtocolError -- or HTTP
            401/429/5xx — caller should retry with backoff.
        _PERMANENT
            HTTP 403, 404/410, 400/413/422, or any other 4xx — event cannot be
            delivered; caller should log loudly and skip. This is a uniform
            RETRY decision; the cause is NOT uniform — see the message-layer
            branches in _worker, which assert 403=forbidden, 404/410=endpoint
            not found/gone, 400/413/422=malformed payload, and stay
            cause-neutral for any other 4xx.

        The Authorization header is produced PER REQUEST via self._strategy.headers().
        This ensures Entra tokens are refreshed by the azure-identity SDK when they
        near expiry — long-lived dispatchers never serve stale tokens.

        Never mutates disable state. _consecutive_failures management belongs to the
        worker loop (Task 5). _last_status is set on every HTTP response so the worker
        (Task 9) can detect persistent auth failures.

        Unclassified exceptions (bare Exception, TypeError, etc.) are NOT caught here;
        they propagate so the worker supervisor (Task 6) can log loud and survive.
        """
        # Reset the auth-token-failure sentinel at the top of every attempt so
        # it can only ever reflect THIS attempt -- a stale True from a prior
        # attempt must never be inherited by an unrelated outcome (mirrors why
        # _last_status is cleared on the network-error path below).
        self._auth_token_failed = False
        # Lazy client creation — no auth header baked in; header goes on each post.
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self._connect_timeout,
                    write=self._dispatch_timeout,
                    read=self._read_timeout,
                    pool=_POOL_TIMEOUT,
                ),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )

        payload = build_payload(event, self._workspace, data, working_dir=self._working_dir)
        # Per-request header: Entra SDK returns cached token and refreshes near expiry.
        try:
            auth_headers = self._strategy.headers()
        except Exception as exc:
            # Auth-strategy failure producing the Authorization header -- e.g. an
            # expired `az login` causes EntraTokenAuth.headers() -> get_token() to
            # raise azure-identity's CredentialUnavailableError / ClientAuthenticationError
            # (see context_intelligence/auth.py, which never caches a failure so the
            # next attempt retries fresh). Catching broadly here is deliberate: this
            # try block is scoped to ONLY the single, well-defined "produce auth
            # headers" step, so any exception at this boundary means "couldn't
            # authenticate this request" -- recoverable, not a reason to lose the
            # event. Static ApiKeyAuth.headers() is a pure f-string that never
            # raises, so this path is entra-only; static behavior is unchanged.
            #
            # HONESTY ABOUT THE BROAD CATCH: because we catch Exception (not the
            # azure-specific types -- azure-identity is optional and not importable
            # here), a masked PROGRAMMING bug inside headers() (TypeError,
            # AttributeError, ...) would otherwise be silently reclassified as an
            # expired login. We therefore name the caught exception's TYPE in the
            # WARNING so such a bug is VISIBLE at the default log level, and attach
            # the full traceback at DEBUG (exc_info) for diagnosis without spamming
            # WARNING. Control flow is intentionally identical for every exception
            # type: clearing _last_status and returning _TRANSIENT is the correct,
            # council-approved recovery for "couldn't authenticate this request."
            #
            # This is NOT an HTTP response, so it carries no status code. Clear
            # _last_status to None -- if left stale (e.g. a genuine 401 from a
            # PRIOR event on this destination), the worker's
            # `is_auth_401 = self._last_status == 401` check would miscount this
            # unrelated failure toward the destination's auth-failure counter.
            # Returning _TRANSIENT keeps this on the retry-with-backoff path, so
            # a mid-session `az login` recovers delivery on the next retry.
            #
            # This IS, however, a deterministic auth fault distinct from a
            # genuine network blip: a locally-broken ability to mint a Bearer
            # token will not resolve itself on the next network attempt any
            # more than a real 401 would. Mark it HARD so the destination-level
            # circuit breaker (see _worker) can still detect a persistent
            # credential problem instead of retrying forever in silence.
            self._last_status = None
            self._auth_token_failed = True
            now = time.monotonic()
            if now - self._last_headers_error_log >= _LOG_RATE_LIMIT_SECONDS:
                self._last_headers_error_log = now
                logger.warning(
                    "%s (%s) could not produce an auth token (%s) -- run `az login` to"
                    " refresh if your session expired; retrying with backoff, events"
                    " remain durable in events.jsonl.",
                    self._name,
                    self._url,
                    type(exc).__name__,
                )
                self._record_forwarding_issue(
                    "auth_token_unavailable", "auth token production failed"
                )
                logger.debug(
                    "%s auth-header production failed: %r",
                    self._name,
                    exc,
                    exc_info=True,
                )
            return _TRANSIENT

        try:
            response = await self._client.post(
                f"{self._url}/events", json=payload, headers=auth_headers
            )
        except RuntimeError as exc:
            # Client closed during session teardown — treat as delivered (done).
            if "closed" in str(exc):
                return _DELIVERED
            raise
        except (
            httpx.TimeoutException,  # ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout
            # NetworkError covers ConnectError AND ReadError/WriteError/CloseError.
            # A connection can die mid-response just as easily as mid-connect (e.g. the
            # peer process is killed while a request is in flight) -- httpx raises
            # ReadError/WriteError for that case, not ConnectError. Narrowing this to
            # ConnectError alone silently drops the in-flight event (falls through to
            # the worker's unclassified-exception handler, which is NOT retried) instead
            # of retrying it, permanently losing an event on a mid-stream server kill.
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ):
            # A timeout / network error carries NO HTTP status. Clear the sentinel
            # so a PRIOR 401 cannot be inherited by this outcome and mis-counted as
            # an auth failure -- that inheritance is what produced false "still
            # rejecting auth (HTTP 401)" warnings for what were really network blips
            # (the counter climbed on timeouts, not on real credential rejections).
            self._last_status = None
            return _TRANSIENT
        # Bare Exception / TypeError / other unclassified exceptions propagate.

        # Record status on every HTTP response (worker uses this for 401 escalation).
        self._last_status = response.status_code

        return _classify_http_outcome(response.status_code)

    async def close(self) -> None:
        """Drain, cancel worker, close client.

        Emits a loud WARNING when shutting down with undelivered events or in a
        degraded state.  The count is honest:
        ``queued(qsize) + in-flight(0 or 1) + overflow-dropped(counter)``.
        The recovery path uses the real ``self._storage_path`` (never a
        placeholder like ``<path>``).  A clean shutdown — count 0 and not
        degraded — emits no such warning.

        The SAME shutdown condition also writes a durable
        ``shutdown_undelivered`` forwarding-diagnostics record (see
        ``_record_forwarding_issue``) carrying the honest count plus circuit
        breaker state and sustained-degraded duration. Real incident this
        closes: a console WARNING is invisible once the process that emitted
        it has exited — every session that ends mid-outage now also drops
        one durable, cross-session line into
        ``forwarding-YYYY-MM-DD.jsonl``, so a multi-day outage across many
        short-lived sessions leaves an aggregable trail even though no
        single process lives long enough to see it end-to-end.

        Drain is bounded by ``_close_drain_timeout``: ``queue.join()`` runs until
        that deadline, then the worker is cancelled regardless.  The worker's
        ``asyncio.sleep`` in ``_sleep_backoff`` is cancellation-safe, so close()
        returns promptly even when the worker is mid-backoff.

        EVERY remaining await in this method is bounded by ``_CLOSE_HARD_TIMEOUT``:
        joining the cancelled worker and closing the httpx client both use
        ``asyncio.wait`` (NOT ``wait_for``) so that even a step that misbehaves
        under cancellation -- e.g. ``AsyncClient.aclose()`` wedged on a
        half-closed CLOSE-WAIT connection, which once hung a host process for
        hours at session end -- can delay close() by at most the deadline.
        On deadline: warn, abandon, return. Delivery is best-effort and must
        NEVER block process exit.
        """
        if self._worker_task is not None:
            # Attempt bounded drain: let the worker flush what it can within the timeout.
            try:
                await asyncio.wait_for(self._queue.join(), timeout=self._close_drain_timeout)
            except asyncio.TimeoutError:
                pass  # worker will be cancelled below; undelivered count computed first

            # Compute honest undelivered count BEFORE cancelling the worker.
            # Cancellation sets self._current = None in the CancelledError handler,
            # so we must read it here to get an accurate in-flight count.
            queued = self._queue.qsize()
            in_flight = 1 if self._current is not None else 0
            dropped = self._overflow_dropped
            total = queued + in_flight + dropped

            if self._degraded_warned or total > 0:
                logger.warning(
                    "%s shutdown: %d undelivered event(s)"
                    " (queued=%d in-flight=%d overflow-dropped=%d)."
                    " Events are durable in events.jsonl."
                    " To manually upload run: context-intelligence-upload --path %s"
                    " (--server-url/--api-key come from flags or env/config; see --help)",
                    self._name,
                    total,
                    queued,
                    in_flight,
                    dropped,
                    self._storage_path,
                )
                degraded_seconds = (
                    time.monotonic() - self._degraded_since
                    if self._degraded_since is not None
                    else 0.0
                )
                self._record_forwarding_issue(
                    "shutdown_undelivered",
                    "shutdown with %d undelivered event(s) (queued=%d in_flight=%d"
                    " overflow_dropped=%d, breaker_open=%s, degraded_seconds=%.0f)"
                    % (total, queued, in_flight, dropped, self._breaker_open, degraded_seconds),
                )

            self._worker_task.cancel()
            # Bounded join via asyncio.wait, NOT wait_for: wait_for blocks until
            # the cancellation actually completes, so a worker wedged in an
            # uncancellable await would reintroduce exactly the unbounded hang
            # this removes. asyncio.wait simply returns at the deadline; the
            # abandoned task's eventual outcome is retrieved by the done-callback
            # installed in _ensure_worker.
            _done, pending = await asyncio.wait({self._worker_task}, timeout=_CLOSE_HARD_TIMEOUT)
            if pending:
                logger.warning(
                    "%s worker did not stop within %.1fs during close — abandoning it;"
                    " events remain durable in events.jsonl.",
                    self._name,
                    _CLOSE_HARD_TIMEOUT,
                )
            self._worker_task = None

        if self._client is not None and not self._client.is_closed:
            # Hand-off: null the attribute first so a re-entrant close can never
            # double-aclose the same client.
            client, self._client = self._client, None
            # aclose() has NO deadline of its own: on a half-closed (CLOSE-WAIT)
            # connection it can block forever — this is the exact call that once
            # wedged a host process for 2.7h at session end. Run it as a task and
            # bound it; on deadline, cancel and abandon (delivery is best-effort
            # and must never block process exit). The done-callback retrieves any
            # late exception — including the RuntimeError('Event loop is closed')
            # teardown race — so it is logged, swallowed, and never left as an
            # unretrieved task exception.
            close_task = asyncio.create_task(client.aclose())
            close_task.add_done_callback(
                partial(_retrieve_task_exception, context=f"{self._name} http client aclose")
            )
            _done, pending = await asyncio.wait({close_task}, timeout=_CLOSE_HARD_TIMEOUT)
            if pending:
                close_task.cancel()
                logger.warning(
                    "%s HTTP client close exceeded %.1fs (half-closed connection?) —"
                    " abandoning it; delivery is best-effort and events remain durable"
                    " in events.jsonl.",
                    self._name,
                    _CLOSE_HARD_TIMEOUT,
                )


# ---------------------------------------------------------------------------
# LoggingHandler
# ---------------------------------------------------------------------------
class LoggingHandler:
    """Always-on flat JSONL session file writer.

    Writes per-session ``events.jsonl`` and ``metadata.json`` files under
    ``base_path / project_slug / sessions / session_id / context-intelligence /``.

    HTTP dispatch fans out to the active _DestinationDispatcher list (set via
    set_dispatchers() in on_session_ready). Before dispatchers are installed,
    all events are JSONL-only.
    """

    handled_events: set[str]

    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver
        self.handled_events = set()
        self._seen_sessions: set[str] = set()
        self._workspace: str | None = getattr(resolver, "workspace", None) or None
        self._parent_id: str = getattr(resolver, "parent_id", "") or ""
        self._resolve_instance_id: str = getattr(resolver, "resolve_instance_id", "") or ""
        self._dispatchers: list[_DestinationDispatcher] = []

    async def set_dispatchers(self, dispatchers: list[_DestinationDispatcher]) -> None:
        """Install the active per-destination dispatchers (called from on_session_ready).

        Closes any previously-installed dispatchers before installing the new list,
        preventing background worker and httpx client leaks on repeated calls.
        First call is a no-op close (empty old list).
        """
        old = self._dispatchers
        self._dispatchers = dispatchers
        if old:
            await asyncio.gather(*(d.close() for d in old), return_exceptions=True)

    def _session_dir(self, session_id: str) -> Path:
        return self._resolver.session_dir(session_id)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        sanitized_data = _sanitize_for_json(data)
        try:
            session_id = sanitized_data.get("session_id")
            if not session_id:
                return HookResult(action="continue")

            session_dir = self._session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)

            # Lazy metadata init: create metadata.json on the very first
            # event we see for a given session_id, regardless of event type.
            if session_id not in self._seen_sessions:
                self._seen_sessions.add(session_id)
                self._ensure_metadata(session_dir, session_id, sanitized_data)

            if event in ("session:start", "session:fork"):
                self._enrich_metadata_from_session_init(session_dir, session_id, sanitized_data)
            elif event in ("session:end", "execution:end"):
                self._finalize_metadata(session_dir, sanitized_data)

            self._append_event(session_dir, event, sanitized_data, self._workspace)
            self._touch_last_event_at(session_dir, sanitized_data.get("timestamp", ""))
        except Exception:
            logger.warning("LoggingHandler disk write error processing %s", event, exc_info=True)

        # Fan-out to all active dispatchers — each enqueue is isolated so that
        # one dispatcher's failure does not starve the others (mirrors the
        # defensive disk-write block above).
        for dispatcher in self._dispatchers:
            try:
                dispatcher.enqueue(event, sanitized_data)
            except Exception:
                logger.warning(
                    "LoggingHandler dispatcher enqueue failed for %s",
                    event,
                    exc_info=True,
                )

        return HookResult(action="continue")

    # -- metadata read/write primitives -------------------------------------
    @staticmethod
    def _read_metadata(meta_path: Path) -> dict[str, Any] | None:
        """Read and parse metadata.json, tolerating missing/empty/corrupt files.

        Returns the parsed object, or ``None`` when the file is absent, empty,
        or not valid JSON. A ``None`` return signals the caller to rebuild
        metadata from defaults rather than raise.

        This is the guard against a file left 0-length by a previously
        interrupted write (e.g. an ``ENOSPC`` truncation while the disk was
        full): ``meta_path.exists()`` is True for such a file, but its content
        is unparseable, so an ``exists()``-only check is not sufficient.
        """
        try:
            raw = meta_path.read_text()
        except OSError:
            return None
        if not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _atomic_write_text(meta_path: Path, text: str) -> None:
        """Write ``text`` to ``meta_path`` atomically via temp file + os.replace.

        Guarantees a reader never observes a partially written or truncated
        file. On success the rename is atomic; on failure (e.g. ``ENOSPC``) the
        existing file at ``meta_path`` is left untouched rather than truncated
        to zero bytes -- which is precisely the corruption that a plain
        ``write_text`` produced when the disk filled mid-write. The temp file
        is best-effort cleaned up on failure.
        """
        tmp_path = meta_path.with_name(f"{meta_path.name}.{os.getpid()}.tmp")
        try:
            tmp_path.write_text(text)
            os.replace(tmp_path, meta_path)
        except OSError:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

    # -- metadata lifecycle -------------------------------------------------
    def _ensure_metadata(
        self,
        session_dir: Path,
        session_id: str,
        data: dict[str, Any],
    ) -> None:
        """Create initial metadata.json on first event for this session.

        Treats a missing, empty, or corrupt file as "needs creation" -- a file
        left 0-length by a prior interrupted write (e.g. ENOSPC while the disk
        was full) is thus repaired here rather than left to error on every
        future event.
        """
        meta_path = session_dir / "metadata.json"
        if self._read_metadata(meta_path) is not None:
            return

        metadata: dict[str, Any] = {
            "format": _METADATA_FORMAT,
            "version": _METADATA_VERSION,
            "session_id": session_id,
            "workspace": self._workspace or "",
            "parent_id": data.get("parent_id") or data.get("parent") or self._parent_id or "",
            "started_at": data.get("timestamp", ""),
            "last_event_at": data.get("timestamp", ""),
            "status": "running",
            "working_dir": self._resolver.working_dir,
        }
        self._atomic_write_text(meta_path, json.dumps(metadata, separators=(",", ":")))

    def _enrich_metadata_from_session_init(
        self,
        session_dir: Path,
        session_id: str,
        data: dict[str, Any],
    ) -> None:
        """Enrich metadata with fields only available in session:start/fork."""
        meta_path = session_dir / "metadata.json"
        meta = self._read_metadata(meta_path)
        if meta is None:
            # metadata.json is missing, empty, or corrupt (e.g. left 0-length by
            # a prior ENOSPC-truncated write). Rebuild from defaults rather than
            # raise, then re-apply the authoritative session-init fields below.
            meta = {
                "format": _METADATA_FORMAT,
                "version": _METADATA_VERSION,
                "session_id": session_id,
                "status": "running",
            }

        meta["format"] = _METADATA_FORMAT
        meta["version"] = _METADATA_VERSION

        # Overwrite with authoritative values from session init
        meta["parent_id"] = (
            data.get("parent_id")
            or data.get("parent")
            or self._parent_id
            or meta.get("parent_id", "")
        )
        meta["started_at"] = data.get("timestamp", "") or meta.get("started_at", "")
        meta["working_dir"] = self._resolver.working_dir or meta.get("working_dir", "")

        for field in _OPTIONAL_METADATA_FIELDS:
            value = data.get(field)
            if value:
                meta[field] = value

        self._atomic_write_text(meta_path, json.dumps(meta, separators=(",", ":")))

    def _finalize_metadata(self, session_dir: Path, data: dict[str, Any]) -> None:
        """Mark session as completed in metadata."""
        meta_path = session_dir / "metadata.json"
        meta = self._read_metadata(meta_path)
        if meta is None:
            # metadata.json is missing, empty, or corrupt (e.g. left 0-length by
            # a prior ENOSPC-truncated write). Rebuild from defaults rather than
            # raise, so the session is still marked completed below.
            meta = {
                "format": _METADATA_FORMAT,
                "version": _METADATA_VERSION,
            }

        meta["format"] = _METADATA_FORMAT
        meta["version"] = _METADATA_VERSION

        meta["status"] = data.get("status", "completed")
        meta["ended_at"] = data.get("timestamp", "")

        self._atomic_write_text(meta_path, json.dumps(meta, separators=(",", ":")))

    # -- lifecycle management ------------------------------------------------
    async def close(self) -> None:
        """Close all destination dispatchers concurrently."""
        await asyncio.gather(*(d.close() for d in self._dispatchers), return_exceptions=True)

    # -- metadata freshness update ------------------------------------------
    def _touch_last_event_at(self, session_dir: Path, timestamp: str) -> None:
        """Update last_event_at in metadata.json after each event append.

        Self-healing: a missing, empty, or corrupt metadata.json (e.g. one left
        0-length by a prior ENOSPC-truncated write while the disk was full) is
        rebuilt from best-effort defaults instead of erroring on every event.

        Best-effort: catches OSError and json.JSONDecodeError, logs a warning,
        and never raises. A failure here must never block event capture.
        """
        try:
            meta_path = session_dir / "metadata.json"
            meta = self._read_metadata(meta_path)
            if meta is None:
                # metadata.json is missing, empty, or corrupt. Rebuild a minimal
                # valid record from what this handler knows so freshness tracking
                # recovers; the session_id folder is meta_path's grandparent
                # (.../sessions/<session_id>/context-intelligence/metadata.json).
                meta = {
                    "format": _METADATA_FORMAT,
                    "version": _METADATA_VERSION,
                    "session_id": session_dir.parent.name,
                    "workspace": self._workspace or "",
                    "parent_id": self._parent_id or "",
                    "started_at": timestamp,
                    "status": "running",
                    "working_dir": self._resolver.working_dir,
                }
            meta["last_event_at"] = timestamp
            self._atomic_write_text(meta_path, json.dumps(meta, separators=(",", ":")))
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "LoggingHandler failed to update last_event_at for %s",
                session_dir,
                exc_info=True,
            )

    # -- shared JSONL appender ----------------------------------------------
    @staticmethod
    def _append_event(
        session_dir: Path, event: str, data: dict[str, Any], workspace: str | None
    ) -> None:
        record = {
            "event": event,
            "workspace": workspace or "",
            "timestamp": data.get("timestamp", ""),
            "data": data,
        }
        with (session_dir / "events.jsonl").open("a") as f:
            f.write(_canonical_json(record) + "\n")
