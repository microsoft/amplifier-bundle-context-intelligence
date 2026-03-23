"""Tests verifying graph query analytics improvements.

Acceptance criteria:
- Agent inline examples use correct property names and traversal paths
- graph-model-reference.md includes RecipeRun, RecipeStep, new edges
- SKILL.md schema tables include new labels/edges, Pattern 2 + 11 bugs fixed
- SKILL.md has analytics sections A–E (wildcard, time-activity, recipe, parallelism, token)
"""

import pathlib

_BUNDLE_DIR = pathlib.Path(__file__).resolve().parent.parent
AGENT_FILE = _BUNDLE_DIR / "agents" / "context-intelligence-graph-analyst.md"
GRAPH_MODEL_FILE = _BUNDLE_DIR / "context" / "graph-model-reference.md"
SKILL_FILE = _BUNDLE_DIR / "skills" / "context-intelligence-graph-query" / "SKILL.md"


def _read(path: pathlib.Path) -> str:
    return path.read_text()


# ---------------------------------------------------------------------------
# Agent inline examples
# ---------------------------------------------------------------------------


class TestAgentInlineExamples:
    """Verify the 4 inline query examples in the agent definition are correct."""

    def test_broken_property_e_type_absent(self):
        """e.type was the old (wrong) property name — must be removed."""
        text = _read(AGENT_FILE)
        # e.type appeared in the broken 'find errors' and 'tool usage' queries
        assert "e.type" not in text

    def test_broken_property_s_session_id_absent(self):
        """s.session_id was the old (wrong) filter — must use node_id."""
        text = _read(AGENT_FILE)
        assert "s.session_id" not in text

    def test_broken_property_agent_name_absent(self):
        """s.agent_name is not a Session property — must be removed."""
        text = _read(AGENT_FILE)
        assert "s.agent_name" not in text

    def test_broken_property_e_timestamp_absent(self):
        """e.timestamp was the old (wrong) property — must use occurred_at."""
        text = _read(AGENT_FILE)
        assert "e.timestamp" not in text

    def test_broken_spawned_from_session_absent(self):
        """SPAWNED goes Delegation→Session, never Session→Session."""
        text = _read(AGENT_FILE)
        assert "Session {session_id: $session_id})-[:SPAWNED*]" not in text

    def test_correct_node_id_filter_present(self):
        """Inline examples must filter sessions by node_id, not session_id."""
        text = _read(AGENT_FILE)
        assert "node_id: $session_id" in text

    def test_correct_tool_execution_traversal_present(self):
        """Inline examples must use the full HAS_RUN→HAS_STEP→TRIGGERED path."""
        text = _read(AGENT_FILE)
        assert "HAS_RUN" in text
        assert "HAS_STEP" in text
        assert "TRIGGERED" in text
        assert "ToolExecution" in text
