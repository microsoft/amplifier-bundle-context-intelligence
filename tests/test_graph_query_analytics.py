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
        """RecipeRun section must document key properties."""
        # Scan the 2000 chars following the first RecipeRun occurrence to
        # cover its full properties table without crossing into the next section.
        _RECIPE_RUN_SCAN_WINDOW = 2000
        text = _read(GRAPH_MODEL_FILE)
        pos = text.find("RecipeRun")
        assert pos != -1
        section = text[pos : pos + _RECIPE_RUN_SCAN_WINDOW]
        assert "recipe_name" in section
        assert "started_at" in section
        assert "ended_at" in section

    def test_recipe_step_in_step_section(self):
        """Step section must mention RecipeStep as a sub-label variant."""
        text = _read(GRAPH_MODEL_FILE)
        assert "RecipeStep" in text

    def test_recipe_loop_iteration_present(self):
        """RecipeLoopIteration node type must be documented for foreach tracking."""
        text = _read(GRAPH_MODEL_FILE)
        assert "RecipeLoopIteration" in text

    def test_recipe_approval_present(self):
        """RecipeApproval node type must be documented for approval-gate tracking."""
        text = _read(GRAPH_MODEL_FILE)
        assert "RecipeApproval" in text

    def test_has_recipe_run_edge_present(self):
        """HAS_RECIPE_RUN edge must be in the Edge Types table."""
        text = _read(GRAPH_MODEL_FILE)
        assert "HAS_RECIPE_RUN" in text

    def test_spans_run_edge_present(self):
        """SPANS_RUN edge must be in the Edge Types table."""
        text = _read(GRAPH_MODEL_FILE)
        assert "SPANS_RUN" in text

    def test_has_step_mentions_recipe_step(self):
        """HAS_STEP description must include RecipeStep alongside PromptStep/AssistantStep."""
        text = _read(GRAPH_MODEL_FILE)
        # Find the HAS_STEP row in the edge table; scan next ~300 chars to
        # cover the rest of that table row without spilling into the next entry.
        _HAS_STEP_ROW_WINDOW = 300
        has_step_pos = text.find("HAS_STEP")
        assert has_step_pos != -1
        has_step_line = text[has_step_pos : has_step_pos + _HAS_STEP_ROW_WINDOW]
        assert "RecipeStep" in has_step_line

    def test_recipe_run_stub_gotcha_present(self):
        """Gotcha about RecipeRun stub behaviour must be documented."""
        text = _read(GRAPH_MODEL_FILE)
        # Should warn about ended_at being null when recipe:complete hasn't fired
        gotchas_pos = text.find("Critical Gotchas")
        assert gotchas_pos != -1
        gotchas_section = text[gotchas_pos:]
        assert (
            "recipe_name" in gotchas_section.lower() or "RecipeRun" in gotchas_section
        )


# ---------------------------------------------------------------------------
# SKILL.md — schema tables and bug fixes
# ---------------------------------------------------------------------------


class TestSkillSchemaAndBugs:
    """Verify SKILL.md schema tables are complete and Pattern 2 + 11 bugs are fixed."""

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
        labels_section = text[labels_pos : labels_pos + 2000]
        assert "RecipeRun" in labels_section

    def test_recipe_loop_iteration_label_present(self):
        """RecipeLoopIteration must appear in the Node Labels table."""
        text = _read(SKILL_FILE)
        labels_pos = text.find("## Node Labels")
        labels_section = text[labels_pos : labels_pos + 2000]
        assert "RecipeLoopIteration" in labels_section

    def test_recipe_approval_label_present(self):
        """RecipeApproval must appear in the Node Labels table."""
        text = _read(SKILL_FILE)
        labels_pos = text.find("## Node Labels")
        labels_section = text[labels_pos : labels_pos + 2000]
        assert "RecipeApproval" in labels_section

    def test_has_recipe_run_in_relationships(self):
        """HAS_RECIPE_RUN must appear in the Relationship Types table."""
        text = _read(SKILL_FILE)
        rels_pos = text.find("## Relationship Types")
        assert rels_pos != -1
        rels_section = text[rels_pos : rels_pos + 2000]
        assert "HAS_RECIPE_RUN" in rels_section

    def test_spans_run_in_relationships(self):
        """SPANS_RUN must appear in the Relationship Types table."""
        text = _read(SKILL_FILE)
        rels_pos = text.find("## Relationship Types")
        rels_section = text[rels_pos : rels_pos + 2000]
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
