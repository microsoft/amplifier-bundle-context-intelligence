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
        self._queue_capacity = queue_capacity
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
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
            maxsize=queue_capacity
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self._enabled = True

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    def enqueue(self, event: str, data: dict[str, Any]) -> None:
        """Enqueue an event for dispatch. Drops if disabled or queue full."""
        if not self._enabled:
            return
        self._ensure_worker()
        try:
            self._queue.put_nowait((event, data))
        except asyncio.QueueFull:
            self._enabled = False
            logger.warning(
                "server_dispatch_queue_full: dest=%s url=%s capacity=%d event=%s"
                " dispatch disabled; local JSONL capture continues.",
                self._name,
                self._url,
                self._queue_capacity,
                event,
            )

    async def _worker(self) -> None:
        while True:
            event, payload_data = await self._queue.get()
            try:
                await self._post(event, payload_data)
            finally:
                self._queue.task_done()

    async def _post(self, event: str, data: dict[str, Any]) -> None:
        """POST one event to this destination. Circuit-breaker per-destination.

        The Authorization header is produced PER REQUEST via self._strategy.headers().
        This ensures Entra tokens are refreshed by the azure-identity SDK when they
        near expiry — long-lived dispatchers never serve stale tokens.
        """
        if not self._enabled:
            return

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

        try:
            payload = build_payload(event, self._workspace, data)
            # Per-request header: Entra SDK returns cached token and refreshes near expiry.
            auth_headers = self._strategy.headers()
            response = await self._client.post(
                f"{self._url}/events", json=payload, headers=auth_headers
            )
            response.raise_for_status()
            self._consecutive_failures = 0
        except RuntimeError as exc:
            # Client closed during session teardown — skip silently.
            if "closed" in str(exc):
                return
            raise
        except Exception:
            self._consecutive_failures += 1
            logger.debug(
                "server_dispatch_failed: attempt %d/%d event=%s dest=%s url=%s",
                self._consecutive_failures,
                self._failure_threshold,
                event,
                self._name,
                self._url,
                exc_info=True,
            )
            if self._consecutive_failures >= self._failure_threshold:
                self._enabled = False
                logger.warning(
                    "Context intelligence server unreachable after %d attempts"
                    " — dispatch disabled for this destination (dest=%s url=%s)."
                    " Local JSONL capture continues.",
                    self._consecutive_failures,
                    self._name,
                    self._url,
                )

    async def close(self) -> None:
        """Drain, cancel worker, close client."""
        if self._worker_task is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=self._close_drain_timeout)
            except asyncio.TimeoutError:
                logger.debug(
                    "server_dispatch_drain_timeout: queued events discarded during shutdown"
                    " dest=%s url=%s",
                    self._name,
                    self._url,
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
