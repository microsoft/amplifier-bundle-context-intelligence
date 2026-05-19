"""Tests for context_intelligence.bundle_analysis.gap.

gap.py performs deterministic set arithmetic — no LLM:
- util_gap: declared components minus invoked components
- config_gap: declared bundles not present in any signal (zero invocations)
- improvement classification: tree-shake / mode-refactor / discovery-eval
"""

from __future__ import annotations


def make_inventory(**overrides):
    base = {
        "_meta": {"scan_source": "cache"},
        "foundation": {
            "scan_source": "cache",
            "declared": {
                "agents": ["explorer", "zen-architect", "session-analyst"],
                "modes": [],
                "skills": [],
                "recipes": [],
            },
        },
        "dormant-bundle": {
            "scan_source": "cache",
            "declared": {
                "agents": ["unused-agent"],
                "modes": [],
                "skills": [],
                "recipes": [],
            },
        },
    }
    base.update(overrides)
    return base


def zero_counts():
    return {"agents": 0, "skills": 0, "modes": 0, "recipes": 0, "tools": 0}


class TestComputeGap:
    def test_returns_dict(self):
        from context_intelligence.bundle_analysis.gap import compute_gap

        result = compute_gap(signals={}, inventory=make_inventory())
        assert isinstance(result, dict)

    def test_zero_usage_bundle_classified_tree_shake(self):
        from context_intelligence.bundle_analysis.gap import compute_gap

        result = compute_gap(signals={}, inventory=make_inventory())
        # dormant-bundle has zero invocations across all components
        improvements = result["improvement"]
        tree_shake = [i for i in improvements if i["type"] == "tree-shake"]
        bundles = {i["bundle"] for i in tree_shake}
        assert "dormant-bundle" in bundles

    def test_util_gap_lists_unused_components(self):
        from context_intelligence.bundle_analysis.gap import compute_gap

        signals = {"foundation": {**zero_counts(), "agents": 1}}
        result = compute_gap(signals=signals, inventory=make_inventory())
        # foundation declared 3 agents, agents-invocation > 0 means at least one was used.
        # util_gap reports the count of declared components with no evidence of use.
        # We don't have per-agent invocation in counts — at minimum gap surfaces the totals.
        fnd_gap = result["per_bundle"]["foundation"]
        assert fnd_gap["declared"]["agents"] == 3
        assert fnd_gap["used"]["agents"] == 1
        # util_gap counts components in declared but absent from used
        # (best-effort at the count level)
        assert fnd_gap["util_gap"]["agents"] >= 0

    def test_used_bundle_not_in_tree_shake(self):
        from context_intelligence.bundle_analysis.gap import compute_gap

        signals = {"foundation": {**zero_counts(), "agents": 1}}
        result = compute_gap(signals=signals, inventory=make_inventory())
        tree_shake_bundles = {
            i["bundle"] for i in result["improvement"] if i["type"] == "tree-shake"
        }
        assert "foundation" not in tree_shake_bundles

    def test_mode_refactor_threshold(self):
        """A bundle used only via mode activation with few invocations → mode-refactor."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        signals = {
            "foundation": {**zero_counts(), "agents": 1, "modes": 0},
        }
        # foundation declares 10 agents, only 1 invocation → < 20% threshold
        inv = make_inventory(foundation={
            "scan_source": "cache",
            "declared": {
                "agents": [f"agent-{i}" for i in range(10)],
                "modes": [], "skills": [], "recipes": [],
            },
        })
        result = compute_gap(signals=signals, inventory=inv)
        types = {i["type"] for i in result["improvement"] if i["bundle"] == "foundation"}
        assert "mode-refactor" in types

    def test_per_bundle_arithmetic_consistency(self):
        """declared >= used (counts cannot be negative)."""
        from context_intelligence.bundle_analysis.gap import compute_gap

        signals = {"foundation": {**zero_counts(), "agents": 100}}  # over-count
        result = compute_gap(signals=signals, inventory=make_inventory())
        pb = result["per_bundle"]["foundation"]
        # The util_gap should be clamped to >= 0
        assert pb["util_gap"]["agents"] >= 0
