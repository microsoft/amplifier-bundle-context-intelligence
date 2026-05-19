"""
Scenario 8 — Improvement action generation report with required categories.

Verifies that the bundle-usage-analyst agent, when asked for a workspace-scope
improvement report, produces output with:

  1. 🌳 TREE-SHAKE section present — at least one bundle entry with bullet items
     that name specific components and cite evidence (invocation counts, session
     counts, or threshold percentages).
  2. Passive-value caveat — the report must include the phrase
     'passive value unclear — manual review required' (case-insensitive check
     for 'passive value' and 'manual review').
  3. Separate categories — tree-shake and config-gap must appear as distinct
     sections, not collapsed into a single "unused" bucket.

All three tests depend on the ``dtu_session`` fixture from conftest.py, which
is in turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.
If ``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tree_shake_section_populated(dtu_session, tmp_path):
    """bundle-usage-analyst must produce a 🌳 TREE-SHAKE section with bullet entries.

    Activates bundle-usage mode, then delegates a workspace-scope improvement
    report request to ``context-intelligence:bundle-usage-analyst``.  The agent
    must write a report with three distinct categories (🌳 TREE-SHAKE,
    ⚙️ MODE-REFACTOR, 🚩 CONFIG-GAP) and the tree-shake section must contain
    at least one bullet entry naming a specific bundle.

    Assertions:
      - The report file exists at ``tmp_path / "improve.md"``.
      - The report contains either the 🌳 emoji or the text "TREE-SHAKE".
      - A regex capturing the tree-shake section body is non-None.
      - The tree-shake body contains at least one bullet entry (``- `` or
        ``* `` followed by a non-whitespace character).

    Diagnosis checklist on failure:
      - If the file is absent: the analyst failed to call write_file; verify
        the prompt explicitly names the output path.
      - If the 🌳/TREE-SHAKE marker is absent: the analyst did not structure the
        report with the required category headers.
      - If the tree-shake section is None: the section boundary regex did not
        match; check that the agent emitted the section header and at least
        one subsequent section or end-of-string.
      - If no bullet entries: the analyst listed the section but included no
        entries; the prompt asks for specific bundle names with evidence.
    """
    report_path = tmp_path / "improve.md"
    prompt = (
        f"Run workspace-scope bundle-usage analysis. "
        f"Produce an improvement report with three distinct categories: "
        f"🌳 TREE-SHAKE, ⚙️ MODE-REFACTOR, 🚩 CONFIG-GAP. "
        f"For each entry include the bundle name and the evidence "
        f"(invocation counts). "
        f"Include the caveat 'passive value unclear — manual review required'. "
        f"Write to {report_path}."
    )

    dtu_session.activate_mode("bundle-usage")
    dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert report_path.exists(), (
        f"Expected improvement report at {report_path} but file was not found. "
        "The analyst must call write_file with the exact path given in the prompt. "
        "Check that the agent's write_file tool has write access to tmp_path."
    )

    report = report_path.read_text()

    assert "🌳" in report or "TREE-SHAKE" in report, (
        "Expected the report to contain either the 🌳 emoji or the text "
        "'TREE-SHAKE' as a section header but neither was found. "
        "The analyst must produce a tree-shake category in the improvement report. "
        f"Report content (first 500 chars): {report[:500]!r}"
    )

    tree_section = re.search(
        r"(?:🌳|TREE-SHAKE)(.*?)(?:⚙️|MODE-REFACTOR|🚩|CONFIG-GAP|$)",
        report,
        re.DOTALL,
    )
    assert tree_section is not None, (
        "Expected a tree-shake section body between the 🌳/TREE-SHAKE header and "
        "the next section (⚙️/MODE-REFACTOR, 🚩/CONFIG-GAP, or end of string) "
        "but the regex did not match. "
        "The analyst must emit the section header followed by entry content. "
        f"Report content (first 500 chars): {report[:500]!r}"
    )

    body = tree_section.group(1)
    assert re.search(r"[-*]\s+\S", body), (
        "Expected at least one bullet entry (starting with '- ' or '* ') in the "
        "tree-shake section body but none were found. "
        "Each entry must name a specific bundle component. "
        f"Tree-shake section body (first 300 chars): {body[:300]!r}"
    )


def test_passive_value_caveat_present(dtu_session, tmp_path):
    """bundle-usage-analyst must include the passive-value manual-review caveat.

    Delegates a workspace improvement report request that explicitly asks for
    the manual-review caveat.  The report text (lowercased) must contain both
    'passive value' and 'manual review'.

    Assertions:
      - The report file exists at ``tmp_path / "caveat.md"``.
      - ``"passive value" in report.lower()`` is True.
      - ``"manual review" in report.lower()`` is True.

    Diagnosis checklist on failure:
      - If the file is absent: the analyst failed to call write_file.
      - If 'passive value' is absent: the analyst omitted the required caveat;
        the prompt explicitly requests it.
      - If 'manual review' is absent: same — the full caveat phrase must appear.
    """
    report_path = tmp_path / "caveat.md"
    prompt = (
        f"Workspace improvement report — include the manual-review caveat "
        f"'passive value unclear — manual review required'. "
        f"Write to {report_path}."
    )

    dtu_session.activate_mode("bundle-usage")
    dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert report_path.exists(), (
        f"Expected caveat report at {report_path} but file was not found. "
        "The analyst must call write_file with the exact path given in the prompt."
    )

    report = report_path.read_text()
    text = report.lower()

    assert "passive value" in text, (
        "Expected the phrase 'passive value' (case-insensitive) in the improvement "
        "report but it was absent. "
        "The analyst must include the caveat 'passive value unclear — manual review "
        "required'. "
        f"Report content (first 500 chars): {report[:500]!r}"
    )
    assert "manual review" in text, (
        "Expected the phrase 'manual review' (case-insensitive) in the improvement "
        "report but it was absent. "
        "The analyst must include the caveat 'passive value unclear — manual review "
        "required'. "
        f"Report content (first 500 chars): {report[:500]!r}"
    )


def test_categories_not_collapsed(dtu_session, tmp_path):
    """bundle-usage-analyst must keep tree-shake and config-gap as separate sections.

    Delegates a workspace improvement report request that explicitly asks the
    analyst to keep the two lowest-signal categories (tree-shake and config-gap)
    as distinct sections rather than collapsing them into a generic "unused"
    bucket.  Both 'tree-shake' and 'config-gap' (or 'config gap') must appear
    in the lowercased report text.

    Assertions:
      - The report file exists at ``tmp_path / "sep.md"``.
      - ``"tree-shake" in report.lower()`` is True.
      - ``"config-gap" in report.lower()`` or ``"config gap" in report.lower()``
        is True.

    Diagnosis checklist on failure:
      - If the file is absent: the analyst failed to call write_file.
      - If 'tree-shake' is absent: the analyst collapsed or renamed the
        tree-shake category; the prompt requires it to be a distinct section.
      - If neither 'config-gap' nor 'config gap' is present: the config-gap
        category was omitted or merged with tree-shake into a generic bucket.
    """
    report_path = tmp_path / "sep.md"
    prompt = (
        f"Workspace improvement report — keep tree-shake and config-gap as "
        f"separate sections, not collapsed into a single 'unused' category. "
        f"Write to {report_path}."
    )

    dtu_session.activate_mode("bundle-usage")
    dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert report_path.exists(), (
        f"Expected separation report at {report_path} but file was not found. "
        "The analyst must call write_file with the exact path given in the prompt."
    )

    report = report_path.read_text()
    text = report.lower()

    assert "tree-shake" in text, (
        "Expected the text 'tree-shake' (case-insensitive) in the improvement "
        "report but it was absent. "
        "The analyst must keep tree-shake as a distinct section, not merged into "
        "a generic 'unused' category. "
        f"Report content (first 500 chars): {report[:500]!r}"
    )
    assert "config-gap" in text or "config gap" in text, (
        "Expected 'config-gap' or 'config gap' (case-insensitive) in the "
        "improvement report but neither was found. "
        "The analyst must keep config-gap as a distinct section separate from "
        "tree-shake. "
        f"Report content (first 500 chars): {report[:500]!r}"
    )
