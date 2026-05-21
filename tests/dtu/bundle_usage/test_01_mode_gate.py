"""
Scenario 1 — Mode-gate footprint.

Verifies that the bundle-usage capability is gated behind the mode:

  1. Fresh session, mode NOT active → bundle_usage absent from tool list.
  2. After mode activation → bundle-usage-analyst IS accessible via delegation.
"""

from __future__ import annotations


def test_bundle_usage_absent_before_mode_activation(dtu_session):
    """bundle_usage must NOT appear in tool list before mode activation.

    Confirms advertised:false and zero-footprint gate works.
    """
    tools = dtu_session.list_tools()
    assert "bundle_usage" not in tools, (
        f"bundle_usage leaked into session without mode activation. Observed tools: {tools}"
    )


def test_analyst_accessible_after_mode_activation(dtu_session):
    """After activating bundle-usage mode, bundle-usage-analyst MUST be reachable.

    Delegates to context-intelligence:bundle-usage-analyst with a trivial
    instruction and verifies the agent responds (not an error).
    """
    dtu_session.activate_mode("bundle-usage")
    output = dtu_session.delegate(
        "context-intelligence:bundle-usage-analyst",
        "Confirm you are ready. Respond with a single sentence only.",
    )
    assert output.strip(), "Analyst returned empty response after mode activation"
    assert any(kw in output.lower() for kw in ("ready", "help", "bundle", "available", "yes")), (
        f"Analyst response did not confirm readiness: {output[:200]}"
    )
