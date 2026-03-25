"""Tests verifying tool-graph-query is declared in behavior YAML and agent frontmatter.

These tests enforce that:
1. behaviors/context-intelligence.yaml has three sections: agents:, tools:, hooks: (in that order)
2. tools: section contains tool-graph-query with correct git source URL
3. agents/context-intelligence-analyst.md frontmatter lists 5 tools including tool-graph-query
"""

from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).parent.parent
BEHAVIOR_YAML = BUNDLE_DIR / "behaviors" / "context-intelligence.yaml"
AGENT_MD = BUNDLE_DIR / "agents" / "graph-analyst.md"

TOOL_GRAPH_QUERY_SOURCE = (
    "git+https://github.com/microsoft/amplifier-bundle-context-intelligence"
    "@main#subdirectory=modules/tool-graph-query"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def behavior_raw() -> str:
    return BEHAVIOR_YAML.read_text()


@pytest.fixture(scope="session")
def behavior_parsed(behavior_raw) -> dict:
    return yaml.safe_load(behavior_raw)


@pytest.fixture(scope="session")
def agent_raw() -> str:
    return AGENT_MD.read_text()


@pytest.fixture(scope="session")
def agent_frontmatter(agent_raw) -> dict:
    """Parse YAML frontmatter from the agent markdown file."""
    lines = agent_raw.splitlines()
    if lines[0].strip() != "---":
        pytest.skip("No frontmatter found in agent file")
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        pytest.skip("Frontmatter not closed")
    frontmatter_text = "\n".join(lines[1:end])
    return yaml.safe_load(frontmatter_text)


# ---------------------------------------------------------------------------
# behaviors/context-intelligence.yaml — tools: section tests
# ---------------------------------------------------------------------------


class TestBehaviorYamlToolsSection:
    """behaviors/context-intelligence.yaml must have a tools: section with tool-graph-query."""

    def test_tools_section_present(self, behavior_parsed):
        """Top-level tools: key must exist."""
        assert "tools" in behavior_parsed, (
            "behaviors/context-intelligence.yaml must have a top-level 'tools:' section"
        )

    def test_tools_section_is_list(self, behavior_parsed):
        """tools: must be a list."""
        assert isinstance(behavior_parsed["tools"], list), (
            "behaviors/context-intelligence.yaml 'tools:' must be a list"
        )

    def test_tools_contains_tool_graph_query(self, behavior_parsed):
        """tools: must include a tool-graph-query entry."""
        tools = behavior_parsed["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert "tool-graph-query" in modules, (
            "behaviors/context-intelligence.yaml 'tools:' must contain 'tool-graph-query'"
        )

    def test_tool_graph_query_has_correct_source(self, behavior_parsed):
        """tool-graph-query must have the correct git source URL."""
        tools = behavior_parsed["tools"]
        for tool in tools:
            if isinstance(tool, dict) and tool.get("module") == "tool-graph-query":
                assert tool.get("source") == TOOL_GRAPH_QUERY_SOURCE, (
                    f"tool-graph-query source must be '{TOOL_GRAPH_QUERY_SOURCE}', "
                    f"got '{tool.get('source')}'"
                )
                return
        pytest.fail("tool-graph-query entry not found in tools: section")

    def test_behavior_yaml_sections_order(self, behavior_raw):
        """agents:, tools:, hooks: must appear in that order in the YAML file."""
        agents_pos = behavior_raw.find("\nagents:")
        tools_pos = behavior_raw.find("\ntools:")
        hooks_pos = behavior_raw.find("\nhooks:")
        assert agents_pos != -1, (
            "behaviors/context-intelligence.yaml must have 'agents:' section"
        )
        assert tools_pos != -1, (
            "behaviors/context-intelligence.yaml must have 'tools:' section"
        )
        assert hooks_pos != -1, (
            "behaviors/context-intelligence.yaml must have 'hooks:' section"
        )
        assert agents_pos < tools_pos, "'agents:' must appear before 'tools:'"
        assert tools_pos < hooks_pos, "'tools:' must appear before 'hooks:'"

    def test_behavior_yaml_has_three_top_level_sections(self, behavior_parsed):
        """YAML must have exactly agents:, tools:, and hooks: as top-level keys (besides comments)."""
        assert "agents" in behavior_parsed
        assert "tools" in behavior_parsed
        assert "hooks" in behavior_parsed


# ---------------------------------------------------------------------------
# agents/graph-analyst.md — frontmatter tools: section tests
# ---------------------------------------------------------------------------


class TestAgentFrontmatterTools:
    """agents/graph-analyst.md frontmatter must list 5 tools including tool-graph-query."""

    def test_frontmatter_has_tools_section(self, agent_frontmatter):
        """Frontmatter must have a tools: section."""
        assert "tools" in agent_frontmatter, (
            "agents/graph-analyst.md frontmatter must have a 'tools:' section"
        )

    def test_frontmatter_tools_has_five_entries(self, agent_frontmatter):
        """tools: section must list exactly 5 modules."""
        tools = agent_frontmatter["tools"]
        assert len(tools) == 5, (
            f"agents/graph-analyst.md frontmatter 'tools:' must list 5 modules, "
            f"got {len(tools)}: {[t.get('module') for t in tools]}"
        )

    def test_frontmatter_tools_contains_tool_graph_query(self, agent_frontmatter):
        """Frontmatter tools: must include tool-graph-query."""
        tools = agent_frontmatter["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert "tool-graph-query" in modules, (
            "agents/graph-analyst.md frontmatter 'tools:' must contain 'tool-graph-query'"
        )

    def test_frontmatter_tool_graph_query_has_correct_source(self, agent_frontmatter):
        """tool-graph-query in agent frontmatter must have the correct git source URL."""
        tools = agent_frontmatter["tools"]
        for tool in tools:
            if isinstance(tool, dict) and tool.get("module") == "tool-graph-query":
                assert tool.get("source") == TOOL_GRAPH_QUERY_SOURCE, (
                    f"tool-graph-query source must be '{TOOL_GRAPH_QUERY_SOURCE}', "
                    f"got '{tool.get('source')}'"
                )
                return
        pytest.fail(
            "tool-graph-query entry not found in agent frontmatter tools: section"
        )

    def test_frontmatter_companion_tools_present(self, agent_frontmatter):
        """All 4 companion tools must be present alongside tool-graph-query."""
        tools = agent_frontmatter["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        expected = {"tool-blob-read", "tool-filesystem", "tool-bash", "tool-skills"}
        for mod in expected:
            assert mod in modules, (
                f"Companion tool '{mod}' must be present in graph-analyst frontmatter tools: section"
            )

    def test_tool_graph_query_is_first_tool(self, agent_frontmatter):
        """tool-graph-query must be the first tool in the list (primary graph capability)."""
        tools = agent_frontmatter["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert modules, "tools list must not be empty"
        assert modules[0] == "tool-graph-query", (
            f"tool-graph-query must be the first tool in graph-analyst frontmatter, "
            f"got '{modules[0]}' first"
        )
