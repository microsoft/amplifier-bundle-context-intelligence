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
    ``base_path / project_slug / sessions / session_id /``.
    """

    handled_events: set[str]

    def __init__(self, base_path: str | Path, project_slug: str) -> None:
        self.base_path = Path(base_path).expanduser()
        self.project_slug = project_slug
        self.handled_events = set()

    def _session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        try:
            session_id = data.get("session_id")
            if not session_id:
                return HookResult(action="continue")

            if event in ("session:start", "session:fork"):
                await self._handle_session_init(event, session_id, data)
            elif event == "session:end":
                await self._handle_session_end(session_id, data)
            else:
                await self._handle_regular_event(event, session_id, data)
        except Exception:
            logger.exception("LoggingHandler error processing %s", event)

        return HookResult(action="continue")

    # -- session init (start / fork) ----------------------------------------
    async def _handle_session_init(self, event: str, session_id: str, data: dict[str, Any]) -> None:
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        parent_id = data.get("parent_id") or data.get("parent") or ""
        timestamp = data.get("timestamp", "")

        metadata: dict[str, Any] = {
            "session_id": session_id,
            "parent_id": parent_id,
            "started_at": timestamp,
            "status": "running",
            "working_dir": data.get("working_dir", ""),
        }

        for field in _OPTIONAL_METADATA_FIELDS:
            value = data.get(field)
            if value:
                metadata[field] = value

        (session_dir / "metadata.json").write_text(json.dumps(metadata, separators=(",", ":")))

        self._append_event(session_dir, event, data)

    # -- session end --------------------------------------------------------
    async def _handle_session_end(self, session_id: str, data: dict[str, Any]) -> None:
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        self._append_event(session_dir, "session:end", data)

        meta_path = session_dir / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            meta = {}

        meta["status"] = data.get("status", "completed")
        meta["ended_at"] = data.get("timestamp", "")

        meta_path.write_text(json.dumps(meta, separators=(",", ":")))

    # -- regular events -----------------------------------------------------
    async def _handle_regular_event(
        self, event: str, session_id: str, data: dict[str, Any]
    ) -> None:
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        self._append_event(session_dir, event, data)

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
