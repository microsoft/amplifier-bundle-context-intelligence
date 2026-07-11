"""Session graph: metadata discovery and topological sort.

Provides two functions for discovering Amplifier session metadata files and
ordering them in BFS topological order for upload processing.

CLI context: this module runs as a CLI tool, so user-facing warnings are written
to stderr via ``print(..., file=sys.stderr)`` rather than the ``logging`` module.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from dataclasses import dataclass
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
    - If *target_path* is a file named ``metadata.json``, read only that file
      (no directory-name restriction — the caller pointed at it explicitly).
    - Otherwise, rglob for ``metadata.json`` under *target_path*, but only
      accept files whose immediate parent directory is named
      ``context-intelligence`` (i.e. ``.../<session>/context-intelligence/metadata.json``).
      This excludes look-alike files such as ``.../<session>/artifacts/metadata.json``.

    Files that trigger ``json.JSONDecodeError`` or ``OSError`` are skipped
    silently.

    Duplicate ``session_id`` values across discovered files: the first
    occurrence encountered is kept; every subsequent duplicate is skipped
    with a warning written to stderr.

    Returns a list of ``(session_dir, metadata)`` tuples where *session_dir*
    is the directory that contains the ``metadata.json`` file.
    """
    if target_path.is_file() and target_path.name == "metadata.json":
        metadata_files: list[Path] = [target_path]
    else:
        metadata_files = [
            p for p in target_path.rglob("metadata.json") if p.parent.name == "context-intelligence"
        ]

    results: list[tuple[Path, dict[str, Any]]] = []
    seen_session_ids: set[str] = set()

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

        sid = data["session_id"]
        if sid in seen_session_ids:
            print(
                f"WARNING: duplicate session_id '{sid}' at {meta_file}, keeping first occurrence",
                file=sys.stderr,
            )
            continue
        seen_session_ids.add(sid)

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


# ---------------------------------------------------------------------------
# Scope resolution: descendants-only closure for the upload CLI
# ---------------------------------------------------------------------------


@dataclass
class UploadScope:
    """The resolved set of sessions to upload for a given ``--path``.

    Attributes:
        sessions: BFS-ordered closure, parents-before-children.
        mode: ``"single"`` (one selected root + its descendants) or
            ``"whole"`` (every root discovered under the sessions/ boundary).
        selected_root_ids: root session_id(s) the closure was computed from.
        total_discovered: count of ALL sessions discovered in the sibling set
            (the whole ``sessions/`` directory), not just the closure.
        selected_count: ``len(sessions)`` \u2014 how many are actually uploaded.
        dangling_parent_ids: parent_id(s) of the selected root(s) that are
            NOT included in the closure. The server tolerates these as benign
            self-healing placeholders (MERGE), so this is informational only.
    """

    sessions: list[tuple[Path, dict[str, Any]]]
    mode: str
    selected_root_ids: list[str]
    total_discovered: int
    selected_count: int
    dangling_parent_ids: list[str]


class ScopeError(Exception):
    """Raised when the requested upload scope cannot be resolved.

    This covers both "nothing discovered at all" and "the specific session
    the caller pointed at could not be identified" \u2014 both are fail-loud
    conditions rather than silent no-ops.
    """


def _find_sessions_dir(target_path: Path) -> Path | None:
    """Return the nearest ancestor of *target_path* (including itself) named
    ``sessions``, or ``None`` if no such ancestor exists."""
    for candidate in (target_path, *target_path.parents):
        if candidate.name == "sessions":
            return candidate
    return None


def _read_session_id(meta_file: Path) -> str | None:
    """Best-effort read of ``session_id`` from a metadata.json file.

    Returns ``None`` on any read/parse error or if the field is absent \u2014
    callers treat that as "could not resolve" and fail loud.
    """
    try:
        with meta_file.open(encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) else None


