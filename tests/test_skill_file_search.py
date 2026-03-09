"""Tests for the file search skill (context-intelligence-file-search).

Validates that SKILL.md exists with correct structure, frontmatter,
schema documentation (directory layout, node/edge JSON structures),
6 query patterns, and notes section.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "context-intelligence-file-search"
)
SKILL_FILE = SKILL_DIR / "SKILL.md"

# Read and parse once at module level — avoids redundant disk reads and re-parsing.
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


def _extract_subsection(content: str, heading: str) -> str:
    """Extract content under a markdown ### heading, up to the next ### or ## or end."""
    pattern = rf"^### {re.escape(heading)}\s*\n(.*?)(?=^### |^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1)


# Pre-extract sections referenced by multiple tests.
_SCHEMA_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Schema") if _SKILL_CONTENT else ""
)
_QUERY_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Query Patterns") if _SKILL_CONTENT else ""
)
_NOTES_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Notes") if _SKILL_CONTENT else ""
)
# Pre-extract the edge-specific subsection for scoped field assertions.
_EDGE_JSON_SUBSECTION: str = (
    _extract_subsection(_SCHEMA_SECTION, "Edge JSON Structure")
    if _SCHEMA_SECTION
    else ""
)


# —— AC-1: File exists ——————————————————————————————————————


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# —— AC-2: YAML frontmatter ———————————————————————————————


def test_frontmatter_name() -> None:
    assert _FRONTMATTER.get("name") == "context-intelligence-file-search"


def test_frontmatter_description() -> None:
    desc = _FRONTMATTER.get("description", "")
    assert len(desc) > 0, "description must be non-empty"
    assert "FileGraphStore" in desc, "description should mention FileGraphStore"
    assert "json" in desc.lower(), "description should mention JSON backend"


def test_frontmatter_version() -> None:
    assert _FRONTMATTER.get("version") == "0.1.0"


def test_frontmatter_license() -> None:
    assert _FRONTMATTER.get("license") == "MIT"


# —— AC-3: Schema section — directory layout ———————————————


def test_schema_section_exists() -> None:
    assert "## Schema" in _SKILL_CONTENT, "SKILL.md should have a ## Schema section"


def test_schema_directory_layout() -> None:
    """Documents the directory layout for nodes and edges."""
    assert "graph_store_root" in _SCHEMA_SECTION, (
        "Schema should reference graph_store_root"
    )
    assert "graph_forest_name" in _SCHEMA_SECTION, (
        "Schema should reference graph_forest_name"
    )
    assert "nodes" in _SCHEMA_SECTION, "Schema should mention nodes directory"
    assert "edges" in _SCHEMA_SECTION, "Schema should mention edges directory"


def test_schema_node_file_pattern() -> None:
    """Documents node file naming: {node_id}.json."""
    assert "{node_id}.json" in _SCHEMA_SECTION or "node_id" in _SCHEMA_SECTION, (
        "Schema should document node file naming pattern"
    )


def test_schema_edge_file_pattern() -> None:
    """Documents edge file naming using ==[edge_type]== pattern."""
    assert "==[" in _SCHEMA_SECTION and "]==" in _SCHEMA_SECTION, (
        "Schema should document edge file naming with ==[type]== pattern"
    )


# —— AC-4: Node ID Format ————————————————————————————————


def test_node_id_format_pattern() -> None:
    """Documents the {session_id}__{event_name}__{timestamp_ms} pattern."""
    assert "{session_id}__{event_name}__{timestamp_ms}" in _SCHEMA_SECTION, (
        "Node ID Format should document the pattern"
    )


# —— AC-5: Edge ID Format ————————————————————————————————


def test_edge_id_format_pattern() -> None:
    """Documents the {source_id}==[{edge_type}]=={target_id} pattern."""
    assert "{source_id}==[{edge_type}]=={target_id}" in _SCHEMA_SECTION, (
        "Edge ID Format should document the pattern"
    )


# —— AC-6: Node JSON structure ———————————————————————————


def test_node_json_structure() -> None:
    """Documents the JSON structure of a node file."""
    for field in ["id", "labels", "properties"]:
        assert field in _SCHEMA_SECTION, (
            f"Node JSON structure should document '{field}' field"
        )


# —— AC-7: Edge JSON structure ———————————————————————————


def test_edge_json_structure() -> None:
    """Documents the JSON structure of an edge file."""
    for field in ["source", "target", "type", "properties"]:
        assert field in _EDGE_JSON_SUBSECTION, (
            f"Edge JSON structure should document '{field}' field"
        )


# —— AC-8: 6 Query Patterns ——————————————————————————————


def test_query_patterns_section_exists() -> None:
    assert "## Query Patterns" in _SKILL_CONTENT, (
        "SKILL.md should have a ## Query Patterns section"
    )


def test_six_query_patterns_exist() -> None:
    """Verify at least 6 distinct query pattern sections."""
    pattern_matches = re.findall(r"### Pattern \d", _SKILL_CONTENT)
    assert len(pattern_matches) >= 6, (
        f"Expected 6 pattern sections, found {len(pattern_matches)}: {pattern_matches}"
    )


