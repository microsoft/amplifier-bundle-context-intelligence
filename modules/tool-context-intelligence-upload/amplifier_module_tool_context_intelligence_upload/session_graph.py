"""Session graph: metadata discovery and topological sort.

Provides two functions for discovering Amplifier session metadata files and
ordering them in BFS topological order for upload processing.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path
from typing import Any


def _discover_sessions(target_path: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Walk target_path and collect valid context-intelligence session metadata.

    Inclusion rules:
    - ``format == 'context-intelligence'`` → process the file
    - ``format`` missing or any other value → skip silently

    Field handling:
    - ``session_id`` present → use it
    - ``session_id`` missing → skip with a warning written to stderr
    - ``parent_id`` present and non-empty → child session
    - ``parent_id`` present and empty string → root session
    - ``parent_id`` missing entirely → treat as root (not an error)

    Path selection:
    - If *target_path* is a file named ``metadata.json``, read only that file.
    - Otherwise, rglob for ``metadata.json`` under *target_path*.

    Files that trigger ``json.JSONDecodeError`` or ``OSError`` are skipped
    silently.

    Returns a list of ``(session_dir, metadata)`` tuples where *session_dir*
    is the directory that contains the ``metadata.json`` file.
    """
    if target_path.is_file() and target_path.name == "metadata.json":
        metadata_files: list[Path] = [target_path]
    else:
        metadata_files = list(target_path.rglob("metadata.json"))

    results: list[tuple[Path, dict[str, Any]]] = []

    for meta_file in metadata_files:
        # Parse JSON — skip on any read or parse error
        try:
            with meta_file.open(encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        # Filter by format — skip silently if not context-intelligence
        if data.get("format") != "context-intelligence":
            continue

        # Require session_id — warn and skip if absent
        if "session_id" not in data:
            print(
                f"WARNING: {meta_file}: missing 'session_id', skipping",
                file=sys.stderr,
            )
            continue

        session_dir = meta_file.parent
        results.append((session_dir, data))

    return results


def discover_and_sort(target_path: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Discover sessions under *target_path* and return them in BFS order.

    Algorithm:
    1. Call :func:`_discover_sessions` to collect ``(session_dir, metadata)``
       pairs.
    2. Build a lookup table ``session_id → (session_dir, metadata)``.
    3. Classify each session:
       - ``parent_id`` absent or empty string → root
       - ``parent_id`` non-empty and present in lookup → child
       - ``parent_id`` non-empty but **not** in lookup → orphan; promoted to
         root with a warning written to stderr
    4. Sort root-level sessions (including promoted orphans) alphabetically by
       ``session_id``.
    5. Perform a BFS traversal starting from those sorted roots.  Children of
       each node are themselves sorted alphabetically before being enqueued to
       keep the output deterministic.

    Returns an ordered list of ``(session_dir, metadata)`` tuples.
    """
    sessions = _discover_sessions(target_path)

    if not sessions:
        return []

    # Build lookup: session_id -> (session_dir, metadata)
    lookup: dict[str, tuple[Path, dict[str, Any]]] = {}
    for session_dir, meta in sessions:
        lookup[meta["session_id"]] = (session_dir, meta)

    # Build children map: parent session_id -> list of child session_ids
    children_map: dict[str, list[str]] = {sid: [] for sid in lookup}
    roots: list[str] = []

    for sid, (_, meta) in lookup.items():
        parent_id = meta.get("parent_id")

        if parent_id is None or parent_id == "":
            # Root: no parent or empty parent_id
            roots.append(sid)
        elif parent_id in lookup:
            # Known parent → register as child
            children_map[parent_id].append(sid)
        else:
            # Orphan: parent referenced but not discovered → promote to root
            print(
                f"WARNING: session '{sid}' references unknown parent '{parent_id}', "
                "promoting to root",
                file=sys.stderr,
            )
            roots.append(sid)

    # BFS traversal — roots sorted alphabetically, children sorted alphabetically
    roots.sort()
    result: list[tuple[Path, dict[str, Any]]] = []
    queue: deque[str] = deque(roots)

    while queue:
        sid = queue.popleft()
        result.append(lookup[sid])
        for child_sid in sorted(children_map.get(sid, [])):
            queue.append(child_sid)

    return result
