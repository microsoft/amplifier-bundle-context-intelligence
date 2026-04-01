"""Tests verifying graph query analytics improvements.

Post-Phase-1 acceptance criteria:
- Agent inline examples must NOT contain deprecated/broken property names
- graph-model-reference.md reflects DL1-only schema (3 nodes, 3 edges; DL2 concepts in warning section)
- SKILL.md must not contain the two known pattern bugs (r.seq, HAS_EVENT from Step)
- SKILL.md schema tables, analytics sections A–E, and inline bootstrap queries are now served
  dynamically from the server; the bundled SKILL.md is a cold-start fallback only.
"""

import pathlib

_BUNDLE_DIR = pathlib.Path(__file__).resolve().parent.parent
AGENT_FILE = _BUNDLE_DIR / "agents" / "graph-analyst.md"
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

    # Post-Phase-1: inline bootstrap queries were removed from the agent.
    # Schema content (node_id filter, HAS_RUN→HAS_STEP→TRIGGERED traversal, etc.)
    # is now served dynamically via the context-intelligence-graph-query skill.
    # The tests below verify only that the deprecated/broken content was removed.


# ---------------------------------------------------------------------------
# graph-model-reference.md — schema completeness
# ---------------------------------------------------------------------------


class TestGraphModelReference:
    """Verify graph-model-reference.md reflects Phase 1 additions."""

    def test_five_node_types_claim_removed(self):
        """The stale 'Five node types' line must be updated."""
        text = _read(GRAPH_MODEL_FILE)
        assert "Five node types" not in text

    def test_recipe_run_node_type_present(self):
        """RecipeRun must appear as a documented node type."""
        text = _read(GRAPH_MODEL_FILE)
        assert "RecipeRun" in text

    def test_recipe_run_properties_documented(self):
        """RecipeRun must be in the DL2 dead-label list — not a properties table."""
        text = _read(GRAPH_MODEL_FILE)
        # RecipeRun is a DL2 stub with no connected edges — documented in the
        # Data Layer 2 Warning section, not as a node type with properties.
        dl2_pos = text.find("Data Layer 2 Warning")
        assert dl2_pos != -1, "Data Layer 2 Warning section must exist"
        dl2_section = text[dl2_pos:]
        assert "RecipeRun" in dl2_section, (
            "RecipeRun must appear in the DL2 warning section as a dead-label"
        )

    def test_recipe_step_in_step_section(self):
        """Step section must mention RecipeStep as a sub-label variant."""
        text = _read(GRAPH_MODEL_FILE)
        assert "RecipeStep" in text

    def test_recipe_loop_iteration_present(self):
        """RecipeLoopIteration node type must be documented for foreach tracking."""
        text = _read(GRAPH_MODEL_FILE)
        assert "RecipeLoopIteration" in text

    def test_recipe_approval_absent_from_dl1_node_types(self):
        """RecipeApproval is a DL2 concept — must NOT appear in the DL1 Node Types table."""
        text = _read(GRAPH_MODEL_FILE)
        # The DL1 Node Types section contains exactly 3 nodes; RecipeApproval is
        # a DL2 concept that does not belong in the DL1 schema table.
        node_types_pos = text.find("## Node Types")
        assert node_types_pos != -1, "## Node Types section must exist"
        edge_types_pos = text.find("## Edge Types")
        assert edge_types_pos != -1, "## Edge Types section must exist"
        node_types_section = text[node_types_pos:edge_types_pos]
        assert "RecipeApproval" not in node_types_section, (
            "RecipeApproval is a DL2 concept and must not appear in the DL1 Node Types table"
        )

    def test_has_recipe_run_absent_from_dl1_edge_types(self):
        """HAS_RECIPE_RUN must NOT appear in the DL1 Edge Types table."""
        text = _read(GRAPH_MODEL_FILE)
        # The DL1 Edge Types section contains exactly 3 edge types; HAS_RECIPE_RUN
        # is a DL2 concept that was never implemented and must not appear there.
        edge_types_pos = text.find("## Edge Types")
        assert edge_types_pos != -1, "## Edge Types section must exist"
        dl2_pos = text.find("## Data Layer 2")
        assert dl2_pos != -1, "## Data Layer 2 section must exist"
        edge_types_section = text[edge_types_pos:dl2_pos]
        assert "HAS_RECIPE_RUN" not in edge_types_section, (
            "HAS_RECIPE_RUN is a DL2 concept and must not appear in the DL1 Edge Types table"
        )

    def test_spans_run_absent_from_dl1_edge_types(self):
        """SPANS_RUN must NOT appear in the DL1 Edge Types table."""
        text = _read(GRAPH_MODEL_FILE)
        # The DL1 Edge Types section contains exactly 3 edge types; SPANS_RUN
        # is a DL2 concept that was never implemented and must not appear there.
        edge_types_pos = text.find("## Edge Types")
        assert edge_types_pos != -1, "## Edge Types section must exist"
        dl2_pos = text.find("## Data Layer 2")
        assert dl2_pos != -1, "## Data Layer 2 section must exist"
        edge_types_section = text[edge_types_pos:dl2_pos]
        assert "SPANS_RUN" not in edge_types_section, (
            "SPANS_RUN is a DL2 concept and must not appear in the DL1 Edge Types table"
        )

    def test_has_step_mentions_recipe_step(self):
        """HAS_STEP must appear in the DL2 non-existent relationships list."""
        text = _read(GRAPH_MODEL_FILE)
        # HAS_STEP is a non-existent relationship — must be documented in the
        # DL2 warning section to prevent agents from querying it.
        dl2_pos = text.find("Data Layer 2 Warning")
        assert dl2_pos != -1, "Data Layer 2 Warning section must exist"
        dl2_section = text[dl2_pos:]
        assert "HAS_STEP" in dl2_section, (
            "HAS_STEP must appear in the DL2 non-existent relationships list"
        )

    def test_recipe_run_stub_gotcha_present(self):
        """DL2 warning section must contain explicit 'do not write queries' text."""
        text = _read(GRAPH_MODEL_FILE)
        # The DL2 warning replaces the old 'Critical Gotchas' section.
        # It must explicitly warn agents not to write queries against DL2 labels.
        dl2_pos = text.find("Data Layer 2 Warning")
        assert dl2_pos != -1, "Data Layer 2 Warning section must exist"
        dl2_section = text[dl2_pos:]
        assert "do not write queries" in dl2_section.lower(), (
            "DL2 warning section must contain explicit 'do not write queries' text"
        )


