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

# Read and parse once at module level — avoids redundant disk reads and re-parsing.
_SKILL_CONTENT: str = SKILL_FILE.read_text() if SKILL_FILE.exists() else ""


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert match, "YAML frontmatter not found (must start with --- and end with ---)"
    return yaml.safe_load(match.group(1))


_FRONTMATTER: dict = _parse_frontmatter(_SKILL_CONTENT) if _SKILL_CONTENT else {}


def _extract_section(content: str, heading: str) -> str:
    """Extract content under a markdown ## heading, up to the next ## or end."""
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    assert match, f"Section '## {heading}' not found in SKILL.md"
    return match.group(1)


# —— AC-1: File exists ——————————————————————————————————————


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# —— AC-2: YAML frontmatter ———————————————————————————————


def test_frontmatter_name() -> None:
    assert _FRONTMATTER["name"] == "context-intelligence-graph-search"


def test_frontmatter_version() -> None:
    assert _FRONTMATTER["version"] == "0.1.0"


def test_frontmatter_license() -> None:
    assert _FRONTMATTER["license"] == "MIT"


def test_frontmatter_description_present() -> None:
    assert "description" in _FRONTMATTER
    assert len(_FRONTMATTER["description"]) > 0


# —— AC-3: Schema section with 3 tables ———————————————————


def test_schema_nodes_table() -> None:
    schema = _extract_section(_SKILL_CONTENT, "Schema")
    assert "VARCHAR" in schema
    for col in ["node_id", "session_id", "labels", "occurred_at", "properties"]:
        assert col in schema, f"nodes table missing column: {col}"


def test_schema_edges_table() -> None:
    schema = _extract_section(_SKILL_CONTENT, "Schema")
    for col in [
        "source",
        "target",
        "edge_type",
        "session_id",
        "occurred_at",
        "seq",
        "properties",
    ]:
        assert col in schema, f"edges table missing column: {col}"


def test_schema_search_index_table() -> None:
    schema = _extract_section(_SKILL_CONTENT, "Schema")
    for col in ["node_id", "field_name", "content"]:
        assert col in schema, f"search_index table missing column: {col}"


def test_schema_mentions_all_three_tables() -> None:
    schema = _extract_section(_SKILL_CONTENT, "Schema")
    assert "nodes" in schema
    assert "edges" in schema
    assert "search_index" in schema


# —— AC-4: Label system with 13 labels ————————————————————

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
    for label in EXPECTED_LABELS:
        assert label in _SKILL_CONTENT, f"Label missing: {label}"


def test_label_count() -> None:
    """Verify the Label System table in SKILL.md contains exactly 13 labels."""
    label_section = _extract_section(_SKILL_CONTENT, "Label System")
    # Each label row starts with "| `LabelName`" in the markdown table.
    label_rows = re.findall(r"^\| `(\w+)`", label_section, re.MULTILINE)
    assert len(label_rows) == 13, (
        f"Expected 13 labels in SKILL.md table, found {len(label_rows)}: {label_rows}"
    )


# —— AC-5: Edge types with 8 types ————————————————————————

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
    for edge_type in EXPECTED_EDGE_TYPES:
        assert edge_type in _SKILL_CONTENT, f"Edge type missing: {edge_type}"


def test_edge_types_have_from_to() -> None:
    """Edge Types table should have From and To columns."""
    edge_section = _extract_section(_SKILL_CONTENT, "Edge Types")
    # Verify the table header row contains "From" and "To" columns
    header_match = re.search(r"^\|.*From.*\|.*To.*\|", edge_section, re.MULTILINE)
    assert header_match, "Edge Types table should have 'From' and 'To' column headers"


# —— AC-6: Search index field_name ———————————————————————


def test_search_index_prompt_text() -> None:
    assert "prompt_text" in _SKILL_CONTENT, (
        "search_index field_name 'prompt_text' not documented"
    )
    assert "PromptStep" in _SKILL_CONTENT, (
        "prompt_text source (PromptStep) not documented"
    )


# —— AC-7: Three query patterns —————————————————————————


def test_query_pattern_fts_bm25() -> None:
    """Pattern 1: Direct FTS with BM25."""
    assert "fts_main_search_index" in _SKILL_CONTENT, (
        "FTS BM25 function reference missing"
    )
    assert "match_bm25" in _SKILL_CONTENT, "match_bm25 function reference missing"


def test_query_pattern_fts_pgq_traversal() -> None:
    """Pattern 2: FTS + PGQ Traversal."""
    assert "GRAPH_TABLE" in _SKILL_CONTENT, (
        "GRAPH_TABLE keyword missing for PGQ traversal"
    )
    # Should use CTE pattern
    assert "WITH" in _SKILL_CONTENT, "CTE (WITH) pattern missing for FTS+PGQ"


def test_query_pattern_pure_pgq() -> None:
    """Pattern 3: Pure PGQ without text search."""
    # Pure PGQ should have GRAPH_TABLE and MATCH pattern
    assert "GRAPH_TABLE" in _SKILL_CONTENT
    # Should have at least one query that doesn't use search_index/FTS
    assert "MATCH" in _SKILL_CONTENT, "PGQ MATCH keyword missing"


def test_three_query_patterns_exist() -> None:
    """Verify exactly 3 distinct query pattern sections."""
    pattern_matches = re.findall(r"Pattern\s+\d", _SKILL_CONTENT, re.IGNORECASE)
    assert len(pattern_matches) >= 3, (
        f"Expected 3+ pattern references, found {len(pattern_matches)}"
    )


# —— AC-8: Notes section ——————————————————————————————————


def test_notes_fts_rebuild_timing() -> None:
    # Should mention FTS index rebuild
    assert re.search(
        r"FTS.*rebuild|rebuild.*FTS|PRAGMA.*create_fts_index",
        _SKILL_CONTENT,
        re.IGNORECASE,
    ), "Notes should cover FTS index rebuild timing"


def test_notes_property_graph_creation() -> None:
    # Should mention property graph created on demand
    assert re.search(
        r"property.graph.*creat|CREATE\s+PROPERTY\s+GRAPH",
        _SKILL_CONTENT,
        re.IGNORECASE,
    ), "Notes should cover property graph creation timing"


def test_notes_json_access() -> None:
    # Should mention JSON property access syntax
    assert re.search(
        r"JSON|json.*access|properties\s*->>|json_extract",
        _SKILL_CONTENT,
        re.IGNORECASE,
    ), "Notes should cover JSON property access syntax"


# —— Property Graph Overlay section ——————————————————————


def test_duckpgq_install_load() -> None:
    assert "INSTALL" in _SKILL_CONTENT, "DuckPGQ INSTALL missing"
    assert "LOAD" in _SKILL_CONTENT, "DuckPGQ LOAD missing"


def test_create_property_graph_ddl() -> None:
    assert "CREATE PROPERTY GRAPH" in _SKILL_CONTENT, (
        "CREATE PROPERTY GRAPH DDL missing"
    )


def test_property_graph_on_demand() -> None:
    assert re.search(
        r"on.demand|not.*startup|when.*needed|lazy|created.*demand",
        _SKILL_CONTENT,
        re.IGNORECASE,
    ), "Should note property graph is created on demand, not at startup"
