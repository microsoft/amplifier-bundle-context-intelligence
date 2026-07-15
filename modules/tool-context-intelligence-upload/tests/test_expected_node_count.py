"""Independent unit test for the dedup-aware expected-count ruler (Phase 4 Task 8, D2).

The T3 real-data parity gate compares live ``/cypher`` node counts against
``compute_expected_node_count`` -- that function IS the measuring stick for the whole
parity check. If the ruler shared assumptions with the thing it measures (or simply
had a bug), a "PASS" from the parity gate would prove nothing.

This test builds a TINY, HAND-COMPUTED corpus (two sessions, one with a duplicate
event) and asserts the function returns exactly the count worked out by hand --
independently of any server call, real corpus, or DTU. D2 requires this test to be
green BEFORE Step 4 (the real-server parity run) may proceed.

Hand computation for the core case (two sessions):

Session A (``sess-a``) -- 3 raw lines, 2 unique node ids:
    1. event=tool:pre,  ts=2026-01-01T00:00:00+00:00  -> node A1
    2. event=tool:post, ts=2026-01-01T00:00:01+00:00  -> node A2
    3. event=tool:pre,  ts=2026-01-01T00:00:00+00:00  -> SAME node id as #1 (exact
       duplicate: same session_id + event + timestamp + no tool_call_id) -> collapses
    => unique node ids for session A = {A1, A2} = 2

Session B (``sess-b``) -- 1 raw line, 1 unique node id:
    1. event=tool:pre, ts=2026-01-01T00:00:00+00:00 -> node B1 (distinct from A1
       because the node id is namespaced by session_id)
    => unique node ids for session B = {B1} = 1

Expected count per file: session A -> 2, session B -> 1, combined (summed across
the corpus, as the real T3 run does per-workspace) -> 3.
"""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_module_tool_context_intelligence_upload.identity import (
    compute_expected_node_count,
)


def _write_events(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(rec) for rec in records) + "\n",
        encoding="utf-8",
    )


def test_expected_node_count_hand_computed_two_sessions_with_duplicate(tmp_path: Path) -> None:
    """Core D2 case: 2 hand-built sessions, one with an exact-duplicate event.

    Session A has a duplicate line (same session_id/event/timestamp, no
    tool_call_id) that must collapse to a single node; session B is a single
    distinct event in a different session (so it does NOT collapse with
    session A's event, even though event name + timestamp match).
    """
    session_a = tmp_path / "session-a" / "events.jsonl"
    _write_events(
        session_a,
        [
            {
                "event": "tool:pre",
                "data": {"session_id": "sess-a", "timestamp": "2026-01-01T00:00:00+00:00"},
            },
            {
                "event": "tool:post",
                "data": {"session_id": "sess-a", "timestamp": "2026-01-01T00:00:01+00:00"},
            },
            # exact duplicate of the first line -> must collapse
            {
                "event": "tool:pre",
                "data": {"session_id": "sess-a", "timestamp": "2026-01-01T00:00:00+00:00"},
            },
        ],
    )

    session_b = tmp_path / "session-b" / "events.jsonl"
    _write_events(
        session_b,
        [
            {
                "event": "tool:pre",
                "data": {"session_id": "sess-b", "timestamp": "2026-01-01T00:00:00+00:00"},
            },
        ],
    )

    count_a = compute_expected_node_count(session_a)
    count_b = compute_expected_node_count(session_b)

    assert count_a == 2, "3 raw lines, 1 exact duplicate -> 2 unique node ids"
    assert count_b == 1, "1 raw line -> 1 unique node id"
    assert count_a + count_b == 3, "combined corpus expected count (as summed per-workspace in T3)"


def test_expected_node_count_missing_session_id_dropped(tmp_path: Path) -> None:
    """Events with no (or empty) session_id are dropped -- mirrors the server's own
    drop rule (no Event node is ever created for such a record).
    """
    events_path = tmp_path / "events.jsonl"
    _write_events(
        events_path,
        [
            # no session_id at all -> dropped
            {"event": "tool:pre", "data": {"timestamp": "2026-01-01T00:00:00+00:00"}},
            # empty-string session_id -> falsy -> dropped
            {
                "event": "tool:post",
                "data": {"session_id": "", "timestamp": "2026-01-01T00:00:01+00:00"},
            },
            # valid session_id -> counted
            {
                "event": "session:start",
                "data": {"session_id": "sess-c", "timestamp": "2026-01-01T00:00:02+00:00"},
            },
        ],
    )

    assert compute_expected_node_count(events_path) == 1


def test_expected_node_count_tool_call_id_disambiguates(tmp_path: Path) -> None:
    """Two lines that share session_id/event/timestamp but carry DIFFERENT
    ``tool_call_id`` values must NOT collapse -- the disambiguator makes them
    distinct nodes, same as two lines with the same three fields and no
    tool_call_id at all WOULD collapse.
    """
    events_path = tmp_path / "events.jsonl"
    _write_events(
        events_path,
        [
            {
                "event": "tool:pre",
                "data": {
                    "session_id": "sess-d",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "tool_call_id": "call-1",
                },
            },
            {
                "event": "tool:pre",
                "data": {
                    "session_id": "sess-d",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "tool_call_id": "call-2",
                },
            },
        ],
    )

    assert compute_expected_node_count(events_path) == 2
