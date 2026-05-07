"""Tests for the redesigned context-intelligence-design mode file.

Frontmatter parses as YAML and has the new tool policy at the right level;
default_action is at mode level (not nested in tools); Required safe-list tools
are present including delegate / load_skill / todo; Dead frontmatter keys
(agents.include, context.include, context.scan) are absent; @mention lines for
context files are present in the body; Mode body mandates the facilitator before
any write; The new output folder convention (.context-intelligence-investigation/)
is referenced; The old folder convention (.amplifier/context-intelligence/) is NOT
referenced; Transition guidance points to /brainstorm (not /write-plan).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
MODE_PATH = REPO_ROOT / "modes" / "context-intelligence-design.md"


@pytest.fixture
def mode_text() -> str:
    return MODE_PATH.read_text()


@pytest.fixture
def frontmatter_dict(mode_text: str) -> dict:
    assert mode_text.startswith("---\n")
    end = mode_text.index("\n---\n", 4)
    yaml_content = mode_text[4:end]
    return yaml.safe_load(yaml_content)


@pytest.fixture
def mode_body(mode_text: str) -> str:
    assert mode_text.startswith("---\n")
    end = mode_text.index("\n---\n", 4)
    return mode_text[end + 5 :]


class TestFrontmatterStructure:
    def test_mode_section_exists(self, frontmatter_dict):
        assert "mode" in frontmatter_dict

    def test_mode_name(self, frontmatter_dict):
        assert frontmatter_dict["mode"]["name"] == "context-intelligence-design"

    def test_default_action_at_mode_level(self, frontmatter_dict):
        mode = frontmatter_dict["mode"]
        # default_action must be at mode level (NOT nested in tools)
        assert "default_action" in mode
        assert mode["default_action"] == "block"
        # Ensure it's NOT nested inside tools
        tools = mode.get("tools", {})
        assert "default_action" not in tools


class TestToolPolicy:
    def test_safe_list_contents(self, frontmatter_dict):
        tools = frontmatter_dict["mode"]["tools"]
        assert set(tools["safe"]) == {
            "graph_query",
            "blob_read",
            "read_file",
            "glob",
            "grep",
            "delegate",
            "load_skill",
            "todo",
        }

    def test_warn_list_contents(self, frontmatter_dict):
        tools = frontmatter_dict["mode"]["tools"]
        assert set(tools["warn"]) == {"bash", "write_file", "edit_file"}

    def test_no_block_list(self, frontmatter_dict):
        tools = frontmatter_dict["mode"]["tools"]
        assert "block" not in tools


class TestDeadFrontmatterRemoved:
    def test_no_agents_include(self, frontmatter_dict):
        mode = frontmatter_dict["mode"]
        assert "agents" not in mode

    def test_no_context_include(self, frontmatter_dict):
        mode = frontmatter_dict["mode"]
        context = mode.get("context", {})
        if context:
            assert "include" not in context

    def test_no_context_scan(self, frontmatter_dict):
        mode = frontmatter_dict["mode"]
        context = mode.get("context", {})
        if context:
            assert "scan" not in context


class TestBodyMentions:
    def test_dual_path_template_mentioned(self, mode_body):
        assert "@context-intelligence:context/dual-path-library-template.md" in mode_body

    def test_jsonl_event_schema_mentioned(self, mode_body):
        assert "@context-intelligence:context/jsonl-event-schema.md" in mode_body


class TestFacilitatorMandate:
    def test_facilitator_referenced(self, mode_body):
        assert "context-intelligence-design-facilitator" in mode_body

    def test_mandate_phrasing(self, mode_body):
        body_lower = mode_body.lower()
        assert "before writing any artifact" in body_lower or "before any write" in body_lower


class TestFolderConvention:
    def test_new_folder_present(self, mode_body):
        assert ".context-intelligence-investigation/" in mode_body

    def test_old_folder_absent(self, mode_text):
        assert ".amplifier/context-intelligence/" not in mode_text


class TestTransitionGuidance:
    def test_brainstorm_suggested(self, mode_body):
        assert "/brainstorm" in mode_body

    def test_write_plan_not_directly_suggested(self, mode_body):
        assert "Suggest /write-plan" not in mode_body
        assert "suggest /write-plan" not in mode_body


class TestOutputTypeConstraint:
    def test_md_cypher_dot_mentioned(self, mode_body):
        assert ".md" in mode_body
        assert ".cypher" in mode_body
        assert ".dot" in mode_body