# ---------------------------------------------------------------------------
# SKILL.md — schema tables and bug fixes
# ---------------------------------------------------------------------------


class TestSkillSchemaAndBugs:
    """Verify SKILL.md does not contain known query bugs.

    Post-Phase-1: SKILL.md is a minimal cold-start fallback only.  Schema tables,
    node label listings, relationship tables, and analytics sections A–E are now
    served dynamically from the server.  Only the two pre-existing pattern bugs
    (r.seq and HAS_EVENT from Step) are checked here — they must not re-appear
    in the cold-start fallback either.
    """

    def test_r_seq_bug_fixed(self):
        """Pattern 2 must not reference r.seq — that property does not exist."""
        text = _read(SKILL_FILE)
        assert "r.seq" not in text

    def test_has_event_from_step_bug_fixed(self):
        """Pattern 11 must not traverse HAS_EVENT from Step — never happens."""
        text = _read(SKILL_FILE)
        assert "(step:Step)-[:HAS_EVENT]" not in text


# ---------------------------------------------------------------------------
# NOTE: TestSkillAnalyticsSections was removed in Phase 1.
# SKILL.md analytics sections A–E (wildcard traversal, time-activity, recipe,
# parallelism, token-efficiency) are now served dynamically from the server.
# The bundled SKILL.md contains only a cold-start fallback; its content is
# validated by tests/test_skill_graph_query_fallback.py instead.
# ---------------------------------------------------------------------------
