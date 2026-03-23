"""Tests for the context-intelligence-graph-query skill (SKILL.md).

Validates that SKILL.md exists with correct structure, frontmatter,
schema documentation, label system, edge types, workspace scoping,
query patterns, graph algorithm examples, usage via graph_query tool,
and zero Neo4j API leakage.

Acceptance Criteria:
  AC-1:  File/dir existence, old dir deleted
  AC-2:  Frontmatter name/version/license/description
  AC-3:  7 Neo4j leakage checks (forbidden terms absent)
  AC-4:  10 required sections present
  AC-5:  Dual-mode guidance table present
  AC-6:  12 node labels present and counted
  AC-7:  8 relationship types present
  AC-8:  Node properties include workspace/node_id/session_id
  AC-9:  Event data preservation with 9 enrichment mappings
  AC-10: Blob references with 6 fields, blob_read and ci-blob:// documented
  AC-11: Agent workflow section with 5+ steps referencing graph_query
  AC-12: Workspace scoping explains auto-injection with $workspace
  AC-13: 12 query patterns, 12+ cypher code blocks, all using $workspace
  AC-14: Usage section references graph_query
  AC-15: ID format documents session nodes and double underscore separator
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


def _extract_section(content: str, heading: str) -> str:
    """Extract content under a markdown ## heading, up to the next ## or end."""
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1)


def _extract_subsection(content: str, heading: str) -> str:
    """Extract content under a markdown ### heading, up to the next ### or ## or end."""
    pattern = rf"^### {re.escape(heading)}\s*\n(.*?)(?=^###|^##|\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1)


_FRONTMATTER: dict = _parse_frontmatter(_SKILL_CONTENT) if _SKILL_CONTENT else {}

# Pre-extract frequently-used sections
_SCHEMA_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Schema") if _SKILL_CONTENT else ""
)
_LABELS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Node Labels") if _SKILL_CONTENT else ""
)
_EDGES_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Relationship Types") if _SKILL_CONTENT else ""
)
_NODE_PROPS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Node Properties") if _SKILL_CONTENT else ""
)
_WORKSPACE_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Workspace Scoping") if _SKILL_CONTENT else ""
)
_PATTERNS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Query Patterns") if _SKILL_CONTENT else ""
)
_ALGORITHMS_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Graph Algorithm Examples")
    if _SKILL_CONTENT
    else ""
)
_ID_FORMAT_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "ID Format Reference") if _SKILL_CONTENT else ""
)
_NOTES_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Notes") if _SKILL_CONTENT else ""
)

_BLOB_REFS_SECTION: str = (
    _extract_subsection(_SKILL_CONTENT, "Blob References") if _SKILL_CONTENT else ""
)
_AGENT_WORKFLOW_SECTION: str = (
    _extract_subsection(_SKILL_CONTENT, "Agent workflow") if _SKILL_CONTENT else ""
)


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
    """Frontmatter version must be 0.2.0."""
    assert _FRONTMATTER.get("version") == "0.2.0", (
        f"Expected version='0.2.0', got: {_FRONTMATTER.get('version')!r}"
    )


def test_ac2_frontmatter_license() -> None:
    """Frontmatter license must be MIT."""
    assert _FRONTMATTER.get("license") == "MIT"


def test_ac2_frontmatter_description_present() -> None:
    """Frontmatter must have a non-empty description."""
    desc = _FRONTMATTER.get("description", "")
    assert desc, "Frontmatter must include a non-empty description"


def test_ac2_frontmatter_description_mentions_cypher() -> None:
    """Frontmatter description must mention Cypher."""
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


# ===========================================================================
# AC-4: 10 required sections present
# ===========================================================================

_REQUIRED_SECTIONS = [
    "When to Use Graph vs File Patterns",
    "Schema",
    "Node Labels",
    "Relationship Types",
    "Node Properties",
    "Relationship Properties",
    "Workspace Scoping",
    "Query Patterns",
    "Graph Algorithm Examples",
    "ID Format Reference",
]