def resolve_upload_sessions(target_path: Path) -> UploadScope:
    """Resolve *target_path* into an :class:`UploadScope` \u2014 the descendants-only
    closure of sub-sessions to upload.

    Sub-sessions live as FLAT SIBLINGS under ``<sessions_dir>/<dir>/context-intelligence/metadata.json``.
    The session directory name may carry an agent suffix and is NOT
    necessarily the ``session_id`` \u2014 the id is always read from metadata.
    A root-pointed upload previously never reached its children because
    discovery only recursed downward from ``--path``. This function walks
    the whole sibling set under the nearest ``sessions/`` boundary and
    computes a proper descendants-only transitive closure from the selected
    root(s), regardless of where under that boundary *target_path* points.

    Boundary resolution:
    - If the nearest ``sessions``-named ancestor IS *target_path* itself, or
      no ``sessions``-named ancestor exists at all, every root discovered is
      selected (mode ``"whole"``).
    - Otherwise *target_path* identifies exactly one session (by directory
      or by its ``metadata.json`` file); only that session's closure is
      selected (mode ``"single"``).

    Ancestors are deliberately NOT included in the closure: the server
    tolerates an absent parent as a benign self-healing MERGE placeholder,
    so uploading strictly downward is sufficient and avoids re-uploading
    unrelated ancestor subtrees.

    Raises:
        ScopeError: if zero sessions are discovered anywhere in the sibling
            set, or (single mode only) if the selected session cannot be
            identified/read or isn't among the discovered sessions.
    """
    sessions_dir = _find_sessions_dir(target_path)
    selected_session_id: str | None = None

    if sessions_dir is not None and target_path == sessions_dir:
        mode = "whole"
        discovery_scope = sessions_dir
    elif sessions_dir is not None:
        mode = "single"
        discovery_scope = sessions_dir
        if target_path.is_file() and target_path.name == "metadata.json":
            selected_session_id = _read_session_id(target_path)
        else:
            try:
                rel = target_path.relative_to(sessions_dir)
            except ValueError:
                rel = None
            if rel is not None and rel.parts:
                candidate_meta = (
                    sessions_dir / rel.parts[0] / "context-intelligence" / "metadata.json"
                )
                selected_session_id = _read_session_id(candidate_meta)
    else:
        mode = "whole"
        discovery_scope = target_path

    discovered = _discover_sessions(discovery_scope)

    if not discovered:
        raise ScopeError(f"no context-intelligence sessions found under {target_path}")

    # Build lookup: session_id -> (session_dir, metadata)
    lookup: dict[str, tuple[Path, dict[str, Any]]] = {}
    for session_dir, meta in discovered:
        lookup[meta["session_id"]] = (session_dir, meta)

    # Build children map + root classification (same rules as discover_and_sort)
    children_map: dict[str, list[str]] = {sid: [] for sid in lookup}
    all_roots: list[str] = []

    for sid, (_, meta) in lookup.items():
        parent_id = meta.get("parent_id")

        if parent_id is None or parent_id == "":
            all_roots.append(sid)
        elif parent_id in lookup:
            children_map[parent_id].append(sid)
        else:
            print(
                f"WARNING: session '{sid}' references unknown parent '{parent_id}', "
                "promoting to root",
                file=sys.stderr,
            )
            all_roots.append(sid)

    if mode == "whole":
        selected_root_ids = sorted(all_roots)
    else:
        if selected_session_id is None or selected_session_id not in lookup:
            raise ScopeError(f"could not resolve selected session for target path: {target_path}")
        selected_root_ids = [selected_session_id]

    # Descendants-only transitive closure, BFS with a visited-set cycle guard.
    visited: set[str] = set(selected_root_ids)
    queue: deque[str] = deque(sorted(selected_root_ids))
    result: list[tuple[Path, dict[str, Any]]] = []

    while queue:
        sid = queue.popleft()
        result.append(lookup[sid])
        for child_sid in sorted(children_map.get(sid, [])):
            if child_sid in visited:
                continue
            visited.add(child_sid)
            queue.append(child_sid)

    # Dangling parents: selected roots whose parent_id exists but falls
    # outside this closure (excluded sibling, or genuinely undiscovered).
    dangling: set[str] = set()
    for sid in selected_root_ids:
        _, meta = lookup[sid]
        parent_id = meta.get("parent_id")
        if parent_id and parent_id not in visited:
            dangling.add(parent_id)

    return UploadScope(
        sessions=result,
        mode=mode,
        selected_root_ids=selected_root_ids,
        total_discovered=len(lookup),
        selected_count=len(result),
        dangling_parent_ids=sorted(dangling),
    )
