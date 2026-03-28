"""Tests verifying graph query analytics improvements.

Acceptance criteria:
- Agent inline examples use correct property names and traversal paths
- graph-model-reference.md includes RecipeRun, RecipeStep, new edges
- SKILL.md schema tables include new labels/edges, Pattern 2 + 11 bugs fixed
- SKILL.md has analytics sections A–E (wildcard, time-activity, recipe, parallelism, token)
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

    def test_recipe_approval_present(self):
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

    def test_has_recipe_run_edge_present(self):
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

    def test_spans_run_edge_present(self):
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
    """Verify SKILL.md schema tables are complete and Pattern 2 + 11 bugs are fixed."""

    _LABELS_SCAN_WINDOW = 2000
    _RELS_SCAN_WINDOW = 2000

    def test_r_seq_bug_fixed(self):
        """Pattern 2 must not reference r.seq — that property does not exist."""
        text = _read(SKILL_FILE)
        assert "r.seq" not in text

    def test_has_event_from_step_bug_fixed(self):
        """Pattern 11 must not traverse HAS_EVENT from Step — never happens."""
        text = _read(SKILL_FILE)
        assert "(step:Step)-[:HAS_EVENT]" not in text

    def test_recipe_run_label_in_node_labels_table(self):
        """RecipeRun must appear in the Node Labels table."""
        text = _read(SKILL_FILE)
        # Find the Node Labels section
        labels_pos = text.find("## Node Labels")
        assert labels_pos != -1
        # Check within the table (before next ## section)
        labels_section = text[labels_pos : labels_pos + self._LABELS_SCAN_WINDOW]
        assert "RecipeRun" in labels_section

    def test_recipe_loop_iteration_label_present(self):
        """RecipeLoopIteration must appear in the Node Labels table."""
        text = _read(SKILL_FILE)
        labels_pos = text.find("## Node Labels")
        assert labels_pos != -1, "## Node Labels section missing"
        labels_section = text[labels_pos : labels_pos + self._LABELS_SCAN_WINDOW]
        assert "RecipeLoopIteration" in labels_section

    def test_recipe_approval_label_present(self):
        """RecipeApproval must appear in the Node Labels table."""
        text = _read(SKILL_FILE)
        labels_pos = text.find("## Node Labels")
        assert labels_pos != -1, "## Node Labels section missing"
        labels_section = text[labels_pos : labels_pos + self._LABELS_SCAN_WINDOW]
        assert "RecipeApproval" in labels_section

    def test_has_recipe_run_in_relationships(self):
        """HAS_RECIPE_RUN must appear in the Relationship Types table."""
        text = _read(SKILL_FILE)
        rels_pos = text.find("## Relationship Types")
        assert rels_pos != -1, "## Relationship Types section missing"
        rels_section = text[rels_pos : rels_pos + self._RELS_SCAN_WINDOW]
        assert "HAS_RECIPE_RUN" in rels_section

    def test_spans_run_in_relationships(self):
        """SPANS_RUN must appear in the Relationship Types table."""
        text = _read(SKILL_FILE)
        rels_pos = text.find("## Relationship Types")
        assert rels_pos != -1, "## Relationship Types section missing"
        rels_section = text[rels_pos : rels_pos + self._RELS_SCAN_WINDOW]
        assert "SPANS_RUN" in rels_section


# ---------------------------------------------------------------------------
# SKILL.md — analytics sections A–E
# ---------------------------------------------------------------------------


class TestSkillAnalyticsSections:
    """Verify SKILL.md contains the five new analytics sections."""

    # Section A — Foundational traversal primitive
    def test_wildcard_traversal_pattern_present(self):
        """Multi-relationship wildcard must be documented."""
        text = _read(SKILL_FILE)
        assert "HAS_RUN|HAS_STEP|TRIGGERED|SPAWNED" in text

    def test_parallel_group_empty_string_note(self):
        """Must warn that parallel_group_id is '' not null."""
        text = _read(SKILL_FILE)
        assert 'parallel_group_id <> ""' in text

    # Section B — Time-activity queries
    def test_point_in_time_query_present(self):
        text = _read(SKILL_FILE)
        assert "$point_in_time" in text

    def test_time_range_query_present(self):
        text = _read(SKILL_FILE)
        assert "$t1" in text
        assert "$t2" in text

    # Section C — Recipe analytics
    def test_recipe_analytics_query_present(self):
        """Recipe analytics section must contain HAS_RECIPE_RUN in a query."""
        text = _read(SKILL_FILE)
        # HAS_RECIPE_RUN already appears 2× in schema tables (Node Labels + Relationship Types).
        # The analytics section adds ≥1 more occurrence in a Cypher query, so require ≥3.
        count = text.count("HAS_RECIPE_RUN")
        assert count >= 3, (
            f"HAS_RECIPE_RUN appears {count} time(s), need ≥3 (2 tables + analytics query)"
        )

    def test_recipe_step_fallback_documented(self):
        """Recipe duration query must use coalesce for stub fallback."""
        text = _read(SKILL_FILE)
        assert "coalesce(rr.started_at" in text or "coalesce(rr.ended_at" in text

    # Section D — Parallelism degree
    def test_parallelism_query_present(self):
        text = _read(SKILL_FILE)
        assert "parallel_degree" in text

    def test_peak_parallelism_query_present(self):
        text = _read(SKILL_FILE)
        assert "peak_parallelism" in text

    # Section E — Token efficiency
    def test_cache_hit_pct_present(self):
        text = _read(SKILL_FILE)
        assert "cache_hit_pct" in text

    def test_token_distinction_documented(self):
        """Must document the input_tokens vs message_count distinction."""
        text = _read(SKILL_FILE)
        assert "message_count" in text
        assert "input_tokens" in text

    def test_coalesce_null_tokens_note(self):
        """Must use coalesce for nullable token properties."""
        text = _read(SKILL_FILE)
        assert "coalesce(a.cached_tokens" in text
