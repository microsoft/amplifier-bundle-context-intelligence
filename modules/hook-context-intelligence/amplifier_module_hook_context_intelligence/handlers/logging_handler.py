"""LoggingHandler — always-on flat JSONL session file writer.

Zero dependency on graph infrastructure — no nodes, edges, cursors, or stores.
Writes per-session events.jsonl and metadata.json files.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
# LoggingHandler
# ---------------------------------------------------------------------------
class LoggingHandler:
    """Always-on flat JSONL session file writer.

    Writes per-session ``events.jsonl`` and ``metadata.json`` files under
    ``base_path / project_slug / sessions / session_id / context-intelligence /``.
    """

    handled_events: set[str]

    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver
        self.handled_events = set()
        self._seen_sessions: set[str] = set()
        self._server_url: str | None = (
            getattr(resolver, "context_intelligence_server_url", None) or None
        )
        self._api_key: str | None = getattr(resolver, "context_intelligence_api_key", None) or None
        self._workspace: str | None = getattr(resolver, "workspace", None) or None
        self._parent_id: str = getattr(resolver, "parent_id", "") or ""
        self._resolve_instance_id: str = getattr(resolver, "resolve_instance_id", "") or ""
        self._client: httpx.AsyncClient | None = None
        self._dispatch_timeout: float = getattr(resolver, "dispatch_timeout", 10.0)
        self._consecutive_failures: int = 0
        self._dispatch_enabled: bool = True
        if not self._server_url:
            self._dispatch_enabled = False
        elif not self._api_key:
            self._dispatch_enabled = False
            logger.debug(
                "context_intelligence: server URL is configured but api_key is missing — "
                "HTTP dispatch disabled. Set context_intelligence_api_key in your bundle config."
            )
        self._failure_threshold: int = getattr(resolver, "dispatch_failure_threshold", 3)
        self._dispatch_queue_capacity: int = getattr(
            resolver, "dispatch_queue_capacity", _DEFAULT_DISPATCH_QUEUE_CAPACITY
        )
        self._close_drain_timeout: float = getattr(
            resolver, "close_drain_timeout", _DEFAULT_CLOSE_DRAIN_TIMEOUT
        )
        self._dispatch_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
            maxsize=self._dispatch_queue_capacity
        )
        self._dispatch_worker_task: asyncio.Task[None] | None = None

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

        if self._server_url and self._dispatch_enabled:
            self._enqueue_dispatch(event, sanitized_data)

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
            "working_dir": data.get("working_dir", ""),
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
        meta["working_dir"] = data.get("working_dir", "") or meta.get("working_dir", "")

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
        """Drain queued dispatch work briefly and close the HTTP client.

        Called from the hook cleanup path. Waits briefly for queued
        dispatches to complete, then cancels the worker and closes the underlying
        ``httpx.AsyncClient`` so connections are released cleanly.
        """
        worker = self._dispatch_worker_task
        if worker is not None:
            try:
                await asyncio.wait_for(
                    self._dispatch_queue.join(), timeout=self._close_drain_timeout
                )
            except asyncio.TimeoutError:
                logger.debug(
                    "server_dispatch_drain_timeout: queued events discarded during shutdown url=%s",
                    self._server_url,
                )
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            self._dispatch_worker_task = None

        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _ensure_dispatch_worker(self) -> None:
        if self._dispatch_worker_task is None or self._dispatch_worker_task.done():
            self._dispatch_worker_task = asyncio.create_task(self._dispatch_worker())

    def _enqueue_dispatch(self, event: str, data: dict[str, Any]) -> None:
        if not self._dispatch_enabled:
            return

        self._ensure_dispatch_worker()
        try:
            self._dispatch_queue.put_nowait((event, data))
        except asyncio.QueueFull:
            self._dispatch_enabled = False
            logger.debug(
                "server_dispatch_queue_full: capacity=%d event=%s url=%s dispatch disabled;"
                " local JSONL capture continues.",
                self._dispatch_queue_capacity,
                event,
                self._server_url,
            )

    async def _dispatch_worker(self) -> None:
        while True:
            event, payload_data = await self._dispatch_queue.get()
            try:
                await self._dispatch_to_server(event, payload_data)
            finally:
                self._dispatch_queue.task_done()

    # -- server dispatch (fire-and-forget) ----------------------------------
    async def _dispatch_to_server(self, event: str, data: dict[str, Any]) -> None:
        """Fire-and-forget POST to the configured server URL.

        JSONL writing is the durable record. HTTP dispatch is best-effort and
        runs behind a single worker so server slowness never blocks the hook.
        Failures are caught and logged at DEBUG level only — the remote server
        is optional, so failures must not pollute the user's terminal.
        Uses a persistent client (lazy-created) with a circuit breaker.
        """
        if not self._dispatch_enabled:
            return

        # Lazy client creation (or recreation if a prior close left it stale)
        if self._client is None or self._client.is_closed:
            client_kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(
                    connect=_CONNECT_TIMEOUT,
                    write=self._dispatch_timeout,
                    read=_READ_TIMEOUT,
                    pool=_POOL_TIMEOUT,
                ),
                "limits": httpx.Limits(max_connections=1, max_keepalive_connections=1),
            }
            if self._api_key:
                client_kwargs["headers"] = {"Authorization": f"Bearer {self._api_key}"}
            client = httpx.AsyncClient(**client_kwargs)
            self._client = client
        else:
            client = self._client

        try:
            payload = build_payload(event, self._workspace, data)
            response = await client.post(f"{self._server_url}/events", json=payload)
            response.raise_for_status()
            self._consecutive_failures = 0
        except RuntimeError as exc:
            # Client closed during session teardown -- skip silently.
            # These are the last events of a dying session; nothing to retry.
            if "closed" in str(exc):
                return
            raise
        except Exception:
            self._consecutive_failures += 1
            logger.debug(
                "server_dispatch_failed: attempt %d/%d event=%s url=%s",
                self._consecutive_failures,
                self._failure_threshold,
                event,
                self._server_url,
                exc_info=True,
            )
            if self._consecutive_failures >= self._failure_threshold:
                self._dispatch_enabled = False
                logger.debug(
                    "Context intelligence server unreachable after %d attempts"
                    " — dispatch disabled for this session. Local JSONL capture continues.",
                    self._consecutive_failures,
                )

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
