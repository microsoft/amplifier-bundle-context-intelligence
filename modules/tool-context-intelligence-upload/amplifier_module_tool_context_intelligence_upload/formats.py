"""Format dispatch table for session discovery and event-line parsing.

Provides ``FORMATS``, a plain module-level dict mapping a ``--format`` value
to a ``(discover_fn, parse_fn)`` tuple. There is deliberately no strategy
interface/class here (council v2 decision #1) — callables plus type aliases
are sufficient.

Seam A — ``discover_fn(target_path: Path) -> list[tuple[Path, dict]]``
    Returns ``(session_dir, metadata)`` pairs in BFS order.

Seam B — ``parse_fn(raw_line: str, session_dir: Path, metadata: dict) ->
    tuple[event, workspace, data] | None``
    Returns the triple :func:`build_payload` consumes, or ``None`` for a
    blank line. MAY raise (e.g. ``json.JSONDecodeError``) on a malformed
    line — the uploader loop owns the skip-with-warning policy for that.

The default entry, ``'context-intelligence'``, reproduces today's inline
discovery and parsing behavior verbatim.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .logging_hook_format import legacy_discover, make_skip_wrapped_parse
from .session_graph import resolve_upload_sessions
from .uploader import _workspace_from_path

DiscoverFn = Callable[[Path], list[tuple[Path, dict[str, Any]]]]
ParseFn = Callable[[str, Path, dict[str, Any]], tuple[str, str, dict[str, Any]] | None]


class MalformedRecordError(ValueError):
    """Raised when a line is valid JSON but not the shape a record must be.

    Covers two cases (tester-breaker TB-1 / TB-15): the top-level record is
    valid JSON but not an object (e.g. ``null``, ``42``, ``[1, 2, 3]``, or a
    bare string), or the record's ``data`` field is present but not an
    object. Both are counted skips at the uploader loop, never an abort.
    """


def ci_discover(target_path: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Discover context-intelligence sessions under *target_path*.

    Wraps :func:`resolve_upload_sessions` and returns its BFS-ordered
    ``sessions`` list of ``(session_dir, metadata)`` pairs.
    """
    return resolve_upload_sessions(target_path).sessions


def ci_parse_line(
    raw_line: str, session_dir: Path, metadata: dict[str, Any]
) -> tuple[str, str, dict[str, Any]] | None:
    """Parse a single ``events.jsonl`` line into the ``build_payload`` triple.

    Returns ``None`` for a blank (or whitespace-only) line. Raises
    ``json.JSONDecodeError`` on malformed JSON, or ``MalformedRecordError``
    when the line is valid JSON but not the shape a record must be (a
    non-dict top-level record, or a non-dict ``data`` field) — the uploader
    loop catches both and applies the skip-with-warning policy, exactly as
    the current inline loop does.
    """
    line = raw_line.strip()
    if not line:
        return None

    record = json.loads(line)

    if not isinstance(record, dict):
        raise MalformedRecordError(f"line is valid JSON but not an object: {type(record).__name__}")

    event = record.get("event", "")
    workspace = (
        record.get("workspace") or metadata.get("workspace") or _workspace_from_path(session_dir)
    )
    data = record.get("data", {})

    if not isinstance(data, dict):
        raise MalformedRecordError(f"'data' is not an object: {type(data).__name__}")

    return (event, workspace, data)


FORMATS: dict[str, tuple[DiscoverFn, ParseFn]] = {
    "context-intelligence": (ci_discover, ci_parse_line),
    "logging-hook": (legacy_discover, make_skip_wrapped_parse()),
}
