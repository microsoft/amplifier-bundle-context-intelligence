"""Legacy hooks-logging discovery (GATE 1a): targets the legacy schema.

A candidate is any ``events.jsonl`` NOT living under a ``context-intelligence/``
directory whose head carries the legacy hooks-logging schema (``amplifier.log``,
tolerating minor/patch drift on the ``1.x`` line per DECISION D1).

Folds in council discovery-robustness findings up front rather than as a
follow-up patch:

* **D1 relaxed schema detection** -- reuses :func:`assert_supported_schema`
  (name + MAJOR version match; minor/patch drift and extra keys tolerated).
* **Non-UTF8 tolerance** -- reads with ``errors="replace"`` so a single bad
  byte cannot abort discovery of an otherwise-legacy file.
* **Scan-past-corrupt-first-line** -- :func:`_iter_records` yields
  ``(line_no, record | None)`` so a malformed/corrupt leading line does not
  hide a legitimate legacy record a few lines further down. Detection is
  bounded to the first 5 records (not lines) to keep discovery O(1)-ish per
  file even when many leading lines are corrupt.
* **Session-level unresolvable-workspace signal** -- a session whose
  ``working_dir`` cannot be determined is skipped with a warning and counted
  (``unresolved_workspace``) rather than silently dropped or aborting the run.
* **Workspace computed once per session** (hygiene I) -- ``working_dir`` /
  ``derive_workspace`` are resolved exactly once and the result is stashed in
  ``metadata["workspace"]`` for downstream consumers.
* **Stats-returning variant** -- :func:`discover_legacy` returns a
  :class:`LegacyDiscovery` with full counters; :func:`legacy_discover` is the
  thin ``FORMATS`` seam-A adapter (``discover_fn(target_path) -> list[tuple[Path, dict]]``)
  that callers plug into ``formats.FORMATS``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from .legacy_transform import (
    LegacyEventError,
    MissingTimestampError,
    SchemaVersionError,
    WorkspaceDerivationError,
    _SUPPORTED_SCHEMA_MAJOR,
    _SUPPORTED_SCHEMA_NAME,
    assert_supported_schema,
    assert_timestamp_present,
    derive_workspace,
    reassemble_event_data,
    read_working_dir,
)

if TYPE_CHECKING:
    from .formats import ParseFn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Events that mark a session as finished (not live/in-progress).
#: Mirrors migrate's classify.py so both tools agree on "done".
_TERMINAL_EVENTS: frozenset[str] = frozenset(
    {"session:end", "orchestrator:complete", "execution:end"}
)

#: Bound on how many records to inspect when sniffing the schema / hunting
#: for the first legacy record -- keeps a corrupt-first-lines file cheap to
#: scan without turning discovery into an unbounded full-file read per file.
_SNIFF_BOUND = 5


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class LegacyDiscovery:
    """Result of :func:`discover_legacy` -- sessions plus discovery stats."""

    sessions: list[tuple[Path, dict[str, Any]]] = field(default_factory=list)
    candidates_seen: int = 0
    live_skipped: int = 0
    unresolved_workspace: int = 0
    live_skipped_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Record iteration helpers
# ---------------------------------------------------------------------------


def _iter_records(path: Path) -> Iterator[tuple[int, dict[str, Any] | None]]:
    """Yield ``(line_no, record | None)`` for each non-blank line in *path*.

    Reads with ``errors="replace"`` so non-UTF8 bytes cannot abort iteration.
    Blank/whitespace-only lines are skipped (not yielded at all). A line that
    is not valid JSON, or that parses to something other than a JSON object,
    yields ``(line_no, None)`` instead of raising -- callers can keep scanning
    past a corrupt line rather than aborting on it.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            yield line_no, None
            continue
        if not isinstance(record, dict):
            yield line_no, None
            continue
        yield line_no, record


