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
) -> None:
    """Build one event line and append it to *events* if the result is not None."""
    ev = _make_event_line(event_type, data_json_str, session_id)
    if ev:
        events.append(ev)


# ---------------------------------------------------------------------------
# Blob resolution
# ---------------------------------------------------------------------------


def _resolve_event_blobs(
    events: list[dict[str, Any]],
    client: CIClient,
    session_id: str,
) -> None:
    """Resolve ``$blob_ref`` values in event data in-place.

    For ``llm:request`` / ``llm:response``: resolves ``data.raw``.
    For ``tool:post``: resolves ``data.result``.

    Resolved blobs are cached by key to avoid redundant fetches.
    """
    blob_cache: dict[str, Any] = {}

    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue

        event_type = event.get("event", "")

        # Determine which field to resolve based on event type
        if event_type in ("llm:request", "llm:response"):
            field = "raw"
        elif event_type == "tool:post":
            field = "result"
        else:
            continue

        value = data.get(field)
        if not isinstance(value, dict) or "$blob_ref" not in value:
            continue

        uri = value["$blob_ref"]
        if not isinstance(uri, str) or not uri.startswith("ci-blob://"):
            continue

        parts = uri[len("ci-blob://") :].split("/", 1)
        if len(parts) != 2:
            continue

        blob_session, blob_key = parts

        # Cache key is blob_key only (not blob_session/blob_key) because
        # extract_events is session-scoped, so a session's blob keys are unique
        # within a single invocation and cannot collide with themselves.
        if blob_key in blob_cache:
            data[field] = blob_cache[blob_key]
            continue

        # Fetch and cache
        resolved = client.fetch_blob(blob_session, blob_key)
        if resolved is not None:
            blob_cache[blob_key] = resolved
            data[field] = resolved
        else:
            log.debug("Could not resolve blob ref: %s", uri)


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

    Queries 7+ graph node types:
    - Session (session:start, session:end)
    - Subsession (session:start, session:end)
    - OrchestratorRun (execution:start, execution:end, orchestrator:complete)
    - PromptStep (prompt:submit)
    - AssistantStep (provider:request, llm:request, llm:response)
    - ToolExecution non-delegate (tool:pre, tool:post)
    - ToolExecution delegate (tool:pre, tool:post, delegate:agent_spawned, delegate:agent_completed)
    - Event (generic events: prompt:complete, session:resume, etc.)

    Parameters
    ----------
    client:
        Authenticated CIClient instance.
    session_id:
        The session whose events to extract.
    workspace:
        Workspace to scope all cypher queries.
    resolve_blobs:
        When True, resolve ``$blob_ref`` values in-place for llm:request,
        llm:response (data.raw), and tool:post (data.result).

    Returns
    -------
    list[dict]
        Events sorted ascending by timestamp.
    """
    events: list[dict[str, Any]] = []

    # ── Session-level events ────────────────────────────────────────────────
    rows = client.cypher(
        f'MATCH (s:Session {{node_id: "{session_id}"}}) RETURN s.data, s.data_session_end',
        workspace=workspace,
    )
    for row in rows:
        _maybe_append(events, "session:start", row.get("s.data"), session_id)
        _maybe_append(events, "session:end", row.get("s.data_session_end"), session_id)

    # ── Subsession events ───────────────────────────────────────────────────
    rows = client.cypher(
        f'MATCH (sub:Subsession)-[:SUBSESSION_OF]->(root:Session {{node_id: "{session_id}"}}) '
        f"RETURN sub.node_id, sub.data, sub.data_session_end",
        workspace=workspace,
    )
    for row in rows:
        _maybe_append(events, "session:start", row.get("sub.data"), session_id)
        _maybe_append(events, "session:end", row.get("sub.data_session_end"), session_id)

    # ── OrchestratorRun events ──────────────────────────────────────────────
    rows = client.cypher(
        f'MATCH (s:Session {{node_id: "{session_id}"}})-[:HAS_RUN]->(r:OrchestratorRun) '
        f"RETURN r.data, r.data_execution_end, r.data_orchestrator_complete",
        workspace=workspace,
    )
    for row in rows:
        _maybe_append(events, "execution:start", row.get("r.data"), session_id)
        _maybe_append(events, "execution:end", row.get("r.data_execution_end"), session_id)
        _maybe_append(
            events, "orchestrator:complete", row.get("r.data_orchestrator_complete"), session_id
        )

    # ── PromptStep events ───────────────────────────────────────────────────
    rows = client.cypher(
        f'MATCH (p:PromptStep) WHERE p.session_id = "{session_id}" RETURN p.data',
        workspace=workspace,
    )
    for row in rows:
        _maybe_append(events, "prompt:submit", row.get("p.data"), session_id)

    # ── AssistantStep events ────────────────────────────────────────────────
    rows = client.cypher(
        f'MATCH (a:AssistantStep) WHERE a.session_id = "{session_id}" '
        f"RETURN a.data, a.data_llm_request, a.data_llm_response",
        workspace=workspace,
    )
    for row in rows:
        _maybe_append(events, "provider:request", row.get("a.data"), session_id)
        _maybe_append(events, "llm:request", row.get("a.data_llm_request"), session_id)
        _maybe_append(events, "llm:response", row.get("a.data_llm_response"), session_id)

    # ── ToolExecution events (non-delegate) ─────────────────────────────────
    rows = client.cypher(
        f'MATCH (t:ToolExecution) WHERE t.session_id = "{session_id}" '
        f"AND (t.tool_name IS NULL OR t.tool_name <> 'delegate') "
        f"RETURN t.data, t.data_tool_post",
        workspace=workspace,
    )
    for row in rows:
        _maybe_append(events, "tool:pre", row.get("t.data"), session_id)
        _maybe_append(events, "tool:post", row.get("t.data_tool_post"), session_id)

    # ── Delegate events from ToolExecution nodes ────────────────────────────
    rows = client.cypher(
        f'MATCH (t:ToolExecution) WHERE t.session_id = "{session_id}" '
        f"AND t.tool_name = 'delegate' "
        f"RETURN t.data, t.data_tool_post, "
        f"t.data_delegate_agent_spawned, t.data_delegate_agent_completed",
        workspace=workspace,
    )
    for row in rows:
        _maybe_append(events, "tool:pre", row.get("t.data"), session_id)
        _maybe_append(events, "tool:post", row.get("t.data_tool_post"), session_id)
        _maybe_append(
            events, "delegate:agent_spawned", row.get("t.data_delegate_agent_spawned"), session_id
        )
        _maybe_append(
            events,
            "delegate:agent_completed",
            row.get("t.data_delegate_agent_completed"),
            session_id,
        )

    # ── Generic Event nodes (prompt:complete, session:resume, etc.) ──────────
    rows = client.cypher(
        f'MATCH (s:Session {{node_id: "{session_id}"}})-[:HAS_EVENT]->(e:Event) '
        f"RETURN e.event_name, e.data, e.occurred_at",
        workspace=workspace,
    )
    for row in rows:
        event_name = row.get("e.event_name", "unknown")
        data_str = row.get("e.data")
        _maybe_append(events, event_name, data_str, session_id)

    # ── Sort by timestamp ───────────────────────────────────────────────────
    def _sort_key(e: dict[str, Any]) -> str:
        return e.get("ts", "") or ""

    events.sort(key=_sort_key)

    # ── Optionally resolve blob refs ────────────────────────────────────────
    if resolve_blobs:
        log.info("  Resolving blob references ...")
        _resolve_event_blobs(events, client, session_id)

    return events
