"""LoggingHandler — always-on flat JSONL session file writer.

Zero dependency on graph infrastructure — no nodes, edges, cursors, or stores.
Writes per-session events.jsonl and metadata.json files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from amplifier_core.models import HookResult

logger = logging.getLogger(__name__)

_OPTIONAL_METADATA_FIELDS = ("agent_name", "parallel_group_id", "recipe_name", "recipe_step")


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

    def _session_dir(self, session_id: str) -> Path:
        return self._resolver.session_dir(session_id)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        try:
            session_id = data.get("session_id")
            if not session_id:
                return HookResult(action="continue")

            session_dir = self._session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)

            # Lazy metadata init: create metadata.json on the very first
            # event we see for a given session_id, regardless of event type.
            if session_id not in self._seen_sessions:
                self._seen_sessions.add(session_id)
                self._ensure_metadata(session_dir, session_id, data)

            if event in ("session:start", "session:fork"):
                self._enrich_metadata_from_session_init(session_dir, session_id, data)
            elif event in ("session:end", "execution:end"):
                self._finalize_metadata(session_dir, data)

            self._append_event(session_dir, event, data)
        except Exception:
            logger.exception("LoggingHandler error processing %s", event)

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
            "session_id": session_id,
            "parent_id": data.get("parent_id") or data.get("parent") or "",
            "started_at": data.get("timestamp", ""),
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
            meta = {"session_id": session_id, "status": "running"}

        # Overwrite with authoritative values from session init
        meta["parent_id"] = data.get("parent_id") or data.get("parent") or meta.get("parent_id", "")
        meta["started_at"] = data.get("timestamp", "") or meta.get("started_at", "")
        meta["working_dir"] = data.get("working_dir", "") or meta.get("working_dir", "")

        for field in _OPTIONAL_METADATA_FIELDS:
            value = data.get(field)
            if value:
                meta[field] = value

        meta_path.write_text(json.dumps(meta, separators=(",", ":")))

    @staticmethod
    def _finalize_metadata(session_dir: Path, data: dict[str, Any]) -> None:
        """Mark session as completed in metadata."""
        meta_path = session_dir / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            meta = {}

        meta["status"] = data.get("status", "completed")
        meta["ended_at"] = data.get("timestamp", "")

        meta_path.write_text(json.dumps(meta, separators=(",", ":")))

    # -- shared JSONL appender ----------------------------------------------
    @staticmethod
    def _append_event(session_dir: Path, event: str, data: dict[str, Any]) -> None:
        record = {
            "event": event,
            "timestamp": data.get("timestamp", ""),
            "data": _sanitize_for_json(data),
        }
        with (session_dir / "events.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
