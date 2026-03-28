"""Core HTTP replay loop for context-intelligence upload.

Provides UploadResult, _count_lines, and run_upload for replaying
session events.jsonl files to the Context Intelligence ingestion endpoint.

CLI context: this module runs as a CLI tool, so user-facing warnings are written
to stderr via ``print(..., file=sys.stderr)`` rather than the ``logging`` module.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from amplifier_module_hook_context_intelligence.upload import build_payload

if TYPE_CHECKING:
    from amplifier_module_tool_context_intelligence_upload.progress import ProgressTracker


class UploadResult:
    """Result of a run_upload call."""

    def __init__(
        self,
        success: bool,
        sessions_uploaded: int,
        events_uploaded: int,
        error: str | None = None,
        failed_at: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.sessions_uploaded = sessions_uploaded
        self.events_uploaded = events_uploaded
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


def run_upload(
    sessions: list[tuple[Path, dict[str, Any]]],
    server_url: str,
    api_key: str,
    tracker: ProgressTracker,
) -> UploadResult:
    """Replay all events from *sessions* to the server.

    Parameters
    ----------
    sessions:
        Ordered list of ``(session_dir, metadata)`` tuples.
    server_url:
        Base URL of the Context Intelligence ingestion server.
    api_key:
        API key used in the ``Authorization: Bearer`` header.
    tracker:
        A :class:`ProgressTracker` instance that is updated after every event.

    Returns
    -------
    UploadResult
        Success result after all sessions complete, or failure result if any
        HTTP error occurs.
    """
    endpoint = f"{server_url}/events"
    timeout = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
    headers = {"Authorization": f"Bearer {api_key}"}

    total_events_uploaded = 0
    total_sessions_uploaded = 0

    with httpx.Client(headers=headers, timeout=timeout) as client:
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

                    # Parse JSON — skip malformed lines with a warning
                    try:
                        record: dict[str, Any] = json.loads(line)
                    except json.JSONDecodeError as exc:
                        print(
                            f"WARNING: malformed JSON in {events_file} "
                            f"at line {event_index}: {exc}",
                            file=sys.stderr,
                        )
                        tracker.event_sent()
                        event_index += 1
                        continue

                    event = record.get("event", "")
                    workspace = record.get("workspace") or _workspace_from_path(session_dir)
                    data: dict[str, Any] = record.get("data", {})

                    payload = build_payload(event, workspace, data)

                    try:
                        response = client.post(endpoint, json=payload)
                    except httpx.HTTPError as exc:
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
                            error=str(exc),
                            failed_at={
                                "session_id": session_id,
                                "event_index": event_index,
                                "http_status": 0,
                            },
                        )

                    if response.status_code < 200 or response.status_code >= 300:
                        body = response.text[:200].strip() if response.text else ""
                        error_msg = f"HTTP {response.status_code} from {endpoint}" + (
                            f": {body}" if body else ""
                        )
                        tracker.mark_failed(
                            session_id=session_id,
                            event_index=event_index,
                            http_status=response.status_code,
                            error=error_msg,
                        )
                        return UploadResult(
                            success=False,
                            sessions_uploaded=total_sessions_uploaded,
                            events_uploaded=total_events_uploaded,
                            error=error_msg,
                            failed_at={
                                "session_id": session_id,
                                "event_index": event_index,
                                "http_status": response.status_code,
                            },
                        )

                    tracker.event_sent()
                    total_events_uploaded += 1
                    event_index += 1

            tracker.session_completed()
            total_sessions_uploaded += 1

    tracker.mark_completed()
    return UploadResult(
        success=True,
        sessions_uploaded=total_sessions_uploaded,
        events_uploaded=total_events_uploaded,
    )
