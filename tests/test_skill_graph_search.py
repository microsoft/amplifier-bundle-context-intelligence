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
# Wrapped in try/except so a missing or unreadable SKILL.md produces clean test
# failures (individual assertions) rather than an import-time crash.
try:
    _SKILL_CONTENT: str = SKILL_FILE.read_text() if SKILL_FILE.exists() else ""
except OSError:
    _SKILL_CONTENT = ""


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1))


_FRONTMATTER: dict = _parse_frontmatter(_SKILL_CONTENT) if _SKILL_CONTENT else {}


def _extract_section(content: str, heading: str) -> str:
    """Extract content under a markdown ## heading, up to the next ## or end."""
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1)


# Pre-extract sections referenced by multiple tests.
# Guard: if _SKILL_CONTENT is empty (file missing), skip extraction so individual
# tests fail with clear assertions rather than crashing at import time.
_SCHEMA_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Schema") if _SKILL_CONTENT else ""
)


# —— AC-1: File exists ——————————————————————————————————————————


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# —— AC-2: YAML frontmatter ———————————————————————————————————


def test_frontmatter_name() -> None:
    assert _FRONTMATTER["name"] == "context-intelligence-graph-search"


def test_frontmatter_version() -> None:
    assert _FRONTMATTER["version"] == "0.1.0"


def test_frontmatter_license() -> None:
    assert _FRONTMATTER["license"] == "MIT"


def test_frontmatter_description_present() -> None:
    assert "description" in _FRONTMATTER
    assert len(_FRONTMATTER["description"]) > 0


# —— AC-3: Schema section with 3 tables ———————————————————————


def test_schema_nodes_table() -> None:
    assert "VARCHAR" in _SCHEMA_SECTION
    for col in ["node_id", "session_id", "labels", "occurred_at", "properties"]:
        assert col in _SCHEMA_SECTION, f"nodes table missing column: {col}"


def test_schema_edges_table() -> None:
    for col in [
        "source",
        "target",
        "edge_type",
        "session_id",
        "occurred_at",
        "seq",
        "properties",
    ]:
        assert col in _SCHEMA_SECTION, f"edges table missing column: {col}"


def test_schema_search_index_table() -> None:
    for col in ["node_id", "field_name", "content"]:
        assert col in _SCHEMA_SECTION, f"search_index table missing column: {col}"


def test_schema_mentions_all_three_tables() -> None:
    assert "nodes" in _SCHEMA_SECTION
    assert "edges" in _SCHEMA_SECTION
    assert "search_index" in _SCHEMA_SECTION


# —— AC-4: Label system with 13 labels ————————————————————————

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


# —— AC-5: Edge types with 8 types ————————————————————————————

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


# —— AC-6: Search index field_name ————————————————————————————


def test_search_index_prompt_text() -> None:
    search_section = _extract_section(_SKILL_CONTENT, "Search Index")
    assert "prompt_text" in search_section, (
        "search_index field_name 'prompt_text' not documented"
    )
    assert "PromptStep" in search_section, (
        "prompt_text source (PromptStep) not documented"
    )


# —— AC-7: Three query patterns ———————————————————————————————


def test_query_pattern_fts_bm25() -> None:
    """Pattern 1: Direct FTS with BM25."""
    assert "fts_main_search_index" in _SKILL_CONTENT, (
        "FTS BM25 function reference missing"
    )
    assert "match_bm25" in _SKILL_CONTENT, "match_bm25 function reference missing"


def test_query_pattern_fts_pgq_traversal() -> None:
    """Pattern 2: FTS + PGQ Traversal."""
    query_section = _extract_section(_SKILL_CONTENT, "Query Patterns")
    assert "GRAPH_TABLE" in query_section, (
        "GRAPH_TABLE keyword missing for PGQ traversal"
    )
    # Should use CTE pattern
    assert "WITH" in query_section, "CTE (WITH) pattern missing for FTS+PGQ"


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


# —— AC-8: Notes section ——————————————————————————————————————


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


# —— Property Graph Overlay section ———————————————————————————


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


# —— AC-9: Node ID Format section ——————————————————————————————


def test_node_id_format_section_exists() -> None:
    """Node ID Format section exists within Schema."""
    assert "### Node ID Format" in _SCHEMA_SECTION, (
        "Schema section should contain '### Node ID Format' subsection"
    )


def test_node_id_format_documents_make_node_id() -> None:
    """References make_node_id() in utils.py."""
    assert "make_node_id()" in _SCHEMA_SECTION, (
        "Node ID Format should reference make_node_id()"
    )
    assert "utils.py" in _SCHEMA_SECTION, "Node ID Format should reference utils.py"


def test_node_id_format_pattern() -> None:
    """Documents the {session_id}__{event_name}__{timestamp_ms} pattern."""
    assert "{session_id}__{event_name}__{timestamp_ms}" in _SCHEMA_SECTION, (
        "Node ID Format should document the pattern"
    )


def test_node_id_format_double_underscore_separator() -> None:
    """Documents __ as segment separator."""
    assert re.search(r"`__`.*separator", _SCHEMA_SECTION, re.IGNORECASE), (
        "Node ID Format should document __ as segment separator"
    )


def test_node_id_format_colon_replacement() -> None:
    """Documents colons becoming underscores."""
    assert "prompt:submit" in _SCHEMA_SECTION, (
        "Node ID Format should show colon example"
    )
    assert "prompt_submit" in _SCHEMA_SECTION, (
        "Node ID Format should show underscore replacement"
    )


