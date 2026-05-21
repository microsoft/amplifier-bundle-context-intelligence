"""Tests for context_intelligence.bundle_analysis.gap — named-set arithmetic.

gap.py performs deterministic set arithmetic — no LLM:
- always_active: declared vs used named sets per component type
- mode_gated: per-mode declared named sets (Phase 1: used always empty)
- modes: declared, activated, never_activated
- improvement classification: tree-shake / mode-refactor / config-gap /
  mode-never-activated
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Test helpers (named-set format)
# ---------------------------------------------------------------------------


def _aa(**kwargs) -> dict:
    """Build a default-empty always_active block.

    Keys: agents, context, skills, recipes (inventory-native keys).
    Pass keyword args to override any key with a set.
    """
    base: dict = {
        "agents": set(),
        "context": set(),
        "skills": set(),
        "recipes": set(),
    }
    base.update(kwargs)
    return base


def _inv_entry(
    always_active: dict | None = None,
    mode_gated: dict | None = None,
    modes: set | None = None,
) -> dict:
    """Build a three-tier inventory entry."""
    return {
        "always_active": always_active if always_active is not None else _aa(),
        "mode_gated": mode_gated if mode_gated is not None else {},
        "modes": modes if modes is not None else set(),
    }


# ---------------------------------------------------------------------------
# TestGapNamedSets — six required tests
# ---------------------------------------------------------------------------


class TestGapNamedSets:
    def test_tree_shake_names_specific_components(self):
        """foundation declares {explorer, zen-architect}; no signals → tree-shake."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        inventory = {
            "foundation": _inv_entry(
                always_active=_aa(agents={"explorer", "zen-architect"}),
            ),
        }
        result = compute_gap(signals={}, inventory=inventory)
        tree_shakes = [i for i in result["improvement"] if i["type"] == "tree-shake"]
        agent_ts = [i for i in tree_shakes if i["component_type"] == "agents"]

        assert len(agent_ts) == 1
        assert agent_ts[0]["bundle"] == "foundation"
        assert agent_ts[0]["scope"] == "always_active"
        assert agent_ts[0]["names"] == ["explorer", "zen-architect"]  # sorted
        assert agent_ts[0]["mode_name"] is None

    def test_mode_refactor_names_unused_components(self):
        """10 declared, 1 used → 10% < 20% threshold → mode-refactor with 9 unused names."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        all_agents = {f"agent-{i}" for i in range(10)}
        used_agent = "agent-0"

        inventory = {
            "testbundle": _inv_entry(
                always_active=_aa(agents=all_agents),
            ),
        }
        signals = {
            "testbundle": {
                "agents": {used_agent},
                "skills": set(),
                "recipes": set(),
                "context": set(),
                "tools": set(),
                "modes": set(),
            },
        }
        result = compute_gap(signals=signals, inventory=inventory)
        refactors = [
            i
            for i in result["improvement"]
            if i["type"] == "mode-refactor" and i["bundle"] == "testbundle"
        ]
        agent_rf = [i for i in refactors if i["component_type"] == "agents"]

        assert len(agent_rf) == 1
        assert len(agent_rf[0]["names"]) == 9  # 10 declared - 1 used = 9 unused
        expected_unused = sorted(all_agents - {used_agent})
        assert agent_rf[0]["names"] == expected_unused

    def test_mode_never_activated_fires(self):
        """A declared mode that never appears in signals.modes → mode-never-activated."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        inventory = {
            "mybundle": _inv_entry(
                modes={"design-mode"},
                mode_gated={
                    "design-mode": {
                        "agents": {"design-agent"},
                        "context": set(),
                        "skills": set(),
                    }
                },
            ),
        }
        result = compute_gap(signals={}, inventory=inventory)
        never_activated = [i for i in result["improvement"] if i["type"] == "mode-never-activated"]

        assert len(never_activated) == 1
        assert never_activated[0]["bundle"] == "mybundle"
        assert never_activated[0]["mode_name"] == "design-mode"
        assert never_activated[0]["component_type"] == "modes"
        assert never_activated[0]["scope"] == "mode_gated"
        assert "design-agent" in never_activated[0]["names"]

    def test_config_gap_bundle_in_signals_not_inventory(self):
        """Bundle present in signals but absent from inventory → config-gap."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        signals = {
            "mystery-bundle": {
                "agents": {"secret-agent"},
                "skills": set(),
                "recipes": set(),
                "context": set(),
                "tools": set(),
                "modes": set(),
            },
        }
        result = compute_gap(signals=signals, inventory={})
        config_gaps = [i for i in result["improvement"] if i["type"] == "config-gap"]

        assert len(config_gaps) == 1
        cg = config_gaps[0]
        assert cg["bundle"] == "mystery-bundle"
        assert cg["component_type"] == "agents"
        assert cg["scope"] == "always_active"
        assert cg["mode_name"] is None
        assert "secret-agent" in cg["names"]
        assert cg["reason"] == "invoked in session but absent from cache inventory"

    def test_two_tier_mode_gated_zero_use_does_not_trigger_tree_shake(self):
        """Mode-gated components with zero usage must NOT trigger tree-shake."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        # Nothing in always_active, but mode_gated has agents
        inventory = {
            "mybundle": _inv_entry(
                always_active=_aa(),  # all empty
                mode_gated={
                    "dev-mode": {
                        "agents": {"dev-agent"},
                        "context": set(),
                        "skills": set(),
                    }
                },
                modes={"dev-mode"},
            ),
        }
        result = compute_gap(signals={}, inventory=inventory)
        tree_shakes = [i for i in result["improvement"] if i["type"] == "tree-shake"]

        assert tree_shakes == []

    def test_per_bundle_schema_has_three_tiers(self):
        """per_bundle entry must have always_active, mode_gated, and modes tiers."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        inventory = {
            "testbundle": _inv_entry(
                always_active=_aa(agents={"explorer"}),
                modes={"design-mode"},
                mode_gated={
                    "design-mode": {
                        "agents": {"design-agent"},
                        "context": set(),
                        "skills": set(),
                    }
                },
            ),
        }
        signals = {
            "testbundle": {
                "agents": {"explorer"},
                "skills": set(),
                "recipes": set(),
                "context": set(),
                "tools": set(),
                "modes": set(),
            },
        }
        result = compute_gap(signals=signals, inventory=inventory)
        bundle_entry = result["per_bundle"]["testbundle"]

        # always_active tier has declared/used/unused/util_pct
        aa = bundle_entry["always_active"]
        assert "declared" in aa
        assert "used" in aa
        assert "unused" in aa
        assert "util_pct" in aa

        # Values are dicts mapping component_type → set (or float/None)
        assert isinstance(aa["declared"]["agents"], (set, frozenset))
        assert isinstance(aa["used"]["agents"], (set, frozenset))
        assert isinstance(aa["unused"]["agents"], (set, frozenset))

        # explorer declared and used → util_pct = 1.0, unused = empty
        assert aa["declared"]["agents"] == {"explorer"}
        assert aa["used"]["agents"] == {"explorer"}
        assert aa["unused"]["agents"] == set()
        assert aa["util_pct"]["agents"] == 1.0

        # modes tier
        assert "modes" in bundle_entry
        modes_block = bundle_entry["modes"]
        assert "declared" in modes_block
        assert "activated" in modes_block
        assert "never_activated" in modes_block

        # design-mode declared, never activated (signals.modes = empty)
        assert "design-mode" in modes_block["declared"]
        assert "design-mode" in modes_block["never_activated"]
        assert "design-mode" not in modes_block["activated"]
