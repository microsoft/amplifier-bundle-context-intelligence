"""
Scenario 1 — Mode-gate footprint test.

Verifies that `bundle_usage` is gated behind the bundle-usage mode:

  1. Fresh session, mode NOT active  → `bundle_usage` must NOT appear in the
     tool list (zero-footprint, gated by ``advertised: false``).
  2. After activating the bundle-usage mode              → `bundle_usage` MUST
     appear in the tool list (contributed via ``modes/bundle-usage.md``
     ``contributes.tools`` entry that points to ``tool-bundle-usage``).

Both tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bundle_usage_absent_before_mode_activation(dtu_session):
    """bundle_usage tool must NOT be present in a fresh session (advertised: false).

    A fresh session — one that has not had any mode activated — must NOT expose
    the ``bundle_usage`` tool.  If this assertion fails it means the tool has
    leaked into the session without the mode gate being raised.

    Diagnosis checklist:
      - ``modes/bundle-usage.md`` frontmatter must have ``advertised: false``.
      - ``modes/bundle-usage.md`` frontmatter must have ``default_action: block``.
      - Confirm the mode is NOT in the bundle's ``always_active`` list (if any).
    """
    tools = dtu_session.list_tools()
    assert "bundle_usage" not in tools, (
        "bundle_usage tool leaked into session without mode activation. "
        "Check modes/bundle-usage.md — 'advertised: false' must be set. "
        f"Observed tools: {tools}"
    )


def test_bundle_usage_present_after_mode_activation(dtu_session):
    """bundle_usage tool MUST be present after activating the bundle-usage mode.

    Activating the ``bundle-usage`` mode (via ``mode(operation="set",
    name="bundle-usage")``) must cause the ``bundle_usage`` tool to appear in
    the tool list.

    Diagnosis checklist:
      - ``modes/bundle-usage.md`` ``contributes.tools`` must declare a module
        entry with ``module: tool-bundle-usage``.
      - The ``source`` URL for that module must be reachable / resolvable.
      - Confirm the tool module exports a function named ``bundle_usage``.
    """
    dtu_session.activate_mode("bundle-usage")
    tools = dtu_session.list_tools()
    assert "bundle_usage" in tools, (
        "bundle_usage tool not available after activating bundle-usage mode. "
        "Check modes/bundle-usage.md 'contributes.tools' entry — "
        "module must be 'tool-bundle-usage'. "
        f"Observed tools: {tools}"
    )
