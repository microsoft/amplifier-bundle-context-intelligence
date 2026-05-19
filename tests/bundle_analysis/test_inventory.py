"""Tests for context_intelligence.bundle_analysis.inventory.

scan_cache() must:
- Return a dict.
- Mark absent cache roots with _meta.scan_source == "absent".
- Enumerate bundles by reading bundle.md's `bundle.name` field (not directory hash).
- Return declared agents, modes, skills, recipes per bundle.
- Tolerate missing subdirectories (agents/, modes/, etc.).
- Mark each bundle's scan_source as "cache" or "fresh".
- Skip directories that have no bundle.md.
"""

from __future__ import annotations

from pathlib import Path


class TestScanCache:
    """Tests for scan_cache() in context_intelligence.bundle_analysis.inventory."""

    # ------------------------------------------------------------------
    # Test 1 — scan_cache returns a dict
    # ------------------------------------------------------------------

    def test_returns_dict(self, fake_bundle_cache: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        result = scan_cache(cache_root=fake_bundle_cache)
        assert isinstance(result, dict)

    # ------------------------------------------------------------------
    # Test 2 — absent cache root is marked absent
    # ------------------------------------------------------------------

    def test_absent_cache_marked_absent(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        missing = tmp_path / "nonexistent_cache"
        result = scan_cache(cache_root=missing)
        assert result.get("_meta", {}).get("scan_source") == "absent"

    # ------------------------------------------------------------------
    # Test 3 — bundles enumerated by bundle.md bundle.name (not dir hash)
    # ------------------------------------------------------------------

    def test_enumerates_bundles_by_directory(self, fake_bundle_cache: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        result = scan_cache(cache_root=fake_bundle_cache)
        assert "foundation" in result
        assert "superpowers" in result

    # ------------------------------------------------------------------
    # Test 4 — agents enumerated from agents/*.md
    # ------------------------------------------------------------------

    def test_agents_enumerated(self, fake_bundle_cache: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        result = scan_cache(cache_root=fake_bundle_cache)
        agents = result["foundation"]["declared"]["agents"]
        assert "explorer" in agents
        assert "zen-architect" in agents

    # ------------------------------------------------------------------
    # Test 5 — modes enumerated from modes/*.md
    # ------------------------------------------------------------------

    def test_modes_enumerated(self, fake_bundle_cache: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        result = scan_cache(cache_root=fake_bundle_cache)
        assert "brainstorm" in result["foundation"]["declared"]["modes"]

    # ------------------------------------------------------------------
    # Test 6 — missing subdirs tolerated (superpowers has no agents/)
    # ------------------------------------------------------------------

    def test_missing_subdirs_tolerated(self, fake_bundle_cache: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        result = scan_cache(cache_root=fake_bundle_cache)
        assert result["superpowers"]["declared"]["agents"] == []

    # ------------------------------------------------------------------
    # Test 7 — per-bundle scan_source is "cache" or "fresh"
    # ------------------------------------------------------------------

    def test_per_bundle_scan_source_marked(self, fake_bundle_cache: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        result = scan_cache(cache_root=fake_bundle_cache)
        assert result["foundation"].get("scan_source") in ("cache", "fresh")

    # ------------------------------------------------------------------
    # Test 8 — directories without bundle.md are skipped
    # ------------------------------------------------------------------

    def test_skips_dirs_without_bundle_md(self, fake_bundle_cache: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        # Create a directory in the cache root without a bundle.md
        (fake_bundle_cache / "stray-dir-no-bundle").mkdir(parents=True, exist_ok=True)

        result = scan_cache(cache_root=fake_bundle_cache)
        # The stray directory has no bundle.md, so it should not appear in results.
        # It also has no bundle.name, so "stray-dir-no-bundle" must not be a key.
        assert "stray-dir-no-bundle" not in result
        # The directory count should still be exactly 2 (foundation + superpowers).
        # Filter out the special _meta key if present.
        bundle_keys = {k for k in result if not k.startswith("_")}
        assert bundle_keys == {"foundation", "superpowers"}
