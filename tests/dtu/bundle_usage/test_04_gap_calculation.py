"""
Scenario 4 — Bundle gap calculation correctness (Layer 1 + Layer 2).

Verifies that the bundle_usage tool correctly computes the coverage gap between
observed signals (Layer 1) and declared bundle inventory (Layer 2):

  1. Arithmetic consistency — util_gap is non-negative for every bundle/component.
  2. Foundation gap accuracy — declared >1 agent, used >= 1 (explorer plus
     any other foundation agents), util_gap is consistent with that arithmetic.
  3. Improvement entries — well-formed dicts with required keys and valid types.

All tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations

from .conftest import KNOWN_SESSION_ID


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_gap_arithmetic_consistent(dtu_session):
    """util_gap must be non-negative for every bundle and component type.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  For every bundle in the gap output and for
    each component dimension (agents, skills, modes, recipes), the utilisation
    gap must be >= 0.  A negative value would indicate a used > declared
    arithmetic inconsistency.

    Assertions:
      - For each bundle b and each k in (agents, skills, modes, recipes):
        ``per_bundle[b]["util_gap"][k] >= 0``.

    Diagnosis checklist on failure:
      - A negative util_gap means the signals layer reported more invocations
        than the inventory layer declared.  Check that compute_gap clamps
        util_gap to max(0, declared - used).
      - Verify the inventory scan returns accurate declared counts for the
        affected bundle.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "gap" in result, (
        f"Expected 'gap' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    per_bundle = result["gap"].get("per_bundle", {})

    for bundle_name, pb in per_bundle.items():
        for k in ("agents", "skills", "modes", "recipes"):
            gap_val = pb.get("util_gap", {}).get(k, 0)
            assert gap_val >= 0, (
                f"Arithmetic inconsistency: util_gap[{k!r}] < 0 for bundle {bundle_name!r}. "
                f"util_gap={pb.get('util_gap')}, "
                f"declared={pb.get('declared')}, "
                f"used={pb.get('used')}"
            )


def test_foundation_util_gap_present(dtu_session):
    """Foundation bundle must appear in gap with declared > 1 agent and used >= 1.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The foundation bundle must appear in the
    per_bundle gap map.  The ground-truth session invoked ``foundation:explorer``
    plus several ``foundation:git-ops`` calls, so the foundation used.agents must
    be >= 1 (at least one agent was called), and declared.agents must be > 1
    (multiple agents are declared by foundation).

    Assertions:
      - ``per_bundle["foundation"]`` is not None.
      - ``pb["declared"]["agents"] > 1`` (foundation declares multiple agents;
        failing this means the inventory scan failed to find them).
      - ``pb["used"]["agents"] >= 1`` (at least one foundation agent was invoked).
      - ``pb["util_gap"]["agents"] >= max(0, declared - used)``.

    Diagnosis checklist on failure:
      - A missing "foundation" entry means the inventory scanner did not find
        the foundation bundle in the local cache or the gap layer skipped it.
      - declared.agents <= 1 means the inventory scan failed to enumerate the
        foundation bundle's agent declarations.  Check bundle cache at
        ``~/.amplifier/cache/foundation/``.
      - used.agents == 0 means the signals layer returned zero agent invocations.
        Verify the Cypher query for the ground-truth session returns foundation rows.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "gap" in result, (
        f"Expected 'gap' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    pb = result["gap"].get("per_bundle", {}).get("foundation")

    assert pb is not None, (
        "Expected 'foundation' bundle in gap.per_bundle but it was absent. "
        f"Observed per_bundle keys: {list(result['gap'].get('per_bundle', {}).keys())}"
    )

    assert pb["declared"]["agents"] > 1, (
        "Expected gap.per_bundle['foundation']['declared']['agents'] > 1 "
        f"but got {pb['declared']['agents']}. "
        "The inventory scan failed to enumerate foundation's declared agents — "
        "check that ~/.amplifier/cache/foundation/ contains agent definitions."
    )

    assert pb["used"]["agents"] >= 1, (
        "Expected gap.per_bundle['foundation']['used']['agents'] >= 1 "
        f"but got {pb['used']['agents']}. "
        "The ground-truth session invokes multiple foundation agents "
        "(explorer, git-ops, etc.); at least one must appear in the signals."
    )

    used_agents = pb["used"]["agents"]
    declared_agents = pb["declared"]["agents"]
    expected_min_gap = max(0, declared_agents - used_agents)
    assert pb["util_gap"]["agents"] >= expected_min_gap, (
        f"Expected gap.per_bundle['foundation']['util_gap']['agents'] >= {expected_min_gap} "
        f"but got {pb['util_gap']['agents']}. "
        f"declared={declared_agents}, used={used_agents}"
    )


def test_improvement_entries_well_formed(dtu_session):
    """Improvement list must be non-empty and every entry must be well-formed.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The improvement list must contain at least
    one entry.  Each entry must have exactly the required keys (bundle, type,
    reason) and the type value must be one of the recognised improvement
    categories.

    Assertions:
      - ``isinstance(imp, list) and imp`` (non-empty list).
      - For each entry: ``set(entry) >= {"bundle", "type", "reason"}``.
      - For each entry: ``entry["type"] in valid_types`` where
        ``valid_types = {"tree-shake", "mode-refactor", "config-gap",
        "discovery-eval"}``.

    Diagnosis checklist on failure:
      - An empty improvement list means no bundles were classified for
        improvement — even with many installed but unused bundles this should
        produce at least one tree-shake candidate.
      - A missing key means compute_gap returned a malformed improvement entry.
        Check the improvement dict construction in gap.py.
      - An unexpected type means a new improvement category was added without
        updating this test.  Add it to valid_types or correct the type in
        gap.py.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "gap" in result, (
        f"Expected 'gap' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    imp = result["gap"].get("improvement")

    assert isinstance(imp, list) and imp, (
        "Expected gap.improvement to be a non-empty list. "
        f"Got: {imp!r}. "
        "The ground-truth session should produce at least one improvement "
        "classification (tree-shake, mode-refactor, or config-gap)."
    )

    valid_types = {"tree-shake", "mode-refactor", "config-gap", "discovery-eval"}
    required_keys = {"bundle", "type", "reason"}

    for i, entry in enumerate(imp):
        missing_keys = required_keys - set(entry)
        assert not missing_keys, (
            f"Improvement entry [{i}] is missing required keys: {missing_keys}. Entry: {entry!r}"
        )

        assert entry["type"] in valid_types, (
            f"Improvement entry [{i}] has unexpected type: {entry['type']!r}. "
            f"Valid types: {valid_types}. "
            f"Entry: {entry!r}"
        )