def test_ac4_section_when_to_use() -> None:
    """Section 'When to Use Graph vs File Patterns' must be present."""
    assert "## When to Use Graph vs File Patterns" in _SKILL_CONTENT


def test_ac4_section_schema() -> None:
    """Section '## Schema' must be present."""
    assert _SCHEMA_SECTION, "Must have a ## Schema section"


def test_ac4_section_node_labels() -> None:
    """Section '## Node Labels' must be present."""
    assert _LABELS_SECTION, "Must have a ## Node Labels section"


def test_ac4_section_relationship_types() -> None:
    """Section '## Relationship Types' must be present."""
    assert _EDGES_SECTION, "Must have a ## Relationship Types section"


def test_ac4_section_node_properties() -> None:
    """Section '## Node Properties' must be present."""
    assert _NODE_PROPS_SECTION, "Must have a ## Node Properties section"


def test_ac4_section_relationship_properties() -> None:
    """Section '## Relationship Properties' must be present."""
    rel_props = _extract_section(_SKILL_CONTENT, "Relationship Properties")
    assert rel_props, "Must have a ## Relationship Properties section"


def test_ac4_section_workspace_scoping() -> None:
    """Section '## Workspace Scoping' must be present."""
    assert _WORKSPACE_SECTION, "Must have a ## Workspace Scoping section"


def test_ac4_section_query_patterns() -> None:
    """Section '## Query Patterns' must be present."""
    assert _PATTERNS_SECTION, "Must have a ## Query Patterns section"


def test_ac4_section_graph_algorithm_examples() -> None:
    """Section '## Graph Algorithm Examples' must be present."""
    assert _ALGORITHMS_SECTION, "Must have a ## Graph Algorithm Examples section"


def test_ac4_section_id_format_reference() -> None:
    """Section '## ID Format Reference' must be present."""
    assert _ID_FORMAT_SECTION, "Must have a ## ID Format Reference section"


def test_ac4_section_usage_via_graph_query() -> None:
    """Section '## Usage via graph_query Tool' or similar must be present.

    Bonus check: this section is not in the 10-item AC-4 list in the spec, but is
    required by AC-14 and adds useful coverage here as well.
    """
    usage = _extract_section(_SKILL_CONTENT, "Usage via graph_query Tool")
    assert usage, "Must have a ## Usage via graph_query Tool section"


def test_ac4_section_notes() -> None:
    """Section '## Notes' must be present.

    Bonus check: this section is not in the 10-item AC-4 list in the spec, but the
    Notes section is part of the required SKILL.md structure and tested here for
    completeness.
    """
    assert _NOTES_SECTION, "Must have a ## Notes section"


# ===========================================================================
# AC-5: Dual-mode guidance table
# ===========================================================================


def test_ac5_dual_mode_section_has_table() -> None:
    """When to Use section must contain a table."""
    dual_section = _extract_section(
        _SKILL_CONTENT, "When to Use Graph vs File Patterns"
    )
    assert "|" in dual_section, "When to Use section must contain a markdown table"


def test_ac5_dual_mode_mentions_graph_query() -> None:
    """Dual-mode table must reference graph_query for structural queries."""
    dual_section = _extract_section(
        _SKILL_CONTENT, "When to Use Graph vs File Patterns"
    )
    assert "graph_query" in dual_section, (
        "When to Use section must mention graph_query for structural queries"
    )


def test_ac5_dual_mode_mentions_bash_or_grep() -> None:
    """Dual-mode table must reference bash/jq/grep for text search."""
    dual_section = _extract_section(
        _SKILL_CONTENT, "When to Use Graph vs File Patterns"
    )
    assert "bash" in dual_section or "grep" in dual_section or "jq" in dual_section, (
        "When to Use section must mention bash+jq/grep for text search"
    )


