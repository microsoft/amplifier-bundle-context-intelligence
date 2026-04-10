"""context_intelligence.reconstruct — session reconstruction utilities.

Public API (imports deferred to Task 9 when implementations land):

    reconstruct_session(session_dir: Path) -> Session
        Rebuild a Session object from a raw session directory on disk.

    load_events(events_path: Path) -> list[Event]
        Parse a JSONL events file into a sequence of typed Event objects.

    extract_transcript(events: list[Event]) -> Transcript
        Distil the conversation transcript from the raw event stream.

    summarise_tools(events: list[Event]) -> ToolSummary
        Produce a summary of tool calls and results from an event stream.

All functions in this subpackage are pure transforms (Level 1) unless they
accept a Path argument, in which case they perform filesystem I/O (Level 3).
"""

from __future__ import annotations

from context_intelligence.reconstruct.discover import (
    discover_sessions,
    sessions_dir_for_project,
    workspace_slug,
)
from context_intelligence.reconstruct.events import extract_events
from context_intelligence.reconstruct.metadata import (
    build_disk_only_metadata,
    extract_metadata,
)
from context_intelligence.reconstruct.transcript import extract_transcript

__all__ = [
    "extract_events",
    "extract_transcript",
    "extract_metadata",
    "build_disk_only_metadata",
    "discover_sessions",
    "workspace_slug",
    "sessions_dir_for_project",
]
