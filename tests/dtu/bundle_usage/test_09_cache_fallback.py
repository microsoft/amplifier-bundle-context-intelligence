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

import json

import pytest

from .conftest import _dtu_exec


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _find_foundation_dir_in_dtu() -> str:
    """Return the name of the foundation cache directory inside the DTU.

    Runs ``ls ~/.amplifier/cache/`` inside the DTU and searches for a
    directory name starting with ``amplifier-foundation-``.

    Returns
    -------
    str
        Directory name (not full path) of the foundation cache directory
        inside the DTU, e.g. ``amplifier-foundation-c909465861f9d6ce``.

    Skips
    -----
    Calls ``pytest.skip`` if no matching directory is found so that
    environments without the foundation bundle installed do not produce
    false failures.
    """
    result = _dtu_exec("ls ~/.amplifier/cache/ 2>/dev/null", timeout=15)
    try:
        outer = json.loads(result.stdout)
        listing = outer.get("stdout", "")
    except Exception:
        listing = result.stdout

    for line in listing.splitlines():
        name = line.strip()
        if name.startswith("amplifier-foundation-"):
            return name

    pytest.skip(
        "No amplifier-foundation-* directory found under ~/.amplifier/cache in the DTU. "
        "Foundation bundle must be cached inside the DTU to run cache-fallback tests."
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
      - The result contains an ``"inventory"`` key.
      - ``result["inventory"].get("foundation")`` is not None.
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

    assert "inventory" in result, (
        f"Expected 'inventory' key in result. Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    fnd = result["inventory"].get("foundation")

    assert fnd is not None, (
        "Expected 'foundation' entry in inventory but it was absent. "
        f"Inventory keys: {list(result['inventory'].keys())}. "
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
    """Moving the foundation cache directory out of the cache root must hide it.

    Simulates a cache miss by moving the foundation bundle cache directory
    from ``~/.amplifier/cache/`` to ``/tmp/`` INSIDE THE DTU.  The
    bundle_usage tool (which also runs inside the DTU) must then either:
      - Report foundation as absent from the inventory, OR
      - Include a foundation entry with scan_source in {"stale", "absent"}.

    The tool MUST NOT silently return the same data as if the cache were
    intact — an absent cache root entry must produce a visible status change.

    The original cache directory is restored in the ``finally`` block to
    prevent bleed into subsequent tests.

    Note: the rename target is ``/tmp/<original_name>`` (outside the cache
    root) so that ``scan_cache`` — which iterates ALL children of the cache
    root — cannot find it via any path.

    Assertions:
      - ``absent or flagged`` where:
        - ``absent = fnd is None``
        - ``flagged = bool(fnd) and fnd.get("scan_source") in ("stale", "absent")``

    Diagnosis checklist on failure:
      - If fnd is not None and scan_source is still "cache"/"fresh": the tool
        is finding the foundation bundle via a path other than the moved
        directory; verify the inventory scan glob covers only the expected
        cache root.
      - If the rename command failed: check the DTU exec output in the
        finally block for restoration errors.
    """
    dir_name = _find_foundation_dir_in_dtu()
    src_path = f"~/.amplifier/cache/{dir_name}"
    tmp_path = f"/tmp/{dir_name}.dtu_backup"

    # Move the foundation cache dir OUT of the cache root inside the DTU.
    move_result = _dtu_exec(
        f"mv {src_path} {tmp_path}",
        timeout=15,
    )
    try:
        outer = json.loads(move_result.stdout)
        move_ok = outer.get("exit_code", 1) == 0
    except Exception:
        move_ok = move_result.returncode == 0

    if not move_ok:
        pytest.skip(f"Could not move foundation cache dir in DTU: {move_result.stdout}")

    try:
        dtu_session.activate_mode("bundle-usage")
        result = dtu_session.call_tool("bundle_usage")

        # Tool call may succeed or gracefully degrade; extract inventory either way.
        fnd = result.get("inventory", {}).get("foundation")

        absent = fnd is None
        flagged = bool(fnd) and fnd.get("scan_source") in ("stale", "absent")

        assert absent or flagged, (
            "Expected the bundle_usage tool to report foundation as absent "
            "OR to set scan_source to 'stale'/'absent' after the cache "
            "directory was moved out of the cache root (simulating a cache miss). "
            f"Got foundation entry: {fnd!r}. "
            "The inventory layer must not return data for a bundle whose cache "
            "directory has been removed from ~/.amplifier/cache/."
        )
    finally:
        # Always restore the cache dir inside the DTU to avoid test bleed.
        _dtu_exec(
            f"mv {tmp_path} {src_path}",
            timeout=15,
        )


def test_cache_restored_returns_to_fresh(dtu_session):
    """After cache restoration, bundle_usage must again report scan_source as 'cache'/'fresh'.

    This test runs after ``test_renamed_cache_visible_in_output`` restores the
    original cache directory.  It confirms that the tool correctly picks up
    the restored cache and returns to reporting scan_source as 'cache' or
    'fresh' for the foundation bundle.

    Assertions:
      - The result contains an ``"inventory"`` key.
      - ``result["inventory"].get("foundation")`` is not None.
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

    assert "inventory" in result, (
        f"Expected 'inventory' key in result after cache restoration. "
        f"Got keys: {list(result.keys())}. "
        f"Raw output (if any): {result.get('_raw', 'N/A')[:300]}"
    )

    fnd = result["inventory"].get("foundation")

    assert fnd is not None, (
        "Expected 'foundation' entry in inventory after cache restoration but "
        "it was absent. "
        f"Inventory keys: {list(result['inventory'].keys())}. "
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