def _matches_legacy_schema(record: dict[str, Any]) -> bool:
    """Return True if *record* carries the (D1-relaxed) legacy schema.

    DECISION D1: match ``schema.name == _SUPPORTED_SCHEMA_NAME`` and the
    MAJOR version component, tolerating minor/patch drift and any extra
    schema keys -- this is a non-raising sniff (used to decide *whether*
    a file is a legacy candidate at all), so it does not call
    :func:`assert_supported_schema` (which is reserved for the stricter,
    warning-emitting validation used once a session is already selected).
    """
    schema = record.get("schema")
    if not isinstance(schema, dict):
        return False
    if schema.get("name") != _SUPPORTED_SCHEMA_NAME:
        return False
    major = str(schema.get("ver", "")).split(".", 1)[0]
    return major == _SUPPORTED_SCHEMA_MAJOR


def _is_legacy_events(path: Path) -> bool:
    """Return True if *path* is a legacy hooks-logging ``events.jsonl``.

    Bounded to the first :data:`_SNIFF_BOUND` records so a corrupt or
    unrelated leading line cannot hide a legitimate legacy record a few
    lines further down, without turning this into an unbounded scan of
    (potentially huge) unrelated files.
    """
    try:
        for i, (_, record) in enumerate(_iter_records(path)):
            if i >= _SNIFF_BOUND:
                break
            if record is not None and _matches_legacy_schema(record):
                return True
    except OSError:
        return False
    return False


def _first_legacy_record(path: Path) -> dict[str, Any] | None:
    """Return the first record in *path* that matches the legacy schema.

    Bounded to the first :data:`_SNIFF_BOUND` records, same rationale as
    :func:`_is_legacy_events`. Returns ``None`` if none is found in bounds.
    """
    for i, (_, record) in enumerate(_iter_records(path)):
        if i >= _SNIFF_BOUND:
            break
        if record is not None and _matches_legacy_schema(record):
            return record
    return None


def _is_ci_native_session(session_dir: Path) -> bool:
    """True iff the session's metadata.json declares the CI-native format.

    The metadata.json `format` field is the authoritative discriminator between
    the CI-native shape (`format == "context-intelligence"`) and the legacy
    hooks-logging shape (no such field). This is preferred over inferring from
    the `context-intelligence/` subfolder name, which is a weaker signal.
    """
    meta = session_dir / "metadata.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("format") == "context-intelligence"


