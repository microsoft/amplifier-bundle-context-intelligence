"""context_intelligence.reconstruct.discover — session discovery from graph and disk.

Discovers sessions for a workspace by querying the context-intelligence graph
and scanning the local filesystem for sessions not yet in the graph.

Level 2 — Network I/O (queries the CI graph server via CIClient).

Extracted from prototype scripts/ci-reconstruct-sessions.py (lines 104-111, 1051-1084).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from context_intelligence.client import CIClient
from context_intelligence.config import AMPLIFIER_DIR

log = logging.getLogger("context_intelligence.reconstruct.discover")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def workspace_slug(project_dir: str) -> str:
    """Derive the workspace slug from an absolute project directory path.

    Converts the absolute path to a slug by replacing every ``/`` with ``-``.

    Examples::

        workspace_slug("/home/bkrabach/dev/attractor-dev-machine")
        # -> "-home-bkrabach-dev-attractor-dev-machine"

    Parameters
    ----------
    project_dir:
        Absolute path to the project directory.

    Returns
    -------
    str
        Slug derived from the absolute path.
    """
    return os.path.abspath(project_dir).replace("/", "-")


def sessions_dir_for_project(project_dir: str) -> Path:
    """Return the sessions directory for a project.

    Builds the path: ``AMPLIFIER_DIR / 'projects' / slug / 'sessions'``,
    where *slug* is derived via :func:`workspace_slug`.

    Parameters
    ----------
    project_dir:
        Absolute path to the project directory.

    Returns
    -------
    Path
        Path to the sessions directory for the project.
    """
    slug = workspace_slug(project_dir)
    return AMPLIFIER_DIR / "projects" / slug / "sessions"


def discover_sessions(
    client: CIClient,
    workspace: str,
    sessions_dir: Path,
) -> tuple[list[dict], list[str]]:
    """Discover all sessions for a workspace from the graph and disk.

    Queries the context-intelligence graph for all sessions in *workspace*,
    then scans *sessions_dir* for session directories not present in the graph.

    Parameters
    ----------
    client:
        Initialised :class:`~context_intelligence.client.CIClient` instance.
    workspace:
        Workspace slug to query.
    sessions_dir:
        Path to the on-disk sessions directory for the project.

    Returns
    -------
    tuple[list[dict], list[str]]
        A 2-tuple of:

        - **graph_sessions** — list of row dicts from the graph query,
          each with keys ``s.node_id``, ``s.status``, ``s.started_at``,
          ``s.ended_at``, ordered by ``s.started_at``.
        - **disk_only_ids** — list of session directory names found on disk
          but absent from the graph (subsession directories starting with
          ``0000000000000000`` are excluded).
    """
    rows = client.cypher(
        f'MATCH (s:Session) WHERE s.workspace = "{workspace}" '
        f"RETURN s.node_id, s.status, s.started_at, s.ended_at "
        f"ORDER BY s.started_at",
        workspace=workspace,
    )

    # Collect graph session IDs
    graph_ids: set[str] = set()
    for row in rows:
        sid = row.get("s.node_id", "")
        if sid:
            graph_ids.add(sid)

    # Scan disk for session directories not in the graph
    disk_only_ids: list[str] = []
    if sessions_dir.is_dir():
        for entry in sorted(sessions_dir.iterdir()):
            if entry.is_dir() and entry.name not in graph_ids:
                # Skip subsession directories (start with 0000000000000000)
                if entry.name.startswith("0000000000000000"):
                    continue
                disk_only_ids.append(entry.name)

    return rows, disk_only_ids
