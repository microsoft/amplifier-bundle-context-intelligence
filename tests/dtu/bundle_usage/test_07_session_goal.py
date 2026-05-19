"""
Scenario 7 — Session goal characterisation specificity test.

Verifies that the bundle-usage-analyst agent, when asked to characterise the
goal of a known session, produces a response that is:

  1. Substantive — more than 50 characters (not a one-word non-answer).
  2. Specific — references at least one term related to the actual session
     topic (CI graph, graph, signal, attribution, context intelligence,
     investigation, cypher).
  3. Not generic — the phrase must not match a generic blacklist
     ('general coding', 'general session', etc.).

The test depends on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations

from .conftest import KNOWN_SESSION_ID


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_goal_is_specific(dtu_session):
    """bundle-usage-analyst must characterise the session goal specifically.

    Activates bundle-usage mode, then delegates to the
    ``context-intelligence:bundle-usage-analyst`` agent asking it to
    characterise the goal of the known ground-truth session in one sentence
    of at least 8 words.  The agent states the goal in its response text.

    Assertions:
      - The delegate() return value is more than 50 characters (not a
        generic non-answer).
      - The output contains at least one session-topic-relevant technical
        term (not just generic words like "session", "analysis", "bundle").
      - The output does not match any generic blacklisted phrase.

    Diagnosis checklist on failure:
      - If output is <= 50 chars: the analyst produced an empty or trivially
        short response; check that the prompt is well-formed and the mode
        is active.
      - If no relevant terms are present: the analyst described the session
        without referencing the actual topic (CI graph / signal attribution /
        graph investigation).
      - If a generic phrase is present: the analyst did not inspect the
        session content and is returning a boilerplate description.
    """
    prompt = (
        f"Characterise the session goal for {KNOWN_SESSION_ID} in one "
        f"sentence of at least 8 words. "
        f"State the goal clearly in your response."
    )

    dtu_session.activate_mode("bundle-usage")
    output = dtu_session.delegate("context-intelligence:bundle-usage-analyst", prompt)

    assert len(output) > 50, (
        f"Expected the agent's output to be more than 50 characters long "
        f"(not a generic non-answer) but got {len(output)} chars. "
        f"Output: {output!r}"
    )

    # Specific technical terms tied to this session's actual topic.
    # Intentionally excludes overly generic words like "session", "analysis",
    # "bundle" — the goal must reference the actual subject matter.
    specific_terms = {
        "ci graph",
        "graph",
        "signal",
        "attribution",
        "context intelligence",
        "investigation",
        "cypher",
        "usage analysis",
        "bundle usage",
        "context files",
        "bundle-usage",
    }
    assert any(term in output.lower() for term in specific_terms), (
        f"The agent's output does not reference any session-topic-relevant "
        f"technical term from {specific_terms!r}. "
        "The analyst must describe the actual content of the session "
        "(CI graph investigation / bundle usage / signal attribution). "
        f"Output (first 500 chars): {output[:500]!r}"
    )

    generic_blacklist = {
        "general coding",
        "general session",
        "a coding session",
        "general analysis",
    }
    assert not any(g in output.lower() for g in generic_blacklist), (
        f"The agent's output matches a generic blacklisted phrase. "
        "The analyst must describe the actual session topic, not produce a "
        "boilerplate description. "
        f"Output (first 500 chars): {output[:500]!r}"
    )