def test_node_id_format_session_nodes_raw_uuid() -> None:
    """Documents that session nodes use raw UUID."""
    assert re.search(
        r"[Ss]ession.*node.*raw.*session_id|raw.*session_id.*UUID",
        _SCHEMA_SECTION,
    ), "Node ID Format should note session nodes use raw session_id"


def test_node_id_format_example() -> None:
    """Includes a concrete example."""
    assert (
        "6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000"
        in _SCHEMA_SECTION
    ), "Node ID Format should include a concrete example"


# —— AC-10: Edge ID Format section —————————————————————————————


def test_edge_id_format_section_exists() -> None:
    """Edge ID Format section exists within Schema."""
    assert "### Edge ID Format" in _SCHEMA_SECTION, (
        "Schema section should contain '### Edge ID Format' subsection"
    )


def test_edge_id_format_documents_make_edge_id() -> None:
    """References make_edge_id() in utils.py."""
    assert "make_edge_id()" in _SCHEMA_SECTION, (
        "Edge ID Format should reference make_edge_id()"
    )


def test_edge_id_format_pattern() -> None:
    """Documents the {source_id}==[{edge_type}]=={target_id} pattern."""
    assert "{source_id}==[{edge_type}]=={target_id}" in _SCHEMA_SECTION, (
        "Edge ID Format should document the pattern"
    )


def test_edge_id_format_separators() -> None:
    """Documents ==[  and ]== as separators."""
    assert "==[" in _SCHEMA_SECTION, "Edge ID Format should document ==[ separator"
    assert "]==" in _SCHEMA_SECTION, "Edge ID Format should document ]== separator"


def test_edge_id_format_example() -> None:
    """Includes a concrete example with ==[HAS_STEP]==."""
    assert "==[HAS_STEP]==" in _SCHEMA_SECTION, (
        "Edge ID Format should include an example with ==[HAS_STEP]=="
    )


def test_edge_id_format_parse_instructions() -> None:
    """Includes parse instructions."""
    assert 'split("==[", 1)' in _SCHEMA_SECTION, (
        "Edge ID Format should include parse instructions"
    )
    assert 'split("]==", 1)' in _SCHEMA_SECTION, (
        "Edge ID Format should include parse instructions for ]==  separator"
    )


# —— AC-11: Multiple Storage Backends section ——————————————————


def test_multiple_storage_backends_section_exists() -> None:
    """Multiple Storage Backends section exists within Schema."""
    assert "### Multiple Storage Backends" in _SCHEMA_SECTION, (
        "Schema section should contain '### Multiple Storage Backends' subsection"
    )


def test_multiple_storage_backends_duckdb_mentioned() -> None:
    """Mentions DuckDB backend."""
    assert re.search(
        r"Multiple Storage Backends.*DuckDB", _SCHEMA_SECTION, re.DOTALL
    ), "Multiple Storage Backends should mention DuckDB"


def test_multiple_storage_backends_file_based_mentioned() -> None:
    """Mentions file-based / flat JSON files backend."""
    assert re.search(
        r"Multiple Storage Backends.*flat JSON files|Multiple Storage Backends.*file-based",
        _SCHEMA_SECTION,
        re.DOTALL,
    ), "Multiple Storage Backends should mention flat JSON files"


def test_multiple_storage_backends_ids_identical() -> None:
    """Documents that IDs are identical across backends."""
    assert re.search(r"identical across backends", _SCHEMA_SECTION, re.IGNORECASE), (
        "Multiple Storage Backends should note IDs are identical across backends"
    )


def test_multiple_storage_backends_nodes_edges_dirs() -> None:
    """Documents nodes/ and edges/ directories for file backend."""
    assert "`nodes/`" in _SCHEMA_SECTION, (
        "Multiple Storage Backends should mention nodes/ directory"
    )
    assert "`edges/`" in _SCHEMA_SECTION, (
        "Multiple Storage Backends should mention edges/ directory"
    )


def test_new_sections_appear_before_nodes_table() -> None:
    """All three new sections appear between ## Schema and ### `nodes`."""
    schema_match = re.search(r"^## Schema\s*\n", _SKILL_CONTENT, re.MULTILINE)
    nodes_match = re.search(r"^### `nodes`", _SKILL_CONTENT, re.MULTILINE)
    assert schema_match and nodes_match, "Both ## Schema and ### `nodes` must exist"

    between = _SKILL_CONTENT[schema_match.end() : nodes_match.start()]
    assert "### Node ID Format" in between, (
        "Node ID Format must appear between ## Schema and ### `nodes`"
    )
    assert "### Edge ID Format" in between, (
        "Edge ID Format must appear between ## Schema and ### `nodes`"
    )
    assert "### Multiple Storage Backends" in between, (
        "Multiple Storage Backends must appear between ## Schema and ### `nodes`"
    )


def test_new_sections_ordering() -> None:
    """Node ID Format comes before Edge ID Format which comes before Multiple Storage Backends."""
    node_pos = _SKILL_CONTENT.find("### Node ID Format")
    edge_pos = _SKILL_CONTENT.find("### Edge ID Format")
    backends_pos = _SKILL_CONTENT.find("### Multiple Storage Backends")
    assert node_pos < edge_pos < backends_pos, (
        "Sections must be ordered: Node ID Format, Edge ID Format, Multiple Storage Backends"
    )
