"""LoggingHandler — always-on flat JSONL session file writer.

Zero dependency on graph infrastructure — no nodes, edges, cursors, or stores.
Writes per-session events.jsonl and metadata.json files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
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
_DEFAULT_CLOSE_DRAIN_TIMEOUT = 0.5
_DEFAULT_BACKOFF_INITIAL = 1.0
_DEFAULT_BACKOFF_MAX = 30.0
_DEFAULT_BACKOFF_JITTER = True
_METADATA_FORMAT = "context-intelligence"
_METADATA_VERSION = "1.0.0"
_CONNECT_TIMEOUT = 0.5
_READ_TIMEOUT = 3.0
_POOL_TIMEOUT = 0.5
#: Minimum seconds between repeated overflow or permanent-skip log warnings.
_LOG_RATE_LIMIT_SECONDS = 60.0

# ---------------------------------------------------------------------------
# _post outcome constants (Task 4)
# ---------------------------------------------------------------------------
#: Returned by _post when the event was successfully delivered (HTTP < 400).
_DELIVERED: str = "delivered"
#: Returned by _post when delivery should be retried with backoff (network
#: errors, HTTP 5xx, HTTP 429, HTTP 401).
_TRANSIENT: str = "transient"
#: Returned by _post when the event must be skipped permanently (HTTP 4xx
#: other than 401/429, i.e. malformed or forbidden).
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
    - ``_PERMANENT``:  403, 400, 413, 422, and any other 4xx (loud skip)
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
    # 403, 400, 413, 422, and any other 4xx
    return _PERMANENT


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
        dispatch_timeout: float,
        failure_threshold: int,
        queue_capacity: int,
        close_drain_timeout: float,
        auth_mode: str = "static",
        auth_resource: str = "",
        backoff_initial: float = _DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = _DEFAULT_BACKOFF_MAX,
        backoff_jitter: bool = _DEFAULT_BACKOFF_JITTER,
        storage_path: str | Path = "",
    ) -> None:
        from context_intelligence.auth import AuthStrategy, build_auth_strategy  # noqa: PLC0415

        self._name = name
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._workspace = workspace
        self._dispatch_timeout = dispatch_timeout
        self._failure_threshold = failure_threshold
        self._close_drain_timeout = close_drain_timeout
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._backoff_jitter = backoff_jitter
        self._storage_path = storage_path
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
        self._current: tuple[str, dict[str, Any]] | None = None  # in-flight held event
        self._overflow_dropped = 0
        self._auth_failures = 0
        self._last_status: int | None = None
        self._last_overflow_log = 0.0
        self._last_permanent_log = 0.0
        self._last_auth_log = 0.0

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

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
                while True:
                    outcome = await self._post(event, payload_data)
                    if outcome == _TRANSIENT:
                        self._consecutive_failures += 1
                        if self._last_status == 401:
                            self._auth_failures += 1
                        if not self._degraded_warned:
                            logger.warning(
                                "%s unreachable, retrying with backoff — events still"
                                " captured locally in events.jsonl, no action needed.",
                                self._name,
                            )
                            self._degraded_warned = True
                        else:
                            logger.debug("server_dispatch_retry dest=%s", self._name)
                        if self._auth_failures >= self._failure_threshold:
                            now = time.monotonic()
                            if now - self._last_auth_log >= _LOG_RATE_LIMIT_SECONDS:
                                self._last_auth_log = now
                                logger.warning(
                                    "%s still rejecting auth (HTTP 401) after %d attempts"
                                    " — this looks like an auth problem, not a network blip."
                                    " Check credentials.",
                                    self._name,
                                    self._auth_failures,
                                )
                        await self._sleep_backoff()
                        continue  # retry the same event
                    # _DELIVERED or _PERMANENT — advance to next event
                    if outcome == _DELIVERED and self._degraded_warned:
                        logger.info(
                            "Reconnected to %s — resuming delivery.",
                            self._name,
                        )
                        self._degraded_warned = False
                    elif outcome == _PERMANENT:
                        now = time.monotonic()
                        if now - self._last_permanent_log >= _LOG_RATE_LIMIT_SECONDS:
                            self._last_permanent_log = now
                            if self._last_status is not None and 300 <= self._last_status < 400:
                                logger.warning(
                                    "%s returned an unexpected redirect (HTTP %d)"
                                    " — destination URL likely misconfigured;"
                                    " not following redirects, event skipped.",
                                    self._name,
                                    self._last_status,
                                )
                            elif self._last_status == 403:
                                logger.warning(
                                    "%s rejected event (HTTP 403) — check credentials.",
                                    self._name,
                                )
                            else:
                                logger.warning(
                                    "%s rejected event (HTTP %d) — malformed event, skipped.",
                                    self._name,
                                    self._last_status,
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
        cap = min(
            self._backoff_initial * (2 ** (self._consecutive_failures - 1)),
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
            Network-level httpx errors (ConnectError, ConnectTimeout, ReadTimeout,
            PoolTimeout, RemoteProtocolError) or HTTP 401/429/5xx — caller should
            retry with backoff.
        _PERMANENT
            HTTP 403 or any other 4xx (400, 413, 422, …) — event cannot be
            delivered; caller should log loudly and skip.

        The Authorization header is produced PER REQUEST via self._strategy.headers().
        This ensures Entra tokens are refreshed by the azure-identity SDK when they
        near expiry — long-lived dispatchers never serve stale tokens.

        Never mutates disable state. _consecutive_failures management belongs to the
        worker loop (Task 5). _last_status is set on every HTTP response so the worker
        (Task 9) can detect persistent auth failures.

        Unclassified exceptions (bare Exception, TypeError, etc.) are NOT caught here;
        they propagate so the worker supervisor (Task 6) can log loud and survive.
        """
        # Lazy client creation — no auth header baked in; header goes on each post.
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=_CONNECT_TIMEOUT,
                    write=self._dispatch_timeout,
                    read=_READ_TIMEOUT,
                    pool=_POOL_TIMEOUT,
                ),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )

        payload = build_payload(event, self._workspace, data)
        # Per-request header: Entra SDK returns cached token and refreshes near expiry.
        auth_headers = self._strategy.headers()

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
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        ):
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

        Drain is bounded by ``_close_drain_timeout``: ``queue.join()`` runs until
        that deadline, then the worker is cancelled regardless.  The worker's
        ``asyncio.sleep`` in ``_sleep_backoff`` is cancellation-safe, so close()
        returns promptly even when the worker is mid-backoff.
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

            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()


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

        # Fan-out to all active dispatchers (each self-gates via _enabled).
        for dispatcher in self._dispatchers:
            dispatcher.enqueue(event, sanitized_data)

        return HookResult(action="continue")

    # -- metadata lifecycle -------------------------------------------------
    def _ensure_metadata(
        self,
        session_dir: Path,
        session_id: str,
        data: dict[str, Any],
    ) -> None:
        """Create initial metadata.json on first event for this session."""
        meta_path = session_dir / "metadata.json"
        if meta_path.exists():
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
        meta_path.write_text(json.dumps(metadata, separators=(",", ":")))

    def _enrich_metadata_from_session_init(
        self,
        session_dir: Path,
        session_id: str,
        data: dict[str, Any],
    ) -> None:
        """Enrich metadata with fields only available in session:start/fork."""
        meta_path = session_dir / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            # defensive: should already exist from _ensure_metadata; this branch
            # is unreachable in normal flow but guards against unexpected race conditions.
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

        meta_path.write_text(json.dumps(meta, separators=(",", ":")))

    def _finalize_metadata(self, session_dir: Path, data: dict[str, Any]) -> None:
        """Mark session as completed in metadata."""
        meta_path = session_dir / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            # defensive: should already exist from _ensure_metadata; this branch
            # is unreachable in normal flow but guards against unexpected race conditions.
            meta = {
                "format": _METADATA_FORMAT,
                "version": _METADATA_VERSION,
            }

        meta["format"] = _METADATA_FORMAT
        meta["version"] = _METADATA_VERSION

        meta["status"] = data.get("status", "completed")
        meta["ended_at"] = data.get("timestamp", "")

        meta_path.write_text(json.dumps(meta, separators=(",", ":")))

    # -- lifecycle management ------------------------------------------------
    async def close(self) -> None:
        """Close all destination dispatchers concurrently."""
        await asyncio.gather(*(d.close() for d in self._dispatchers), return_exceptions=True)

    # -- metadata freshness update ------------------------------------------
    def _touch_last_event_at(self, session_dir: Path, timestamp: str) -> None:
        """Update last_event_at in metadata.json after each event append.

        Best-effort: catches OSError and json.JSONDecodeError, logs a warning,
        and never raises. A failure here must never block event capture.
        """
        try:
            meta_path = session_dir / "metadata.json"
            meta = json.loads(meta_path.read_text())
            meta["last_event_at"] = timestamp
            meta_path.write_text(json.dumps(meta, separators=(",", ":")))
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