def _has_terminal_event(path: Path) -> bool:
    """Return True if *path* contains any :data:`_TERMINAL_EVENTS` record.

    Scans the whole file (terminal events are typically near the end, but
    position is not guaranteed) -- unlike the schema sniff, this is not
    bounded because a live/in-progress vs. finished determination must not
    be guessed from a truncated prefix.
    """
    try:
        for _, record in _iter_records(path):
            if record is not None and record.get("event") in _TERMINAL_EVENTS:
                return True
    except OSError:
        return False
    return False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_legacy(target_path: Path) -> LegacyDiscovery:
    """Discover legacy hooks-logging sessions under *target_path*.

    A candidate is any ``events.jsonl`` whose sibling ``metadata.json`` does
    NOT declare ``format == "context-intelligence"`` (that is the CI-native
    format, not the legacy one) and whose head carries the legacy schema. The
    metadata.json ``format`` field is the authoritative discriminator --
    preferred over inferring from the ``context-intelligence/`` subfolder
    name, which is a weaker signal (see :func:`_is_ci_native_session`).

    For each candidate:

    * Live/in-progress sessions (no terminal event) are skipped with a
      stderr warning and counted in ``live_skipped``.
    * Sessions whose ``working_dir`` cannot be resolved (no metadata.json
      entry and no ``session:start`` event) are skipped with a stderr
      warning and counted in ``unresolved_workspace``.
    * Otherwise the workspace is derived exactly once (hygiene I) and
      stashed in the returned metadata's ``workspace`` key.

    Emits a stderr warning if zero candidates were discovered at all (UA-4).
    """
    sessions: list[tuple[Path, dict[str, Any]]] = []
    candidates_seen = 0
    live_skipped = 0
    unresolved_workspace = 0
    live_skipped_ids: list[str] = []

    candidate_paths = sorted(
        p for p in target_path.rglob("events.jsonl") if not _is_ci_native_session(p.parent)
    )

    for events_path in candidate_paths:
        if not _is_legacy_events(events_path):
            continue
        candidates_seen += 1

        if not _has_terminal_event(events_path):
            print(
                f"WARNING: {events_path}: session appears live/in-progress "
                "(no terminal event found), skipping",
                file=sys.stderr,
            )
            live_skipped += 1
            live_skipped_ids.append(str(events_path.parent))
            continue

        session_dir = events_path.parent
        try:
            working_dir = read_working_dir(session_dir, events_path)
            workspace = derive_workspace(working_dir)
        except (ValueError, WorkspaceDerivationError) as exc:
            print(
                f"WARNING: {events_path}: cannot resolve workspace ({exc}), skipping",
                file=sys.stderr,
            )
            unresolved_workspace += 1
            continue

        session_id = ""
        first_record = _first_legacy_record(events_path)
        if first_record is not None:
            try:
                assert_supported_schema(first_record)
                _, data = reassemble_event_data(first_record)
                assert_timestamp_present(data)
                session_id = str(data.get("session_id", ""))
            except ValueError:
                # Schema drift beyond D1 tolerance, no 'event' field, or no
                # timestamp -- fall back to the raw top-level 'session_id'
                # (a PROMOTED_KEYS field) rather than dropping the session.
                session_id = str(first_record.get("session_id", ""))

        metadata: dict[str, Any] = {
            "session_id": session_id,
            "format": "logging-hook",
            "workspace": workspace,
        }
        sessions.append((session_dir, metadata))

    if candidates_seen == 0:
        print(
            f"WARNING: no legacy hooks-logging sessions discovered under {target_path}",
            file=sys.stderr,
        )

    return LegacyDiscovery(
        sessions=sessions,
        candidates_seen=candidates_seen,
        live_skipped=live_skipped,
        unresolved_workspace=unresolved_workspace,
        live_skipped_ids=live_skipped_ids,
    )


def legacy_discover(target_path: Path) -> list[tuple[Path, dict[str, Any]]]:
    """``FORMATS`` seam-A adapter: discover legacy sessions, sessions only.

    Thin wrapper around :func:`discover_legacy` that drops the stats,
    matching the ``DiscoverFn`` shape expected by ``formats.FORMATS``.
    """
    return discover_legacy(target_path).sessions


# ---------------------------------------------------------------------------
# Parsing (GATE 1b, Seam B)
# ---------------------------------------------------------------------------


class UnknownEventError(ValueError):
    """Raised when a legacy event name cannot map to a graph :Event node.

    This is a structural, corpus-independent check (item D): a missing,
    empty, or non-string event name is unmappable. Deliberately conservative
    -- it never flags a legitimately-named event. The SEMANTIC catalog check
    (whether the name is a *known* event type) is the Phase 4 Task 8 O-5
    gate, not this one.
    """


def is_mappable_event(event: Any) -> bool:
    """Return True iff *event* is usable as an ``:Event`` label source.

    True iff ``event`` is a non-empty string (``isinstance(event, str) and
    event.strip() != ""``).
    """
    return isinstance(event, str) and event.strip() != ""


