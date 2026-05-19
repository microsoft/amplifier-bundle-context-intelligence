"""context_intelligence.bundle_analysis.gap — coverage gap analysis.

Pure set arithmetic + threshold rules combining Layer 1 (signals) and
Layer 2 (inventory) outputs.  No LLM cycles.  No external calls.

Produces three improvement categories (S-8, S-15, S-17 reasoning):
- tree-shake: declared, zero invocations across all sessions surveyed.
- mode-refactor: used < 20% of declared components → candidate for opt-in mode.
- config-gap: invoked in session but not present in cache inventory.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE_REFACTOR_THRESHOLD = 0.20

_COMPONENT_KEYS = ("agents", "skills", "modes", "recipes", "tools")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _declared_counts(inv_entry: dict[str, Any]) -> dict[str, int]:
    """Return declared component counts from an inventory entry.

    ``tools`` is always 0 because tools are not declared in bundle.md /
    agents/ — Layer 2 cannot enumerate them.
    """
    declared = inv_entry.get("declared", {})
    counts: dict[str, int] = {}
    for k in _COMPONENT_KEYS:
        if k == "tools":
            counts[k] = 0
        else:
            value = declared.get(k, [])
            counts[k] = len(value) if isinstance(value, (list, tuple, set)) else 0
    return counts


def _used_counts(sig_entry: dict[str, Any] | None) -> dict[str, int]:
    """Return observed usage counts from a signals entry.

    Returns a zero dict when *sig_entry* is ``None`` or not a mapping.
    """
    if not isinstance(sig_entry, dict):
        return {k: 0 for k in _COMPONENT_KEYS}
    return {k: int(sig_entry.get(k, 0)) for k in _COMPONENT_KEYS}


def _util_gap(declared: dict[str, int], used: dict[str, int]) -> dict[str, int]:
    """Return the per-component utilisation gap (clamped to >= 0)."""
    return {k: max(0, declared[k] - used[k]) for k in _COMPONENT_KEYS}


def _total_used(used: dict[str, int]) -> int:
    """Sum all used-component counts."""
    return sum(used.values())


def _total_declared(declared: dict[str, int]) -> int:
    """Sum all declared-component counts."""
    return sum(declared.values())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
        ``{"per_bundle": ..., "improvement": [...]}``
    """
    per_bundle: dict[str, Any] = {}
    improvements: list[dict[str, Any]] = []

    for bundle, inv_entry in inventory.items():
        # Skip the _meta sentinel and any non-dict entries
        if bundle == "_meta" or not isinstance(inv_entry, dict):
            continue

        sig_entry = signals.get(bundle)
        declared = _declared_counts(inv_entry)
        used = _used_counts(sig_entry)
        gap = _util_gap(declared, used)

        per_bundle[bundle] = {
            "declared": declared,
            "used": used,
            "util_gap": gap,
        }

        td = _total_declared(declared)
        tu = _total_used(used)

        if tu == 0 and td > 0:
            improvements.append(
                {
                    "bundle": bundle,
                    "type": "tree-shake",
                    "reason": f"declared {td} components, zero invocations observed",
                }
            )
        elif td > 0 and (tu / td) < MODE_REFACTOR_THRESHOLD:
            ratio = tu / td
            improvements.append(
                {
                    "bundle": bundle,
                    "type": "mode-refactor",
                    "reason": (
                        f"used {tu}/{td} ({ratio:.1%})"
                        f" — below mode-refactor threshold of {MODE_REFACTOR_THRESHOLD:.0%}"
                    ),
                }
            )

    # Config-gap: bundles invoked in sessions but absent from cache inventory
    for bundle in signals:
        if bundle == "_meta":
            continue
        if bundle not in per_bundle:
            improvements.append(
                {
                    "bundle": bundle,
                    "type": "config-gap",
                    "reason": "invoked in session but not present in cache inventory",
                }
            )

    return {"per_bundle": per_bundle, "improvement": improvements}
