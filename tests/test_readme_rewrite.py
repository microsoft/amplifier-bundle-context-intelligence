"""Tests for the rewritten README.md.

Validates that the README contains both correct agents, accurate tool names,
correct configuration defaults, and an accurate repository structure tree.

These tests focus on requirements NOT already covered by test_docs_and_yaml.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
README = BUNDLE_ROOT / "README.md"


@pytest.fixture(scope="session")
def readme() -> str:
    return README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Prohibited content tests
# ---------------------------------------------------------------------------


class TestProhibitedContent:
    """README must NOT contain stale references from the old design."""

    def test_no_old_analyst_agent_name(self, readme: str) -> None:
        """`context-intelligence-analyst` (the old single-agent name) must not appear."""
        # Use word-boundary match: must not contain as a standalone identifier
        # Allow 'graph-analyst' and 'navigator' but not the old 'context-intelligence-analyst'
        matches = re.findall(r"\bcontext-intelligence-analyst\b", readme)
        assert len(matches) == 0, (
            f"README must not reference old agent name 'context-intelligence-analyst', "
            f"found {len(matches)} occurrence(s). Use 'context-intelligence-graph-analyst' "
            f"and 'context-intelligence-navigator' instead."
        )

    def test_no_blob_list_tool(self, readme: str) -> None:
        """`blob_list` tool must not be referenced in the README."""
        assert "blob_list" not in readme, (
            "README must not reference 'blob_list' — this tool was removed from the design."
        )

    def test_no_blob_dump_tool(self, readme: str) -> None:
        """`blob_dump` tool must not be referenced in the README."""
        assert "blob_dump" not in readme, (
            "README must not reference 'blob_dump' — this tool was removed from the design."
        )

    def test_no_neo4j_search_skill(self, readme: str) -> None:
        """`context-intelligence-neo4j-search` skill must not be in README."""
        assert "context-intelligence-neo4j-search" not in readme, (
            "README must not reference 'context-intelligence-neo4j-search' — "
            "this skill was renamed or removed."
        )

    def test_no_behaviors_directory(self, readme: str) -> None:
        """`behaviors/` directory must not appear in README repo structure."""
        assert "behaviors/" not in readme, (
            "README repo structure tree must not include 'behaviors/' — "
            "the bundle uses modules/, not a behaviors/ directory."
        )


# ---------------------------------------------------------------------------
# Both agents must be described
# ---------------------------------------------------------------------------


class TestAgentsSection:
    """README must describe both agents: graph-analyst and navigator."""

    def test_agents_section_present(self, readme: str) -> None:
        """README must contain an '## Agents' section."""
        assert "## Agents" in readme, "README must contain an '## Agents' section"

    def test_graph_analyst_agent_mentioned(self, readme: str) -> None:
        """README must mention context-intelligence-graph-analyst."""
        assert "graph-analyst" in readme, (
            "README must describe the context-intelligence-graph-analyst agent"
        )

    def test_navigator_agent_mentioned(self, readme: str) -> None:
        """README must mention context-intelligence-navigator."""
        assert "navigator" in readme, (
            "README must describe the context-intelligence-navigator agent"
        )

    def test_graph_analyst_is_primary_entry_point(self, readme: str) -> None:
        """README must describe graph-analyst as the primary entry point."""
        # Find the agents section and verify graph-analyst is described as primary
        agents_pos = readme.find("## Agents")
        assert agents_pos != -1, "README must contain an '## Agents' section"
        agents_section = readme[agents_pos:agents_pos + 2000]
        assert "primary" in agents_section.lower() or "entry point" in agents_section.lower(), (
            "README must describe graph-analyst as the primary entry point in the Agents section"
        )

    def test_navigator_is_local_fallback(self, readme: str) -> None:
        """README must describe navigator as local fallback."""
        assert "fallback" in readme.lower(), (
            "README must describe navigator as the local fallback agent"
        )

    def test_agents_table_present(self, readme: str) -> None:
        """README Agents section must include a table of agents and their tools."""
        agents_pos = readme.find("## Agents")
        assert agents_pos != -1
        agents_section = readme[agents_pos:agents_pos + 2000]
        # Check for table formatting (markdown table has | separators)
        assert "|" in agents_section, (
            "README Agents section must contain a table listing agents and their tools"
        )

    def test_delegation_chain_described(self, readme: str) -> None:
        """README must describe the delegation chain from graph-analyst to navigator."""
        assert "delegation" in readme.lower() or "delegates" in readme.lower(), (
            "README must describe the delegation chain from graph-analyst to navigator"
        )


# ---------------------------------------------------------------------------
# Accurate repository structure
# ---------------------------------------------------------------------------


class TestRepositoryStructure:
    """README must have an accurate repository structure tree."""

    def test_repository_structure_section_present(self, readme: str) -> None:
        """README must contain a 'Repository structure' section."""
        assert "## Repository structure" in readme, (
            "README must contain a '## Repository structure' section"
        )

    def test_tree_has_agents_directory(self, readme: str) -> None:
        """Repo structure must show agents/ directory."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        assert "agents/" in structure_section, (
            "README repo structure must show agents/ directory"
        )

    def test_tree_has_graph_analyst_in_agents(self, readme: str) -> None:
        """Repo structure must show graph-analyst in agents/."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        assert "graph-analyst" in structure_section, (
            "README repo structure must show context-intelligence-graph-analyst.md in agents/"
        )

    def test_tree_has_navigator_in_agents(self, readme: str) -> None:
        """Repo structure must show navigator in agents/."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        assert "navigator" in structure_section, (
            "README repo structure must show context-intelligence-navigator.md in agents/"
        )

    def test_tree_has_delegation_strategy_dot(self, readme: str) -> None:
        """Repo structure must show context/delegation-strategy.dot."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        assert "delegation-strategy.dot" in structure_section, (
            "README repo structure must show context/delegation-strategy.dot"
        )

    def test_tree_has_tool_graph_query_module(self, readme: str) -> None:
        """Repo structure must show modules/tool-graph-query."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        assert "tool-graph-query" in structure_section, (
            "README repo structure must show modules/tool-graph-query"
        )

    def test_tree_has_tool_blob_read_module(self, readme: str) -> None:
        """Repo structure must show modules/tool-blob-read."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        assert "tool-blob-read" in structure_section, (
            "README repo structure must show modules/tool-blob-read"
        )

    def test_tree_has_two_skills_directories(self, readme: str) -> None:
        """Repo structure must show 2 skill directories."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        # Check that skills/ is mentioned
        assert "skills/" in structure_section, (
            "README repo structure must show skills/ directory"
        )
        # Check both skill directories
        assert "context-intelligence-graph-query" in structure_section, (
            "README repo structure must show context-intelligence-graph-query skill"
        )
        assert "context-intelligence-session-navigation" in structure_section, (
            "README repo structure must show context-intelligence-session-navigation skill"
        )

    def test_tree_has_docs_directory(self, readme: str) -> None:
        """Repo structure must show docs/ directory."""
        structure_pos = readme.find("## Repository structure")
        assert structure_pos != -1
        structure_section = readme[structure_pos:structure_pos + 2000]
        assert "docs/" in structure_section, (
            "README repo structure must show docs/ directory"
        )


