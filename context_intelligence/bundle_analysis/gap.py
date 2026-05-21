"""context_intelligence.bundle_analysis.gap — coverage gap analysis.

Pure named-set arithmetic + threshold rules combining Layer 1 (signals) and
Layer 2 (inventory) outputs.  No LLM cycles.  No external calls.

Output schema::

    {
        "per_bundle": {
            "<bundle>": {
                "always_active": {
                    "declared":  {<key>: set[str]},
                    "used":      {<key>: set[str]},
                    "unused":    {<key>: set[str]},
                    "util_pct":  {<key>: float | None},
                },
                "mode_gated": {
                    "<mode>": {
                        "declared":        {<key>: set[str]},
                        "used":            {<key>: set[str]},
                        "unused":          {<key>: set[str]},
                        "mode_activated":  bool,
                    }
                },
                "modes": {
                    "declared":        set[str],
                    "activated":       set[str],
                    "never_activated": set[str],
                },
            }
        },
        "improvement": [
            {
                "bundle":         str,
                "type":           str,   # tree-shake | mode-refactor | config-gap |
                                         # mode-never-activated
                "component_type": str,
                "scope":          str,   # always_active | mode_gated
                "mode_name":      str | None,
                "names":          list[str],  # sorted
                "reason":         str,
            },
            ...
        ],
    }

Produces four improvement categories:
- tree-shake:            declared but zero invocations on always_active scope.
- mode-refactor:         used < 20% of declared components on always_active scope.
- config-gap:            invoked in session but not present in cache inventory.
- mode-never-activated:  declared mode never appeared in signals.modes.

Two-tier semantic: mode-gated components with zero usage do NOT trigger
tree-shake — they are expected dormant when the mode is off.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE_REFACTOR_THRESHOLD = 0.20

#: Component keys evaluated in the always-active scope.
_ALWAYS_ACTIVE_KEYS: tuple[str, ...] = ("agents", "skills", "recipes", "context", "tools")

#: Component keys evaluated per mode in the mode-gated scope.
_MODE_GATED_KEYS: tuple[str, ...] = ("agents", "context", "skills")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_set(value: Any) -> set[str]:
    """Return a set of strings from *value*.

    Accepts sets, lists, or tuples; ignores non-string elements.
    Returns an empty set for any other type (including ``None``).
    """
    if isinstance(value, (set, list, tuple)):
        return {v for v in value if isinstance(v, str)}
    return set()


def _set_diff(
    declared: dict[str, set[str]],
    used: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Return per-key ``declared[k] - used[k]`` for each key in *declared*."""
    return {k: declared[k] - used.get(k, set()) for k in declared}


