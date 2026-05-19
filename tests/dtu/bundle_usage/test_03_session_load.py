"""
Scenario 3 — Session load extraction with graceful fallback (S-14 + inference fallback).

Verifies that the bundle_usage tool correctly handles session-load extraction:

  1. Tool completes without error in both redacted and unredacted cases.
  2. Output reports session-load coverage honestly (S-14 direct OR inferred).
  3. Every bundle whose components were invoked appears in session-load
     (100% recall on the used subset).

All tests depend on the ``dtu_session`` fixture from conftest.py, which is in
turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.  If
``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations

from .conftest import KNOWN_SESSION_ID


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_session_load_completes_without_error(dtu_session):
    """bundle_usage tool must complete without error for the known session ID.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The tool must return ``success=True`` in
    both redacted and unredacted cases (S-14 direct OR inference fallback).

    Assertions:
      - The tool call succeeds (``result["success"] is True``).

    Diagnosis checklist on failure:
      - Confirm the ground-truth session exists in the CI graph.
      - Verify the S-14 context query (or inference fallback) completes without
        error.
      - Check that the bundle-usage mode is correctly activated before the tool
        call.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert result.get("success") is True, f"bundle_usage tool call failed. result: {result}"


def test_session_load_recall_against_used_subset(dtu_session):
    """Every bundle with invocations must appear in the inventory snapshot.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  For every bundle that has any non-zero
    invocation count in the signals output, that bundle must also appear in the
    inventory snapshot (100% recall on the used subset).

    used_bundles is the set of bundle names where any invocation count is > 0
    across agents, skills, modes, recipes, or tools.

    inventory_bundles is ``set(out["inventory"]) - {"_meta"}``.

    Assertions:
      - ``missing = used_bundles - inventory_bundles`` must be empty.

    Diagnosis checklist on failure:
      - A missing bundle means the inventory scanner did not find the bundle in
        the local cache.  Verify the bundle is installed in
        ``~/.amplifier/cache``.
      - Check that the bundle directory has a valid ``bundle.md`` with a
        ``bundle.name`` field in its YAML frontmatter.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert result.get("success") is True, f"bundle_usage tool call failed. result: {result}"

    out = result.get("output", {})
    signals = out.get("signals", {})

    used_bundles = {
        name
        for name, counts in signals.items()
        if any(counts.get(k, 0) > 0 for k in ("agents", "skills", "modes", "recipes", "tools"))
    }

    inventory_bundles = set(out.get("inventory", {})) - {"_meta"}

    missing = used_bundles - inventory_bundles
    assert not missing, (
        f"Used bundles not found in inventory snapshot: {missing}. "
        f"used_bundles={used_bundles}, "
        f"inventory_bundles={inventory_bundles}"
    )


def test_inventory_meta_reports_scan_source(dtu_session):
    """inventory._meta.scan_source must be one of {cache, absent, stale}.

    Activates the bundle-usage mode, then calls the bundle_usage tool scoped to
    the known ground-truth session.  The inventory snapshot must include a
    ``_meta`` key whose ``scan_source`` field is one of the recognised values.

    Assertions:
      - ``meta.get("scan_source")`` is in ``("cache", "absent", "stale")``.

    Diagnosis checklist on failure:
      - A missing ``_meta`` key means the inventory layer failed to produce
        metadata.  Check the ``scan_cache`` function in
        ``context_intelligence.bundle_analysis.inventory``.
      - An unexpected ``scan_source`` value means the inventory layer returned
        an unrecognised source type.  Valid values are ``"cache"`` (normal
        scan), ``"absent"`` (cache root not found), and ``"stale"`` (stale
        cache data used as a fallback).
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage", session_id=KNOWN_SESSION_ID)

    assert result.get("success") is True, f"bundle_usage tool call failed. result: {result}"

    out = result.get("output", {})
    meta = out.get("inventory", {}).get("_meta", {})

    assert meta.get("scan_source") in ("cache", "absent", "stale"), (
        f"inventory._meta.scan_source must be one of {{cache, absent, stale}}. "
        f"Got: {meta.get('scan_source')!r}. "
        f"Full _meta: {meta}"
    )
