"""Verify migrated sessions against the CI graph via POST /cypher.

Gate A: graph Event count settles to compute_expected_node_count(ci_events_path).
Gate B: zero $blob_error markers in the graph for that session.

Gate A design rationale
------------------------
Ingestion is asynchronous (POST /events returns 202); the graph may lag behind
immediately after upload.  Additionally, the server deduplicates events by node
identity (same session_id + event + millisecond timestamp + optional tool_call_id),
so the expected node count is *less than* the raw JSONL line count for sessions
that contain same-millisecond same-event lines without a tool_call_id.

Strategy: poll the graph count until it matches the expected count computed
offline by ``compute_expected_node_count`` (which mirrors the server's identity
function exactly), or until SETTLE_TIMEOUT_S elapses, or until an overshoot is
detected (graph > expected → identity drift, fail-safe).

All Cypher is isolated behind well-named constants so queries are trivial to adjust.
Unit tests mock _post_cypher — no real network calls.

Confirmed live graph schema
---------------------------
* Every JSONL event becomes an ``:Event`` node carrying property ``session_id``
  equal to the session's string ID.
* ``Session`` nodes carry ``node_id``.
* Tool events are linked ``(:Session)-[:HAS_EVENT]->(:Event)``; lifecycle events
  (``session:end``, ``orchestrator:complete``, etc.) are linked via
  ``(:Session)-[:SOURCED_FROM]->(:Event)``.
* Because event→session linkage spans two relationship types, counting via the
  direct ``session_id`` property on ``:Event`` is the correct and complete approach.
* The live server returns ``{"results": [{col: val}, ...]}`` (flat row-dicts),
  NOT the Neo4j REST envelope ``{results: [{columns:…, data:[{row:…}]}]}``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .identity import compute_expected_node_count


# ---------------------------------------------------------------------------
# Settle / poll constants for Gate A
# ---------------------------------------------------------------------------

#: Maximum seconds to wait for Neo4j to commit all async-ingested events.
SETTLE_TIMEOUT_S: float = 60.0

#: Seconds between graph-count polls during Gate A settling.
POLL_INTERVAL_S: float = 2.0


# ---------------------------------------------------------------------------
# Cypher constants
# ---------------------------------------------------------------------------

#: Gate A — count Event nodes for this session via session_id property.
#: Uses session_id property directly rather than a relationship traversal because
#: events are linked to sessions via two relationship types (HAS_EVENT and
#: SOURCED_FROM), so a property-based count is the correct complete approach.
_COUNT_CYPHER = "MATCH (e:Event {session_id:$sid}) RETURN count(e) AS c"

#: Gate B — count Event nodes with $blob_error markers.
#: Uses size(collect(...)) instead of count() so the query string contains
#: "blob_error" (for mock discrimination) but not "count(" (which the count
#: query uses, allowing _mock_client to distinguish the two queries).
_BLOB_ERROR_CYPHER = (
    "MATCH (e:Event {session_id:$sid}) "
    "WHERE toString(e.data) CONTAINS '$blob_error' "
    "WITH collect(e) AS errs "
    "RETURN size(errs) AS blob_errors"
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reason: str


@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    event_count_graph: int
    event_count_file: int
    blob_errors: int
    message: str


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class CypherClient:
    """Thin wrapper around POST /cypher with bearer authentication.

    The ``_post_cypher`` instance method is the single HTTP seam; tests replace
    it with a fake to keep the test suite hermetic.
    """

    def __init__(self, server_url: str, api_key: str) -> None:
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key

    def _post_cypher(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """POST {server_url}/cypher with bearer auth and return row data.

        Body: ``{"query": cypher, "params": params, "workspace": "*"}``.
        Returns the ``data`` rows from the response (list of row-dicts).
        Raises on non-2xx.
        """
        url = f"{self._server_url}/cypher"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "query": cypher,
            "params": params or {},
            "workspace": "*",
        }
        with httpx.Client() as client:
            resp = client.post(url, headers=headers, json=payload, timeout=30.0)
        resp.raise_for_status()
        raw = resp.json()
        # Parse the live server shape and the Neo4j REST envelope.
        #
        # Live server: {"results": [{"col": val, ...}, ...]} — flat row-dicts
        # Neo4j REST:  {"results": [{"columns": [...], "data": [{"row": [...]}]}]}
        #
        # If raw is already a bare list, return it directly.
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            results = raw.get("results", [])
            if results:
                first = results[0]
                # Live flat-row shape: first element has neither "columns",
                # "data", nor "row" keys → return results list as-is.
                if "columns" not in first and "data" not in first and "row" not in first:
                    return list(results)
                # Neo4j REST envelope fallback: {columns:[…], data:[{row:[…]}]}
                cols = first.get("columns", [])
                rows = []
                for item in first.get("data", []):
                    row_vals = item.get("row", [])
                    rows.append(dict(zip(cols, row_vals)))
                return rows
        return []

    def query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
        *,
        workspace: str = "*",
    ) -> list[dict[str, Any]]:
        """Convenience alias around :meth:`_post_cypher`.

        *workspace* is accepted for API symmetry but the value used in the
        real request is always ``"*"`` (global scope); pass explicit ``$sid``
        parameters in Cypher instead.
        """
        return self._post_cypher(cypher, params)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_int_from_rows(rows: list[dict[str, Any]], *keys: str) -> int:
    """Extract an integer from the first row of *rows* under any of *keys*."""
    if not rows:
        return 0
    first = rows[0]
    for key in keys:
        val = first.get(key)
        if val is not None:
            return int(val)
    return 0


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------


def count_events_in_graph(client: CypherClient, session_id: str) -> int:
    """Gate A: count Event nodes linked to *session_id* in the graph."""
    rows = client._post_cypher(_COUNT_CYPHER, {"sid": session_id})
    return _extract_int_from_rows(rows, "count", "c", "n")


def count_blob_errors(client: CypherClient, session_id: str) -> int:
    """Gate B: count Event nodes with ``$blob_error`` markers for *session_id*."""
    rows = client._post_cypher(_BLOB_ERROR_CYPHER, {"sid": session_id})
    return _extract_int_from_rows(rows, "blob_errors", "count", "c", "n")


def verify_session(
    client: CypherClient,
    session_id: str,
    *,
    ci_events_path: Path,
    settle_timeout: float = SETTLE_TIMEOUT_S,
    poll_interval: float = POLL_INTERVAL_S,
) -> VerifyResult:
    """Run Gate A (settle-and-count) and Gate B (blob-error) for a single session.

    Gate A: polls ``count_events_in_graph`` until it equals the expected node
            count computed offline from *ci_events_path*, or until
            *settle_timeout* seconds elapse, or until an overshoot is detected
            (graph > expected → identity drift, fail-safe).

    Gate B: ``count_blob_errors == 0``  (checked only when Gate A passes).

    ``passed = (A) and (B)``.

    Why poll instead of a one-shot comparison
    ------------------------------------------
    Ingestion is asynchronous (POST /events → 202 Accepted); the graph count
    may lag immediately after upload (observed: 17/479 and 27/636 in live DTU
    runs).  The expected count is computed by :func:`.identity.compute_expected_node_count`
    which mirrors the server's dedup rules, so same-millisecond same-event lines
    without a ``tool_call_id`` correctly collapse to one node instead of
    inflating the target.
    """
    expected = compute_expected_node_count(ci_events_path)

    start = time.monotonic()
    deadline = start + settle_timeout

    gate_a = False
    gate_a_message = ""
    last_graph_count = 0

    while True:
        g = count_events_in_graph(client, session_id)
        last_graph_count = g

        if g == expected:
            gate_a = True
            break

        if g > expected:
            elapsed = time.monotonic() - start
            gate_a_message = (
                f"Gate A FAIL (overshoot — identity drift): "
                f"graph={g} expected={expected} elapsed={elapsed:.1f}s"
            )
            break

        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - start
            gate_a_message = (
                f"Gate A FAIL (timeout after {elapsed:.1f}s): "
                f"graph={last_graph_count} expected={expected}"
            )
            break

        time.sleep(poll_interval)

    blob_errs = 0
    if gate_a:
        blob_errs = count_blob_errors(client, session_id)

    gate_b = blob_errs == 0
    passed = gate_a and gate_b

    if not passed:
        parts: list[str] = []
        if not gate_a:
            parts.append(gate_a_message)
        if not gate_b:
            parts.append(f"Gate B FAIL: {blob_errs} $blob_error(s)")
        message = "; ".join(parts)
    else:
        message = f"OK: {last_graph_count} events, 0 blob_errors"

    return VerifyResult(
        passed=passed,
        event_count_graph=last_graph_count,
        event_count_file=expected,
        blob_errors=blob_errs,
        message=message,
    )


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight(server_url: str, api_key: str) -> PreflightResult:
    """Check that the server is reachable and credentials are valid.

    POST /cypher with ``RETURN 1 AS ok``; returns ok=True on success.
    Uses httpx.Client as a context manager (mockable in tests via
    ``patch("httpx.Client")``).
    """
    base_url = server_url.rstrip("/")

    try:
        with httpx.Client() as client:
            resp = client.post(
                f"{base_url}/cypher",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": "RETURN 1 AS ok", "params": {}, "workspace": "*"},
                timeout=10.0,
            )
    except httpx.ConnectError as exc:
        return PreflightResult(ok=False, reason=f"connection refused to {base_url}: {exc}")
    except httpx.TimeoutException as exc:
        return PreflightResult(ok=False, reason=f"timeout connecting to {base_url}: {exc}")
    except Exception as exc:  # noqa: BLE001
        return PreflightResult(ok=False, reason=f"unexpected error: {exc}")

    if resp.status_code == 401:
        return PreflightResult(ok=False, reason="auth probe: 401 Unauthorized — check api_key")
    if resp.status_code == 403:
        return PreflightResult(ok=False, reason="auth probe: 403 Forbidden — check api_key")
    if resp.status_code >= 400:
        return PreflightResult(ok=False, reason=f"auth probe: HTTP {resp.status_code}")

    return PreflightResult(ok=True, reason="")