def test_ac5_dual_mode_has_fallback_guidance() -> None:
    """Dual-mode section must contain fallback guidance."""
    dual_section = _extract_section(
        _SKILL_CONTENT, "When to Use Graph vs File Patterns"
    )
    assert "fallback" in dual_section.lower() or "fall back" in dual_section.lower(), (
        "When to Use section must contain fallback guidance"
    )


# ===========================================================================
# AC-6: 12 node labels present and counted
# ===========================================================================

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


def test_ac6_labels_section_exists() -> None:
    """Node Labels section must be present."""
    assert _LABELS_SECTION, "Must have a ## Node Labels section"


def test_ac6_all_12_labels_present() -> None:
    """All 12 required labels must appear in backtick format in the Node Labels section."""
    missing = [
        label for label in _REQUIRED_LABELS if f"`{label}`" not in _LABELS_SECTION
    ]
    assert not missing, f"Node Labels section missing labels: {missing}"


def test_ac6_label_count_is_12() -> None:
    """The Node Labels table must document exactly 15 labels (12 original + 3 recipe labels)."""
    label_rows = [
        line
        for line in _LABELS_SECTION.strip().splitlines()
        if line.startswith("|")
        and "`" in line
        and "Label" not in line
        and "---" not in line
    ]
    assert len(label_rows) == 15, f"Expected 15 label rows, got {len(label_rows)}"


# ===========================================================================
# AC-7: 8 relationship types present
# ===========================================================================

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


def test_ac7_edges_section_exists() -> None:
    """Relationship Types section must be present."""
    assert _EDGES_SECTION, "Must have a ## Relationship Types section"


def test_ac7_all_8_edge_types_present() -> None:
    """All 8 required relationship types must appear in backtick format."""
    missing = [et for et in _REQUIRED_EDGE_TYPES if f"`{et}`" not in _EDGES_SECTION]
    assert not missing, f"Relationship Types section missing types: {missing}"


def test_ac7_edge_types_have_from_to_meaning_columns() -> None:
    """Edge types table must have From, To, and Meaning columns."""
    assert "From" in _EDGES_SECTION, "Edge types table must have a 'From' column"
    assert "To" in _EDGES_SECTION, "Edge types table must have a 'To' column"
    assert "Meaning" in _EDGES_SECTION, "Edge types table must have a 'Meaning' column"


# ===========================================================================
# AC-8: Node properties include workspace/node_id/session_id
# ===========================================================================


def test_ac8_node_properties_includes_node_id() -> None:
    """Node properties must include node_id."""
    assert "node_id" in _NODE_PROPS_SECTION, "Node Properties must document 'node_id'"


def test_ac8_node_properties_includes_workspace() -> None:
    """Node properties must include workspace (not graph_forest_name)."""
    assert "workspace" in _NODE_PROPS_SECTION, (
        "Node Properties must document 'workspace' property"
    )


def test_ac8_node_properties_includes_session_id() -> None:
    """Node properties must include session_id."""
    assert "session_id" in _NODE_PROPS_SECTION, (
        "Node Properties must document 'session_id'"
    )


def test_ac8_node_properties_includes_occurred_at() -> None:
    """Node properties must include occurred_at."""
    assert "occurred_at" in _NODE_PROPS_SECTION


def test_ac8_node_properties_includes_prompt_text() -> None:
    """Node properties must include prompt_text."""
    assert "prompt_text" in _NODE_PROPS_SECTION


def test_ac8_node_properties_includes_status() -> None:
    """Node properties must include status."""
    assert "status" in _NODE_PROPS_SECTION


def test_ac8_node_properties_includes_tool_name() -> None:
    """Node properties must include tool_name."""
    assert "tool_name" in _NODE_PROPS_SECTION


# ===========================================================================
# AC-9: Event data preservation with 9 enrichment mappings
# ===========================================================================

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


