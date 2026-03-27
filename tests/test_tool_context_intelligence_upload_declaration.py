"""Tests verifying tool-context-intelligence-upload is declared in behavior YAML.

These tests enforce that:
1. behaviors/context-intelligence.yaml tools: section contains tool-context-intelligence-upload
2. tool-context-intelligence-upload has the correct git source URL
3. tool-context-intelligence-upload appears after tool-blob-read in the tools: list
4. The tools: section contains exactly three entries in the correct order
"""

from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).parent.parent
BEHAVIOR_YAML = BUNDLE_DIR / "behaviors" / "context-intelligence.yaml"

TOOL_UPLOAD_SOURCE = (
    "git+https://github.com/microsoft/amplifier-bundle-context-intelligence"
    "@main#subdirectory=modules/tool-context-intelligence-upload"
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
# behaviors/context-intelligence.yaml — tool-context-intelligence-upload tests
# ---------------------------------------------------------------------------


class TestBehaviorYamlToolUpload:
    """behaviors/context-intelligence.yaml must include a tool-context-intelligence-upload entry."""

    def test_tools_contains_upload_tool(self, behavior_parsed):
        """tools: must include a tool-context-intelligence-upload entry."""
        tools = behavior_parsed["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert "tool-context-intelligence-upload" in modules, (
            "behaviors/context-intelligence.yaml 'tools:' must contain "
            "'tool-context-intelligence-upload'"
        )

    def test_upload_tool_has_correct_source(self, behavior_parsed):
        """tool-context-intelligence-upload must have the correct git source URL."""
        tools = behavior_parsed["tools"]
        for tool in tools:
            if (
                isinstance(tool, dict)
                and tool.get("module") == "tool-context-intelligence-upload"
            ):
                assert tool.get("source") == TOOL_UPLOAD_SOURCE, (
                    f"tool-context-intelligence-upload source must be '{TOOL_UPLOAD_SOURCE}', "
                    f"got '{tool.get('source')}'"
                )
                return
        pytest.fail(
            "tool-context-intelligence-upload entry not found in tools: section"
        )

    def test_upload_tool_after_blob_read(self, behavior_parsed):
        """tool-context-intelligence-upload must appear after tool-blob-read in the tools: list."""
        tools = behavior_parsed["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        assert "tool-blob-read" in modules, "tool-blob-read must be in tools list"
        assert "tool-context-intelligence-upload" in modules, (
            "tool-context-intelligence-upload must be in tools list"
        )
        blob_read_idx = modules.index("tool-blob-read")
        upload_idx = modules.index("tool-context-intelligence-upload")
        assert blob_read_idx < upload_idx, (
            "tool-context-intelligence-upload must appear after tool-blob-read in tools: list"
        )

    def test_tools_section_has_exactly_three_entries(self, behavior_parsed):
        """tools: section must contain exactly three entries."""
        tools = behavior_parsed["tools"]
        assert len(tools) == 3, (
            f"tools: section must have exactly 3 entries, found {len(tools)}: "
            f"{[t.get('module') for t in tools if isinstance(t, dict)]}"
        )

    def test_tools_section_order(self, behavior_parsed):
        """tools: section must list tools in exact order: tool-graph-query, tool-blob-read, tool-context-intelligence-upload."""
        tools = behavior_parsed["tools"]
        modules = [t.get("module") for t in tools if isinstance(t, dict)]
        expected = [
            "tool-graph-query",
            "tool-blob-read",
            "tool-context-intelligence-upload",
        ]
        assert modules == expected, (
            f"tools: section must list tools in order {expected}, got {modules}"
        )

    def test_yaml_still_parses_correctly(self, behavior_parsed):
        """YAML file must parse without any errors after adding the upload tool."""
        assert behavior_parsed is not None
        assert isinstance(behavior_parsed, dict)
        assert "tools" in behavior_parsed
