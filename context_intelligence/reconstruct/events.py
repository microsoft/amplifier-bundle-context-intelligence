"""context_intelligence.reconstruct.events — event extraction from graph.

Extracts session events from the context-intelligence graph server and
reconstructs them into the events.jsonl format.

Level 2 — Network I/O (queries the CI graph server via CIClient).

Extracted from prototype scripts/ci-reconstruct-sessions.py (lines 250-501).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from context_intelligence.client import CIClient, _safe_json_loads
from context_intelligence.config import LOG_SCHEMA

log = logging.getLogger("context_intelligence.reconstruct.events")

# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------

# Matches nanosecond-precision timestamps: 2026-04-10T13:41:17.111671945+00:00
# Group 1: millisecond prefix (YYYY-MM-DDTHH:MM:SS.mmm)
# Group 2: timezone suffix (+HH:MM or Z)
_TS_NANO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3})\d+(.*)")


def _normalize_ts(ts: Any) -> Any:
    """Normalize a timestamp to millisecond precision.

    Input:  2026-04-10T13:41:17.111671945+00:00
    Output: 2026-04-10T13:41:17.111+00:00

    Non-string values (including None) are returned unchanged.
    """
    if not ts or not isinstance(ts, str):
        return ts
    m = _TS_NANO_RE.match(ts)
    if m:
        return m.group(1) + m.group(2)
    return ts


# ---------------------------------------------------------------------------
# Event line builder
# ---------------------------------------------------------------------------


def _make_event_line(
    event_type: str, data_json_str: str | None, session_id: str
) -> dict[str, Any] | None:
    """Build one events.jsonl line from a graph node data property.

    The *data_json_str* is the raw JSON string stored on the graph node.
    It contains ``timestamp``, ``session_id``, and the rest of the event payload.

    Extracts ``session_id``, ``redaction``, and ``timestamp`` from data to
    top-level fields (matching hook-logging format). Normalizes timestamp to
    millisecond precision.

    Returns ``None`` when *data_json_str* is falsy or does not parse as a dict.

    Field ordering: ts / lvl / schema / event / session_id / redaction? / data
    """
    if not data_json_str:
        return None
    data = _safe_json_loads(data_json_str)
    if not isinstance(data, dict):
        return None

    # Pop fields that belong at top level, not inside data
    ts = _normalize_ts(data.pop("timestamp", ""))
    sid = data.pop("session_id", session_id)
    redaction = data.pop("redaction", None)

    # NOTE: lvl/schema are SYNTHESIZED constants (not read from the original line) and ts is
    # millisecond-truncated — this envelope is a faithful-but-synthesized reconstruction, not byte-identical metadata.
    result: dict[str, Any] = {
        "ts": ts,
        "lvl": "INFO",
        "schema": LOG_SCHEMA,
        "event": event_type,
        "session_id": sid,
    }
    if redaction is not None:
        result["redaction"] = redaction
    result["data"] = data
    return result


def _maybe_append(
    events: list[dict[str, Any]],
    event_type: str,
    data_json_str: str | None,
    session_id: str,
) -> bool:
    """Build one event line and append it to *events* if the result is not None.

    Returns ``True`` when an event was DROPPED because its data was present but
    unparseable / not a dict (a JSON decode failure), so callers can surface the
    loss.  A falsy *data_json_str* (legitimately empty) is not counted as a drop.
    """
    ev = _make_event_line(event_type, data_json_str, session_id)
    if ev:
        events.append(ev)
        return False
    return bool(data_json_str)


# ---------------------------------------------------------------------------
# Blob resolution
# ---------------------------------------------------------------------------


def _resolve_blobs_in_value(
    value: Any,
    client: CIClient,
    blob_cache: dict[str, Any],
    stats: dict[str, int] | None = None,
) -> Any:
    """Recursively resolve ``$blob_ref`` markers anywhere in a value.

    - A dict that contains a ``$blob_ref`` key is treated as a blob reference
      and replaced with the fetched content.
    - A plain dict (no ``$blob_ref``) has each of its values recursively
      resolved; a new dict is returned.
    - A list has each element recursively resolved; a new list is returned.
    - Scalars are returned unchanged.

    If a blob fetch fails (returns ``None``) or the URI cannot be parsed, the
    original marker is left in place (fail-soft, no exception raised) and, when
    a *stats* dict is provided, its ``"unresolved"`` counter is incremented so
    callers can surface the silent loss.

    The *blob_cache* is a mutable dict shared across calls within a session
    to avoid redundant fetches.  Keys are the blob key component of the URI
    (the portion after ``ci-blob://SESSION_ID/``).
    """
    if isinstance(value, dict):
        if "$blob_ref" in value:
            uri = value["$blob_ref"]
            if isinstance(uri, str) and uri.startswith("ci-blob://"):
                parts = uri[len("ci-blob://") :].split("/", 1)
                if len(parts) == 2:
                    blob_session, blob_key = parts
                    # Cache key is blob_key only — extract_events is
                    # session-scoped so keys are unique within one call.
                    if blob_key in blob_cache:
                        return blob_cache[blob_key]
                    resolved = client.fetch_blob(blob_session, blob_key)
                    if resolved is not None:
                        blob_cache[blob_key] = resolved
                        return resolved
                    log.debug("Could not resolve blob ref: %s", uri)
            # fail-soft: return original marker unchanged, but record the loss
            if stats is not None:
                stats["unresolved"] += 1
            return value
        # Plain dict — recurse into each value
        return {k: _resolve_blobs_in_value(v, client, blob_cache, stats) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_blobs_in_value(item, client, blob_cache, stats) for item in value]
    return value


def _resolve_event_blobs(
    events: list[dict[str, Any]],
    client: CIClient,
) -> int:
    """Resolve ``$blob_ref`` values in event data in-place.

    Recursively walks the entire ``data`` dict of every event and replaces any
    ``{"$blob_ref": "ci-blob://SESSION_ID/KEY"}`` marker at any nesting depth
    with the fetched blob content.  All event types are covered — no allow-list.

    Resolved blobs are cached by key to avoid redundant fetches.  Returns the
    number of blob refs that could not be resolved (markers left in place).
    """
    blob_cache: dict[str, Any] = {}
    stats: dict[str, int] = {"unresolved": 0}

    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue

        for key in list(data.keys()):
            data[key] = _resolve_blobs_in_value(data[key], client, blob_cache, stats)

    return stats["unresolved"]


# ---------------------------------------------------------------------------
# Event extraction
# ---------------------------------------------------------------------------


def extract_events(
    client: CIClient,
    session_id: str,
    workspace: str,
    *,
    resolve_blobs: bool = False,
) -> list[dict[str, Any]]:
    """Extract all events for a session and return them sorted by timestamp.

    Uses the single source-of-truth query against the ``Event`` node type,
    filtering by ``session_id``.  All event types — including ``session:start``
    and ``session:end``, which are attached via ``SOURCED_FROM`` rather than
    ``HAS_EVENT`` — are stored as ``Event`` nodes and returned by this query.
    A ``Session``-based traversal (``HAS_EVENT``) would be lossy.

    Parameters
    ----------
    client:
        Authenticated CIClient instance.
    session_id:
        The session whose events to extract.
    workspace:
        Workspace to scope all cypher queries.
    resolve_blobs:
        When True, recursively resolve any ``$blob_ref`` markers found
        anywhere in each event's ``data`` dict, regardless of event type
        or nesting depth.

    Returns
    -------
    list[dict]
        Events sorted ascending by occurred_at / timestamp.
    """
    events: list[dict[str, Any]] = []

    # Single source-of-truth: all Event nodes for this session, ordered by
    # occurred_at.  The graph server returns them pre-sorted; we also apply a
    # secondary in-process sort on the synthesised `ts` field (millisecond
    # precision) so the final list is stable even if occurred_at has ties.
    rows = client.cypher(
        f'MATCH (e:Event {{session_id: "{session_id}"}}) '
        f"RETURN e.event_name AS event_name, e.data AS data, e.occurred_at AS occurred_at "
        f"ORDER BY e.occurred_at",
        workspace=workspace,
    )
    dropped = 0
    for row in rows:
        event_name = row.get("event_name") or "unknown"
        if _maybe_append(events, event_name, row.get("data"), session_id):
            dropped += 1

    # Stable sort on the synthesised `ts` (derived from each event's
    # `data.timestamp`, i.e. emission time, millisecond-truncated).  Python's
    # sort is stable, so the server's `occurred_at` ORDER BY is preserved as the
    # fallback ordering whenever two events share the same `ts`.
    def _sort_key(e: dict[str, Any]) -> str:
        return e.get("ts", "") or ""

    events.sort(key=_sort_key)

    # Optionally resolve blob refs in-place
    unresolved = 0
    if resolve_blobs:
        log.info("  Resolving blob references ...")
        unresolved = _resolve_event_blobs(events, client)

    # Surface silent losses (both are 0 on the happy path, so nothing logs).
    if unresolved or dropped:
        log.warning(
            "reconstruct: %d blob ref(s) left unresolved; %d event(s) dropped due to unparseable data",
            unresolved,
            dropped,
        )

    return events
