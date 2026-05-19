"""context_intelligence.bundle_analysis — bundle usage analysis entry point.

Composes three deterministic layers:

1. **signals** (``run_signals``) — reads the context-intelligence graph via
   Cypher queries and returns structured usage metrics for each bundle/agent
   pair observed in session history.

2. **inventory** (``scan_cache``) — scans the local Amplifier bundle cache on
   disk and returns a structured inventory of every installed bundle, its
   agents, modes, and behaviors.

3. **gap** (``compute_gap``) — performs a pure, deterministic diff between the
   signals and the inventory to surface which agents are available but unused
   (coverage gaps) and which are actively used.

The orchestration function :func:`run_bundle_analysis` calls all three layers
and returns a single composite dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_intelligence.client import AsyncCIClient

from .gap import compute_gap
from .inventory import scan_cache
from .signals import run_signals


@dataclass
class _Scope:
    """Lightweight container for the analysis scope parameters."""

    workspace: str
    session_id: str | None


async def run_bundle_analysis(
    *,
    client: AsyncCIClient,
    workspace: str,
    session_id: str | None = None,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Run the full bundle analysis pipeline.

    Parameters
    ----------
    client:
        Async CI client used by the *signals* layer to query the graph.
    workspace:
        Workspace identifier to scope graph queries.
    session_id:
        Optional session ID to narrow signal queries to a single session.
    cache_root:
        Root of the local Amplifier bundle cache.  Defaults to
        ``~/.amplifier/cache``.

    Returns
    -------
    dict
        A composite dict with the keys:

        * ``"scope"``     — :class:`_Scope` capturing *workspace* and
          *session_id*.
        * ``"signals"``   — output of :func:`.signals.run_signals`.
        * ``"inventory"`` — output of :func:`.inventory.scan_cache`.
        * ``"gap"``       — output of :func:`.gap.compute_gap`.
    """
    if cache_root is None:
        cache_root = Path.home() / ".amplifier" / "cache"

    signals = await run_signals(
        client=client,
        workspace=workspace,
        session_id=session_id,
    )
    inventory = scan_cache(cache_root=cache_root)
    gap = compute_gap(signals=signals, inventory=inventory)

    return {
        "scope": _Scope(workspace=workspace, session_id=session_id),
        "signals": signals,
        "inventory": inventory,
        "gap": gap,
    }


__all__ = ["run_bundle_analysis"]