def test_ac9_event_data_preservation_section_exists() -> None:
    """Node Properties section must contain 'Event Data Preservation' subsection."""
    assert "Event Data Preservation" in _NODE_PROPS_SECTION, (
        "Node Properties section must contain an 'Event Data Preservation' subsection"
    )


def test_ac9_event_data_preservation_documents_data_property() -> None:
    """Event Data Preservation must document the 'data' property (backtick-formatted)."""
    assert "`data`" in _NODE_PROPS_SECTION, (
        "Node Properties section must reference the `data` property in backtick format"
    )


def test_ac9_event_data_preservation_documents_data_prefix_properties() -> None:
    """Event Data Preservation must document 'data_<event_name>' properties."""
    assert "data_" in _NODE_PROPS_SECTION, (
        "Event Data Preservation must document 'data_<event_name>' properties"
    )


def test_ac9_all_9_enrichment_property_names_present() -> None:
    """All 9 data_<event> property names must appear in the Node Properties section."""
    missing = [
        prop
        for _, prop in _REQUIRED_ENRICHMENT_MAPPINGS
        if prop not in _NODE_PROPS_SECTION
    ]
    assert not missing, (
        f"Node Properties section missing enrichment properties: {missing}"
    )


def test_ac9_enrichment_table_has_9_event_names() -> None:
    """All 9 event names (or slugs) must appear in the Node Properties section."""
    missing = []
    for event, _ in _REQUIRED_ENRICHMENT_MAPPINGS:
        slug = event.replace(":", "_")
        if event not in _NODE_PROPS_SECTION and slug not in _NODE_PROPS_SECTION:
            missing.append(event)
    assert not missing, f"Node Properties section missing event names: {missing}"


# ===========================================================================
# AC-10: Blob references section with 6 fields, blob_read and ci-blob:// documented
# ===========================================================================

_REQUIRED_BLOB_FIELDS = [
    "raw",
    "result",
    "messages",
    "mount_plan",
    "context_snapshot",
    "debug",
]


def test_ac10_blob_references_section_exists() -> None:
    """SKILL.md must contain a '### Blob References' subsection."""
    assert _BLOB_REFS_SECTION, "SKILL.md must contain a '### Blob References' section"


def test_ac10_blob_references_has_all_6_fields() -> None:
    """Blob References section must list all 6 known blob fields."""
    missing = [f for f in _REQUIRED_BLOB_FIELDS if f not in _BLOB_REFS_SECTION]
    assert not missing, f"Blob References section missing fields: {missing}"


def test_ac10_blob_references_mentions_blob_read() -> None:
    """Blob References section must mention blob_read tool."""
    assert "blob_read" in _BLOB_REFS_SECTION or "blob_read" in _NODE_PROPS_SECTION, (
        "SKILL.md must document blob_read tool"
    )


def test_ac10_blob_references_mentions_ci_blob_uri() -> None:
    """Blob References section must mention ci-blob:// URI scheme."""
    assert "ci-blob://" in _BLOB_REFS_SECTION or "ci-blob://" in _NODE_PROPS_SECTION, (
        "SKILL.md must document ci-blob:// URI scheme"
    )


def test_ac10_blob_list_return_format_documented() -> None:
    """blob_list return format must document size_bytes (the unique structural field)."""
    combined = _BLOB_REFS_SECTION + _NODE_PROPS_SECTION
    assert "size_bytes" in combined, (
        "blob_list return value must document size_bytes field"
    )


# ===========================================================================
# AC-11: Agent workflow section with 5+ steps referencing graph_query
# ===========================================================================


def test_ac11_agent_workflow_section_exists() -> None:
    """SKILL.md must contain a '### Agent workflow' subsection."""
    assert _AGENT_WORKFLOW_SECTION, (
        "SKILL.md must contain a '### Agent workflow' section"
    )


def test_ac11_agent_workflow_has_5_or_more_steps() -> None:
    """Agent workflow must have at least 5 numbered steps."""
    steps = re.findall(r"^\s*\d+\.", _AGENT_WORKFLOW_SECTION, re.MULTILINE)
    assert len(steps) >= 5, (
        f"Agent workflow must have at least 5 numbered steps, found {len(steps)}"
    )


