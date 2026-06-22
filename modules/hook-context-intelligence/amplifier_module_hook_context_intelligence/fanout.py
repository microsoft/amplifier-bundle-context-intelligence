"""Fan-out selection — normalize the match key and select active destinations.

The hook NEVER computes the match key from event payloads. It is derived ONCE
from the session.working_dir capability (C2: working_dir ONLY, no project_slug
fallback). Matching uses pathspec gitwildmatch (gitignore) semantics so '**'
recurses (D6); exclude wins over include, per-destination (D7/S3).
"""

from __future__ import annotations

from pathlib import Path

import pathspec

from .config_resolver import Destination


def normalize_match_key(working_dir: str) -> str:
    """Normalize working_dir to an absolute, ~-expanded, symlink-resolved POSIX path (D8).

    The key carries a TRAILING SLASH because ``working_dir`` is always a directory.
    Representing it as a directory is what makes pathspec apply real ``.gitignore``
    *directory* semantics: a directory pattern (``foo``, ``foo/``, ``**/foo``,
    ``**/foo/``) then matches the directory itself AND everything beneath it, exactly
    as it would in a ``.gitignore`` file, while ``foo/**`` keeps its standard
    "contents only" meaning. Without the trailing slash pathspec cannot tell the
    path is a directory, so ``foo/`` silently fails to match and ``**/foo/**`` misses
    the directory root — which would silently misroute a session started from that
    directory (e.g. ``cd client-x && amplifier``).

    Raises:
        ValueError: if working_dir is empty/None (C2 fail-loud — never fall back).
    """
    if not working_dir:
        raise ValueError("session.working_dir is empty; cannot compute fan-out match key")
    resolved = Path(working_dir).expanduser().resolve().as_posix()
    return resolved if resolved.endswith("/") else resolved + "/"


def _matches(patterns: tuple[str, ...], key: str) -> bool:
    if not patterns:
        return False
    # Use "gitignore" (GitIgnoreSpecPattern) — the successor to the deprecated "gitwildmatch"
    # alias; semantics are identical for the patterns the design uses.
    spec = pathspec.PathSpec.from_lines("gitignore", patterns)
    return spec.match_file(key)


def destination_is_active(dest: Destination, match_key: str) -> bool:
    """Active iff key matches an include AND not an exclude (exclude wins, S3)."""
    if not _matches(dest.include, match_key):
        return False
    if _matches(dest.exclude, match_key):
        return False
    return True


def select_active(destinations: dict[str, Destination], match_key: str) -> dict[str, Destination]:
    """Return the subset of destinations active for match_key (fan-out, D4)."""
    return {
        name: dest for name, dest in destinations.items() if destination_is_active(dest, match_key)
    }
