"""Tests verifying the Forest Awareness section in graph-store-protocol.md.

These tests ensure the protocol documentation includes all required
forest awareness content per the specification.
"""

import re
from pathlib import Path

import pytest

PROTOCOL_DOC = Path(__file__).parent.parent / "context" / "graph-store-protocol.md"


@pytest.fixture
def doc_content():
    """Load the protocol doc content."""
    return PROTOCOL_DOC.read_text()


# --- Section placement ---


def test_forest_awareness_section_exists(doc_content):
    """Forest Awareness section must exist as a level-2 heading."""
    assert "## Forest Awareness" in doc_content


def test_forest_awareness_after_guarantees_before_write_path(doc_content):
    """Forest Awareness must appear after Non-Negotiable Guarantees and before Write Path."""
    guarantees_pos = doc_content.index("## Non-Negotiable Guarantees")
    forest_pos = doc_content.index("## Forest Awareness")
    write_path_pos = doc_content.index("## Write Path")
    assert guarantees_pos < forest_pos < write_path_pos


# --- Forest definition ---


def test_forest_definition(doc_content):
    """Section must define graph forest as a named collection of sessions."""
    # Extract the Forest Awareness section
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "named collection of sessions" in section.lower() or "named collection" in section.lower()


# --- Property Contract subsection ---


def test_property_contract_subsection(doc_content):
    """Must have a Property Contract subsection."""
    assert "Property Contract" in doc_content


def test_property_contract_signature(doc_content):
    """Property Contract must show the @property def graph_forest_name signature."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "@property" in section
    assert "def graph_forest_name(self) -> str" in section


# --- Write Scoping subsection ---


def test_write_scoping_subsection(doc_content):
    """Must have a Write Scoping subsection."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "Write Scoping" in section


def test_write_scoping_file_store_paths(doc_content):
    """Write Scoping must describe FileGraphStore directory layout."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "FileGraphStore" in section
    assert "{graph_forest_name}/nodes/" in section or "graph_forest_name}/nodes/" in section
    assert "{graph_forest_name}/edges/" in section or "graph_forest_name}/edges/" in section


def test_write_scoping_duckdb_store(doc_content):
    """Write Scoping must describe DuckDBGraphStore column stamping."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "DuckDBGraphStore" in section
    assert "column" in section.lower()


# --- Query Scoping subsection ---


def test_query_scoping_subsection(doc_content):
    """Must have a Query Scoping subsection."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "Query Scoping" in section


def test_query_scoping_table_entries(doc_content):
    """Query Scoping must document None, explicit string, and '*' behaviors."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    # Table must describe: None=own forest, explicit string=that forest, "*"=cross-forest
    assert "None" in section
    assert "own forest" in section.lower() or "caller" in section.lower()
    assert '"*"' in section or "`*`" in section or "'*'" in section
    assert "cross-forest" in section.lower() or "cross forest" in section.lower() or "all forest" in section.lower()


# --- Point Lookups subsection ---


def test_point_lookups_subsection(doc_content):
    """Must have a Point Lookups subsection."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "Point Lookups" in section


def test_point_lookups_explains_global_uniqueness(doc_content):
    """Point Lookups must explain forest-agnostic behavior due to globally unique IDs."""
    forest_start = doc_content.index("## Forest Awareness")
    write_path_start = doc_content.index("## Write Path")
    section = doc_content[forest_start:write_path_start]
    assert "globally unique" in section.lower() or "global" in section.lower()
    assert "forest-agnostic" in section.lower() or "agnostic" in section.lower()


# --- Protocol Interface code block updates ---


def test_protocol_interface_has_graph_forest_name_property(doc_content):
    """Protocol Interface code block must include graph_forest_name property before upsert_node."""
    interface_match = re.search(r"```python\n(.*?)```", doc_content, re.DOTALL)
    assert interface_match, "Protocol Interface code block not found"
    code_block = interface_match.group(1)
    prop_pos = code_block.index("graph_forest_name")
    upsert_pos = code_block.index("upsert_node")
    assert prop_pos < upsert_pos, "graph_forest_name must appear before upsert_node"


def test_execute_query_has_graph_forest_name_param(doc_content):
    """execute_query in Protocol Interface must include graph_forest_name parameter."""
    interface_match = re.search(r"```python\n(.*?)```", doc_content, re.DOTALL)
    assert interface_match, "Protocol Interface code block not found"
    code_block = interface_match.group(1)
    # Find execute_query signature - it should have graph_forest_name param
    eq_match = re.search(r"async def execute_query\(([^)]+)\)", code_block, re.DOTALL)
    assert eq_match, "execute_query method not found in code block"
    params = eq_match.group(1)
    assert "graph_forest_name" in params
