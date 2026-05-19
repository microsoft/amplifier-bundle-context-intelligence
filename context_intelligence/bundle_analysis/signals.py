"""context_intelligence.bundle_analysis.signals — graph-based usage signals.

Stub implementation.  Full implementation is provided in Task 7.
"""

from __future__ import annotations

from typing import Any

from context_intelligence.client import AsyncCIClient


async def run_signals(
    *,
    client: AsyncCIClient,
    workspace: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Query the context-intelligence graph and return bundle usage signals.

    Parameters
    ----------
    client:
        Async CI client for Cypher queries.
    workspace:
        Workspace to scope queries.
    session_id:
        Optional session ID to narrow the query scope.

    Returns
    -------
    dict
        Structured usage metrics.  Stub returns an empty dict.
    """
    return {}