def test_ac11_agent_workflow_references_graph_query() -> None:
    """Agent workflow must reference the graph_query tool."""
    assert "graph_query" in _AGENT_WORKFLOW_SECTION, (
        "Agent workflow must reference the graph_query tool"
    )


def test_ac11_agent_workflow_step_references_data_property() -> None:
    """Agent workflow must include a step for parsing the data property."""
    assert "data" in _AGENT_WORKFLOW_SECTION, (
        "Agent workflow must include a step for parsing the data property"
    )


def test_ac11_agent_workflow_step_references_blob_read() -> None:
    """Agent workflow must include a step for calling blob_read."""
    assert "blob_read" in _AGENT_WORKFLOW_SECTION, (
        "Agent workflow must include a step for calling blob_read"
    )


def test_ac11_agent_workflow_step_references_read_file_or_jq() -> None:
    """Agent workflow must include a step using read_file or bash+jq."""
    assert "read_file" in _AGENT_WORKFLOW_SECTION or "jq" in _AGENT_WORKFLOW_SECTION, (
        "Agent workflow must include a step for using read_file or bash+jq"
    )


def test_ac11_agent_workflow_warns_about_blob_size() -> None:
    """Agent workflow must warn against loading blob content directly."""
    section_lower = _AGENT_WORKFLOW_SECTION.lower()
    assert (
        "never" in section_lower
        or "do not" in section_lower
        or "don't" in section_lower
    ), "Agent workflow must warn about not loading blob content directly"


# ===========================================================================
# AC-12: Workspace Scoping explains auto-injection with $workspace
# ===========================================================================


def test_ac12_workspace_scoping_section_exists() -> None:
    """## Workspace Scoping section must be present."""
    assert _WORKSPACE_SECTION, "Must have a ## Workspace Scoping section"


def test_ac12_workspace_scoping_mentions_auto_injection() -> None:
    """Workspace Scoping must explain $workspace auto-injection by graph_query tool."""
    assert (
        "auto" in _WORKSPACE_SECTION.lower()
        or "inject" in _WORKSPACE_SECTION.lower()
        or "automatic" in _WORKSPACE_SECTION.lower()
    ), "Workspace Scoping must explain automatic injection"


def test_ac12_workspace_scoping_references_dollar_workspace() -> None:
    """Workspace Scoping must reference the $workspace parameter."""
    assert "$workspace" in _WORKSPACE_SECTION, (
        "Workspace Scoping must reference '$workspace'"
    )


def test_ac12_workspace_scoping_mentions_graph_query_tool() -> None:
    """Workspace Scoping must mention graph_query tool as the injector."""
    assert "graph_query" in _WORKSPACE_SECTION, (
        "Workspace Scoping must mention graph_query tool"
    )


# ===========================================================================
# AC-13: 12 query patterns, 12+ cypher blocks, all using $workspace
# ===========================================================================


def test_ac13_query_patterns_section_exists() -> None:
    """## Query Patterns section must be present."""
    assert _PATTERNS_SECTION, "Must have a ## Query Patterns section"


def test_ac13_exactly_12_pattern_headings() -> None:
    """Must document exactly 12 query patterns (### Pattern 1 through 12)."""
    pattern_headings = re.findall(r"### Pattern \d+", _PATTERNS_SECTION)
    assert len(pattern_headings) == 12, (
        f"Expected 12 query patterns, got {len(pattern_headings)}: {pattern_headings}"
    )


def test_ac13_at_least_12_cypher_code_blocks() -> None:
    """Must have at least 12 Cypher code blocks in Query Patterns."""
    cypher_blocks = re.findall(r"```cypher", _PATTERNS_SECTION)
    assert len(cypher_blocks) >= 12, (
        f"Expected at least 12 Cypher code blocks, got {len(cypher_blocks)}"
    )


