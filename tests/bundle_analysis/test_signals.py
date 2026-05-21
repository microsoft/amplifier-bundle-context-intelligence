"""Tests for context_intelligence.bundle_analysis.signals (fetchers+processor orchestrator).

signals.py must be a thin synchronous orchestrator:
- Reads JSONL events from disk via JSONLFetcher.
- Passes events and inventory to process_events for attribution.
- No CI graph (Cypher) path — removed in Phase 1.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# TestRunSignalsShape
# ---------------------------------------------------------------------------


class TestRunSignalsShape:
    def test_run_signals_is_synchronous(self):
        """run_signals must be a plain synchronous function, not a coroutine function."""
        from context_intelligence.bundle_analysis.signals import run_signals

        assert not inspect.iscoroutinefunction(run_signals)

    def test_run_signals_returns_dict_for_empty_workspace(self, tmp_path):
        """run_signals returns a dict even when the workspace has no sessions."""
        from context_intelligence.bundle_analysis.signals import run_signals

        result = run_signals(workspace="empty-ws", base_path=tmp_path, inventory={})
        assert isinstance(result, dict)

    def test_run_signals_requires_inventory_kwarg(self):
        """run_signals signature includes 'inventory' and NOT 'client'."""
        from context_intelligence.bundle_analysis.signals import run_signals

        sig = inspect.signature(run_signals)
        assert "inventory" in sig.parameters
        assert "client" not in sig.parameters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_jsonl_path(base: Path, ws: str, sid: str) -> Path:
    """Return the path to a session's events.jsonl file."""
    return base / ws / "sessions" / sid / "context-intelligence" / "events.jsonl"


def _write_events(path: Path, records: list[dict]) -> None:
    """Write JSONL event records to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_AGENT_SPAWNED_EVENT = {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}
