"""Tests for the Cypher skill file (context-intelligence-neo4j-search).

Validates that SKILL.md exists with correct structure, frontmatter,
schema documentation, label system, edge types, forest scoping,
query patterns, graph algorithm examples, and notes section.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "context-intelligence-neo4j-search"
)
SKILL_FILE = SKILL_DIR / "SKILL.md"

# Read and parse once at module level.
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
_SCHEMA_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Schema") if _SKILL_CONTENT else ""
)
_LABELS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Node Labels") if _SKILL_CONTENT else ""
)
_EDGES_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Relationship Types") if _SKILL_CONTENT else ""
)
_FOREST_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Forest Scoping") if _SKILL_CONTENT else ""
)
_PATTERNS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Query Patterns") if _SKILL_CONTENT else ""
)
_ALGORITHMS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Graph Algorithm Examples")
    if _SKILL_CONTENT
    else ""
)
_NOTES_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Notes") if _SKILL_CONTENT else ""
)
_ID_FORMAT_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "ID Format Reference") if _SKILL_CONTENT else ""
)


# -- AC-1: File exists --------------------------------------------------------


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# -- AC-2: YAML frontmatter ---------------------------------------------------


def test_frontmatter_name() -> None:
    assert _FRONTMATTER["name"] == "context-intelligence-neo4j-search"


def test_frontmatter_version() -> None:
    assert "version" in _FRONTMATTER
    assert _FRONTMATTER["version"]  # non-empty


def test_frontmatter_license() -> None:
    assert _FRONTMATTER.get("license") == "MIT"


def test_frontmatter_description_present() -> None:
    desc = _FRONTMATTER.get("description", "")
    assert desc, "Frontmatter must include a non-empty description"


def test_frontmatter_description_mentions_cypher() -> None:
    desc = _FRONTMATTER.get("description", "")
    assert "cypher" in desc.lower(), "Description should mention Cypher dialect"


# -- AC-3: Title and dialect ---------------------------------------------------


def test_title_includes_cypher_dialect() -> None:
    assert "Cypher" in _SKILL_CONTENT, "Title or intro should mention Cypher dialect"


def test_dialect_scope_note_mentions_neo4j() -> None:
    first_500 = _SKILL_CONTENT[:500]
    assert "Neo4j" in first_500, "Intro should mention Neo4j as the backend"


def test_dialect_scope_note_mentions_queryable_store() -> None:
    first_500 = _SKILL_CONTENT[:500]
    assert "QueryableStore" in first_500, "Intro should mention QueryableStore protocol"


# -- AC-4: Schema section -----------------------------------------------------


def test_schema_section_exists() -> None:
    assert _SCHEMA_SECTION, "Must have a ## Schema section"


def test_schema_has_node_id_format() -> None:
    assert "Node ID Format" in _SCHEMA_SECTION


def test_schema_has_relationship_id_format() -> None:
    assert "Relationship ID Format" in _SCHEMA_SECTION


def test_schema_has_storage_backend() -> None:
    assert "Storage Backend" in _SCHEMA_SECTION


def test_schema_mentions_supported_dialects() -> None:
    assert "supported_dialects" in _SCHEMA_SECTION


def test_schema_mentions_cypher_dialect_set() -> None:
    assert '"cypher"' in _SCHEMA_SECTION or "'cypher'" in _SCHEMA_SECTION


# -- AC-5: Node labels ---------------------------------------------------------


_REQUIRED_LABELS = [
    "Session",
    "Root",
    "Subsession",
    "ForkedSession",
    "OrchestratorRun",
    "Step",
    "PromptStep",
    "AssistantStep",
    "RecipeStep",
    "ToolExecution",
    "Delegation",
    "Event",
]


def test_labels_section_exists() -> None:
    assert _LABELS_SECTION, "Must have a ## Node Labels section"


def test_all_12_labels_present() -> None:
    for label in _REQUIRED_LABELS:
        assert f"`{label}`" in _LABELS_SECTION, (
            f"Node Labels section missing label: {label}"
        )


def test_label_count() -> None:
    """The table should document all 12 labels."""
    label_rows = [
        line
        for line in _LABELS_SECTION.strip().splitlines()
        if line.startswith("|")
        and "`" in line
        and "Label" not in line
        and "---" not in line
    ]
    assert len(label_rows) == 12, f"Expected 12 label rows, got {len(label_rows)}"


# -- AC-6: Relationship types --------------------------------------------------


_REQUIRED_EDGE_TYPES = [
    "HAS_RUN",
    "HAS_STEP",
    "NEXT",
    "TRIGGERED",
    "PARALLEL_WITH",
    "SPAWNED",
    "SUBSESSION_OF",
    "HAS_EVENT",
]


def test_edges_section_exists() -> None:
    assert _EDGES_SECTION, "Must have a ## Relationship Types section"


def test_all_8_edge_types_present() -> None:
    for edge_type in _REQUIRED_EDGE_TYPES:
        assert f"`{edge_type}`" in _EDGES_SECTION, (
            f"Relationship Types section missing: {edge_type}"
        )


def test_edge_types_have_from_to() -> None:
    """Each edge type row should document From and To columns."""
    assert "From" in _EDGES_SECTION or "from" in _EDGES_SECTION.lower()
    assert "To" in _EDGES_SECTION


# -- AC-7: Node properties -----------------------------------------------------


def test_node_properties_section_exists() -> None:
    section = _extract_section(_SKILL_CONTENT, "Node Properties")
    assert section, "Must have a ## Node Properties section"


def test_node_properties_includes_node_id() -> None:
    section = _extract_section(_SKILL_CONTENT, "Node Properties")
    assert "node_id" in section


def test_node_properties_includes_graph_forest_name() -> None:
    section = _extract_section(_SKILL_CONTENT, "Node Properties")
    assert "graph_forest_name" in section


def test_node_properties_includes_session_id() -> None:
    section = _extract_section(_SKILL_CONTENT, "Node Properties")
    assert "session_id" in section


# -- AC-8: Relationship properties ---------------------------------------------


def test_relationship_properties_section_exists() -> None:
    section = _extract_section(_SKILL_CONTENT, "Relationship Properties")
    assert section, "Must have a ## Relationship Properties section"


def test_relationship_properties_includes_graph_forest_name() -> None:
    section = _extract_section(_SKILL_CONTENT, "Relationship Properties")
    assert "graph_forest_name" in section


# -- AC-9: Indexes -------------------------------------------------------------


def test_indexes_section_exists() -> None:
    section = _extract_section(_SKILL_CONTENT, "Indexes")
    assert section, "Must have a ## Indexes section"


def test_indexes_documents_node_id_index() -> None:
    section = _extract_section(_SKILL_CONTENT, "Indexes")
    assert "idx_node_id" in section or "node_id" in section


def test_indexes_documents_forest_index() -> None:
    section = _extract_section(_SKILL_CONTENT, "Indexes")
    assert "idx_forest" in section or "graph_forest_name" in section


# -- AC-10: Forest scoping -----------------------------------------------------


def test_forest_scoping_section_exists() -> None:
    assert _FOREST_SECTION, "Must have a ## Forest Scoping section"


def test_forest_scoping_explains_parameter_injection() -> None:
    assert (
        "parameter injection" in _FOREST_SECTION.lower()
        or "$graph_forest_name" in _FOREST_SECTION
    )


def test_forest_scoping_documents_default_query() -> None:
    assert (
        "default" in _FOREST_SECTION.lower() or "own forest" in _FOREST_SECTION.lower()
    )


def test_forest_scoping_documents_wildcard() -> None:
    assert '"*"' in _FOREST_SECTION or "wildcard" in _FOREST_SECTION.lower()


def test_forest_scoping_documents_explicit_override() -> None:
    assert (
        "explicit" in _FOREST_SECTION.lower() or "override" in _FOREST_SECTION.lower()
    )


# -- AC-11: Query patterns -----------------------------------------------------


def test_query_patterns_section_exists() -> None:
    assert _PATTERNS_SECTION, "Must have a ## Query Patterns section"


def test_12_query_patterns_exist() -> None:
    """Must document all 12 query patterns."""
    pattern_headings = re.findall(r"### Pattern \d+", _PATTERNS_SECTION)
    assert len(pattern_headings) == 12, (
        f"Expected 12 query patterns, got {len(pattern_headings)}: {pattern_headings}"
    )


def test_query_patterns_include_cypher_code_blocks() -> None:
    """Each pattern should include at least one Cypher code block."""
    cypher_blocks = re.findall(r"```cypher", _PATTERNS_SECTION)
    assert len(cypher_blocks) >= 12, (
        f"Expected at least 12 Cypher code blocks, got {len(cypher_blocks)}"
    )


def test_query_pattern_sessions_in_forest() -> None:
    assert "Pattern 1" in _PATTERNS_SECTION
    assert "Session" in _PATTERNS_SECTION


def test_query_pattern_delegations() -> None:
    assert "Delegation" in _PATTERNS_SECTION


def test_query_pattern_prompt_text_search() -> None:
    assert "prompt_text" in _PATTERNS_SECTION or "Prompt Text" in _PATTERNS_SECTION


# -- AC-12: Graph algorithm examples -------------------------------------------


def test_algorithms_section_exists() -> None:
    assert _ALGORITHMS_SECTION, "Must have a ## Graph Algorithm Examples section"


def test_algorithms_shortest_path() -> None:
    assert (
        "shortestPath" in _ALGORITHMS_SECTION or "Shortest Path" in _ALGORITHMS_SECTION
    )


def test_algorithms_variable_length_traversal() -> None:
    assert "Variable-Length" in _ALGORITHMS_SECTION or "*" in _ALGORITHMS_SECTION


# -- AC-13: Usage via execute_query --------------------------------------------


def test_usage_section_exists() -> None:
    section = _extract_section(_SKILL_CONTENT, "Usage via `execute_query`")
    assert section, "Must have a ## Usage via execute_query section"


def test_usage_mentions_execute_query_method() -> None:
    section = _extract_section(_SKILL_CONTENT, "Usage via `execute_query`")
    assert "execute_query" in section


# -- AC-14: ID format reference ------------------------------------------------


def test_id_format_section_exists() -> None:
    assert _ID_FORMAT_SECTION, "Must have a ## ID Format Reference section"


def test_id_format_documents_session_nodes() -> None:
    assert (
        "Session nodes" in _ID_FORMAT_SECTION or "session" in _ID_FORMAT_SECTION.lower()
    )


def test_id_format_documents_make_node_id() -> None:
    assert "make_node_id" in _ID_FORMAT_SECTION or "node_id" in _ID_FORMAT_SECTION


def test_id_format_documents_double_underscore_separator() -> None:
    assert "__" in _ID_FORMAT_SECTION


# -- AC-15: Notes section ------------------------------------------------------


def test_notes_section_exists() -> None:
    assert _NOTES_SECTION, "Must have a ## Notes section"


def test_notes_properties_vs_labels() -> None:
    assert (
        "Properties vs labels" in _NOTES_SECTION
        or "properties" in _NOTES_SECTION.lower()
    )


def test_notes_multi_label_nodes() -> None:
    assert "Multi-label" in _NOTES_SECTION or "multi-label" in _NOTES_SECTION


def test_notes_forest_property_on_relationships() -> None:
    assert (
        "Forest property on relationships" in _NOTES_SECTION
        or "graph_forest_name" in _NOTES_SECTION
    )


def test_notes_buffer_visibility() -> None:
    assert "Buffer visibility" in _NOTES_SECTION or "buffer" in _NOTES_SECTION.lower()


# -- AC-16: Event Data Preservation section ------------------------------------


_NODE_PROPS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Node Properties") if _SKILL_CONTENT else ""
)


def test_event_data_preservation_section_exists() -> None:
    """SKILL.md must contain an Event Data Preservation subsection."""
    assert "Event Data Preservation" in _NODE_PROPS_SECTION, (
        "Node Properties section must contain an 'Event Data Preservation' subsection"
    )


def test_event_data_preservation_documents_data_property() -> None:
    """Every node carries a 'data' property (JSON string of full event payload)."""
    assert "data" in _NODE_PROPS_SECTION, (
        "Event Data Preservation must document the 'data' property"
    )


def test_event_data_preservation_documents_enriched_data_properties() -> None:
    """Enriched nodes have 'data_<event_name>' properties."""
    assert "data_" in _NODE_PROPS_SECTION, (
        "Event Data Preservation must document 'data_<event_name>' properties"
    )


# -- AC-17: Enrichment property naming table -----------------------------------

_REQUIRED_ENRICHMENT_MAPPINGS = [
    ("llm:request", "data_llm_request"),
    ("llm:response", "data_llm_response"),
    ("tool:post", "data_tool_post"),
    ("tool:error", "data_tool_error"),
    ("execution:end", "data_execution_end"),
    ("orchestrator:complete", "data_orchestrator_complete"),
    ("session:end", "data_session_end"),
    ("delegate:agent_spawned", "data_delegate_agent_spawned"),
    ("delegate:agent_completed", "data_delegate_agent_completed"),
]


def test_enrichment_property_table_has_all_9_mappings() -> None:
    """Must document all 9 event-to-property name mappings."""
    for _event, prop in _REQUIRED_ENRICHMENT_MAPPINGS:
        assert prop in _NODE_PROPS_SECTION, (
            f"Enrichment property table missing mapping for property: {prop}"
        )


def test_enrichment_property_table_documents_event_names() -> None:
    """All 9 event names must appear in the Node Properties section."""
    for event, _prop in _REQUIRED_ENRICHMENT_MAPPINGS:
        # Event names appear either directly or with colon replaced by underscore
        event_slug = event.replace(":", "_")
        assert event in _NODE_PROPS_SECTION or event_slug in _NODE_PROPS_SECTION, (
            f"Enrichment table missing event name: {event}"
        )


# -- AC-18: Blob References section -------------------------------------------

_BLOB_REFS_SECTION: str = ""
# Blob References may be a ### subsection within Node Properties
_blob_refs_match = re.search(
    r"### Blob References\s*\n(.*?)(?=^###|^##|\Z)",
    _SKILL_CONTENT,
    re.MULTILINE | re.DOTALL,
)
if _blob_refs_match:
    _BLOB_REFS_SECTION = _blob_refs_match.group(1)


def test_blob_references_section_exists() -> None:
    """SKILL.md must contain a Blob References section."""
    assert _BLOB_REFS_SECTION, (
        "SKILL.md must contain a '### Blob References' section"
    )


def test_blob_references_documents_blob_ref_pattern() -> None:
    """Must explain the $blob_ref pattern."""
    assert "$blob_ref" in _BLOB_REFS_SECTION or "blob_ref" in _BLOB_REFS_SECTION, (
        "Blob References section must explain the $blob_ref pattern"
    )


def test_blob_references_includes_json_example() -> None:
    """Must include a JSON example showing the $blob_ref structure."""
    assert "```json" in _BLOB_REFS_SECTION or "```" in _BLOB_REFS_SECTION, (
        "Blob References section must include a code/JSON example"
    )


_REQUIRED_BLOB_FIELDS = ["raw", "result", "messages", "mount_plan", "context_snapshot", "debug"]


def test_blob_references_lists_known_blob_fields() -> None:
    """Must list all known blob fields."""
    for field in _REQUIRED_BLOB_FIELDS:
        assert field in _BLOB_REFS_SECTION, (
            f"Blob References section missing known blob field: {field}"
        )


# -- AC-19: Resolving blob refs (blob tool operations) -------------------------

_RESOLVING_BLOBS_SECTION: str = ""
_resolving_match = re.search(
    r"### Resolving [Bb]lob [Rr]efs?\s*\n(.*?)(?=^###|^##|\Z)",
    _SKILL_CONTENT,
    re.MULTILINE | re.DOTALL,
)
if _resolving_match:
    _RESOLVING_BLOBS_SECTION = _resolving_match.group(1)


def test_resolving_blob_refs_section_exists() -> None:
    """SKILL.md must contain a 'Resolving blob refs' section."""
    assert _RESOLVING_BLOBS_SECTION, (
        "SKILL.md must contain a '### Resolving blob refs' section"
    )


def test_resolving_blobs_documents_blob_list() -> None:
    """Must document blob_list(session_id)."""
    assert "blob_list" in _RESOLVING_BLOBS_SECTION, (
        "Resolving blob refs must document blob_list tool"
    )


def test_resolving_blobs_blob_list_returns_uri_field_node_size() -> None:
    """blob_list must be documented as returning [{uri, field, node_id, size_bytes}]."""
    section = _RESOLVING_BLOBS_SECTION
    assert "uri" in section and "field" in section and "size_bytes" in section, (
        "blob_list return value must document {uri, field, node_id, size_bytes}"
    )


def test_resolving_blobs_documents_blob_dump() -> None:
    """Must document blob_dump(uri) returning a file path."""
    assert "blob_dump" in _RESOLVING_BLOBS_SECTION, (
        "Resolving blob refs must document blob_dump tool"
    )


def test_resolving_blobs_blob_dump_returns_file_path() -> None:
    """blob_dump must be documented as returning a file path."""
    assert "file path" in _RESOLVING_BLOBS_SECTION or "path" in _RESOLVING_BLOBS_SECTION, (
        "blob_dump documentation must mention that it returns a file path"
    )


# -- AC-20: Agent workflow (5-step process) ------------------------------------

_AGENT_WORKFLOW_SECTION: str = ""
_workflow_match = re.search(
    r"### Agent [Ww]orkflow\s*\n(.*?)(?=^###|^##|\Z)",
    _SKILL_CONTENT,
    re.MULTILINE | re.DOTALL,
)
if _workflow_match:
    _AGENT_WORKFLOW_SECTION = _workflow_match.group(1)


def test_agent_workflow_section_exists() -> None:
    """SKILL.md must contain an Agent workflow section."""
    assert _AGENT_WORKFLOW_SECTION, (
        "SKILL.md must contain a '### Agent workflow' section"
    )


def test_agent_workflow_has_5_steps() -> None:
    """Agent workflow must document 5 steps."""
    # Count numbered list items (1. 2. 3. 4. 5.)
    steps = re.findall(r"^\s*\d+\.", _AGENT_WORKFLOW_SECTION, re.MULTILINE)
    assert len(steps) >= 5, (
        f"Agent workflow must have at least 5 numbered steps, found {len(steps)}"
    )


def test_agent_workflow_step_query_neo4j() -> None:
    """Step 1: query Neo4j."""
    assert "Neo4j" in _AGENT_WORKFLOW_SECTION or "query" in _AGENT_WORKFLOW_SECTION.lower(), (
        "Agent workflow must include a step for querying Neo4j"
    )


def test_agent_workflow_step_parse_data_property() -> None:
    """Step 2: parse data property."""
    assert "data" in _AGENT_WORKFLOW_SECTION, (
        "Agent workflow must include a step for parsing the data property"
    )


def test_agent_workflow_step_call_blob_dump() -> None:
    """Step 3: call blob_dump."""
    assert "blob_dump" in _AGENT_WORKFLOW_SECTION, (
        "Agent workflow must include a step for calling blob_dump"
    )


def test_agent_workflow_step_read_file_or_jq() -> None:
    """Step 4: use read_file or bash+jq."""
    assert (
        "read_file" in _AGENT_WORKFLOW_SECTION or "jq" in _AGENT_WORKFLOW_SECTION
    ), (
        "Agent workflow must include a step for using read_file or bash+jq"
    )


def test_agent_workflow_never_load_blob_directly() -> None:
    """Step 5: never load blob content directly."""
    section_lower = _AGENT_WORKFLOW_SECTION.lower()
    assert "never" in section_lower or "do not" in section_lower or "don't" in section_lower, (
        "Agent workflow must include a warning about not loading blob content directly"
    )
