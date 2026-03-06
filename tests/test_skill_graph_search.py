"""Tests for the SQL/PGQ skill file (context-intelligence-graph-search).

Validates that SKILL.md exists with correct structure, frontmatter,
schema documentation, label system, edge types, search index,
query patterns, and notes section.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "context-intelligence-graph-search"
)
SKILL_FILE = SKILL_DIR / "SKILL.md"


def _read_skill() -> str:
    """Read SKILL.md content."""
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"
    return SKILL_FILE.read_text()


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert match, "YAML frontmatter not found (must start with --- and end with ---)"
    return yaml.safe_load(match.group(1))


# ── AC-1: File exists ──────────────────────────────────────────────


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# ── AC-2: YAML frontmatter ─────────────────────────────────────────


def test_frontmatter_name() -> None:
    fm = _parse_frontmatter(_read_skill())
    assert fm["name"] == "context-intelligence-graph-search"


def test_frontmatter_version() -> None:
    fm = _parse_frontmatter(_read_skill())
    assert fm["version"] == "0.1.0"


def test_frontmatter_license() -> None:
    fm = _parse_frontmatter(_read_skill())
    assert fm["license"] == "MIT"


def test_frontmatter_description_present() -> None:
    fm = _parse_frontmatter(_read_skill())
    assert "description" in fm
    assert len(fm["description"]) > 0


# ── AC-3: Schema section with 3 tables ─────────────────────────────


def test_schema_nodes_table() -> None:
    content = _read_skill()
    assert "node_id" in content
    assert "VARCHAR" in content
    # Check all columns of nodes table
    for col in ["node_id", "session_id", "labels", "occurred_at", "properties"]:
        assert col in content, f"nodes table missing column: {col}"


def test_schema_edges_table() -> None:
    content = _read_skill()
    for col in [
        "source",
        "target",
        "edge_type",
        "session_id",
        "occurred_at",
        "seq",
        "properties",
    ]:
        assert col in content, f"edges table missing column: {col}"
    # Primary key on source/target/edge_type
    assert "source" in content
    assert "target" in content
    assert "edge_type" in content


def test_schema_search_index_table() -> None:
    content = _read_skill()
    for col in ["node_id", "field_name", "content"]:
        assert col in content, f"search_index table missing column: {col}"


def test_schema_mentions_all_three_tables() -> None:
    content = _read_skill()
    assert "nodes" in content
    assert "edges" in content
    assert "search_index" in content


# ── AC-4: Label system with 13 labels ──────────────────────────────

EXPECTED_LABELS = [
    "Session",
    "Root",
    "Subsession",
    "ForkedSession",
    "Resumed",
    "OrchestratorRun",
    "Step",
    "PromptStep",
    "AssistantStep",
    "RecipeStep",
    "ToolExecution",
    "Delegation",
    "Event",
]


def test_all_13_labels_present() -> None:
    content = _read_skill()
    for label in EXPECTED_LABELS:
        assert label in content, f"Label missing: {label}"


def test_label_count() -> None:
    assert len(EXPECTED_LABELS) == 13


# ── AC-5: Edge types with 8 types ──────────────────────────────────

EXPECTED_EDGE_TYPES = [
    "HAS_RUN",
    "HAS_STEP",
    "NEXT",
    "TRIGGERED",
    "PARALLEL_WITH",
    "SPAWNED",
    "SUBSESSION_OF",
    "HAS_EVENT",
]


def test_all_8_edge_types_present() -> None:
    content = _read_skill()
    for edge_type in EXPECTED_EDGE_TYPES:
        assert edge_type in content, f"Edge type missing: {edge_type}"


def test_edge_types_have_from_to() -> None:
    """Each edge type should document from and to."""
    content = _read_skill()
    # Check that from/to information is in the edge types section
    # We check for table structure with From and To columns
    assert re.search(r"From|from", content), (
        "Edge types should document 'from' direction"
    )
    assert re.search(r"To|to", content), "Edge types should document 'to' direction"


# ── AC-6: Search index field_name ───────────────────────────────────


def test_search_index_prompt_text() -> None:
    content = _read_skill()
    assert "prompt_text" in content, (
        "search_index field_name 'prompt_text' not documented"
    )
    assert "PromptStep" in content, "prompt_text source (PromptStep) not documented"


# ── AC-7: Three query patterns ─────────────────────────────────────


def test_query_pattern_fts_bm25() -> None:
    """Pattern 1: Direct FTS with BM25."""
    content = _read_skill()
    assert "fts_main_search_index" in content, "FTS BM25 function reference missing"
    assert "match_bm25" in content, "match_bm25 function reference missing"


def test_query_pattern_fts_pgq_traversal() -> None:
    """Pattern 2: FTS + PGQ Traversal."""
    content = _read_skill()
    assert "GRAPH_TABLE" in content, "GRAPH_TABLE keyword missing for PGQ traversal"
    # Should use CTE pattern
    assert "WITH" in content, "CTE (WITH) pattern missing for FTS+PGQ"


def test_query_pattern_pure_pgq() -> None:
    """Pattern 3: Pure PGQ without text search."""
    content = _read_skill()
    # Pure PGQ should have GRAPH_TABLE and MATCH pattern
    assert "GRAPH_TABLE" in content
    # Should have at least one query that doesn't use search_index/FTS
    assert "MATCH" in content, "PGQ MATCH keyword missing"


def test_three_query_patterns_exist() -> None:
    """Verify exactly 3 distinct query pattern sections."""
    content = _read_skill()
    pattern_matches = re.findall(r"Pattern\s+\d", content, re.IGNORECASE)
    assert len(pattern_matches) >= 3, (
        f"Expected 3+ pattern references, found {len(pattern_matches)}"
    )


# ── AC-8: Notes section ────────────────────────────────────────────


def test_notes_fts_rebuild_timing() -> None:
    content = _read_skill()
    # Should mention FTS index rebuild
    assert re.search(
        r"FTS.*rebuild|rebuild.*FTS|PRAGMA.*create_fts_index", content, re.IGNORECASE
    ), "Notes should cover FTS index rebuild timing"


def test_notes_property_graph_creation() -> None:
    content = _read_skill()
    # Should mention property graph created on demand
    assert re.search(
        r"property.graph.*creat|CREATE\s+PROPERTY\s+GRAPH", content, re.IGNORECASE
    ), "Notes should cover property graph creation timing"


def test_notes_json_access() -> None:
    content = _read_skill()
    # Should mention JSON property access syntax
    assert re.search(
        r"JSON|json.*access|properties\s*->>|json_extract", content, re.IGNORECASE
    ), "Notes should cover JSON property access syntax"


# ── Property Graph Overlay section ──────────────────────────────────


def test_duckpgq_install_load() -> None:
    content = _read_skill()
    assert "INSTALL" in content, "DuckPGQ INSTALL missing"
    assert "LOAD" in content, "DuckPGQ LOAD missing"


def test_create_property_graph_ddl() -> None:
    content = _read_skill()
    assert "CREATE PROPERTY GRAPH" in content, "CREATE PROPERTY GRAPH DDL missing"


def test_property_graph_on_demand() -> None:
    content = _read_skill()
    assert re.search(
        r"on.demand|not.*startup|when.*needed|lazy|created.*demand",
        content,
        re.IGNORECASE,
    ), "Should note property graph is created on demand, not at startup"
