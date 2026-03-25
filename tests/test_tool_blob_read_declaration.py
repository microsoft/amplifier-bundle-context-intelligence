"""Tests verifying tool-blob-read is declared in behavior YAML.

These tests enforce that:
1. behaviors/context-intelligence.yaml tools: section contains tool-blob-read
2. tool-blob-read has the correct git source URL
3. tool-blob-read appears after tool-graph-query in the tools: list
"""

from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).parent.parent
BEHAVIOR_YAML = BUNDLE_DIR / "behaviors" / "context-intelligence.yaml"

TOOL_BLOB_READ_SOURCE = (
    "git+https://github.com/colombod/amplifier-bundle-context-intelligence"
    "@main#subdirectory=modules/tool-blob-read"
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


# ---------------------------------------------------------------------------
# behaviors/context-intelligence.yaml — tool-blob-read tests
# ---------------------------------------------------------------------------


class TestBehaviorYamlToolBlobRead:
    """behaviors/context-intelligence.yaml must include a tool-blob-read entry."""

    def test_tools_contains_tool_blob_read(self, behavior_parsed):
        """tools: must include a tool-blob-read entry."""
        tools = behavior_parsed["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert "tool-blob-read" in modules, (
            "behaviors/context-intelligence.yaml 'tools:' must contain 'tool-blob-read'"
        )

    def test_tool_blob_read_has_correct_source(self, behavior_parsed):
        """tool-blob-read must have the correct git source URL."""
        tools = behavior_parsed["tools"]
        for tool in tools:
            if isinstance(tool, dict) and tool.get("module") == "tool-blob-read":
                assert tool.get("source") == TOOL_BLOB_READ_SOURCE, (
                    f"tool-blob-read source must be '{TOOL_BLOB_READ_SOURCE}', "
                    f"got '{tool.get('source')}'"
                )
                return
        pytest.fail("tool-blob-read entry not found in tools: section")

    def test_tool_blob_read_after_tool_graph_query(self, behavior_parsed):
        """tool-blob-read must appear after tool-graph-query in the tools: list."""
        tools = behavior_parsed["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert "tool-graph-query" in modules, "tool-graph-query must be in tools list"
        assert "tool-blob-read" in modules, "tool-blob-read must be in tools list"
        graph_query_idx = modules.index("tool-graph-query")
        blob_read_idx = modules.index("tool-blob-read")
        assert graph_query_idx < blob_read_idx, (
            "tool-blob-read must appear after tool-graph-query in the tools: list"
        )

    def test_both_tools_present(self, behavior_parsed):
        """Both tool-graph-query and tool-blob-read must be in the tools: section."""
        tools = behavior_parsed["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert "tool-graph-query" in modules, (
            "behaviors/context-intelligence.yaml 'tools:' must still contain 'tool-graph-query'"
        )
        assert "tool-blob-read" in modules, (
            "behaviors/context-intelligence.yaml 'tools:' must contain 'tool-blob-read'"
        )
