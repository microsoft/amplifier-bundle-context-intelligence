"""
Scenario 9 — Cache fallback chain: fresh / stale / absent (LS-7 freshness).

Verifies that the bundle_usage tool correctly reflects the state of the
foundation bundle cache across three conditions:

  1. Fresh cache   → inventory["foundation"]["scan_source"] in {"cache", "fresh"}.
  2. Renamed cache → bundle absent from inventory OR scan_source in {"stale","absent"}
                     (tool must NOT silently return stale data).
  3. Restored cache → scan_source returns to "cache" / "fresh".

The renamed-cache test manipulates ~/.amplifier/cache/amplifier-foundation-*
on the host to simulate a stale-hash cache miss, then restores the directory
in a ``finally`` block to avoid test bleed.

All three tests depend on the ``dtu_session`` fixture from conftest.py, which
is in turn guarded by the session-scoped ``dtu_bootstrap`` autouse fixture.
If ``amplifier-tester`` is not installed or the DTU cannot be stood up, the
entire session is skipped gracefully.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _foundation_cache_dir() -> Path:
    """Locate the foundation bundle cache directory inside the DTU sandbox.

    Searches ``~/.amplifier/cache`` for directories whose name starts with
    ``amplifier-foundation-``.  Returns the first match.

    Returns
    -------
    Path
        Absolute path to the foundation cache directory.

    Skips
    -----
    Calls ``pytest.skip`` if no matching directory is found so that
    environments without the foundation bundle installed do not produce
    false failures.
    """
    cache_root = Path.home() / ".amplifier" / "cache"
    for entry in cache_root.glob("amplifier-foundation-*"):
        if entry.is_dir():
            return entry
    pytest.skip(
        "No amplifier-foundation-* directory found under ~/.amplifier/cache. "
        "Foundation bundle must be cached to run cache-fallback tests."
    )
    raise AssertionError("unreachable: pytest.skip() always raises")  # satisfy type checker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fresh_cache_marked_fresh(dtu_session):
    """bundle_usage must report scan_source as 'cache' or 'fresh' for the foundation bundle.

    Activates the bundle-usage mode, then calls the bundle_usage tool without
    a session_id (workspace scope) so that the inventory layer scans the
    bundle cache.  The foundation bundle's inventory entry must be present
    and its ``scan_source`` field must indicate a fresh cache read.

    Assertions:
      - The tool call succeeds (``result["success"] is True``).
      - ``out["inventory"].get("foundation")`` is not None.
      - ``fnd["scan_source"] in ("cache", "fresh")``.

    Diagnosis checklist on failure:
      - If the tool call fails: verify bundle-usage mode and workspace-scope
        path in the tool implementation.
      - If "foundation" is absent from inventory: the cache scan may be
        skipping the foundation bundle; check the glob pattern used by
        the inventory layer.
      - If scan_source is not "cache" or "fresh": the tool may not be
        populating scan_source for cache hits; verify LS-7 freshness
        classification logic.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage")

    assert result.get("success") is True, (
        f"bundle_usage workspace-scope call failed. result: {result}"
    )

    out = result.get("output", {})
    fnd = out.get("inventory", {}).get("foundation")

    assert fnd is not None, (
        "Expected 'foundation' entry in inventory but it was absent. "
        f"Inventory keys: {list(out.get('inventory', {}).keys())}. "
        "Verify that the foundation bundle cache directory exists under "
        "~/.amplifier/cache and that the workspace-scope inventory scan "
        "includes it."
    )

    assert fnd.get("scan_source") in ("cache", "fresh"), (
        "Expected inventory['foundation']['scan_source'] to be 'cache' or "
        f"'fresh' but got {fnd.get('scan_source')!r}. "
        "The tool must mark a warm cache hit with scan_source='cache' or "
        "'fresh' per the LS-7 freshness spec. "
        f"Full foundation entry: {fnd}"
    )


