"""
Scenario 5 — Workspace efficiency aggregate covering all installed bundles.

Verifies that the bundle_usage tool, called without a session_id, produces a
workspace-scoped aggregate that covers all installed bundles:

  1. Inventory coverage — all installed bundles appear (count >= 30 expected
     for the reference workspace).
  2. Order-of-magnitude accuracy — aggregate invocation counts for the three
     most-active bundles match the graph-analyst reference figures within ±15.
  3. Dormant bundle purity — bundles with no known activity must report zero
     total invocations (at most 2 exceptions tolerated for natural noise).

All tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _total_invocations(bundle_counts: dict) -> int:
    """Sum invocation counts across all component dimensions for one bundle.

    Parameters
    ----------
    bundle_counts:
        Dict with optional keys ``agents``, ``skills``, ``modes``,
        ``recipes``, ``tools``.

    Returns
    -------
    int
        Sum of all present component counts (missing keys treated as 0).
    """
    return sum(bundle_counts.get(k, 0) for k in ("agents", "skills", "modes", "recipes", "tools"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_workspace_aggregate_covers_all_installed(dtu_session):
    """Workspace-scope aggregate must cover at least 30 installed bundles.

    Activates the bundle-usage mode, then calls the bundle_usage tool WITHOUT
    a session_id so that the tool operates in workspace-scope aggregate mode.
    The resulting inventory must contain at least 30 bundle entries (excluding
    the ``_meta`` key).

    Assertions:
      - The tool call succeeds (``result["success"] is True``).
      - ``inventory_count >= 30`` where
        ``inventory_count = len([k for k in out["inventory"] if k != "_meta"])``.

    Diagnosis checklist on failure:
      - If inventory_count is 0 or very small, the workspace-scope path may
        not be scanning the bundle cache at all.  Verify that calling
        bundle_usage with no session_id triggers a full cache scan.
      - If inventory_count is > 0 but < 30, the bundle cache may be
        incomplete.  Check ``~/.amplifier/cache`` to confirm that 30+
        bundle directories are present.
      - A tool failure (``success != True``) means the workspace-scope path
        is broken; check the bundle_usage tool implementation for the
        ``session_id`` omitted code path.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage")

    assert result.get("success") is True, (
        f"bundle_usage workspace-scope call failed. result: {result}"
    )

    out = result.get("output", {})
    inventory_count = len([k for k in out.get("inventory", {}) if k != "_meta"])

    assert inventory_count >= 30, (
        f"Expected workspace inventory to contain >= 30 installed bundles "
        f"but found {inventory_count}. "
        f"Inventory keys (excluding _meta): "
        f"{[k for k in out.get('inventory', {}) if k != '_meta']}. "
        "Verify that the workspace-scope path performs a full bundle cache scan "
        "and that ~/.amplifier/cache contains at least 30 bundle directories."
    )


def test_aggregate_matches_known_active_bundles(dtu_session):
    """Aggregate invocation counts for active bundles match graph-analyst figures.

    Activates the bundle-usage mode, then calls the bundle_usage tool without
    a session_id (workspace scope).  The aggregate invocation totals for the
    three most-active bundles must match the reference figures captured by
    graph-analyst within a generous ±15 drift allowance.

    Reference figures (graph-analyst workspace scan):
      - foundation:        ≈ 42 total invocations
      - superpowers:       ≈ 27 total invocations
      - context-intelligence: ≈ 22 total invocations

    DRIFT = 15 — accommodates session churn, new sessions added since the
    reference measurement, and minor query differences.

    Assertions:
      - For each (name, expected) in
        [("foundation", 42), ("superpowers", 27), ("context-intelligence", 22)]:
        ``abs(_total_invocations(sig.get(name, {})) - expected) <= DRIFT``

    Diagnosis checklist on failure:
      - A zero-count for a named bundle means the workspace aggregate is not
        picking up signals for that bundle.  Verify the Cypher workspace-scope
        query returns rows for all sessions, not just the ground-truth session.
      - A large deviation (> 15) may indicate the reference session corpus has
        grown significantly.  Update the expected values to the new baseline.
      - If ``sig`` is empty, the signals layer in workspace mode may be
        returning nothing; check the workspace-scope signals query.
    """
    DRIFT = 15

    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage")

    assert result.get("success") is True, (
        f"bundle_usage workspace-scope call failed. result: {result}"
    )

    out = result.get("output", {})
    sig = out.get("signals", {})

    known_active = [
        ("foundation", 42),
        ("superpowers", 27),
        ("context-intelligence", 22),
    ]

    for name, expected in known_active:
        actual = _total_invocations(sig.get(name, {}))
        assert abs(actual - expected) <= DRIFT, (
            f"Workspace aggregate invocation count for '{name}' is out of range. "
            f"Expected ≈{expected} (±{DRIFT}) but got {actual}. "
            f"Bundle signals entry: {sig.get(name, 'ABSENT')}. "
            "The workspace-scope query may not be aggregating all sessions, "
            "or the reference corpus has changed significantly."
        )


def test_dormant_bundles_have_zero_invocations(dtu_session):
    """Dormant bundles (not in the known-active set) must report zero invocations.

    Activates the bundle-usage mode, then calls the bundle_usage tool without
    a session_id (workspace scope).  Any bundle NOT in the known-active set
    should have zero total invocations.  At most 2 exceptions are tolerated
    to allow for natural noise (occasional one-off invocations that are not
    part of a regular workflow).

    known-active set: {foundation, superpowers, context-intelligence, recipes,
    parallax-discovery}.

    Assertions:
      - ``len(dormant_with_invocations) <= 2``
        where ``dormant_with_invocations`` is the dict of bundles outside the
        known-active set that nonetheless have _total_invocations > 0.

    Diagnosis checklist on failure:
      - If > 2 dormant bundles show non-zero counts, either:
        (a) the workspace signal query is returning spurious rows (verify Cypher
            queries S-1..S-13 for false positives), or
        (b) the reference workspace has genuinely expanded usage to additional
            bundles.  In the latter case, add the new active bundles to the
            known-active set.
      - Print ``dormant_with_invocations`` to identify which bundles triggered
        the assertion.
    """
    active = {
        "foundation",
        "superpowers",
        "context-intelligence",
        "recipes",
        "parallax-discovery",
    }

    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage")

    assert result.get("success") is True, (
        f"bundle_usage workspace-scope call failed. result: {result}"
    )

    out = result.get("output", {})
    sig = out.get("signals", {})

    dormant_with_invocations = {
        name: counts
        for name, counts in sig.items()
        if name not in active and _total_invocations(counts) > 0
    }

    assert len(dormant_with_invocations) <= 2, (
        f"Expected at most 2 dormant bundles with non-zero invocations "
        f"but found {len(dormant_with_invocations)}: {dormant_with_invocations}. "
        "Either the workspace has expanded usage to new bundles (update the "
        "known-active set) or the workspace-scope signal query is producing "
        "false-positive rows (check Cypher queries S-1..S-13)."
    )
