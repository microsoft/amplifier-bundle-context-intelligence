"""context_intelligence.bundle_analysis.signals — thin orchestrator.

Delegates to GraphFetcher (graph path) or JSONLFetcher (local path), then
aggregates raw events with process_events.

When client.server_url is set:
  - GraphFetcher.fetch is called first.
  - If server_ok is True, graph result is authoritative (even if empty).
  - If server_ok is False, fall back to JSONLFetcher.
When client.server_url is None:
  - Skip graph; read JSONL directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from context_intelligence.client import AsyncCIClient

from .fetchers import GraphFetcher, JSONLFetcher
from .processor import process_events

logger = logging.getLogger("context_intelligence.bundle_analysis.signals")


async def run_signals(
    *,
    client: AsyncCIClient,
    workspace: str,
    session_id: str | None = None,
    base_path: Path | None = None,
) -> dict[str, Any]:
    """Return per-bundle usage signals, preferring the CI graph when available."""
    server_url = getattr(client, "server_url", None)

    if server_url:
        events, server_ok = await GraphFetcher().fetch(
            client=client, workspace=workspace, session_id=session_id
        )
        if server_ok:
            return process_events(events)
        logger.info("CI graph server unreachable — falling back to JSONL signal extraction")

    jsonl_events = JSONLFetcher().fetch(
        workspace=workspace, session_id=session_id, base_path=base_path
    )
    return process_events(jsonl_events)


__all__ = ["run_signals"]
