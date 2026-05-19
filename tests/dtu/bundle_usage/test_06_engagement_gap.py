"""
Scenario 6 — Engagement gap reasoning via delegation to bundle-usage-analyst.

Verifies that the bundle-usage-analyst agent, when delegated an engagement-gap
analysis request, performs Layer 3 reasoning correctly:

  1. Engagement categories — the analyst must categorise foundation context files
     as 'gap', 'informational only', or 'used'. At least one file must be
     classified as 'informational only' (not every unused file is a gap).
  2. Content citation — the analyst must cite specific content from at least one
     bundle file (proof that read_file was used, not just signal data).

Both tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations

import re

from .conftest import KNOWN_SESSION_ID


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_analyst_produces_engagement_categories(dtu_session, tmp_path):
    """bundle-usage-analyst must categorise context files including 'informational only'.

    Activates the bundle-usage mode, then delegates to the
    ``context-intelligence:bundle-usage-analyst`` agent with an engagement-gap
    analysis request scoped to the known ground-truth session.  The agent must
    write a report to disk and the report must contain at least one context file
    categorised as "informational only" (not a gap).

    The "informational only" category is required because the ground-truth
    session used foundation:explorer for a graph-investigation task.  Foundation
    contributes general-purpose context files (e.g. LANGUAGE_PHILOSOPHY.md,
    common-agent-base.md) that provide background information but do not
    prescribe specific actions for an investigative session \u2014 these should be
    "informational only", not gaps.

    Assertions:
      - The report file exists at ``tmp_path / "engagement.md"``.
      - ``re.search(r"informational only", report_text, re.IGNORECASE)`` is truthy.

    Diagnosis checklist on failure:
      - If the report file is absent: the analyst failed to call write_file;
        check that the prompt explicitly names the output path and that the
        analyst's write_file tool has write access to tmp_path.
      - If "informational only" is absent: every context file was incorrectly
        flagged as a gap.  Verify that the analyst is reading actual context
        file content (not just filenames) and applying the session goal
        (investigative / graph analysis) to the relevance decision.
      - If the delegate call itself fails: ensure bundle-usage mode is active
        before delegation (bundle_usage tool required by the analyst).
    """
    report_path = tmp_path / "engagement.md"
    prompt = (
        f"Run an engagement-gap analysis for session {KNOWN_SESSION_ID}. "
        f"Identify foundation context files that were loaded; for each, "
        f"categorise as 'gap', 'informational only', or 'used'. "
        f"Write the report to {report_path}."
    )

    dtu_session.activate_mode("bundle-usage")
    dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert report_path.exists(), (
        f"Expected engagement report at {report_path} but file was not found. "
        "The analyst must call write_file with the exact path given in the prompt. "
        "Check that the agent's write_file tool has write access to the tmp_path directory."
    )

    report_text = report_path.read_text()

    assert re.search(r"informational only", report_text, re.IGNORECASE), (
        "Expected at least one context file categorised as 'informational only' "
        "in the engagement report but the phrase was absent. "
        "The analyst must distinguish between files that prescribe relevant behavior "
        "(gap) and files that merely provide background information (informational only). "
        f"Report content (first 500 chars): {report_text[:500]!r}"
    )


def test_analyst_cites_content_not_just_signals(dtu_session, tmp_path):
    """bundle-usage-analyst must cite specific content from bundle files.

    Activates the bundle-usage mode, then delegates to the
    ``context-intelligence:bundle-usage-analyst`` agent asking it to cite at
    least one line of content from a foundation context file.  The report must
    contain a quoted or blockquoted fragment longer than 20 characters \u2014 proof
    that the analyst called read_file on the actual file content, not just
    reasoned from signal data or filenames.

    The pattern ``r'"([^"]{20,})"|`([^`]{20,})`|> .{20,}'`` matches:
      - double-quoted strings of 20+ characters
      - backtick-quoted strings of 20+ characters
      - blockquote lines (> ...) of 20+ characters

    Assertions:
      - ``re.findall(r'"([^"]{20,})"|`([^`]{20,})`|> .{20,}', report)`` is
        non-empty (at least one citation found).

    Diagnosis checklist on failure:
      - If no citations found: the analyst is reasoning from filenames or signal
        counts rather than reading file content.  Check that the agent's
        engagement-gap workflow calls read_file on each context file.
      - If all citations are shorter than 20 chars: the analyst is quoting only
        titles or component names.  The prompt explicitly asks for a line of
        content, not a name.
      - If the report file is absent: the analyst failed to call write_file;
        check write_file tool access to tmp_path.
    """
    report_path = tmp_path / "cite.md"
    prompt = (
        f"Run an engagement-gap analysis for session {KNOWN_SESSION_ID}. "
        f"For at least one foundation context file, cite a specific line of "
        f"content from the file (not just its name) to justify your categorisation. "
        f"Write the report to {report_path}."
    )

    dtu_session.activate_mode("bundle-usage")
    dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert report_path.exists(), (
        f"Expected citation report at {report_path} but file was not found. "
        "The analyst must call write_file with the exact path given in the prompt."
    )

    report = report_path.read_text()

    quoted = re.findall(r'"([^"]{20,})"|`([^`]{20,})`|> .{20,}', report)

    assert quoted, (
        "Expected at least one quoted or blockquoted fragment longer than 20 "
        "characters in the engagement report but none were found. "
        "The analyst must cite specific content from bundle files \u2014 not just "
        "signal counts or file names. "
        f"Report content (first 500 chars): {report[:500]!r}"
    )
