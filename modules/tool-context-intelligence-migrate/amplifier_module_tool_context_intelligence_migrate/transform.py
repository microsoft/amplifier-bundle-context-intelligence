"""Transform legacy hooks-logging events.jsonl into CI format.

Reverses the hooks-logging envelope and serialises with the hook's own
canonical-json so output matches the live CI hook's events.jsonl.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from amplifier_module_hook_context_intelligence.upload import (
    _canonical_json,
    _compute_idempotency_key,
)

# ---------------------------------------------------------------------------
# Constants (mirrored from logging_handler.py)
# ---------------------------------------------------------------------------

#: Keys that hooks-logging promotes to the top-level record envelope.
PROMOTED_KEYS: tuple[str, ...] = (
    "redaction",
    "status",
    "duration_ms",
    "module",
    "component",
    "error",
    "request_id",
    "span_id",
    "parent_span_id",
    "session_id",
)

#: Optional metadata fields (in emission order).
_OPTIONAL_METADATA_FIELDS: tuple[str, ...] = (
    "agent_name",
    "parallel_group_id",
    "recipe_name",
    "recipe_step",
)

_SUPPORTED_SCHEMA: dict[str, str] = {"name": "amplifier.log", "ver": "1.0.0"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SchemaVersionError(ValueError):
    """Raised when a legacy event line has an unexpected schema version."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_supported_schema(record: dict[str, Any]) -> None:
    """Raise SchemaVersionError if *record* does not carry the expected schema."""
    schema = record.get("schema", {})
    if schema != _SUPPORTED_SCHEMA:
        raise SchemaVersionError(f"Unsupported schema {schema!r}; expected {_SUPPORTED_SCHEMA!r}")