def test_pattern_1_find_nodes_by_label() -> None:
    """Pattern 1: Find Nodes by Label with grep/jq."""
    assert re.search(
        r"Pattern 1.*Find Nodes by Label", _QUERY_SECTION, re.IGNORECASE
    ), "Pattern 1 should be 'Find Nodes by Label'"
    # Should use grep or jq within pattern 1's own subsection
    p1 = _extract_subsection(_QUERY_SECTION, "Pattern 1: Find Nodes by Label")
    assert "grep" in p1 or "jq" in p1, "Pattern 1 should use grep or jq"


def test_pattern_2_find_edges_by_type() -> None:
    """Pattern 2: Find Edges by Type with glob on ==[TYPE]== pattern."""
    assert re.search(r"Pattern 2.*Find Edges by Type", _QUERY_SECTION, re.IGNORECASE), (
        "Pattern 2 should be 'Find Edges by Type'"
    )
    # Scope to Pattern 2's own subsection for consistency with Patterns 1 and 6
    p2 = _extract_subsection(_QUERY_SECTION, "Pattern 2: Find Edges by Type")
    assert "==[" in p2, "Pattern 2 should reference ==[TYPE]== glob pattern"


def test_pattern_3_find_nodes_for_session() -> None:
    """Pattern 3: Find Nodes for Specific Session using session prefix."""
    assert re.search(r"Pattern 3.*Session", _QUERY_SECTION, re.IGNORECASE), (
        "Pattern 3 should find nodes for a specific session"
    )
    # Should mention session prefix matching — scoped to Pattern 3's subsection
    p3 = _extract_subsection(
        _QUERY_SECTION, "Pattern 3: Find Nodes for Specific Session"
    )
    assert re.search(r"prefix|session.id", p3, re.IGNORECASE), (
        "Pattern 3 should use session prefix"
    )


def test_pattern_4_traverse_a_path() -> None:
    """Pattern 4: Traverse a Path with shell pipeline."""
    assert re.search(r"Pattern 4.*Traverse", _QUERY_SECTION, re.IGNORECASE), (
        "Pattern 4 should be 'Traverse a Path'"
    )
    # Should show session→run→step traversal — scoped to Pattern 4's subsection
    p4 = _extract_subsection(_QUERY_SECTION, "Pattern 4: Traverse a Path")
    assert re.search(r"session|run|step", p4, re.IGNORECASE), (
        "Pattern 4 should show session→run→step traversal"
    )


def test_pattern_5_cross_forest_queries() -> None:
    """Pattern 5: Cross-Forest Queries."""
    assert re.search(r"Pattern 5.*Cross.Forest", _QUERY_SECTION, re.IGNORECASE), (
        "Pattern 5 should be 'Cross-Forest Queries'"
    )
    # Should mention navigating to graph_store_root — scoped to Pattern 5's subsection
    p5 = _extract_subsection(_QUERY_SECTION, "Pattern 5: Cross-Forest Queries")
    assert "graph_store_root" in p5, "Pattern 5 should reference graph_store_root"


def test_pattern_6_full_text_search() -> None:
    """Pattern 6: Full-Text Search across properties."""
    assert re.search(r"Pattern 6.*Full.Text", _QUERY_SECTION, re.IGNORECASE), (
        "Pattern 6 should be 'Full-Text Search'"
    )
    # Should use grep or jq within pattern 6's own subsection
    p6 = _extract_subsection(
        _QUERY_SECTION, "Pattern 6: Full-Text Search Across Properties"
    )
    assert "grep" in p6 or "jq" in p6, "Pattern 6 should use grep or jq"


# —— AC-9: Notes section —————————————————————————————————


def test_notes_section_exists() -> None:
    assert "## Notes" in _SKILL_CONTENT, "SKILL.md should have a ## Notes section"


def test_notes_path_resolution() -> None:
    """Notes should explain path resolution from config."""
    assert re.search(
        r"config|graph_store_root|~/.amplifier/graphs",
        _NOTES_SECTION,
        re.IGNORECASE,
    ), "Notes should explain path resolution from config"


def test_notes_not_queryablestore() -> None:
    """Notes should state FileGraphStore does NOT implement QueryableStore."""
    assert re.search(
        r"FileGraphStore.*NOT.*QueryableStore|does not.*implement.*QueryableStore|not.*implement.*QueryableStore",
        _NOTES_SECTION,
        re.IGNORECASE,
    ), "Notes should state FileGraphStore does NOT implement QueryableStore"


# —— AC-10: Code examples contain shell commands ——————————


def test_query_patterns_have_code_blocks() -> None:
    """Query patterns section should have code blocks."""
    code_blocks = re.findall(r"```", _QUERY_SECTION)
    # Each code block has opening and closing ```, so pairs = len / 2
    assert len(code_blocks) >= 12, (
        f"Expected at least 6 code blocks (12 markers), found {len(code_blocks)} markers"
    )