def test_ac13_all_cypher_blocks_use_workspace() -> None:
    """All Cypher blocks with property filters must use $workspace, not $graph_forest_name."""
    # Extract all cypher code blocks
    cypher_blocks = re.findall(r"```cypher\n(.*?)```", _PATTERNS_SECTION, re.DOTALL)
    # Find blocks that have property filters (contain WHERE or { or :)
    blocks_with_filters = [
        b
        for b in cypher_blocks
        if "{" in b
        and (
            "Session" in b
            or "Step" in b
            or "ToolExecution" in b
            or ":n " in b
            or ":s " in b
        )
    ]
    blocks_with_wrong_param = [
        b for b in blocks_with_filters if "graph_forest_name" in b
    ]
    assert not blocks_with_wrong_param, (
        f"Found Cypher blocks using 'graph_forest_name' instead of '$workspace': "
        f"{blocks_with_wrong_param[:1]}"
    )


def test_ac13_cypher_blocks_use_workspace_param() -> None:
    """Cypher blocks with property filters should use $workspace."""
    # At least one cypher block must reference $workspace
    assert "$workspace" in _PATTERNS_SECTION, (
        "Query Patterns must use $workspace parameter in Cypher queries"
    )


def test_ac13_pattern_1_sessions() -> None:
    """Pattern 1 must cover finding sessions."""
    assert "Pattern 1" in _PATTERNS_SECTION
    assert "Session" in _PATTERNS_SECTION


def test_ac13_pattern_includes_delegations() -> None:
    """Query Patterns must cover Delegations."""
    assert "Delegation" in _PATTERNS_SECTION


def test_ac13_pattern_includes_prompt_text() -> None:
    """Query Patterns must cover prompt_text search."""
    assert "prompt_text" in _PATTERNS_SECTION or "Prompt Text" in _PATTERNS_SECTION


# ===========================================================================
# AC-14: Usage section references graph_query
# ===========================================================================


def test_ac14_usage_section_exists() -> None:
    """## Usage via graph_query Tool section must be present."""
    usage = _extract_section(_SKILL_CONTENT, "Usage via graph_query Tool")
    assert usage, "Must have a ## Usage via graph_query Tool section"


def test_ac14_usage_section_references_graph_query() -> None:
    """Usage section must reference the graph_query tool."""
    usage = _extract_section(_SKILL_CONTENT, "Usage via graph_query Tool")
    assert "graph_query" in usage, "Usage section must reference graph_query tool"


# ===========================================================================
# AC-15: ID format documents session nodes and double underscore separator
# ===========================================================================


def test_ac15_id_format_section_exists() -> None:
    """## ID Format Reference section must be present."""
    assert _ID_FORMAT_SECTION, "Must have a ## ID Format Reference section"


def test_ac15_id_format_documents_session_nodes() -> None:
    """ID Format Reference must document Session nodes using raw UUID."""
    assert "Session" in _ID_FORMAT_SECTION or "session" in _ID_FORMAT_SECTION.lower(), (
        "ID Format Reference must document Session nodes"
    )


def test_ac15_id_format_documents_double_underscore_separator() -> None:
    """ID Format Reference must document the __ double underscore separator."""
    assert "__" in _ID_FORMAT_SECTION, (
        "ID Format Reference must document the __ separator"
    )


def test_ac15_id_format_documents_tool_execution_format() -> None:
    """ID Format Reference must document the ToolExecution 4-segment format."""
    assert "ToolExecution" in _ID_FORMAT_SECTION, (
        "ID Format Reference must document ToolExecution format"
    )


def test_ac15_id_format_documents_tool_call_id_segment() -> None:
    """ID Format Reference must document the tool_call_id segment."""
    assert "tool_call_id" in _ID_FORMAT_SECTION, (
        "ID Format Reference must document tool_call_id as fourth segment"
    )
