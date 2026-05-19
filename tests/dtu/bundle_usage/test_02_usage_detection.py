"""
Scenario 2 — Bundle usage detection against ground-truth session.

Verifies that the bundle_usage tool correctly detects which bundles were used
in a known ground-truth session (21d92985-34a9-40ed-8636-f77cd61b7ca1):

  Known truth (verified via direct Cypher queries against CI graph):
    - foundation bundle: 8 agent invocations (7× git-ops, 1× explorer)
    - amplifier-tester bundle: 1 agent invocation (setup-digital-twin)
    - No other bundles, no skills/modes/recipes in either bundle

Pass criteria:
  - signals["foundation"]["agents"] >= 1
  - signals["amplifier-tester"]["agents"] >= 1
  - No bundles OTHER than foundation and amplifier-tester appear in signals
  - sum(skills + modes + recipes across ALL bundles) == 0

Both tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
the DTU is not available the entire session is skipped gracefully.
"""

from __future__ import annotations

from .conftest import KNOWN_SESSION_ID


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_KNOWN_BUNDLES = frozenset({"foundation", "amplifier-tester"})


def test_detects_foundation_and_amplifier_tester(dtu_session):
    """bundle_usage tool must detect both foundation and amplifier-tester bundles.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The session contains:
      - foundation bundle: 8 agent invocations (7× git-ops, 1× explorer)
      - amplifier-tester bundle: 1 agent invocation (setup-digital-twin)

    Assertions:
      - The tool call returns a result with a ``"signals"`` key.
      - The ``signals`` dict contains a ``"foundation"`` key with agents >= 1.
      - The ``signals`` dict contains an ``"amplifier-tester"`` key with agents >= 1.

    Diagnosis checklist on failure:
      - Confirm the ground-truth session exists in the CI graph.
      - Verify Cypher queries S-1..S-2 return rows for session
        ``21d92985-34a9-40ed-8636-f77cd61b7ca1``.
      - Check that bundle names returned by the graph are ``"foundation"`` and
        ``"amplifier-tester"``.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "signals" in result, (
        f"Expected 'signals' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    signals = result["signals"]

    assert "foundation" in signals, (
        "Expected 'foundation' bundle in signals but it was absent. "
        f"Observed signals keys: {list(signals.keys())}"
    )
    assert signals["foundation"]["agents"] >= 1, (
        "Expected signals['foundation']['agents'] >= 1 "
        f"but got {signals['foundation']['agents']}. "
        "The ground-truth session contains 8 foundation agent invocations."
    )

    assert "amplifier-tester" in signals, (
        "Expected 'amplifier-tester' bundle in signals but it was absent. "
        f"Observed signals keys: {list(signals.keys())}"
    )
    assert signals["amplifier-tester"]["agents"] >= 1, (
        "Expected signals['amplifier-tester']['agents'] >= 1 "
        f"but got {signals['amplifier-tester']['agents']}. "
        "The ground-truth session contains 1 amplifier-tester agent invocation."
    )


def test_no_false_positive_invocations(dtu_session):
    """bundle_usage tool must NOT report any unexpected bundles or component types.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The session is known to contain agent
    invocations for EXACTLY two bundles: ``foundation`` and ``amplifier-tester``.
    No other bundles appear, and no skills, modes, or recipes were invoked.

    Assertions:
      - No bundle OTHER than ``foundation`` or ``amplifier-tester`` appears in
        signals (zero false-positive bundle detections).
      - ``total_other_components`` (sum of skills + modes + recipes across ALL
        bundles) equals 0.

    Diagnosis checklist on failure:
      - Unexpected bundle name → check Cypher queries S-1..S-2 for spurious rows.
      - Non-zero ``total_other_components`` → check queries S-3..S-13.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "signals" in result, (
        f"Expected 'signals' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    signals = result["signals"]

    unexpected_bundles = {name for name in signals if name not in _KNOWN_BUNDLES}
    assert not unexpected_bundles, (
        f"False positive: unexpected bundle(s) detected in signals: {unexpected_bundles}. "
        f"Only 'foundation' and 'amplifier-tester' are expected for this session. "
        f"Full signals keys: {list(signals.keys())}"
    )

    total_other_components = sum(
        b.get("skills", 0) + b.get("modes", 0) + b.get("recipes", 0) for b in signals.values()
    )
    assert total_other_components == 0, (
        f"False positive: detected skill/mode/recipe invocations across bundles. "
        f"total_other_components={total_other_components}. "
        f"Signals breakdown: { {k: {c: v[c] for c in ('skills', 'modes', 'recipes')} for k, v in signals.items()} }"
    )
