"""Shared post-discovery session filter (thin glue over the hook's own helpers).

After a format's ``discover_fn`` returns ``list[(session_dir, metadata)]``,
this module decides which of those sessions the SELECTED destination should
receive. It reuses the live hook's capture-time helpers verbatim --
``fanout.normalize_match_key`` + ``fanout.destination_is_active`` -- so an
upload-time include/exclude decision is identical to the decision the hook
made when the session was captured. The matching rules are never
reimplemented here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amplifier_module_hook_context_intelligence.config_resolver import Destination
from amplifier_module_hook_context_intelligence.fanout import (
    destination_is_active,
    normalize_match_key,
)

from .legacy_transform import unslug_approximate


def default_scan_root() -> Path:
    """Return the default auto-discovery root: ``~/.amplifier/projects``.

    This is the app-cli project root the live hook writes under. Phase 3 uses
    it as the discovery target when ``--path`` is omitted (the zero-arg
    gesture). Pure path computation -- the directory need not exist.
    """
    return Path.home() / ".amplifier" / "projects"


def resolve_session_working_dir(
    session_dir: Path,
    metadata: dict[str, Any],
    path_fallback: str | Path | None,
) -> str | None:
    """Return the working directory to match this session against, or ``None``.

    Precedence -- the SESSION'S OWN recorded working directory always wins:

    1. ``metadata["working_dir"]`` -- the exact recorded path. CI-native
       sessions always carry it; legacy sessions carry it since discovery
       started surfacing it.
    2. ``unslug_approximate(metadata["workspace"])`` -- an APPROXIMATE path
       reconstructed from the lossy slug, for the rare session that has a
       slug but no recorded path.
    3. *path_fallback* (the ``--path`` value) -- LAST RESORT only.
    4. ``None`` -- nothing derivable. Callers must INCLUDE such a session
       rather than silently dropping it.

    Why ``--path`` is never the primary discriminator: it may point at a
    backup or copy folder that does not represent where the session actually
    ran, so matching include/exclude against it would filter the wrong way.
    ``--path`` scopes WHICH files are discovered; the recorded working dir
    decides matching.

    *session_dir* is accepted for call-site symmetry and future diagnostics;
    the decision is made purely from *metadata* and *path_fallback*.
    """
    _ = session_dir

    recorded = metadata.get("working_dir")
    if isinstance(recorded, str) and recorded:
        return recorded

    slug = metadata.get("workspace")
    if isinstance(slug, str) and slug:
        approximate = unslug_approximate(slug)
        if approximate:
            return approximate

    if path_fallback:
        return str(path_fallback)

    return None


def filter_sessions(
    sessions: list[tuple[Path, dict[str, Any]]],
    destination: Destination,
    path_fallback: str | Path | None,
) -> tuple[list[tuple[Path, dict[str, Any]]], int]:
    """Keep the sessions *destination* should receive; count the ones it should not.

    Returns ``(kept_sessions, filtered_out_count)``. Input order and tuple
    identity are preserved for the kept sessions.

    Each session's working directory is resolved by
    :func:`resolve_session_working_dir` (recorded dir first, ``--path`` last),
    then normalized and matched with the hook's OWN capture-time helpers, so
    the include/exclude decision here is identical to the one the hook made
    when the session was captured.

    A session whose working directory cannot be derived (or cannot be
    normalized) is INCLUDED, never silently dropped: an undecidable session is
    surfaced to the user rather than disappearing.
    """
    kept: list[tuple[Path, dict[str, Any]]] = []
    filtered_out = 0

    for session_dir, metadata in sessions:
        working_dir = resolve_session_working_dir(session_dir, metadata, path_fallback)
        if not working_dir:
            # Undecidable -> include rather than drop.
            kept.append((session_dir, metadata))
            continue

        try:
            match_key = normalize_match_key(working_dir)
        except (ValueError, OSError):
            # Un-normalizable path -> include rather than drop.
            kept.append((session_dir, metadata))
            continue

        if destination_is_active(destination, match_key):
            kept.append((session_dir, metadata))
        else:
            filtered_out += 1

    return kept, filtered_out
