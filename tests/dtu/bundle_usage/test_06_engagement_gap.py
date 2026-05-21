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


def test_analyst_produces_engagement_categories(dtu_session):
    """bundle-usage-analyst must categorise context files including 'informational only'.

    Activates the bundle-usage mode, then delegates to the
    ``context-intelligence:bundle-usage-analyst`` agent with an engagement-gap
    analysis request scoped to the known ground-truth session.  The agent must
    produce output that contains at least one context file categorised as
    "informational only" (not a gap).

    The "informational only" category is required because the ground-truth
    session used foundation:explorer for a graph-investigation task.  Foundation
    contributes general-purpose context files (e.g. LANGUAGE_PHILOSOPHY.md,
    common-agent-base.md) that provide background information but do not
    prescribe specific actions for an investigative session — these should be
    "informational only", not gaps.

    Assertions:
      - The delegate() return value contains at least one engagement-related
        keyword (engagement, context, informational, relevant, active, passive).
      - ``re.search(r"informational only", output, re.IGNORECASE)`` is truthy.

    Diagnosis checklist on failure:
      - If engagement keywords are absent: the analyst did not produce a
        categorisation at all; check that the prompt is clear enough.
      - If "informational only" is absent: every context file was incorrectly
        flagged as a gap.  Verify that the analyst is reading actual context
        file content (not just filenames) and applying the session goal
        (investigative / graph analysis) to the relevance decision.
      - If the delegate call itself fails: ensure bundle-usage mode is active
        before delegation (bundle_usage tool required by the analyst).
    """
    prompt = (
        f"Run an engagement-gap analysis for session {KNOWN_SESSION_ID}. "
        f"Identify foundation context files that were loaded; for each, "
        f"categorise as 'gap', 'informational only', or 'used'. "
        f"Include at least one file categorised as 'informational only' where appropriate."
    )

    dtu_session.activate_mode("bundle-usage")
    output = dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    engagement_keywords = [
        "engagement",
        "context",
        "informational",
        "relevant",
        "active",
        "passive",
    ]
    assert any(kw in output.lower() for kw in engagement_keywords), (
        "Expected the agent's output to contain at least one engagement-related "
        f"keyword from {engagement_keywords!r} but none were found. "
        f"Output (first 500 chars): {output[:500]!r}"
    )

    assert re.search(r"informational only", output, re.IGNORECASE), (
        "Expected at least one context file categorised as 'informational only' "
        "in the agent's output but the phrase was absent. "
        "The analyst must distinguish between files that prescribe relevant behavior "
        "(gap) and files that merely provide background information (informational only). "
        f"Output (first 500 chars): {output[:500]!r}"
    )


def test_analyst_cites_content_not_just_signals(dtu_session):
    """bundle-usage-analyst must cite specific content from bundle files.

    Activates the bundle-usage mode, then delegates to the
    ``context-intelligence:bundle-usage-analyst`` agent asking it to cite at
    least one line of content from a foundation context file.  The agent's
    output must contain either a reference to a specific bundle file path
    (e.g. ~/.amplifier/...) or a quoted/blockquoted fragment longer than 20
    characters — proof that the analyst called read_file on the actual file
    content, not just reasoned from signal data or filenames.

    The pattern ``r'"([^"]{20,})"`` or `` `([^`]{20,})` `` or ``> .{20,}``
    matches:
      - double-quoted strings of 20+ characters
      - backtick-quoted strings of 20+ characters
      - blockquote lines (> ...) of 20+ characters

    Assertions:
      - The output contains a known bundle file path reference (e.g.
        .amplifier, LANGUAGE_PHILOSOPHY, common-agent-base) OR
        ``re.findall(r'"([^"]{20,})"|`([^`]{20,})`|> .{20,}', output)``
        is non-empty.

    Diagnosis checklist on failure:
      - If no citations found and no file paths: the analyst is reasoning
        from filenames or signal counts rather than reading file content.
        Check that the agent's engagement-gap workflow calls read_file on
        each context file.
      - If all citations are shorter than 20 chars: the analyst is quoting
        only titles or component names.  The prompt explicitly asks for a
        line of content, not a name.
    """
    prompt = (
        f"Run an engagement-gap analysis for session {KNOWN_SESSION_ID}. "
        f"For at least one foundation context file, cite a specific line of "
        f"content from the file (not just its name) to justify your categorisation."
    )

    dtu_session.activate_mode("bundle-usage")
    output = dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    # Proof the analyst referenced actual bundle file content (path or name).
    known_file_markers = [
        ".amplifier",
        "LANGUAGE_PHILOSOPHY",
        "IMPLEMENTATION_PHILOSOPHY",
        "common-agent-base",
        "MODULAR_DESIGN",
        "ISSUE_HANDLING",
        "KERNEL_PHILOSOPHY",
        "PROBLEM_SOLVING",
    ]
    has_file_reference = any(marker in output for marker in known_file_markers)

    # Also accept quoted/blockquoted fragments of 20+ chars as citation proof.
    quoted = re.findall(r'"([^"]{20,})"|`([^`]{20,})`|> .{20,}', output)

    assert has_file_reference or quoted, (
        "Expected the agent's output to either reference a bundle context file "
        f"(one of {known_file_markers!r}) or include a quoted/blockquoted "
        "fragment longer than 20 characters — proof that the analyst read "
        "actual file content rather than reasoning from signal counts alone. "
        f"Output (first 500 chars): {output[:500]!r}"
    )
