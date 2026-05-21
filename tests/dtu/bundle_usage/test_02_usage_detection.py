"""
Scenario 2 — Bundle usage detection from JSONL session events.

Verifies that the bundle_usage tool correctly detects bundle usage signals
from JSONL session events for a known ground-truth session.

Known truth (from KNOWN_SESSION_ID events.jsonl):
  - foundation bundle: at least one agent invocation (e.g. explorer)
  - At least one bundle appears in signals

Pass criteria (JSONL-based — no graph required):
  - signals is a non-empty dict
  - At least one bundle has a non-empty agents list
  - result['scope']['session_id'] == KNOWN_SESSION_ID
  - All signal value containers serialise as lists (sets -> JSON lists)

Both tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
the DTU is not available the entire session is skipped gracefully.
"""

from __future__ import annotations

from .conftest import KNOWN_SESSION_ID

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SIGNAL_KEYS = ("agents", "skills", "recipes", "context", "tools", "modes")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detects_at_least_one_bundle_from_jsonl(dtu_session):
    """bundle_usage tool must detect at least one bundle with agent invocations from JSONL.

    Calls the bundle_usage tool scoped to the known ground-truth session.
    The session JSONL contains at least one delegate:agent_spawned event for
    a foundation bundle agent (e.g. explorer).

    Assertions:
      - The tool call returns a result with a ``"signals"`` key.
      - ``signals`` is a non-empty dict.
      - At least one bundle has a non-empty agents list.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "signals" in result, (
        f"Expected 'signals' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    signals = result["signals"]

    assert isinstance(signals, dict), (
        f"Expected signals to be a dict. Got: {type(signals).__name__}"
    )

    bundles_with_agents = {name: v for name, v in signals.items() if v.get("agents")}

    assert len(bundles_with_agents) >= 1, (
        "Expected at least one bundle to have non-empty agents list in signals. "
        f"signals keys: {list(signals.keys())}. "
        f"agents per bundle: { {k: v.get('agents') for k, v in signals.items()} }"
    )


def test_scope_reflects_session_id(dtu_session):
    """bundle_usage tool must return scope with the requested session_id.

    Calls the bundle_usage tool scoped to the known ground-truth session.
    The result scope must record the session_id that was passed in.

    Assertions:
      - The result contains a ``"scope"`` key.
      - ``result['scope']['session_id'] == KNOWN_SESSION_ID``.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "scope" in result, (
        f"Expected 'scope' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    assert result["scope"]["session_id"] == KNOWN_SESSION_ID, (
        f"Expected scope['session_id'] == {KNOWN_SESSION_ID!r}. "
        f"Got: {result['scope'].get('session_id')!r}"
    )


def test_signals_are_named_lists_not_counts(dtu_session):
    """bundle_usage signals must use named lists, not integer counts.

    Calls the bundle_usage tool scoped to the known ground-truth session.
    For every bundle in signals, each component key ('agents', 'skills',
    'recipes', 'context', 'tools', 'modes') must be a list — catching
    regressions to the old count-based schema.

    Assertions:
      - For each bundle, for each key in _SIGNAL_KEYS, the value is a list
        (when the key is present).
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert "signals" in result, (
        f"Expected 'signals' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    signals = result["signals"]

    for bundle_name, bundle_signals in signals.items():
        for key in _SIGNAL_KEYS:
            value = bundle_signals.get(key)
            if value is None:
                continue  # absent keys are allowed; only present values are checked
            assert isinstance(value, list), (
                f"signals[{bundle_name!r}][{key!r}] must be a list (not a count). "
                f"Got {type(value).__name__}: {value!r}. "
                "This suggests a regression to the old count-based schema."
            )
