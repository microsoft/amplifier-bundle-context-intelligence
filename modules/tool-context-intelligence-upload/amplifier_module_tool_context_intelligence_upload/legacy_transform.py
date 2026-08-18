"""Legacy hooks-logging → context-intelligence transform (pure, network-free).

Moved here from amplifier-ci-migrate so the surviving upload tool owns the
single definition of "correct legacy schema". amplifier-ci-migrate imports
these functions from this module (dependency points doomed-tool → survivor).

Reverses the hooks-logging envelope and serialises with the hook's own
canonical-json so output matches the live CI hook's events.jsonl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from amplifier_module_hook_context_intelligence.config_resolver import (
    _slugify_path as _hook_slugify_path,
)
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
_SUPPORTED_SCHEMA_NAME = "amplifier.log"
_SUPPORTED_SCHEMA_VER = "1.0.0"
_SUPPORTED_SCHEMA_MAJOR = "1"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SchemaVersionError(ValueError):
    """Raised when a legacy event line has an unexpected schema version."""


class LegacyEventError(ValueError):
    """Raised when a legacy record has no 'event' field (malformed line)."""


class WorkspaceDerivationError(ValueError):
    """Raised when a workspace identifier cannot be resolved.

    ``_slugify_path``/``derive_workspace`` no longer raise this for degenerate
    *input* (empty, relative, or root path) -- they delegate directly to the
    live CI hook's own slugifier (``config_resolver._slugify_path``), which
    handles every input deterministically (including an explicit empty-input
    fallback), so there is no input that fails slug derivation any more than
    it fails for the hook itself. The class is retained because
    ``logging_hook_format`` still raises it when a session's pre-computed
    ``metadata['workspace']`` is missing/empty (a discovery-time invariant,
    unrelated to slugification), and callers on the Phase 3 upload path still
    catch it alongside the other legacy-parse errors.
    """


class MissingTimestampError(ValueError):
    """Raised when reassembled event data has no non-empty ``timestamp``.

    The live context-intelligence CI server (v6.7.0) returns HTTP 400 for
    any event lacking ``data.timestamp``. Legacy hooks-logging records carry
    the timestamp at the envelope's top level as ``ts``; ``reassemble_event_data``
    maps ``ts`` -> ``data["timestamp"]``, but a legacy record missing ``ts``
    entirely reassembles to an empty string rather than raising.

    Phase 3 of the migration (bulk upload of legacy sessions) must not let
    such events reach the CI server only to be rejected there. Callers on
    that path should catch this error and skip the offending event with a
    warning rather than aborting the whole upload.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_timestamp_present(data: dict[str, Any]) -> None:
    """Raise MissingTimestampError if *data* has no non-empty ``timestamp``.

    The CI server rejects events lacking ``data.timestamp`` with HTTP 400;
    this is a fail-loud guard for the Phase 3 upload path so such events can
    be skipped-with-warning instead of causing an opaque 400 from the server.
    """
    if not data.get("timestamp"):
        raise MissingTimestampError(
            "event data has no non-empty 'timestamp'; the CI server rejects such events with HTTP 400"
        )


def assert_supported_schema(record: dict[str, Any]) -> None:
    """Raise SchemaVersionError if *record* does not carry a supported schema.

    DECISION D1: the envelope shape is stable across the ``1.x`` line, so a
    minor/patch version bump (e.g. ``1.0.1``, ``1.2.0``) or an extra key in
    the schema dict is forward-compatible drift -- tolerated, with a warning
    printed to stderr. Fail loud only when the schema is missing/malformed,
    the schema ``name`` is unrecognised, or the MAJOR version component is
    unrecognised.
    """
    schema = record.get("schema")
    if not isinstance(schema, dict):
        raise SchemaVersionError(f"missing or malformed schema: {schema!r}")

    name = schema.get("name")
    ver = str(schema.get("ver", ""))

    if name != _SUPPORTED_SCHEMA_NAME:
        raise SchemaVersionError(
            f"Unsupported schema name {name!r}; expected {_SUPPORTED_SCHEMA_NAME!r}"
        )

    major = ver.split(".", 1)[0]
    if major != _SUPPORTED_SCHEMA_MAJOR:
        raise SchemaVersionError(
            f"Unsupported schema major version {ver!r}; expected major {_SUPPORTED_SCHEMA_MAJOR!r}"
        )

    if schema != _SUPPORTED_SCHEMA:
        print(
            f"warning: schema drift detected: {schema!r}; expected exactly {_SUPPORTED_SCHEMA!r} "
            "(tolerated: envelope shape is stable across 1.x)",
            file=sys.stderr,
        )


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
    if "event" not in legacy_record:
        raise LegacyEventError("legacy record has no 'event' field")
    return legacy_record["event"], data


def _slugify_path(path: str) -> str:
    """Convert a working_dir path to the CI workspace slug.

    Mirrors the live CI hook's *default-config* workspace derivation,
    ``HookConfigResolver._slug_from_working_dir``, which resolves the working
    directory (symlinks included) before slugifying:
    ``_hook_slugify_path(str(Path(working_dir).resolve()))``. Matching that
    branch -- rather than the bare, non-resolving
    :func:`amplifier_module_hook_context_intelligence.config_resolver._slugify_path`
    the hook uses internally -- is what makes migrated data land in the
    *exact* same workspace the live hook writes: the hook always resolves
    ``session.working_dir`` before deriving a slug from it, so a
    ``working_dir`` that traverses a symlink must be resolved here too, or
    the import lands in a different workspace than the hook wrote to.

    The empty-string case is preserved as a special case (rather than being
    resolved) because ``Path("").resolve()`` returns the current working
    directory, not "no path" -- resolving it would silently manufacture a
    bogus slug from the CWD instead of falling through to the hook's
    documented empty-input fallback (``_DEFAULT_PROJECT_SLUG``).
    """
    if not path:
        return _hook_slugify_path(path)
    return _hook_slugify_path(str(Path(path).resolve()))


def derive_workspace(working_dir: str) -> str:
    """Derive the CI workspace slug from a working directory path."""
    return _slugify_path(working_dir)


def unslug_approximate(slug: str) -> str:
    """Reconstruct an APPROXIMATE working-directory path from a workspace slug.

    Best-effort inverse of :func:`derive_workspace`: strip the leading ``-``
    and turn every remaining ``-`` back into ``/``.

    **This is lossy and it is meant to be.** ``derive_workspace`` maps every
    ``/`` to ``-``, so a literal hyphen inside a directory name is
    indistinguishable from a path separator: ``/home/user/my-app`` and
    ``/home/user/my/app`` slug identically. There is no way to tell them
    apart from the slug alone.

    It is therefore a RARE, deep fallback used only when a session has a
    ``workspace`` slug but no recorded ``working_dir``. Virtually every
    session records its real ``working_dir`` (CI-native always; legacy since
    the discovery change), so the exact path is normally available and wins.

    Returns ``""`` when nothing is derivable -- an empty slug, the hook's
    ``"default"`` empty-input sentinel, or a degenerate ``"-"``. Callers
    treat ``""`` as "no working dir derivable" and must not drop the session.
    """
    if not slug or slug == "default":
        return ""
    body = slug.removeprefix("-")
    if not body:
        return ""
    return "/" + body.replace("-", "/")


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
