"""Tests for the context-intelligence-graph-query skill (SKILL.md).

Validates that SKILL.md exists with correct structure, YAML frontmatter,
and no Neo4j API leakage. The SKILL.md is now the cold-start fallback
for when the server is unreachable — it contains only the fallback message,
not rich Cypher patterns (those are now served from the server).

For acceptance criteria on the fallback body content, see
test_skill_graph_query_fallback.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BUNDLE_DIR = Path(__file__).resolve().parent.parent
NEW_SKILL_DIR = _BUNDLE_DIR / "skills" / "context-intelligence-graph-query"
NEW_SKILL_FILE = NEW_SKILL_DIR / "SKILL.md"
OLD_SKILL_DIR = _BUNDLE_DIR / "skills" / "context-intelligence-neo4j-search"

# ---------------------------------------------------------------------------
# Content loading
# ---------------------------------------------------------------------------

try:
    _SKILL_CONTENT: str = NEW_SKILL_FILE.read_text() if NEW_SKILL_FILE.exists() else ""
except OSError:
    _SKILL_CONTENT = ""


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1))


_FRONTMATTER: dict = _parse_frontmatter(_SKILL_CONTENT) if _SKILL_CONTENT else {}


# ===========================================================================
# AC-1: File/directory existence and old directory deletion
# ===========================================================================


def test_ac1_new_skill_file_exists() -> None:
    """New SKILL.md must exist at skills/context-intelligence-graph-query/SKILL.md."""
    assert NEW_SKILL_FILE.exists(), f"SKILL.md not found at {NEW_SKILL_FILE}"


def test_ac1_new_skill_dir_exists() -> None:
    """New skill directory must exist."""
    assert NEW_SKILL_DIR.is_dir(), f"Skill directory not found at {NEW_SKILL_DIR}"


def test_ac1_old_skill_dir_deleted() -> None:
    """Old skill directory skills/context-intelligence-neo4j-search/ must be deleted."""
    assert not OLD_SKILL_DIR.exists(), (
        f"Old skill directory still exists: {OLD_SKILL_DIR}. It must be deleted."
    )


def test_ac1_old_neo4j_test_file_deleted() -> None:
    """Old test file tests/test_skill_neo4j_search.py must be deleted."""
    old_test = _BUNDLE_DIR / "tests" / "test_skill_neo4j_search.py"
    assert not old_test.exists(), (
        f"Old test file still exists: {old_test}. It must be deleted."
    )


def test_ac1_old_md_changes_test_file_deleted() -> None:
    """Old test file tests/test_skill_md_changes.py must be deleted."""
    old_test = _BUNDLE_DIR / "tests" / "test_skill_md_changes.py"
    assert not old_test.exists(), (
        f"Old test file still exists: {old_test}. It must be deleted."
    )


# ===========================================================================
# AC-2: YAML frontmatter
# ===========================================================================


def test_ac2_frontmatter_name() -> None:
    """Frontmatter name must be context-intelligence-graph-query."""
    assert _FRONTMATTER.get("name") == "context-intelligence-graph-query", (
        f"Expected name='context-intelligence-graph-query', got: {_FRONTMATTER.get('name')!r}"
    )


def test_ac2_frontmatter_version() -> None:
    """Frontmatter version must be 1.0.0."""
    assert _FRONTMATTER.get("version") == "1.0.0", (
        f"Expected version='1.0.0', got: {_FRONTMATTER.get('version')!r}"
    )


def test_ac2_frontmatter_license() -> None:
    """Frontmatter license must be MIT."""
    assert _FRONTMATTER.get("license") == "MIT"


def test_ac2_frontmatter_description_present() -> None:
    """Frontmatter must have a non-empty description."""
    desc = _FRONTMATTER.get("description", "")
    assert desc, "Frontmatter must include a non-empty description"


def test_ac2_frontmatter_description_mentions_cypher() -> None:
    """Frontmatter description must mention Cypher (the skill name references Cypher)."""
    desc = _FRONTMATTER.get("description", "")
    assert "cypher" in desc.lower() or "Cypher" in desc, (
        "Description must mention Cypher"
    )


def test_ac2_frontmatter_description_mentions_graph_query() -> None:
    """Frontmatter description must mention graph_query."""
    desc = _FRONTMATTER.get("description", "")
    assert "graph_query" in desc, "Description must mention graph_query"


# ===========================================================================
# AC-3: Zero Neo4j leakage — forbidden terms must be absent
# ===========================================================================

_FORBIDDEN_TERMS = [
    "neo4j",
    "Neo4jGraphStore",
    "execute_query",
    "$graph_forest_name",
    "graph_forest_name",
    "QueryableStore",
    "supported_dialects",
]
# Note: _FORBIDDEN_TERMS is the authoritative list. test_ac3_forbidden_terms_list_all_absent
# loops over it so adding a new term here automatically adds coverage. The individual
# test_ac3_* functions below remain for CI traceability — each maps to a named AC item.


def test_ac3_forbidden_terms_list_all_absent() -> None:
    """All items in _FORBIDDEN_TERMS must be absent from SKILL.md (drives the list).

    This is the authoritative gate: adding a term to _FORBIDDEN_TERMS is sufficient
    to add coverage — no companion test function required.
    """
    content_lower = _SKILL_CONTENT.lower()
    found = []
    for term in _FORBIDDEN_TERMS:
        # 'neo4j' check is case-insensitive; all other terms are checked as-is
        if term == "neo4j":
            if term in content_lower:
                found.append(term)
        elif term in _SKILL_CONTENT:
            found.append(term)
    assert not found, (
        f"SKILL.md contains forbidden terms (from _FORBIDDEN_TERMS): {found}"
    )


def test_ac3_no_neo4j_lowercase() -> None:
    """SKILL.md must not contain 'neo4j' (case-insensitive)."""
    assert "neo4j" not in _SKILL_CONTENT.lower(), (
        "SKILL.md contains forbidden term 'neo4j'"
    )


def test_ac3_no_neo4j_graph_store() -> None:
    """SKILL.md must not contain 'Neo4jGraphStore'."""
    assert "Neo4jGraphStore" not in _SKILL_CONTENT, (
        "SKILL.md contains forbidden term 'Neo4jGraphStore'"
    )


def test_ac3_no_execute_query() -> None:
    """SKILL.md must not contain 'execute_query'."""
    assert "execute_query" not in _SKILL_CONTENT, (
        "SKILL.md contains forbidden term 'execute_query'"
    )


def test_ac3_no_graph_forest_name_param() -> None:
    """SKILL.md must not contain '$graph_forest_name'."""
    assert "$graph_forest_name" not in _SKILL_CONTENT, (
        "SKILL.md contains forbidden term '$graph_forest_name'"
    )


def test_ac3_no_graph_forest_name_property() -> None:
    """SKILL.md must not contain 'graph_forest_name'."""
    assert "graph_forest_name" not in _SKILL_CONTENT, (
        "SKILL.md contains forbidden property 'graph_forest_name'"
    )


def test_ac3_no_queryable_store() -> None:
    """SKILL.md must not contain 'QueryableStore'."""
    assert "QueryableStore" not in _SKILL_CONTENT, (
        "SKILL.md contains forbidden term 'QueryableStore'"
    )


def test_ac3_no_supported_dialects() -> None:
    """SKILL.md must not contain 'supported_dialects'."""
    assert "supported_dialects" not in _SKILL_CONTENT, (
        "SKILL.md contains forbidden term 'supported_dialects'"
    )
