"""context_intelligence.reconstruct — session reconstruction utilities.

Public API:

    extract_events(client, session_id) -> list[dict]
        Fetch raw event records for a session from the CI graph (Level 2).

    extract_transcript(client, session_id) -> list[dict]
        Reconstruct the ordered conversation transcript for a session from
        the CI graph, merging text chunks and tool call/result pairs (Level 2).

    extract_metadata(client, session_id, session_data=None) -> dict
        Extract rich session metadata (model, bundle, timing, turn counts)
        from the CI graph, falling back to disk when graph data is absent (Level 2).

    build_disk_only_metadata(session_dir) -> dict
        Build best-effort session metadata purely from local disk artefacts —
        no network calls required (Level 1/3 filesystem I/O).

    discover_sessions(client, workspace=None) -> list[dict]
        List sessions available in the CI graph, optionally filtered by
        workspace slug (Level 2).

    workspace_slug(project_path) -> str
        Derive the workspace identifier slug from a project directory path
        (Level 1 pure transform).

    sessions_dir_for_project(project_path) -> Path
        Return the Amplifier sessions directory for the given project path
        (Level 1 pure transform).
"""

from __future__ import annotations

from context_intelligence.reconstruct.discover import (
    DiskScanResult,
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
    "DiskScanResult",
    "discover_sessions",
    "workspace_slug",
    "sessions_dir_for_project",
]
