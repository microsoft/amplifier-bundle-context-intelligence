"""Node-ID computation mirroring the CI server's identity function.

MIRRORS ci-server ``context_intelligence_server/utils.py:10-36`` ``make_node_id`` and
the ``handlers/data_layer_1/default.py`` drop/disambiguator rules. Keep in sync.

The server creates exactly one ``Event`` node per unique ``node_id``; events whose
``data.session_id`` is falsy are silently dropped (no node created). When
``data.tool_call_id`` is not None it is appended as a disambiguator, which prevents
same-millisecond same-event collisions for tool calls.

``compute_expected_node_count`` is the dedup-aware "ruler" the Phase 4 Task 8 (T3)
real-data parity gate uses to compute the EXPECTED graph count for a session's
post-transform ``context-intelligence/events.jsonl`` before polling the live server --
comparing the raw JSONL line count would over-count deduplicated events.

OWNERSHIP NOTE: this is an independent copy of the same algorithm that lives in
``amplifier_module_tool_context_intelligence_migrate.identity`` (the doomed migrate
tool). The upload tool is the SURVIVOR and must never import from migrate (see
``tests/test_dependency_direction.py``), so this module owns its own copy rather than
importing the migrate package as an oracle. ``tests/test_expected_node_count.py``
proves this copy's dedup/drop/disambiguator behavior against a hand-computed corpus
independently of the migrate-side implementation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _make_node_id(
    session_id: str,
    event_name: str,
    timestamp: str,
    disambiguator: str | None = None,
) -> str:
    """Replicate ci-server ``make_node_id`` verbatim.

    Source: context_intelligence_server/utils.py:10-36.

    * Colons in *event_name* are replaced with underscores.
    * *timestamp* is parsed as ISO-8601; epoch milliseconds are **floor-
      truncated** (``int()`` on a positive float truncates toward zero).
    * *disambiguator* is appended as ``__{disambiguator}`` only when not None.
    """
    safe_event = event_name.replace(":", "_")
    dt = datetime.fromisoformat(timestamp)
    epoch_ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    node_id = f"{session_id}__{safe_event}__{epoch_ms}"
    if disambiguator is not None:
        node_id = f"{node_id}__{disambiguator}"
    return node_id


def compute_expected_node_count(ci_events_path: Path) -> int:
    """Count distinct node IDs the CI server would create from *ci_events_path*.

    Reads each line of the post-transform ``context-intelligence/events.jsonl``,
    applies the server's drop (no ``session_id`` -> skip) and disambiguator
    (``tool_call_id`` -> appended) rules, and returns the number of unique
    ``node_id`` values.

    Used by the T3 real-data parity gate to obtain the expected graph count before
    polling; this correctly handles server-side deduplication so the gate does not
    fail sessions that have same-millisecond same-event lines without a
    ``tool_call_id``.
    """
    node_ids: set[str] = set()
    text = ci_events_path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            continue

        data: dict[str, Any] = rec.get("data") or {}

        # Server drops events without a session_id -- mirror that.
        session_id = data.get("session_id")
        if not session_id:
            continue

        # ``event`` field matches usage throughout this module (rec["event"]).
        event_name: str = str(rec["event"])
        ts: str = str(data.get("timestamp") or "")
        tool_call_id = data.get("tool_call_id")

        node_id = _make_node_id(
            str(session_id),
            event_name,
            ts,
            str(tool_call_id) if tool_call_id is not None else None,
        )
        node_ids.add(node_id)

    return len(node_ids)
