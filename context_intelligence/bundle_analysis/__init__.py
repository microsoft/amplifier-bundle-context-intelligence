"""context_intelligence.bundle_analysis — bundle usage analysis entry point.

Composes three deterministic layers:

1. **inventory** (``scan_cache``) — scans the local Amplifier bundle cache on
   disk and returns a structured inventory of every installed bundle, its
   agents, modes, and behaviors.  Called FIRST so the processor can use the
   inventory as a reverse-lookup table for tool/mode attribution.

2. **signals** (``run_signals``) — reads JSONL event files from the local
   Amplifier projects directory and returns structured usage metrics for each
   bundle/agent pair observed in session history.  Receives the inventory so
   it can attribute tool/mode invocations back to the declaring bundle.

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
    workspace: str,
    session_id: str | None = None,
    cache_root: Path | None = None,
    base_path: Path | None = None,
) -> dict[str, Any]:
    """Run the full bundle analysis pipeline.

    Parameters
    ----------
    workspace:
        Workspace identifier to scope graph queries.
    session_id:
        Optional session ID to narrow signal queries to a single session.
    cache_root:
        Root of the local Amplifier bundle cache.  Defaults to
        ``~/.amplifier/cache``.
    base_path:
        Root of the Amplifier projects directory used by the JSONL fetcher
        in the signals layer.  Defaults to ``~/.amplifier/projects``.

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

    # Step 1: build inventory FIRST — processor uses it as reverse-lookup for
    # tool/mode attribution when processing JSONL events.
    inventory = scan_cache(cache_root=cache_root)

    # Step 2: collect usage signals, passing inventory for attribution.
    signals = run_signals(
        workspace=workspace,
        session_id=session_id,
        base_path=base_path,
        inventory=inventory,
    )

    # Step 3: compute the coverage gap between signals and inventory.
    gap = compute_gap(signals=signals, inventory=inventory)

    return {
        "scope": _Scope(workspace=workspace, session_id=session_id),
        "signals": signals,
        "inventory": inventory,
        "gap": gap,
    }


__all__ = ["run_bundle_analysis"]
