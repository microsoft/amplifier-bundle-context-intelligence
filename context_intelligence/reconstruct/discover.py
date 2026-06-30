"""context_intelligence.reconstruct.discover — session discovery from graph and disk.

Discovers sessions for a workspace by querying the context-intelligence graph
and scanning the local filesystem for sessions not yet in the graph.

Level 2 — Network I/O (queries the context-intelligence graph server via CIClient).

Extracted from prototype scripts/ci-reconstruct-sessions.py (lines 104-111, 1051-1084).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from context_intelligence.client import CIClient
from context_intelligence.config import (
    capture_paths_under_sessions_dir,
    context_intelligence_base_path,
)

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

    Builds the path: ``context_intelligence_base_path() / slug / 'sessions'``,
    where *slug* is derived via :func:`workspace_slug`.

    The base path honours ``AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH`` when
    set (and guarantees an absolute result via the canonicalizer); falls back
    to :data:`~context_intelligence.config.DEFAULT_BASE_PATH`.

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
    return context_intelligence_base_path() / slug / "sessions"


@dataclass(frozen=True)
class DiskScanResult:
    """Tagged result from a disk scan — prevents silent misreading of absent roots.

    The two distinct fail-loud states ride the **return value**, not only the
    log, so callers must branch on :attr:`root_exists` rather than treating an
    empty :attr:`disk_only_ids` as "success".

    Attributes
    ----------
    root:
        The ``sessions/`` directory that was (or was attempted to be) scanned.
    root_exists:
        ``True`` when ``root`` existed at scan time; ``False`` when the directory
        was absent or was a typo'd / missing relocated root.  A ``False`` value
        means **the scan was impossible** — it is NOT equivalent to
        "found zero captures".
    disk_only_ids:
        Session IDs present on disk (via the canonical
        ``events.jsonl`` marker) but absent from the graph.
        Computed as ``candidate_ids − graph_ids``.
        Empty list when ``root_exists`` is ``False``.
    candidate_ids:
        The full capture-candidate set — all session IDs that have an
        ``events.jsonl`` file under ``root``, regardless of graph membership.
        Subsessions (directories whose name begins with ``0000000000000000``)
        ARE included; the ``events.jsonl`` marker is the sole discriminator.
        Empty list when ``root_exists`` is ``False``.
    """

    root: Path
    root_exists: bool
    disk_only_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)


def discover_sessions(
    client: CIClient,
    workspace: str,
    sessions_dir: Path,
) -> tuple[list[dict], DiskScanResult]:
    """Discover all sessions for a workspace from the graph and disk.

    Queries the context-intelligence graph for all sessions in *workspace*,
    then scans *sessions_dir* for captures (``events.jsonl`` files) not present
    in the graph.

    The canonical capture definition (§D.1) is
    ``<sessions_dir>/<session_id>/context-intelligence/events.jsonl`` — a
    **fixed-shape** glob, **not** recursive.  Subsessions (flat siblings whose
    name begins with ``0000000000000000``) **are** included; they are real
    captures that were silently dropped by the old ``0000…`` skip.

    Two distinct fail-loud states ride the **return value**:

    - ``root_exists=False`` — the sessions directory was absent or a typo'd
      relocated root.  ``disk_only_ids`` and ``candidate_ids`` are both empty.
      This is **not** the same as "found zero captures" and the caller MUST
      branch on it.
    - ``root_exists=True, candidate_ids=[]`` — the directory exists but contains
      no qualifying ``events.jsonl`` captures.

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
    tuple[list[dict], DiskScanResult]
        A 2-tuple of:

        - **graph_sessions** — list of row dicts from the graph query,
          each with keys ``s.node_id``, ``s.status``, ``s.started_at``,
          ``s.ended_at``, ordered by ``s.started_at``.
        - **scan** — :class:`DiskScanResult` with tagged disk-scan outcome.
          Callers must branch on ``scan.root_exists`` before interpreting
          ``scan.disk_only_ids``.
    """
    rows = client.cypher(
        f'MATCH (s:Session) WHERE s.workspace = "{workspace}" '
        f"RETURN s.node_id, s.status, s.started_at, s.ended_at "
        f"ORDER BY s.started_at",
        workspace=workspace,
    )

    # Collect graph session IDs.
    graph_ids: set[str] = set()
    for row in rows:
        sid = row.get("s.node_id", "")
        if sid:
            graph_ids.add(sid)

    # --- Absent-root guard (§D.3 FIX 4) ------------------------------------
    if not sessions_dir.is_dir():
        log.warning(
            "context_intelligence root does not exist: %s",
            sessions_dir,
        )
        return rows, DiskScanResult(
            root=sessions_dir,
            root_exists=False,
        )

    # --- Shared capture-candidate set (§D.1) --------------------------------
    # Fixed-shape glob keyed on events.jsonl — the writer's real output.
    # Subsessions are flat siblings under sessions/ and ARE counted.
    # The events.jsonl marker excludes bare dirs AND Amplifier-core's
    # sessions/<id>/metadata.json (no context-intelligence/ segment).
    capture_paths = capture_paths_under_sessions_dir(sessions_dir)
    candidate_ids: list[str] = [p.parent.parent.name for p in capture_paths]
    disk_only_ids: list[str] = [sid for sid in candidate_ids if sid not in graph_ids]

    if not candidate_ids:
        log.warning(
            "looked in %s, found 0 context_intelligence captures",
            sessions_dir,
        )

    return rows, DiskScanResult(
        root=sessions_dir,
        root_exists=True,
        disk_only_ids=disk_only_ids,
        candidate_ids=candidate_ids,
    )