def test_renamed_cache_visible_in_output(dtu_session):
    """Renaming the foundation cache directory must produce a visible status change.

    Simulates a stale-hash cache miss by renaming the foundation bundle cache
    directory to ``<original>.dtu_renamed``.  When the bundle_usage tool is
    called in this state it must either:
      - Report foundation as absent from the inventory, OR
      - Include a foundation entry with scan_source in {"stale", "absent"}.

    The tool MUST NOT silently return the same data as if the cache were
    intact — a renamed cache must produce a visible status change.

    The original cache directory is restored in the ``finally`` block to
    prevent bleed into subsequent tests.

    Assertions:
      - ``absent or flagged`` where:
        - ``absent = fnd is None``
        - ``flagged = bool(fnd) and fnd.get("scan_source") in ("stale", "absent")``

    Diagnosis checklist on failure:
      - If fnd is not None and scan_source is still "cache"/"fresh": the tool
        is returning stale cached data without re-checking the filesystem;
        verify that the inventory layer re-scans on every call and does not
        hold an in-process cache.
      - If the assert fails with absent=False, flagged=False: the tool
        returned a foundation entry but did not update scan_source to
        indicate the cache miss.  Check LS-7 stale-detection logic.
    """
    src = _foundation_cache_dir()
    moved = src.with_name(src.name + ".dtu_renamed")
    shutil.move(str(src), str(moved))

    try:
        dtu_session.activate_mode("bundle-usage")
        result = dtu_session.call_tool("bundle_usage")

        # Tool call may succeed or gracefully degrade; extract inventory either way.
        out = result.get("output", {})
        fnd = out.get("inventory", {}).get("foundation")

        absent = fnd is None
        flagged = bool(fnd) and fnd.get("scan_source") in ("stale", "absent")

        assert absent or flagged, (
            "Expected the bundle_usage tool to report foundation as absent "
            "OR to set scan_source to 'stale'/'absent' after the cache "
            "directory was renamed (simulating a stale hash). "
            f"Got foundation entry: {fnd!r}. "
            "The tool must not silently return stale data when the cache "
            "directory is missing; LS-7 freshness classification must detect "
            "the cache miss and reflect it in the output."
        )
    finally:
        # Always restore the cache to avoid test bleed.
        shutil.move(str(moved), str(src))


def test_cache_restored_returns_to_fresh(dtu_session):
    """After cache restoration, bundle_usage must again report scan_source as 'cache'/'fresh'.

    This test runs after ``test_renamed_cache_visible_in_output`` restores the
    original cache directory.  It confirms that the tool correctly picks up
    the restored cache and returns to reporting scan_source as 'cache' or
    'fresh' for the foundation bundle.

    Assertions:
      - The tool call succeeds (``result["success"] is True``).
      - ``out["inventory"].get("foundation")`` is not None.
      - ``fnd["scan_source"] in ("cache", "fresh")``.

    Diagnosis checklist on failure:
      - If foundation is absent: the cache directory may not have been
        restored correctly by the previous test's finally block.
      - If scan_source is still "stale"/"absent": the tool may be caching
        the stale-detection result across calls; verify the inventory layer
        does not memoize negative cache results.
    """
    dtu_session.activate_mode("bundle-usage")
    result = dtu_session.call_tool("bundle_usage")

    assert result.get("success") is True, (
        f"bundle_usage workspace-scope call failed after cache restoration. result: {result}"
    )

    out = result.get("output", {})
    fnd = out.get("inventory", {}).get("foundation")

    assert fnd is not None, (
        "Expected 'foundation' entry in inventory after cache restoration but "
        "it was absent. "
        f"Inventory keys: {list(out.get('inventory', {}).keys())}. "
        "Verify that the foundation bundle cache directory was restored by "
        "the previous test's finally block."
    )

    assert fnd.get("scan_source") in ("cache", "fresh"), (
        "Expected inventory['foundation']['scan_source'] to be 'cache' or "
        f"'fresh' after cache restoration but got {fnd.get('scan_source')!r}. "
        "The tool must detect the restored cache and revert scan_source to "
        "a fresh-cache status per the LS-7 freshness spec. "
        f"Full foundation entry: {fnd}"
    )
