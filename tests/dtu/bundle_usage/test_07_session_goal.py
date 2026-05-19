"""
Scenario 7 — Session goal characterisation specificity test.

Verifies that the bundle-usage-analyst agent, when asked to characterise the
goal of a known session, produces a sentence that is:

  1. Substantive — at least 8 words long.
  2. Specific — references at least one term related to the actual session
     topic (CI graph, bundle, signals, attribution, context intelligence,
     usage).
  3. Not generic — the phrase must not match a generic blacklist
     ('general coding', 'general session', etc.).

The test depends on the ``dtu_session`` fixture from conftest.py, which is in
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


def test_goal_is_specific(dtu_session, tmp_path):
    """bundle-usage-analyst must characterise the session goal specifically.

    Activates bundle-usage mode, then delegates to the
    ``context-intelligence:bundle-usage-analyst`` agent asking it to
    characterise the goal of the known ground-truth session in one sentence
    of at least 8 words.  The agent writes the sentence to disk prefixed with
    ``GOAL: ``.

    Assertions:
      - The output file exists at ``tmp_path / "goal.md"``.
      - The file contains a line matching ``GOAL: <text>``.
      - The extracted goal text is at least 8 words long.
      - The goal text does not match any generic blacklisted phrase.
      - The goal text contains at least one session-topic-relevant term.

    Diagnosis checklist on failure:
      - If the file is absent: the analyst failed to call write_file; verify
        the prompt explicitly names the output path and that the agent's
        write_file tool has write access to tmp_path.
      - If the GOAL: prefix is absent: the analyst did not follow the output
        format specified in the prompt.
      - If the goal is fewer than 8 words: the analyst produced a summary
        that is too terse; the prompt asks for at least 8 words.
      - If a generic phrase is present: the analyst did not inspect the
        session content and is returning a boilerplate description.
      - If no relevant terms are present: the analyst described the session
        without referencing the actual topic (CI graph / bundle usage /
        signal attribution).
    """
    goal_path = tmp_path / "goal.md"
    prompt = (
        f"Characterise the session goal for {KNOWN_SESSION_ID} in one "
        f"sentence of at least 8 words. "
        f"Write to {goal_path} prefixed with 'GOAL: '."
    )

    dtu_session.activate_mode("bundle-usage")
    dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert goal_path.exists(), (
        f"Expected goal file at {goal_path} but file was not found. "
        "The analyst must call write_file with the exact path given in the "
        "prompt. Check that the agent's write_file tool has write access to "
        "the tmp_path directory."
    )

    text = goal_path.read_text()

    match = re.search(r"GOAL:\s*(.+)", text)
    assert match is not None, (
        "Expected a line starting with 'GOAL: ' in the output file but the "
        "pattern was not found. "
        "The analyst must write the goal sentence prefixed with 'GOAL: '. "
        f"File content (first 500 chars): {text[:500]!r}"
    )

    goal = match.group(1).strip()

    assert len(goal.split()) >= 8, (
        f"Expected the goal sentence to be at least 8 words long but got "
        f"{len(goal.split())} word(s): {goal!r}. "
        "The prompt explicitly asks for a sentence of at least 8 words."
    )

    generic_blacklist = {
        "general coding",
        "general session",
        "a coding session",
        "general analysis",
    }
    assert not any(g in goal.lower() for g in generic_blacklist), (
        f"The goal sentence matches a generic blacklisted phrase: {goal!r}. "
        "The analyst must describe the actual session topic, not produce a "
        "boilerplate description."
    )

    relevant_terms = {
        "bundle",
        "ci graph",
        "signal",
        "attribution",
        "context intelligence",
        "usage",
    }
    assert any(term in goal.lower() for term in relevant_terms), (
        f"The goal sentence does not reference any session-topic-relevant "
        f"term from {relevant_terms!r}. "
        f"Goal: {goal!r}. "
        "The analyst must describe the actual content of the session "
        "(CI graph / bundle usage / signal attribution)."
    )
