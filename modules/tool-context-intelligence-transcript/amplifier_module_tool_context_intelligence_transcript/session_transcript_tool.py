"""SessionTranscriptTool — bounded native transcript retrieval for agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amplifier_core.models import ToolResult
from context_intelligence.native_transcript import (
    CaptureLocator,
    NativeTranscriptError,
    TranscriptRequest,
    read_native_transcript,
    render_native_transcript,
)
from context_intelligence.session_ids import (
    SessionResolutionError,
    is_safe_session_id,
    resolve_session_id,
)

_CAPTURE_RESOLVER_CAPABILITY = "context_intelligence.capture_resolver"
_HOOK_RESOLVER_CAPABILITY = "context_intelligence.hook_config_resolver"
_MAX_SESSIONS_PER_REQUEST = 3
_MAX_TOTAL_CONTENT_CHARS = 100_000


class SessionTranscriptTool:
    """Read bounded native transcripts without graph or provider-raw fallback."""

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        self._capture_resolver: Any | None = None
        self._hook_resolver: Any | None = None

    @property
    def name(self) -> str:
        return "session_transcript"

    @property
    def description(self) -> str:
        return (
            "Retrieve verbatim user and assistant messages from a native Context Intelligence "
            "capture, with role markers and periodic timestamps. Stored captures can contain "
            "sensitive content. USE WHEN the conversation itself is needed. DO NOT USE WHEN "
            "graph relationships, tool executions, or cross-session metrics are needed -- use "
            "graph_query."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": _MAX_SESSIONS_PER_REQUEST,
                    "description": (
                        "Optional full session IDs or unique prefixes (at least 8 characters). "
                        "Omit to retrieve the calling session. Use returned full IDs for pagination."
                    ),
                },
                "after_event_line": {"type": "integer", "minimum": 0, "default": 0},
                "after_event_lines": {
                    "type": "object",
                    "additionalProperties": {"type": "integer", "minimum": 0},
                    "description": (
                        "Per-session pagination cursors. Required instead of after_event_line "
                        "when requesting more than one session."
                    ),
                },
                "max_messages": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "max_content_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_TOTAL_CONTENT_CHARS,
                    "default": 50_000,
                },
                "timestamp_every_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 86400,
                    "default": 300,
                },
                "format": {"type": "string", "enum": ["text", "json"], "default": "text"},
            },
            "additionalProperties": False,
        }

    @staticmethod
    def _coerce_locator(value: Any) -> CaptureLocator:
        if isinstance(value, CaptureLocator):
            return value
        if isinstance(value, dict):
            events_path = value.get("events_path")
            metadata_path = value.get("metadata_path")
            if isinstance(events_path, str) and isinstance(metadata_path, str):
                return CaptureLocator(Path(events_path), Path(metadata_path))
        raise NativeTranscriptError(
            "capture_unavailable",
            "capture resolver did not return events_path and metadata_path",
        )

    @staticmethod
    def _find_capture_metadata(base_path: Path, session_id: str) -> list[Path]:
        """Match directory names only; do not open every capture to resolve a prefix."""
        candidates: dict[str, list[Path]] = {}
        for project_dir in base_path.iterdir():
            sessions_dir = project_dir / "sessions"
            if not sessions_dir.is_dir():
                continue
            for session_dir in sessions_dir.iterdir():
                if not session_dir.name.startswith(session_id):
                    continue
                metadata_path = session_dir / "context-intelligence" / "metadata.json"
                if metadata_path.is_file():
                    candidates.setdefault(session_dir.name, []).append(metadata_path)
        canonical_id = resolve_session_id(session_id, candidates)
        return candidates[canonical_id]

    def _resolve_locator(self, session_id: str) -> CaptureLocator:
        """Resolve through an embedding host first, then the mounted CI hook."""
        if self._capture_resolver is None:
            self._capture_resolver = self._coordinator.get_capability(_CAPTURE_RESOLVER_CAPABILITY)
        if self._capture_resolver is not None:
            resolve = getattr(self._capture_resolver, "resolve_capture", self._capture_resolver)
            if callable(resolve):
                return self._coerce_locator(resolve(session_id))

        if self._hook_resolver is None:
            self._hook_resolver = self._coordinator.get_capability(_HOOK_RESOLVER_CAPABILITY)
        session_dir = getattr(self._hook_resolver, "session_dir", None)
        if callable(session_dir):
            directory = session_dir(session_id)
            if isinstance(directory, (Path, str)):
                locator = CaptureLocator.from_session_dir(directory)
                base_path = getattr(self._hook_resolver, "base_path", None)
                if isinstance(base_path, (Path, str)):
                    try:
                        matches = self._find_capture_metadata(Path(base_path), session_id)
                    except SessionResolutionError as exc:
                        raise NativeTranscriptError(exc.code, str(exc)) from exc
                    except OSError as exc:
                        raise NativeTranscriptError(
                            "capture_unavailable", f"cannot search local captures: {exc}"
                        ) from exc
                    if len(matches) == 1:
                        return CaptureLocator(
                            events_path=matches[0].with_name("events.jsonl"),
                            metadata_path=matches[0],
                        )
                    if len(matches) > 1:
                        raise NativeTranscriptError(
                            "ambiguous_session",
                            f"session ID {session_id!r} is present in multiple capture roots",
                        )
                return locator
            raise NativeTranscriptError(
                "capture_unavailable",
                "hook capture resolver did not return a capture directory",
            )

        raise NativeTranscriptError(
            "capture_resolver_unavailable",
            "this host did not provide a Context Intelligence capture resolver; pass an explicit "
            "capture directory to the context-intelligence transcript CLI",
        )

    @staticmethod
    def _is_safe_session_id(value: object) -> bool:
        return is_safe_session_id(value)

    def _requested_sessions(self, input_data: dict[str, Any]) -> list[tuple[str, int]]:
        raw_ids = input_data.get("session_ids")
        if raw_ids is None:
            current_id = getattr(self._coordinator, "session_id", None)
            if not isinstance(current_id, str) or not self._is_safe_session_id(current_id):
                raise NativeTranscriptError(
                    "current_session_unavailable",
                    "this invocation has no safe runtime session identity; pass session_ids explicitly",
                )
            session_ids = [current_id]
        else:
            if (
                not isinstance(raw_ids, list)
                or not raw_ids
                or len(raw_ids) > _MAX_SESSIONS_PER_REQUEST
                or not all(self._is_safe_session_id(session_id) for session_id in raw_ids)
            ):
                raise NativeTranscriptError(
                    "invalid_request",
                    "session_ids must contain one to three opaque IDs without path separators or "
                    "glob metacharacters",
                )
            session_ids = raw_ids

        per_session = input_data.get("after_event_lines")
        if per_session is not None:
            if not isinstance(per_session, dict) or not all(
                self._is_safe_session_id(session_id)
                and isinstance(cursor, int)
                and not isinstance(cursor, bool)
                and cursor >= 0
                for session_id, cursor in per_session.items()
            ):
                raise NativeTranscriptError(
                    "invalid_request",
                    "after_event_lines must map safe session IDs to non-negative integer cursors",
                )
            if not set(per_session).issubset(session_ids):
                raise NativeTranscriptError(
                    "invalid_request", "after_event_lines includes an unrequested session ID"
                )
            return [(session_id, per_session.get(session_id, 0)) for session_id in session_ids]

        after_event_line = input_data.get("after_event_line", 0)
        if len(session_ids) > 1 and after_event_line:
            raise NativeTranscriptError(
                "invalid_request",
                "use after_event_lines for a paged request containing multiple sessions",
            )
        return [(session_id, after_event_line) for session_id in session_ids]

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        if not isinstance(input_data, dict):
            return ToolResult(
                success=False,
                error={"type": "invalid_request", "message": "tool input must be an object"},
            )
        try:
            output_format = input_data.get("format", "text")
            if output_format not in ("text", "json"):
                raise NativeTranscriptError("invalid_request", "format must be text or json")
            sessions = self._requested_sessions(input_data)
            max_messages = input_data.get("max_messages", 50)
            max_content_chars = input_data.get("max_content_chars", 50_000)
            timestamp_every_seconds = input_data.get("timestamp_every_seconds", 300)
            if (
                not isinstance(max_content_chars, int)
                or isinstance(max_content_chars, bool)
                or not 1 <= max_content_chars <= _MAX_TOTAL_CONTENT_CHARS
            ):
                raise NativeTranscriptError(
                    "invalid_request",
                    "max_content_chars must be an integer between 1 and "
                    f"{_MAX_TOTAL_CONTENT_CHARS}",
                )
            per_session_chars = min(max_content_chars, _MAX_TOTAL_CONTENT_CHARS // len(sessions))
            pages = [
                read_native_transcript(
                    self._resolve_locator(session_id),
                    TranscriptRequest(
                        after_event_line=cursor,
                        max_messages=max_messages,
                        max_content_chars=per_session_chars,
                        timestamp_every_seconds=timestamp_every_seconds,
                    ),
                )
                for session_id, cursor in sessions
            ]
        except NativeTranscriptError as exc:
            return ToolResult(success=False, error={"type": exc.code, "message": str(exc)})

        output: dict[str, Any] = {
            "status": "partial" if any(page.status == "partial" for page in pages) else "complete",
            "sessions": [page.as_dict() for page in pages],
            "limits": {
                "max_sessions": _MAX_SESSIONS_PER_REQUEST,
                "max_total_content_chars": _MAX_TOTAL_CONTENT_CHARS,
            },
        }
        if output_format == "text":
            output["text"] = "\n\n".join(
                render_native_transcript(page, timestamp_every_seconds=timestamp_every_seconds)
                for page in pages
            )
        return ToolResult(success=True, output=output)