def reassemble_event_data(legacy_record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Reverse the hooks-logging envelope.

    Returns ``(event_name, data)`` where *data* is the full CI event-data dict
    (the same dict that would have been passed to ``_append_event`` in the hook):

    * Start with ``legacy_record["data"]`` (the remainder after promotion).
    * Re-inject each PROMOTED_KEYS field present at the top level of the record.
    * Map ``legacy_record["ts"]`` → ``data["timestamp"]``.
    """
    data: dict[str, Any] = dict(legacy_record.get("data", {}))
    for k in PROMOTED_KEYS:
        if k in legacy_record:
            data[k] = legacy_record[k]
    data["timestamp"] = legacy_record.get("ts", "")
    return legacy_record["event"], data


def _slugify_path(path: str) -> str:
    """Convert an absolute path to the CI workspace slug.

    ``os.path.abspath(path).replace("/", "-")``
    (handles macOS/Linux; the absolute path has a leading "/",
    so the slug always starts with "-".)
    """
    return os.path.abspath(path).replace("/", "-")


def derive_workspace(working_dir: str) -> str:
    """Derive the CI workspace slug from a working directory path."""
    return _slugify_path(working_dir)


def read_working_dir(session_dir: Path, legacy_events: Path) -> str:
    """Return the working directory for a session.

    Lookup order:
    1. ``session_dir/metadata.json``  ``working_dir`` key (if non-empty).
    2. First ``session:start`` record in *legacy_events* ``data.working_dir`` (if non-empty).
    3. ``ValueError`` — loud failure.
    """
    # 1. CLI metadata.json
    meta_path = session_dir / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            wd = meta.get("working_dir", "")
            if wd:
                return wd
        except (OSError, json.JSONDecodeError):
            pass

    # 2. Legacy events.jsonl session:start
    if legacy_events.exists():
        try:
            for line in legacy_events.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    assert_supported_schema(rec)
                    if rec.get("event") == "session:start":
                        _, data = reassemble_event_data(rec)
                        wd = data.get("working_dir", "")
                        if wd:
                            return wd
                except (json.JSONDecodeError, SchemaVersionError):
                    continue
        except OSError:
            pass

    raise ValueError(
        f"Cannot determine working_dir for session in {session_dir}: "
        "no working_dir in metadata.json and no session:start event in legacy events.jsonl"
    )


# ---------------------------------------------------------------------------
# Public/private wrappers matching the SPEC test API
# ---------------------------------------------------------------------------


def _reassemble_data(rec: dict[str, Any]) -> dict[str, Any]:
    """Return just the reconstructed data dict from a legacy record.

    Convenience wrapper around :func:`reassemble_event_data` that discards
    the event name and returns only the data dict.  Drops hooks-logging
    artifacts (``lvl``, ``schema``, ``event``) if they happen to appear in
    ``rec["data"]``.
    """
    _, data = reassemble_event_data(rec)
    # Safety pop: hooks-logging artifacts must not bleed into data
    for _artifact in ("lvl", "schema", "event"):
        data.pop(_artifact, None)
    return data


def _transform_line(line: str, *, workspace: str) -> str:
    """Transform one raw legacy JSON line into a CI-format JSON string.

    Validates schema version, reassembles the original event data, and
    serialises using the hook's canonical-json function.

    Raises :exc:`SchemaVersionError` on unsupported schema.
    """
    record: dict[str, Any] = json.loads(line)
    assert_supported_schema(record)
    event_name, data = reassemble_event_data(record)

    ci_record: dict[str, Any] = {
        "event": event_name,
        "workspace": workspace or "",
        "timestamp": data.get("timestamp", ""),
        "data": data,
    }
    return _canonical_json(ci_record)


# ---------------------------------------------------------------------------
# Metadata builder (replays the hook's lifecycle over the legacy file)
# ---------------------------------------------------------------------------


def build_metadata(
    legacy_events: Path,
    *,
    workspace: str,
    working_dir: str,
) -> dict[str, Any]:
    """Replay the CI hook's metadata lifecycle over *legacy_events*.

    Returns the final metadata dict in the exact insertion-order the hook
    would produce, suitable for ``json.dumps(meta, separators=(",", ":"))``
    (no sort_keys — insertion order is part of the bytes contract).
    """
    lines = [
        line for line in legacy_events.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not lines:
        raise ValueError(f"Legacy events file is empty: {legacy_events}")

    # Initialise from the first event (mirrors _ensure_metadata)
    first_record = json.loads(lines[0])
    assert_supported_schema(first_record)
    _, first_data = reassemble_event_data(first_record)

    meta: dict[str, Any] = {
        "format": "context-intelligence",
        "version": "1.0.0",
        "session_id": first_data.get("session_id", ""),
        "workspace": workspace,
        "parent_id": first_data.get("parent_id") or first_data.get("parent") or "",
        "started_at": first_data.get("timestamp", ""),
        "last_event_at": first_data.get("timestamp", ""),
        "status": "running",
        "working_dir": working_dir,
    }

    # Replay all events
    for line in lines:
        record = json.loads(line)
        assert_supported_schema(record)
        event_name, data = reassemble_event_data(record)

        # Mirrors _touch_last_event_at (reassigns existing key — no order change)
        meta["last_event_at"] = data.get("timestamp", "")

        if event_name in ("session:start", "session:fork"):
            # Mirrors _enrich_metadata_from_session_init
            meta["format"] = "context-intelligence"
            meta["version"] = "1.0.0"
            meta["parent_id"] = (
                data.get("parent_id") or data.get("parent") or meta.get("parent_id", "")
            )
            meta["started_at"] = data.get("timestamp", "") or meta.get("started_at", "")
            meta["working_dir"] = data.get("working_dir", "") or meta.get("working_dir", "")
            for field in _OPTIONAL_METADATA_FIELDS:
                value = data.get(field)
                if value:
                    meta[field] = value  # NEW key appended at end first time seen

        elif event_name in ("session:end", "execution:end"):
            # Mirrors _finalize_metadata
            meta["format"] = "context-intelligence"
            meta["version"] = "1.0.0"
            meta["status"] = data.get("status", "completed")
            meta["ended_at"] = data.get("timestamp", "")  # NEW key appended at end

    return meta


# ---------------------------------------------------------------------------
# Core transform
# ---------------------------------------------------------------------------


def transform_session(
    legacy_events: Path,
    ci_dir: Path,
    *,
    workspace: str | None = None,
    working_dir: str | None = None,
    session_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Convert *legacy_events* to CI format and write into *ci_dir*.

    If *workspace* or *working_dir* is None they are auto-derived:
    - *working_dir*: read from ``session_dir/metadata.json`` or session:start event.
    - *workspace*: ``derive_workspace(working_dir)``.

    Returns (ci_events_path, ci_metadata_path).
    """
    # Derive working_dir / workspace if not provided
    if working_dir is None:
        if session_dir is None:
            session_dir = legacy_events.parent
        working_dir = read_working_dir(session_dir, legacy_events)

    if workspace is None:
        workspace = derive_workspace(working_dir)

    ci_dir.mkdir(parents=True, exist_ok=True)

    ci_events_path = ci_dir / "events.jsonl"
    ci_metadata_path = ci_dir / "metadata.json"

    # --- Build events.jsonl ---
    lines_out: list[str] = []
    for line in legacy_events.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        assert_supported_schema(record)
        event_name, data = reassemble_event_data(record)

        # Mirrors LoggingHandler._append_event
        ci_record: dict[str, Any] = {
            "event": event_name,
            "workspace": workspace or "",
            "timestamp": data.get("timestamp", ""),
            "data": data,
        }
        lines_out.append(_canonical_json(ci_record))

    ci_events_path.write_text("\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8")

    # --- Build metadata.json ---
    meta = build_metadata(legacy_events, workspace=workspace, working_dir=working_dir)
    ci_metadata_path.write_text(
        json.dumps(meta, separators=(",", ":")),
        encoding="utf-8",
    )

    return ci_events_path, ci_metadata_path


# ---------------------------------------------------------------------------
# Superset check
# ---------------------------------------------------------------------------


def is_content_superset(legacy_events: Path, ci_events: Path) -> bool:
    """Return True iff every legacy event is represented in *ci_events*.

    Identity = the hook's idempotency key, computed over (event, workspace, data)
    for each side.  Returns ``legacy_keys <= ci_keys``.

    The workspace is read from the first CI record (it was embedded there by
    ``transform_session``).
    """
    # Read CI file: collect keys and workspace
    ci_keys: set[str] = set()
    workspace = ""
    for line in ci_events.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        event_name = rec["event"]
        ws: str = rec.get("workspace", "")
        if not workspace and ws:
            workspace = ws
        ci_keys.add(_compute_idempotency_key(event_name, ws, rec["data"]))

    # Read legacy file: compute keys using the derived workspace.
    # assert_supported_schema is called here (as on the main transform path) so
    # an unknown schema version fails loud on the superset path too.
    legacy_keys: set[str] = set()
    for line in legacy_events.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        assert_supported_schema(rec)
        event_name, data = reassemble_event_data(rec)
        legacy_keys.add(_compute_idempotency_key(event_name, workspace, data))

    return legacy_keys <= ci_keys