# ---------------------------------------------------------------------------
# Dispatch timeout correct default
# ---------------------------------------------------------------------------


class TestDispatchTimeoutDefault:
    """dispatch_timeout must show default of 30 with correct env var."""

    def test_dispatch_timeout_default_30_on_same_line(self, readme: str) -> None:
        """dispatch_timeout row must have AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT and 30."""
        # This is the spec acceptance criteria pattern
        lines = readme.splitlines()
        matching_lines = [
            line for line in lines
            if "dispatch_timeout" in line
            and "AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT" in line
            and "30" in line
        ]
        assert len(matching_lines) == 1, (
            f"README must have exactly 1 line with dispatch_timeout + "
            f"AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT + '30', "
            f"found {len(matching_lines)}: {matching_lines}"
        )


# ---------------------------------------------------------------------------
# Section ordering
# ---------------------------------------------------------------------------


class TestSectionOrdering:
    """Key sections must appear in the correct order."""

    def test_configuration_reference_before_server_dispatch(self, readme: str) -> None:
        """Configuration reference must appear before Server dispatch."""
        config_pos = readme.find("## Configuration reference")
        dispatch_pos = readme.find("## Server dispatch")
        assert config_pos != -1, "README must have '## Configuration reference'"
        assert dispatch_pos != -1, "README must have '## Server dispatch'"
        assert config_pos < dispatch_pos, (
            "'## Configuration reference' must appear before '## Server dispatch'"
        )

    def test_server_dispatch_before_what_gets_stored(self, readme: str) -> None:
        """Server dispatch must appear before What gets stored."""
        dispatch_pos = readme.find("## Server dispatch")
        stored_pos = readme.find("## What gets stored")
        assert dispatch_pos != -1, "README must have '## Server dispatch'"
        assert stored_pos != -1, "README must have '## What gets stored'"
        assert dispatch_pos < stored_pos, (
            "'## Server dispatch' must appear before '## What gets stored'"
        )

    def test_agents_section_present_and_ordered(self, readme: str) -> None:
        """README must have an Agents section after What gets stored."""
        stored_pos = readme.find("## What gets stored")
        agents_pos = readme.find("## Agents")
        assert stored_pos != -1
        assert agents_pos != -1
        assert stored_pos < agents_pos, (
            "'## Agents' must appear after '## What gets stored'"
        )


# ---------------------------------------------------------------------------
# Context intelligence path in What gets stored
# ---------------------------------------------------------------------------


class TestWhatGetsStored:
    """What gets stored must show context-intelligence/ subdirectory."""

    def test_context_intelligence_subdir_in_path(self, readme: str) -> None:
        """What gets stored must show context-intelligence/ as subdirectory in path."""
        stored_pos = readme.find("## What gets stored")
        assert stored_pos != -1
        stored_section = readme[stored_pos:stored_pos + 2000]
        assert "context-intelligence/" in stored_section, (
            "README 'What gets stored' must show context-intelligence/ subdirectory in path"
        )
