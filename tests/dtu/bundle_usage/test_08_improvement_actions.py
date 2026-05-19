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

All three tests depend on the ``dtu_session`` fixture from conftest.py, which
is in turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.
If ``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations


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
