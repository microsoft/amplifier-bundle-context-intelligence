"""context_intelligence.bundle_analysis.gap — coverage gap analysis.

Stub implementation.  Full implementation is provided in a future task.
"""

from __future__ import annotations

from typing import Any


def compute_gap(
    *,
    signals: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Compute the coverage gap between usage signals and the bundle inventory.

    Parameters
    ----------
    signals:
        Output of :func:`.signals.run_signals` — observed usage metrics.
    inventory:
        Output of :func:`.inventory.scan_cache` — installed bundle inventory.

    Returns
    -------
    dict
        Gap analysis result.  Stub returns an empty dict.
    """
    return {}
