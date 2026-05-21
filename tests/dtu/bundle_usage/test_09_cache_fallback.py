"""
Scenario 9 — JSONL is the primary path (Phase 1 redesign, 2026-05-21).

After the Phase 1 redesign, JSONL session files are the PRIMARY source of
truth for bundle_usage.  The CI graph server is OPTIONAL — no graph server
is needed to return a complete result.

This module verifies three JSONL-primary invariants in the DTU environment:

  1. Without any CI server URL configured, bundle_usage returns a fully
     populated result dict (signals, inventory, gap keys all present).

  2. The bundle-cache mount (~/.amplifier/cache/ -> /mnt/amplifier-cache)
     supplies a non-empty inventory: at least one bundle key must appear.

  3. Every inventory entry for a real bundle exposes the three-tier shape
     (always_active, agent_level, mode_gated) plus the required sub-keys
     inside always_active (agents, context, skills, recipes).

All tests depend on the ``dtu_session`` fixture from conftest.py, which is
guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If the DTU
is not running the entire suite is skipped gracefully.

Design rationale
----------------
The old Scenario 9 tested cache-freshness classification (fresh/stale/absent)
which assumed the CI graph server was the primary data path and JSONL was a
fallback.  After the Phase 1 redesign that assumption is inverted:

  OLD: graph primary → JSONL fallback when graph unreachable
  NEW: JSONL primary → graph enrichment when graph reachable (optional)

These tests encode the new invariant so that any regression to
graph-primary behaviour is caught immediately.
"""

from __future__ import annotations

from .conftest import KNOWN_SESSION_ID


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_jsonl_primary_path_returns_results(dtu_session):
    """bundle_usage returns results without requiring a CI graph server.

    Calls bundle_usage with a known session_id and asserts that the result
    is a non-None dict containing the three top-level output keys (signals,
    inventory, gap) and that the scope reflects a discovered workspace.

    This test deliberately avoids any assertion that depends on the CI
    graph server being reachable.  If signals are empty that is fine as
    long as the structural keys are present and scope.workspace is set.

    Assertions:
      - result is not None
      - 'signals', 'inventory', 'gap' are all present in result
      - result['scope']['workspace'] is a non-empty string

    Diagnosis checklist on failure:
      - If result is None: _call_bundle_usage_direct() failed to parse any
        JSON output; check stderr / _raw key for the Python traceback.
      - If keys are missing: run_bundle_analysis() may have changed its
        return shape; verify the 'scope', 'signals', 'inventory', 'gap'
        contract in context_intelligence.bundle_analysis.
      - If workspace is empty: the JSONL workspace-discovery loop in the
        bundle analysis script did not find the session; verify the DTU
        projects mount at /mnt/amplifier-projects contains the session dir.
    """
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert result is not None, (
        "bundle_usage returned None.  Expected a dict with at minimum "
        "'signals', 'inventory', and 'gap' keys.  The _raw output (if any): "
        f"{result!r}"
    )

    for key in ("signals", "inventory", "gap"):
        assert key in result, (
            f"Expected '{key}' in bundle_usage result but it was absent. "
            f"Keys present: {list(result.keys())}. "
            f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
        )

    workspace = result.get("scope", {}).get("workspace", "")
    assert workspace, (
        "Expected scope.workspace to be a non-empty string but got "
        f"{workspace!r}.  The JSONL-primary workspace discovery loop "
        "must find the workspace for session_id={KNOWN_SESSION_ID} from the "
        "projects mount at /mnt/amplifier-projects.  Verify that the DTU "
        "is launched with the ~/.amplifier/projects/ bind mount."
    )


def test_bundle_cache_scan_returns_inventory(dtu_session):
    """The bundle-cache mount yields at least one bundle entry in inventory.

    Calls bundle_usage with a known session_id and inspects the inventory
    dict returned.  At least one non-metadata key (i.e. not prefixed with
    '_') must be present to confirm that the cache scan successfully read
    bundle directories from /mnt/amplifier-cache.

    Assertions:
      - result['inventory'] is a dict
      - At least one key in inventory does NOT start with '_'

    Diagnosis checklist on failure:
      - If inventory is not a dict: run_bundle_analysis() returned an
        unexpected type; check the bundle_analysis contract.
      - If bundle_keys is empty: no bundle directories were found under
        /mnt/amplifier-cache.  Verify the DTU is launched with the
        ~/.amplifier/cache/ bind mount (read-only) mapped to
        /mnt/amplifier-cache, and that the cache directory is non-empty.
    """
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    inventory = result["inventory"]

    assert isinstance(inventory, dict), (
        f"Expected result['inventory'] to be a dict but got {type(inventory).__name__!r}. "
        f"Value: {inventory!r}"
    )

    bundle_keys = [k for k in inventory if not k.startswith("_")]

    assert len(bundle_keys) > 0, (
        "Expected at least one non-metadata key in inventory but found none. "
        f"All inventory keys: {list(inventory.keys())}. "
        "This indicates ~/.amplifier/cache/ is not mounted into the DTU at "
        "/mnt/amplifier-cache, or the cache directory contains no recognised "
        "bundle directories.  Launch the DTU with the cache bind mount to "
        "enable inventory scanning."
    )


def test_inventory_three_tier_shape_visible_in_dtu(dtu_session):
    """Every real bundle entry exposes the three-tier shape and always_active sub-keys.

    Picks the first non-metadata key from the inventory and verifies that
    the entry has the expected shape:
      - Top-level tier keys: always_active, agent_level, mode_gated, modes,
        scan_source
      - Sub-keys inside always_active: agents, context, skills, recipes

    This confirms that the inventory layer is returning fully structured
    three-tier entries rather than a flat or partial shape.

    Assertions:
      - A sample bundle entry exists (at least one non-'_' key)
      - The sample entry contains all five top-level shape keys
      - entry['always_active'] contains all four resource-type sub-keys

    Diagnosis checklist on failure:
      - If sample_bundle is None: inventory is empty; see
        test_bundle_cache_scan_returns_inventory for diagnosis.
      - If a top-level key is missing: the inventory scanner may not be
        emitting the full three-tier structure; check BundleInventoryEntry
        serialisation in context_intelligence.bundle_analysis.inventory.
      - If an always_active sub-key is missing: the always-active resource
        collector is not returning the expected resource type; check the
        resource enumeration logic.
    """
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    inventory = result["inventory"]
    sample_bundle = next(
        (k for k in inventory if not k.startswith("_")),
        None,
    )

    assert sample_bundle is not None, (
        "No non-metadata key found in inventory — cannot check three-tier shape. "
        f"All inventory keys: {list(inventory.keys())}. "
        "Verify the cache mount and bundle scanner as described in "
        "test_bundle_cache_scan_returns_inventory."
    )

    entry = inventory[sample_bundle]

    for key in ("always_active", "agent_level", "mode_gated", "modes", "scan_source"):
        assert key in entry, (
            f"Expected '{key}' in inventory['{sample_bundle}'] but it was absent. "
            f"Keys present: {list(entry.keys())}. "
            "The three-tier shape (always_active, agent_level, mode_gated) plus "
            "modes and scan_source must be present in every bundle entry."
        )

    always_active = entry["always_active"]
    for key in ("agents", "context", "skills", "recipes"):
        assert key in always_active, (
            f"Expected '{key}' in inventory['{sample_bundle}']['always_active'] "
            f"but it was absent.  Keys present: {list(always_active.keys())}. "
            "The always_active tier must expose all four resource types: "
            "agents, context, skills, recipes."
        )
