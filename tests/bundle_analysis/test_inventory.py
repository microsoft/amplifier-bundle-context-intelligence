"""Tests for context_intelligence.bundle_analysis.inventory.

Three-tier schema:
  always_active — agents/context/skills/recipes loaded unconditionally
  agent_level   — per-agent declared tools/context/skills
  mode_gated    — per-mode contributed agents/context/skills

Disk cache:
  .bundle-scan.json inside each bundle dir, keyed by dir name.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    """Create parent dirs and write UTF-8 content to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_bundle(root: Path, slug: str, hash16: str = "0123456789abcdef") -> Path:
    """Create ``amplifier-<slug>-<hash16>/`` with a minimal bundle.md."""
    bundle_dir = root / f"amplifier-{slug}-{hash16}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write(
        bundle_dir / "bundle.md",
        textwrap.dedent(f"""\
            ---
            bundle:
              name: {slug}
            ---
        """),
    )
    return bundle_dir


# ---------------------------------------------------------------------------
# TestInventoryThreeTierSchema
# ---------------------------------------------------------------------------


class TestInventoryThreeTierSchema:
    """10 tests verifying the three-tier schema returned by scan_cache()."""

    # ------------------------------------------------------------------
    # 1  _meta key present
    # ------------------------------------------------------------------

    def test_returns_meta_key(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        cache.mkdir()
        result = scan_cache(cache_root=cache)
        assert "_meta" in result
        assert result["_meta"]["scan_source"] == "cache"

    # ------------------------------------------------------------------
    # 2  absent cache root
    # ------------------------------------------------------------------

    def test_absent_cache_marked_absent(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        missing = tmp_path / "nonexistent_cache"
        result = scan_cache(cache_root=missing)
        assert result["_meta"]["scan_source"] == "absent"

    # ------------------------------------------------------------------
    # 3  bundle entry has three tiers
    # ------------------------------------------------------------------

    def test_bundle_entry_has_three_tiers(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        _make_bundle(cache, "myb")
        result = scan_cache(cache_root=cache)
        entry = result["myb"]
        assert "always_active" in entry
        assert "agent_level" in entry
        assert "mode_gated" in entry

    # ------------------------------------------------------------------
    # 4  always_active.agents from behaviors/*.yaml agents.include
    # ------------------------------------------------------------------

    def test_always_active_agents_from_behaviors_yaml(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")
        _write(
            bundle_dir / "behaviors" / "main.yaml",
            textwrap.dedent("""\
                agents:
                  include:
                    - bundle:explorer
                    - zen-architect
            """),
        )
        result = scan_cache(cache_root=cache)
        agents = result["myb"]["always_active"]["agents"]
        assert "explorer" in agents
        assert "zen-architect" in agents

    # ------------------------------------------------------------------
    # 5  always_active.context from behaviors/*.yaml context.include
    # ------------------------------------------------------------------

    def test_always_active_context_from_behaviors_yaml(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")
        _write(
            bundle_dir / "behaviors" / "main.yaml",
            textwrap.dedent("""\
                context:
                  include:
                    - context/overview.md
                    - context/guide.md
            """),
        )
        result = scan_cache(cache_root=cache)
        context = result["myb"]["always_active"]["context"]
        assert "context/overview.md" in context
        assert "context/guide.md" in context

    # ------------------------------------------------------------------
    # 6  always_active.skills from skills/ directory
    # ------------------------------------------------------------------

    def test_always_active_skills_from_skills_dir(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")
        (bundle_dir / "skills" / "python-coding").mkdir(parents=True)
        _write(bundle_dir / "skills" / "flat-skill.md", "# Flat skill")
        result = scan_cache(cache_root=cache)
        skills = result["myb"]["always_active"]["skills"]
        assert "python-coding" in skills
        assert "flat-skill" in skills

    # ------------------------------------------------------------------
    # 7  mode_gated.agents from modes/*.md mode.contributes
    # ------------------------------------------------------------------

    def test_mode_gated_agents_from_contributes(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")
        _write(
            bundle_dir / "modes" / "brainstorm.md",
            textwrap.dedent("""\
                ---
                mode:
                  name: brainstorm
                  contributes:
                    agents:
                      creative-thinker: {}
                      idea-generator: {}
                    context:
                      - context/brainstorm-tips.md
                    skills:
                      - brainstorming
                ---
                # Brainstorm mode
            """),
        )
        result = scan_cache(cache_root=cache)
        mode_gated = result["myb"]["mode_gated"]
        assert "brainstorm" in mode_gated
        agents = mode_gated["brainstorm"]["agents"]
        assert "creative-thinker" in agents
        assert "idea-generator" in agents

    # ------------------------------------------------------------------
    # 8  agent_level.tools from agents/*.md frontmatter
    # ------------------------------------------------------------------

    def test_agent_level_tools_from_frontmatter(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")
        _write(
            bundle_dir / "agents" / "explorer.md",
            textwrap.dedent("""\
                ---
                meta:
                  name: explorer
                tools:
                  - module: code_search
                    name: grep
                  - module: file_ops
                    name: read
                ---
                # Explorer agent
            """),
        )
        result = scan_cache(cache_root=cache)
        agent_level = result["myb"]["agent_level"]
        assert "explorer" in agent_level
        tools = agent_level["explorer"]["tools"]
        assert {"module": "code_search", "name": "grep"} in tools
        assert {"module": "file_ops", "name": "read"} in tools

    # ------------------------------------------------------------------
    # 9  bundle.yaml fallback (no bundle.md)
    # ------------------------------------------------------------------

    def test_bundle_yaml_support(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        bundle_dir = cache / "amplifier-myb-0123456789abcdef"
        bundle_dir.mkdir(parents=True)
        # Only bundle.yaml — no bundle.md
        _write(
            bundle_dir / "bundle.yaml",
            textwrap.dedent("""\
                bundle:
                  name: myb
            """),
        )
        result = scan_cache(cache_root=cache)
        assert "myb" in result

    # ------------------------------------------------------------------
    # 10  missing subdirs (no behaviors/, agents/, modes/, skills/) — no crash
    # ------------------------------------------------------------------

    def test_missing_subdirs_tolerated(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import scan_cache

        cache = tmp_path / "cache"
        _make_bundle(cache, "myb")  # only bundle.md; no subdirs
        result = scan_cache(cache_root=cache)
        entry = result["myb"]
        assert len(entry["always_active"]["agents"]) == 0
        assert len(entry["always_active"]["context"]) == 0
        assert len(entry["agent_level"]) == 0
        assert len(entry["mode_gated"]) == 0


# ---------------------------------------------------------------------------
# TestInventoryDiskCache
# ---------------------------------------------------------------------------


class TestInventoryDiskCache:
    """3 tests verifying .bundle-scan.json disk caching behaviour."""

    # ------------------------------------------------------------------
    # 1  cache file written after first scan
    # ------------------------------------------------------------------

    def test_disk_cache_written_after_first_scan(self, tmp_path: Path) -> None:
        from context_intelligence.bundle_analysis.inventory import (
            _CACHE_FILE,
            scan_cache,
        )

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")
        scan_cache(cache_root=cache)
        assert (bundle_dir / _CACHE_FILE).exists()

    # ------------------------------------------------------------------
    # 2  cache hit returns pre-seeded sentinel value
    # ------------------------------------------------------------------

    def test_disk_cache_hit_skips_parse(self, tmp_path: Path) -> None:
        """Pre-seed .bundle-scan.json; prove scan_cache returns cached data."""
        from context_intelligence.bundle_analysis.inventory import (
            _CACHE_FILE,
            scan_cache,
        )

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")

        cache_payload = {
            "_bundle_name": "myb",
            "cache_key": bundle_dir.name,
            "always_active": {
                "agents": ["from-cache"],
                "context": [],
                "skills": [],
                "recipes": [],
            },
            "agent_level": {},
            "mode_gated": {},
            "modes": [],
            "scan_source": "present",
        }
        (bundle_dir / _CACHE_FILE).write_text(json.dumps(cache_payload), encoding="utf-8")

        result = scan_cache(cache_root=cache)
        agents = result["myb"]["always_active"]["agents"]
        # sentinel value from cache — filesystem has no behaviors/*.yaml
        assert "from-cache" in agents

    # ------------------------------------------------------------------
    # 3  stale cache_key causes cache miss → fresh scan
    # ------------------------------------------------------------------

    def test_disk_cache_miss_when_cache_key_changes(self, tmp_path: Path) -> None:
        """Stale cache_key='wrong-key' is rejected; fresh scan returns no agents."""
        from context_intelligence.bundle_analysis.inventory import (
            _CACHE_FILE,
            scan_cache,
        )

        cache = tmp_path / "cache"
        bundle_dir = _make_bundle(cache, "myb")

        stale_payload = {
            "_bundle_name": "myb",
            "cache_key": "wrong-key",  # deliberate mismatch
            "always_active": {
                "agents": ["from-stale-cache"],
                "context": [],
                "skills": [],
                "recipes": [],
            },
            "agent_level": {},
            "mode_gated": {},
            "modes": [],
            "scan_source": "present",
        }
        (bundle_dir / _CACHE_FILE).write_text(json.dumps(stale_payload), encoding="utf-8")

        result = scan_cache(cache_root=cache)
        # Fresh scan: no behaviors/*.yaml in the bundle → zero declared agents
        agents = result["myb"]["always_active"]["agents"]
        assert "from-stale-cache" not in agents
