"""
Scenario 2 — Bundle usage detection against ground-truth session.

Verifies that the bundle_usage tool correctly detects which bundles were used
in a known ground-truth session (21d92985-34a9-40ed-8636-f77cd61b7ca1):

  Known truth:
    - foundation:explorer was invoked exactly once (duration ~4m53s, success)
    - No other bundle invocations occurred

Pass criteria (exact match, no false positives or negatives):
  - signals["foundation"]["agents"] >= 1
  - sum(agents across all OTHER bundles) == 0
  - sum(skills + modes + recipes across ALL bundles) == 0

Both tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations

from .conftest import KNOWN_SESSION_ID


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detects_foundation_explorer_only(dtu_session):
    """bundle_usage tool must detect foundation:explorer in the ground-truth session.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The session is known to contain exactly one
    agent invocation: ``foundation:explorer``.

    Assertions:
      - The tool call succeeds (``result["success"] is True``).
      - The ``signals`` dict contains a ``"foundation"`` key.
      - ``signals["foundation"]["agents"] >= 1`` (at least one agent invocation
        for the foundation bundle).

    Diagnosis checklist on failure:
      - Confirm the ground-truth session exists in the CI graph.
      - Verify Cypher queries S-1..S-2 (agents) return rows for session
        ``21d92985-34a9-40ed-8636-f77cd61b7ca1``.
      - Check that the bundle name returned by the graph is ``"foundation"``.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert result.get("success") is True, f"bundle_usage tool call failed. result: {result}"

    signals = result.get("output", {}).get("signals", {})

    assert "foundation" in signals, (
        "Expected 'foundation' bundle in signals but it was absent. "
        f"Observed signals keys: {list(signals.keys())}"
    )

    assert signals["foundation"]["agents"] >= 1, (
        "Expected signals['foundation']['agents'] >= 1 "
        f"but got {signals['foundation']['agents']}. "
        "The ground-truth session contains foundation:explorer invocation."
    )


def test_no_false_positive_invocations(dtu_session):
    """bundle_usage tool must NOT report any non-foundation or non-agent invocations.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The session is known to contain:
      - exactly one agent invocation (foundation:explorer)
      - zero invocations of any other bundle
      - zero skill, mode, or recipe invocations across all bundles

    Assertions:
      - ``other_agents`` (sum of agent counts for all bundles except foundation)
        must equal 0.
      - ``total_other_components`` (sum of skills + modes + recipes across ALL
        bundles) must equal 0.

    Diagnosis checklist on failure:
      - A non-zero ``other_agents`` means a false-positive agent detection;
        check Cypher query S-1..S-2 for spurious rows.
      - A non-zero ``total_other_components`` means skills, modes, or recipes
        were incorrectly attributed; check queries S-3..S-13 for that session.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert result.get("success") is True, f"bundle_usage tool call failed. result: {result}"

    signals = result.get("output", {}).get("signals", {})

    other_agents = sum(b["agents"] for name, b in signals.items() if name != "foundation")
    assert other_agents == 0, (
        f"False positive: detected agent invocations in non-foundation bundles. "
        f"other_agents={other_agents}. "
        f"Signals by bundle: { {k: v['agents'] for k, v in signals.items() if k != 'foundation'} }"
    )

    total_other_components = sum(
        b.get("skills", 0) + b.get("modes", 0) + b.get("recipes", 0) for b in signals.values()
    )
    assert total_other_components == 0, (
        f"False positive: detected skill/mode/recipe invocations across bundles. "
        f"total_other_components={total_other_components}. "
        f"Signals breakdown: { {k: {c: v[c] for c in ('skills', 'modes', 'recipes')} for k, v in signals.items()} }"
    )
