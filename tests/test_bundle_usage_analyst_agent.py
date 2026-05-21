"""Smoke tests for the bundle-usage-analyst agent file."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


AGENT_FILE = Path(__file__).resolve().parents[1] / "agents" / "bundle-usage-analyst.md"


class TestBundleUsageAnalystAgent:
    def test_agent_file_exists(self):
        assert AGENT_FILE.exists(), f"Missing agent file: {AGENT_FILE}"

    def test_frontmatter_parses(self):
        text = AGENT_FILE.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "Agent file must start with YAML frontmatter"
        parts = text.split("---", 2)
        assert len(parts) >= 3, "Agent file missing closing --- delimiter"
        fm = yaml.safe_load(parts[1])
        assert isinstance(fm, dict), "Frontmatter must parse to a dict"

    def test_frontmatter_meta_name_matches(self):
        text = AGENT_FILE.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm.get("meta", {}).get("name") == "bundle-usage-analyst", (
            "meta.name must be exactly 'bundle-usage-analyst' — referenced by "
            "modes/bundle-usage.md mode.contributes.agents"
        )

    def test_frontmatter_has_description(self):
        text = AGENT_FILE.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1])
        desc = fm.get("meta", {}).get("description")
        assert isinstance(desc, str) and desc.strip(), "meta.description must be a non-empty string"

    def test_body_mentions_bundle_usage_tool(self):
        """The body should instruct the agent to call the bundle_usage tool."""
        text = AGENT_FILE.read_text(encoding="utf-8")
        body = text.split("---", 2)[2]
        assert "bundle_usage" in body, "Agent body must reference the bundle_usage tool"

    def test_mode_wiring_consistent(self):
        """modes/bundle-usage.md must reference exactly this agent name."""
        mode_file = AGENT_FILE.parent.parent / "modes" / "bundle-usage.md"
        if not mode_file.exists():
            pytest.skip("modes/bundle-usage.md not present in this submodule layout")
        mode_text = mode_file.read_text(encoding="utf-8")
        assert "bundle-usage-analyst" in mode_text, (
            "modes/bundle-usage.md must reference bundle-usage-analyst — wiring drift"
        )