def legacy_parse_line(
    raw_line: str, session_dir: Path, metadata: dict[str, Any]
) -> tuple[str, str, dict[str, Any]] | None:
    """Parse a single legacy hooks-logging line into the ``build_payload`` triple.

    ``FORMATS`` seam-B adapter for the legacy format. Returns ``None`` for a
    blank (or whitespace-only) line. Raises ``MalformedRecordError`` (lazily
    imported from :mod:`.formats`) when the line is valid JSON but not a
    top-level object, :class:`~.legacy_transform.SchemaVersionError` on an
    unsupported schema, :class:`UnknownEventError` when the event name is
    structurally unmappable, and
    :class:`~.legacy_transform.MissingTimestampError` when the reassembled
    data has no non-empty ``timestamp``.

    Reads the pre-computed workspace from ``metadata["workspace"]``
    (hygiene I) -- never re-derives it per line; that is discovery's job.
    Raises :class:`WorkspaceDerivationError` if the metadata's workspace is
    falsy, so hand-built metadata fails loud rather than posting an empty
    workspace.
    """
    # Lazy import: avoids a module-level import cycle with .formats.
    from .formats import MalformedRecordError

    line = raw_line.strip()
    if not line:
        return None

    record = json.loads(line)
    if not isinstance(record, dict):
        raise MalformedRecordError(f"line is valid JSON but not an object: {type(record).__name__}")

    assert_supported_schema(record)
    event, data = reassemble_event_data(record)

    if not is_mappable_event(event):
        raise UnknownEventError(
            f"legacy event name is unmappable to a graph :Event node: {event!r}"
        )

    assert_timestamp_present(data)

    workspace = metadata.get("workspace")
    if not workspace:
        raise WorkspaceDerivationError(
            "metadata['workspace'] is missing/empty; legacy_parse_line reads the "
            "pre-computed workspace from discovery and never re-derives it per line"
        )

    return event, workspace, data


# ---------------------------------------------------------------------------
# Skip wrapper (Council v2 decision #3 -- ONE error contract)
# ---------------------------------------------------------------------------


class SkipLine(Exception):
    """Wrapper exception: a named legacy-parse error becomes a counted skip.

    ONE error contract (council v2 decision #3): every parse_fn-raised error
    that legitimately means "skip this line" is normalized to ``SkipLine``
    before reaching the uploader loop, so :func:`~.uploader.run_upload` only
    needs a single additional skip branch beyond the two byte-locked/Phase 2
    branches for malformed JSON and non-dict records.

    ``category`` distinguishes *why* the line was skipped:

    * ``"skip"`` (default) -- malformed/drift/missing-field:
      :class:`~.legacy_transform.SchemaVersionError`,
      :class:`~.legacy_transform.LegacyEventError`,
      :class:`~.legacy_transform.MissingTimestampError`,
      :class:`~.legacy_transform.WorkspaceDerivationError`.
    * ``"unmapped"`` -- unknown/unmappable event name
      (:class:`UnknownEventError`).
    """

    def __init__(self, reason: str, *, category: str = "skip") -> None:
        super().__init__(reason)
        self.reason = reason
        self.category = category


def make_skip_wrapped_parse() -> ParseFn:
    """Return a ``parse_fn`` wrapping :func:`legacy_parse_line` with the ONE error contract.

    Named-errors-only translation (item C) -- deliberately NO broad
    ``except Exception``:

    * ``json.JSONDecodeError`` -- re-raised unchanged (uploader's
      malformed-JSON branch, byte-locked for GATE 2).
    * :class:`~.formats.MalformedRecordError` -- re-raised unchanged
      (uploader's non-dict branch, Phase 2).
    * :class:`UnknownEventError` -- becomes ``SkipLine(category="unmapped")``.
    * :class:`~.legacy_transform.SchemaVersionError`,
      :class:`~.legacy_transform.LegacyEventError`,
      :class:`~.legacy_transform.MissingTimestampError`,
      :class:`~.legacy_transform.WorkspaceDerivationError` -- become
      ``SkipLine(category="skip")``.
    * Anything else (a genuine bug -- ``AttributeError``, ``KeyError``,
      ``TypeError``, ...) is NOT caught here and propagates to crash loud.
    """
    # Lazy import: avoids a module-level import cycle with .formats.
    from .formats import MalformedRecordError

    def parse_fn(
        raw_line: str, session_dir: Path, metadata: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]] | None:
        try:
            return legacy_parse_line(raw_line, session_dir, metadata)
        except json.JSONDecodeError:
            raise
        except MalformedRecordError:
            raise
        except UnknownEventError as exc:
            raise SkipLine(str(exc), category="unmapped") from exc
        except (
            SchemaVersionError,
            LegacyEventError,
            MissingTimestampError,
            WorkspaceDerivationError,
        ) as exc:
            raise SkipLine(str(exc), category="skip") from exc

    return parse_fn
