"""context_intelligence.bundle_analysis.signals — thin synchronous orchestrator.

JSONLFetcher reads events.jsonl from the Amplifier projects directory; process_events
normalises raw events into bundle-keyed named sets using the inventory as a
reverse-lookup table for tool/mode attribution; the CI graph (Cypher) path was
removed in Phase 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .fetchers import JSONLFetcher
from .processor import process_events


def run_signals(
    *,
    workspace: str,
    session_id: str | None = None,
    base_path: Path | None = None,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Return per-bundle usage signals from local JSONL event files."""
    events = JSONLFetcher().fetch(workspace=workspace, session_id=session_id, base_path=base_path)
    return process_events(events, inventory)


__all__ = ["run_signals"]
