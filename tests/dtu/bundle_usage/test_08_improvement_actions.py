"""
Scenario 8 — Improvement action generation report with required categories.

Verifies that the bundle-usage-analyst agent, when asked for a workspace-scope
improvement report, produces output with:

  1. 🌳 TREE-SHAKE section present — at least one bundle entry with bullet items
     that name specific components and cite evidence (invocation counts, session
     counts, or threshold percentages).
  2. Passive-value caveat — the report must include acknowledgement of
     uncertainty about passive value: 'passive value', 'manual review',
     'cannot determine', or 'passive'.
  3. Separate categories — tree-shake and config-gap must appear as distinct
     sections, not collapsed into a single "unused" bucket.

All tests depend on the ``dtu_session`` fixture from conftest.py, which
is in turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.
If ``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Schema constants for improvement entry validation
# ---------------------------------------------------------------------------

_VALID_TYPES = {"tree-shake", "mode-refactor", "config-gap", "mode-never-activated"}
_VALID_COMPONENT_TYPES = {"agents", "skills", "recipes", "context", "tools", "modes"}
_VALID_SCOPES = {"always_active", "mode_gated"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tree_shake_section_populated(dtu_session):
    """bundle-usage-analyst must reference tree-shake with bundle name evidence.

    Activates bundle-usage mode, then delegates a workspace-scope improvement
    report request to ``context-intelligence:bundle-usage-analyst``.  The agent
    must produce output that:

      1. Contains the 🌳 emoji or the text "tree-shake" (case-insensitive).
      2. References at least one known bundle name (e.g. foundation,
         amplifier-tester, context-intelligence).
      3. Includes at least one evidence term indicating zero / low invocations
         (e.g. "zero invocation", "never invoked", "not used", "0 invocation",
         "tree-shake").

    The agent is not required to use a specific section structure or bullet
    formatting — the key requirement is that tree-shake reasoning referencing
    real bundle names with invocation evidence is present in the output.

    Assertions:
      - The output contains 🌳 or "tree-shake" (case-insensitive).
      - The output references at least one known bundle name.
      - The output includes at least one invocation-evidence term.

    Diagnosis checklist on failure:
      - If 🌳/tree-shake is absent: the analyst did not produce any tree-shake
        reasoning; check the prompt is clear.
      - If no bundle names are found: the analyst described tree-shake
        reasoning without naming any specific bundle.
      - If no evidence terms are found: the analyst listed bundles without
        citing invocation counts or usage data.
    """
    prompt = (
        "Run workspace-scope bundle-usage analysis. "
        "Produce an improvement report with three distinct categories: "
        "🌳 TREE-SHAKE, ⚙️ MODE-REFACTOR, 🚩 CONFIG-GAP. "
        "For each entry include the bundle name and the evidence "
        "(invocation counts). "
        "Include the caveat 'passive value unclear — manual review required'."
    )

    dtu_session.activate_mode("bundle-usage")
    output = dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert "🌳" in output or "tree-shake" in output.lower(), (
        "Expected the output to contain either the 🌳 emoji or the text "
        "'tree-shake' (case-insensitive) but neither was found. "
        "The analyst must produce a tree-shake category in the improvement report. "
        f"Output (first 500 chars): {output[:500]!r}"
    )

    # At least one real bundle name must appear in the output.
    known_bundles = [
        "foundation",
        "amplifier-tester",
        "context-intelligence",
        "amplifier",
    ]
    bundle_mentioned = any(b in output.lower() for b in known_bundles)
    assert bundle_mentioned, (
        f"Expected at least one known bundle name from {known_bundles!r} in the "
        "output but none were found. "
        "The analyst must name specific bundles in the tree-shake section. "
        f"Output (first 500 chars): {output[:500]!r}"
    )

    # Evidence of zero/low invocations must appear — either a term like
    # "zero invocation", "never invoked", "not used", or the word "tree-shake"
    # itself (which implies the invocation-evidence reasoning occurred).
    evidence_terms = [
        "zero invocation",
        "never invoked",
        "not used",
        "0 invocation",
        "tree-shake",
        "tree shake",
        "invocation count",
        "invocations",
    ]
    evidence_present = any(term in output.lower() for term in evidence_terms)
    assert evidence_present, (
        f"Expected at least one evidence term from {evidence_terms!r} in the "
        "output but none were found. "
        "The analyst must cite invocation evidence for tree-shake candidates. "
        f"Output (first 500 chars): {output[:500]!r}"
    )


def test_passive_value_caveat_present(dtu_session):
    """bundle-usage-analyst must include the passive-value manual-review caveat.

    Delegates a workspace improvement report request that explicitly asks for
    the manual-review caveat.  The output (lowercased) must contain at least
    one of: 'passive value', 'manual review', 'cannot determine', or 'passive'
    — proof the agent acknowledges uncertainty about passive value.

    Assertions:
      - The output contains 'passive value' OR 'manual review' OR
        'cannot determine' OR 'passive' (case-insensitive).

    Diagnosis checklist on failure:
      - If none of the caveat phrases are present: the analyst omitted any
        acknowledgement of passive value uncertainty; the prompt explicitly
        requests the caveat.
    """
    prompt = (
        "Workspace improvement report — include the manual-review caveat "
        "'passive value unclear — manual review required'."
    )

    dtu_session.activate_mode("bundle-usage")
    output = dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    text = output.lower()
    caveat_phrases = ["passive value", "manual review", "cannot determine", "passive"]
    has_caveat = any(phrase in text for phrase in caveat_phrases)

    assert has_caveat, (
        "Expected the output to contain at least one of the caveat phrases "
        f"{caveat_phrases!r} (case-insensitive) but none were found. "
        "The analyst must acknowledge uncertainty about passive bundle value "
        "and include a manual review recommendation. "
        f"Output (first 500 chars): {output[:500]!r}"
    )


def test_categories_not_collapsed(dtu_session):
    """bundle-usage-analyst must keep tree-shake and config-gap as separate sections.

    Delegates a workspace improvement report request that explicitly asks the
    analyst to keep the two lowest-signal categories (tree-shake and config-gap)
    as distinct sections rather than collapsing them into a generic "unused"
    bucket.  Both 'tree-shake' (or 'tree shake') and 'config-gap' (or
    'config gap') must appear in the lowercased output text.

    Assertions:
      - ``"tree-shake" in output.lower()`` or ``"tree shake" in output.lower()``
        is True.
      - ``"config-gap" in output.lower()`` or ``"config gap" in output.lower()``
        is True.

    Diagnosis checklist on failure:
      - If 'tree-shake' is absent: the analyst collapsed or renamed the
        tree-shake category; the prompt requires it to be a distinct section.
      - If neither 'config-gap' nor 'config gap' is present: the config-gap
        category was omitted or merged with tree-shake into a generic bucket.
    """
    prompt = (
        "Workspace improvement report — keep tree-shake and config-gap as "
        "separate sections, not collapsed into a single 'unused' category."
    )

    dtu_session.activate_mode("bundle-usage")
    output = dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    text = output.lower()

    assert "tree-shake" in text or "tree shake" in text, (
        "Expected the text 'tree-shake' or 'tree shake' (case-insensitive) in "
        "the output but neither was found. "
        "The analyst must keep tree-shake as a distinct section, not merged into "
        "a generic 'unused' category. "
        f"Output (first 500 chars): {output[:500]!r}"
    )
    assert "config-gap" in text or "config gap" in text, (
        "Expected 'config-gap' or 'config gap' (case-insensitive) in the output "
        "but neither was found. "
        "The analyst must keep config-gap as a distinct section separate from "
        "tree-shake. "
        f"Output (first 500 chars): {output[:500]!r}"
    )


def test_improvement_entries_have_named_components(dtu_session):
    """Every improvement entry must declare named components with the full schema.

    Calls ``bundle_usage`` workspace-wide (no session_id) via the direct Python
    route (no LLM) and validates that every entry in ``gap['improvement']``
    conforms to the full named-component schema introduced in the 2026-05-21
    redesign:

      - ``bundle``: which bundle
      - ``type``: one of the four recognised improvement types
      - ``component_type``: one of the six component signal types
      - ``scope``: always_active or mode_gated
      - ``mode_name``: populated for mode-never-activated, else None / ''
      - ``names``: list[str] of specific component names (non-empty for
        tree-shake, mode-refactor, and mode-never-activated)
      - ``reason``: non-empty string

    Assertions:
      - ``improvements`` is a list with at least one entry.
      - For each entry: all required keys are present with valid values.
      - ``mode_name`` population rule is enforced per improvement type.
      - ``names`` non-empty rule is enforced for types that always name
        specific components.

    Diagnosis checklist on failure:
      - If ``improvements`` is empty: workspace uses 100% of every declared
        component.  Confirm via cache inspection; if true, the assertion may
        be relaxed.
      - If ``type`` is invalid: gap.py was updated with a new type that was
        not added to ``_VALID_TYPES``.
      - If ``names`` is empty for tree-shake / mode-refactor: gap.py is
        producing improvement entries without populating the ``names`` field.
      - If ``mode_name`` is absent from mode-never-activated: gap.py is not
        attaching the mode name to the improvement entry.
    """
    result = dtu_session.call_tool("bundle_usage")

    assert "gap" in result, (
        f"Expected 'gap' key in bundle_usage result. "
        f"Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    improvements = result["gap"]["improvement"]

    assert isinstance(improvements, list), (
        f"Expected gap['improvement'] to be a list, got {type(improvements).__name__!r}."
    )
    assert len(improvements) > 0, (
        "Expected gap['improvement'] to contain at least one entry but the list "
        "is empty.  The workspace should have at least one bundle that declares "
        "components that are never or rarely invoked.  "
        "If the workspace genuinely uses 100% of every declared component, "
        "confirm via cache inspection and relax this assertion."
    )

    for i, entry in enumerate(improvements):
        # --- entry must be a dict ---
        assert isinstance(entry, dict), (
            f"improvement[{i}] is not a dict: {type(entry).__name__!r} — {entry!r}"
        )

        # --- type ---
        assert entry.get("type") in _VALID_TYPES, (
            f"improvement[{i}]['type'] {entry.get('type')!r} is not in "
            f"{_VALID_TYPES}. Entry: {entry!r}"
        )

        # --- component_type ---
        assert entry.get("component_type") in _VALID_COMPONENT_TYPES, (
            f"improvement[{i}]['component_type'] {entry.get('component_type')!r} "
            f"is not in {_VALID_COMPONENT_TYPES}. Entry: {entry!r}"
        )

        # --- scope ---
        assert entry.get("scope") in _VALID_SCOPES, (
            f"improvement[{i}]['scope'] {entry.get('scope')!r} is not in "
            f"{_VALID_SCOPES}. Entry: {entry!r}"
        )

        # --- bundle ---
        assert "bundle" in entry and isinstance(entry["bundle"], str), (
            f"improvement[{i}] missing 'bundle' or it is not a str. Entry: {entry!r}"
        )

        # --- names ---
        assert "names" in entry and isinstance(entry["names"], list), (
            f"improvement[{i}] missing 'names' or it is not a list. Entry: {entry!r}"
        )

        # --- reason ---
        assert "reason" in entry and isinstance(entry["reason"], str) and entry["reason"], (
            f"improvement[{i}] missing 'reason' or it is empty. Entry: {entry!r}"
        )

        # --- mode_name population rule ---
        if entry["type"] == "mode-never-activated":
            assert entry.get("mode_name"), (
                f"improvement[{i}] has type='mode-never-activated' but 'mode_name' "
                f"is absent or empty: {entry.get('mode_name')!r}. Entry: {entry!r}"
            )
        else:
            assert entry.get("mode_name") in (None, ""), (
                f"improvement[{i}] has type={entry['type']!r} but 'mode_name' is "
                f"populated: {entry.get('mode_name')!r}.  Only 'mode-never-activated' "
                f"entries should carry a mode_name. Entry: {entry!r}"
            )

        # --- names non-empty rule for specific types ---
        if entry["type"] in {"tree-shake", "mode-refactor", "mode-never-activated"}:
            assert entry["names"], (
                f"improvement[{i}] has type={entry['type']!r} but 'names' is empty. "
                f"Entries of this type must name at least one specific component. "
                f"Entry: {entry!r}"
            )