def _util_pct(
    declared: dict[str, set[str]],
    used: dict[str, set[str]],
) -> dict[str, float | None]:
    """Return per-key ``len(used[k]) / len(declared[k])`` or ``None`` when declared is empty."""
    result: dict[str, float | None] = {}
    for k, d_set in declared.items():
        d = len(d_set)
        if d == 0:
            result[k] = None
        else:
            result[k] = len(used.get(k, set())) / d
    return result


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
        Output of :func:`.signals.run_signals` — observed usage as named sets.
        Schema per bundle: ``{agents, skills, recipes, context, tools, modes}``
        where each value is ``set[str]``.
    inventory:
        Output of :func:`.inventory.scan_cache` — three-tier installed bundle
        inventory.  Each bundle entry has ``always_active``, ``mode_gated``,
        ``agent_level``, ``modes``.

    Returns
    -------
    dict
        ``{"per_bundle": ..., "improvement": [...]}``
    """
    per_bundle: dict[str, Any] = {}
    improvements: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Pass 1 — iterate inventory bundles
    # ------------------------------------------------------------------
    for bundle, inv_entry in inventory.items():
        # Skip the _meta sentinel and any non-dict entries
        if bundle == "_meta" or not isinstance(inv_entry, dict):
            continue

        sig_entry: dict[str, Any] | None = signals.get(bundle)
        if not isinstance(sig_entry, dict):
            sig_entry = None

        # Modes activated in signals (may be empty — processor Phase 1 leaves this empty)
        raw_sig_modes = sig_entry.get("modes", set()) if sig_entry else set()
        activated_modes: set[str] = _coerce_set(raw_sig_modes)

        # ----------------------------------------------------------------
        # always_active tier
        # ----------------------------------------------------------------
        inv_aa = inv_entry.get("always_active", {})
        if not isinstance(inv_aa, dict):
            inv_aa = {}

        aa_declared: dict[str, set[str]] = {}
        aa_used: dict[str, set[str]] = {}
        for k in _ALWAYS_ACTIVE_KEYS:
            aa_declared[k] = _coerce_set(inv_aa.get(k))
            aa_used[k] = _coerce_set(sig_entry.get(k)) if sig_entry else set()

        aa_unused = _set_diff(aa_declared, aa_used)
        aa_util_pct = _util_pct(aa_declared, aa_used)

        # ----------------------------------------------------------------
        # mode_gated tier (Phase 1: used sets are always empty)
        # ----------------------------------------------------------------
        inv_mg = inv_entry.get("mode_gated", {})
        if not isinstance(inv_mg, dict):
            inv_mg = {}

        mg_per_mode: dict[str, Any] = {}
        for mode_name, mode_data in inv_mg.items():
            if not isinstance(mode_data, dict):
                continue
            mg_declared: dict[str, set[str]] = {
                k: _coerce_set(mode_data.get(k)) for k in _MODE_GATED_KEYS
            }
            mg_used: dict[str, set[str]] = {k: set() for k in _MODE_GATED_KEYS}
            mg_unused = _set_diff(mg_declared, mg_used)
            mg_per_mode[mode_name] = {
                "declared": mg_declared,
                "used": mg_used,
                "unused": mg_unused,
                "mode_activated": mode_name in activated_modes,
            }

        # ----------------------------------------------------------------
        # modes tier
        # ----------------------------------------------------------------
        inv_modes: set[str] = _coerce_set(inv_entry.get("modes"))
        activated: set[str] = activated_modes & inv_modes
        never_activated: set[str] = inv_modes - activated

        # ----------------------------------------------------------------
        # Assemble per-bundle entry
        # ----------------------------------------------------------------
        per_bundle[bundle] = {
            "always_active": {
                "declared": aa_declared,
                "used": aa_used,
                "unused": aa_unused,
                "util_pct": aa_util_pct,
            },
            "mode_gated": mg_per_mode,
            "modes": {
                "declared": inv_modes,
                "activated": activated,
                "never_activated": never_activated,
            },
        }

        # ----------------------------------------------------------------
        # Improvements — always_active scope only (two-tier semantic)
        # ----------------------------------------------------------------
        for k in _ALWAYS_ACTIVE_KEYS:
            td = len(aa_declared[k])
            tu = len(aa_used[k])

            if td == 0:
                continue  # nothing declared for this key → skip

            if tu == 0:
                # (1) tree-shake: declared but zero invocations
                improvements.append(
                    {
                        "bundle": bundle,
                        "type": "tree-shake",
                        "component_type": k,
                        "scope": "always_active",
                        "mode_name": None,
                        "names": sorted(aa_declared[k]),
                        "reason": f"{td} {k} declared, zero invocations",
                    }
                )
            elif (tu / td) < MODE_REFACTOR_THRESHOLD and aa_unused[k]:
                # (2) mode-refactor: used < threshold, non-empty unused set
                improvements.append(
                    {
                        "bundle": bundle,
                        "type": "mode-refactor",
                        "component_type": k,
                        "scope": "always_active",
                        "mode_name": None,
                        "names": sorted(aa_unused[k]),
                        "reason": f"{tu}/{td} ({tu / td:.1%}) below threshold",
                    }
                )

        # ----------------------------------------------------------------
        # Improvements — mode-never-activated
        # ----------------------------------------------------------------
        for mode_name in sorted(never_activated):
            # Collect all names declared under this mode across _MODE_GATED_KEYS
            mode_data = inv_mg.get(mode_name)
            names_set: set[str] = set()
            if isinstance(mode_data, dict):
                for k in _MODE_GATED_KEYS:
                    names_set |= _coerce_set(mode_data.get(k))
            # When a mode declares no gated components, fall back to the mode
            # name itself so that 'names' is always non-empty for this type.
            if not names_set:
                names_set = {mode_name}

            improvements.append(
                {
                    "bundle": bundle,
                    "type": "mode-never-activated",
                    "component_type": "modes",
                    "scope": "mode_gated",
                    "mode_name": mode_name,
                    "names": sorted(names_set),
                    "reason": f"mode '{mode_name}' declared but never activated",
                }
            )

    # ------------------------------------------------------------------
    # Pass 2 — config-gap: bundles in signals but absent from inventory
    # ------------------------------------------------------------------
    for bundle, sig_entry in signals.items():
        if bundle == "_meta":
            continue
        if not isinstance(sig_entry, dict):
            continue
        if bundle in per_bundle:
            continue

        # Collect union of all names used across _ALWAYS_ACTIVE_KEYS
        names_set = set()
        for k in _ALWAYS_ACTIVE_KEYS:
            names_set |= _coerce_set(sig_entry.get(k))

        improvements.append(
            {
                "bundle": bundle,
                "type": "config-gap",
                "component_type": "agents",
                "scope": "always_active",
                "mode_name": None,
                "names": sorted(names_set),
                "reason": "invoked in session but absent from cache inventory",
            }
        )

    return {"per_bundle": per_bundle, "improvement": improvements}
