"""Tests verifying SKILL.md documentation changes (Task 5.2).

These tests ensure all 4 required documentation changes are present in
skills/context-intelligence-neo4j-search/SKILL.md:

1. ToolExecution node ID format (4-segment with tool_call_id disambiguator).
2. HAS_EVENT relationship reflects run-aware DefaultHandler behavior.
3. ID Format Reference section includes ToolExecution parsing example.
4. Pattern 11 includes a note about DefaultHandler run-awareness.
"""

from pathlib import Path

import pytest

SKILL_MD = (
    Path(__file__).parent.parent
    / "skills"
    / "context-intelligence-neo4j-search"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_content():
    """Load SKILL.md content once for all tests in this module."""
    assert SKILL_MD.exists(), f"SKILL.md not found: {SKILL_MD}"
    return SKILL_MD.read_text()


class TestToolExecutionNodeIdFormat:
    """Change 1: ToolExecution node ID format updated with tool_call_id disambiguator."""

    def test_toolexecution_pattern_present(self, skill_content):
        """ToolExecution pattern line added to Node ID Format section."""
        assert (
            "**ToolExecution pattern:** `{session_id}__{event_name}__{timestamp_ms}__{tool_call_id}`"
            in skill_content
        ), "ToolExecution pattern not found in Node ID Format section"

    def test_toolexecution_bullet_point(self, skill_content):
        """ToolExecution bullet about tool_call_id disambiguator present."""
        assert (
            "ToolExecution nodes include `tool_call_id` as a fourth segment to prevent"
            in skill_content
        ), "ToolExecution tool_call_id bullet point not found"

    def test_toolexecution_example(self, skill_content):
        """ToolExecution ID example line present."""
        assert (
            "ToolExecution example:" in skill_content
            and "toolu_01G9FD9g" in skill_content
        ), "ToolExecution example with toolu_01G9FD9g not found"


class TestHasEventRunAwareSemantics:
    """Change 2: HAS_EVENT relationship reflects run-aware DefaultHandler behavior."""

    def test_has_event_orchestrator_run_aware(self, skill_content):
        """HAS_EVENT relationship source reflects OrchestratorRun-or-Session routing."""
        assert (
            "OrchestratorRun` (when active) / `Session` (fallback)" in skill_content
        ), "HAS_EVENT run-aware source not found in relationship table"

    def test_has_event_default_handler_note(self, skill_content):
        """HAS_EVENT description includes DefaultHandler cursors check."""
        assert "DefaultHandler checks `cursors.current_run_id`" in skill_content, (
            "DefaultHandler cursors.current_run_id check not found in HAS_EVENT description"
        )


class TestIdFormatReferenceSection:
    """Change 3: ID Format Reference section includes ToolExecution parsing example."""

    def test_toolexecution_id_format_section(self, skill_content):
        """ToolExecution nodes section exists in ID Format Reference."""
        assert "### ToolExecution nodes" in skill_content, (
            "ToolExecution nodes section not found in ID Format Reference"
        )

    def test_toolexecution_parsing_parts3(self, skill_content):
        """Parsing example includes parts[3] = tool_call_id."""
        assert "parts[3] = tool_call_id" in skill_content, (
            "parts[3] = tool_call_id not found in parsing example"
        )


class TestPattern11RunAwarenessNote:
    """Change 4: Pattern 11 includes a note about DefaultHandler run-awareness."""

    def test_pattern11_run_awareness_note(self, skill_content):
        """Pattern 11 has a note about run-awareness."""
        assert "Since the DefaultHandler run-awareness fix" in skill_content, (
            "DefaultHandler run-awareness note not found near Pattern 11"
        )

    def test_pattern11_has_event_note_location(self, skill_content):
        """Note about HAS_EVENT run-awareness appears within Pattern 11 section."""
        p11_idx = skill_content.find("### Pattern 11:")
        assert p11_idx != -1, "Pattern 11 section not found"
        nearby_content = skill_content[p11_idx : p11_idx + 800]
        assert "DefaultHandler run-awareness fix" in nearby_content, (
            "Run-awareness note not found within Pattern 11 section"
        )
