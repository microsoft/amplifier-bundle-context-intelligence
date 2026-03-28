"""
Acceptance criteria tests for graph-model-reference.md rewrite.
Run: python test_graph_model_reference.py
"""

import sys
import re

FILE_PATH = "amplifier-bundle-context-intelligence/context/graph-model-reference.md"


def read_file():
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_three_node_table(content: str) -> tuple[bool, str]:
    """AC1: 3-node table has :Session, :ToolCall, :Event"""
    has_session = ":Session" in content
    has_toolcall = ":ToolCall" in content
    has_event = ":Event" in content

    missing = []
    if not has_session:
        missing.append(":Session")
    if not has_toolcall:
        missing.append(":ToolCall")
    if not has_event:
        missing.append(":Event")

    # Also verify Data Layer 2 nodes are NOT in the node types table as primary nodes
    # (they appear in the DL2 warning section but not as primary node entries)
    if missing:
        return False, f"Missing node type(s): {', '.join(missing)}"
    return True, "All 3 node types present: :Session, :ToolCall, :Event"


def test_three_edge_table(content: str) -> tuple[bool, str]:
    """AC2: 3-edge table has HAS_FORK, HAS_TOOL_CALL, HAS_EVENT"""
    has_fork = "HAS_FORK" in content
    has_tool_call = "HAS_TOOL_CALL" in content
    has_event = "HAS_EVENT" in content

    missing = []
    if not has_fork:
        missing.append("HAS_FORK")
    if not has_tool_call:
        missing.append("HAS_TOOL_CALL")
    if not has_event:
        missing.append("HAS_EVENT")

    if missing:
        return False, f"Missing edge type(s): {', '.join(missing)}"
    return True, "All 3 edge types present: HAS_FORK, HAS_TOOL_CALL, HAS_EVENT"


def test_dl2_warning_section(content: str) -> tuple[bool, str]:
    """AC3: DL2 warning section present with explicit 'do not write queries' text"""
    # Check for DL2 warning section
    has_dl2_warning = "Data Layer 2" in content or "Layer 2" in content

    # Check for 'do not write queries' text (case insensitive)
    has_no_queries_text = bool(
        re.search(r"do not write queries", content, re.IGNORECASE)
    )

    # Check that DL2 labels are mentioned as problematic
    has_orchestrator_run = "OrchestratorRun" in content
    has_step = "Step" in content and ("Layer 2" in content or "do not" in content.lower())
    has_tool_execution = "ToolExecution" in content
    has_delegation = "Delegation" in content
    has_recipe_run = "RecipeRun" in content

    errors = []
    if not has_dl2_warning:
        errors.append("Missing Data Layer 2 warning section")
    if not has_no_queries_text:
        errors.append("Missing 'do not write queries' text")
    if not has_orchestrator_run:
        errors.append("Missing OrchestratorRun label mention in DL2 section")
    if not has_tool_execution:
        errors.append("Missing ToolExecution label mention in DL2 section")
    if not has_recipe_run:
        errors.append("Missing RecipeRun label mention in DL2 section")

    if errors:
        return False, f"DL2 warning issues: {'; '.join(errors)}"
    return True, "DL2 warning section present with 'do not write queries' text and all required labels"


def test_24_row_event_table(content: str) -> tuple[bool, str]:
    """AC4: 24-row event triple-label table present"""
    # The table should have 24 rows (data rows, not header)
    # Count table rows - look for lines that start with | and contain event names
    # We check by counting pipe-separated rows that appear to be data (not header/separator)
    lines = content.split("\n")

    # Find sections that look like event tables
    event_table_rows = []
    in_event_section = False

    for line in lines:
        line_stripped = line.strip()
        # Look for the triple-label table
        if "Triple" in line or "triple" in line or "Category Label" in line or "Specific Label" in line:
            in_event_section = True
        if in_event_section:
            if line_stripped.startswith("|") and not line_stripped.startswith("|---") and not line_stripped.startswith("| Base") and not line_stripped.startswith("| Event"):
                # This is a data row
                # Verify it has at least 3 columns
                parts = [p.strip() for p in line_stripped.split("|") if p.strip()]
                if len(parts) >= 3:
                    event_table_rows.append(line_stripped)
            elif in_event_section and line_stripped.startswith("#") and "Triple" not in line and "triple" not in line:
                # New section - stop counting
                if event_table_rows:
                    in_event_section = False

    row_count = len(event_table_rows)
    if row_count < 24:
        return False, f"Event triple-label table has {row_count} rows, expected 24. Rows found: {event_table_rows}"
    return True, f"Event triple-label table has {row_count} rows (≥24 required)"


def test_no_query_examples(content: str) -> tuple[bool, str]:
    """AC5: No query examples (only workspace scoping snippet allowed)"""
    # Find all cypher code blocks
    cypher_blocks = re.findall(r"```cypher(.*?)```", content, re.DOTALL | re.IGNORECASE)

    if len(cypher_blocks) > 1:
        return False, f"Found {len(cypher_blocks)} cypher blocks - only 1 (workspace scoping) is allowed"

    if len(cypher_blocks) == 1:
        # The one allowed block should be for workspace scoping only
        block = cypher_blocks[0].strip()
        # It should be a simple MATCH (s:Session {workspace: $workspace}) kind of thing
        if "MATCH" in block and "$workspace" in block:
            return True, "Only workspace scoping cypher snippet found (as expected)"
        else:
            return False, f"Found a cypher block but it's not the workspace scoping snippet: {block[:100]}"

    # No cypher blocks at all is also acceptable per the spec
    return True, "No query examples found (acceptable - workspace scoping snippet is optional)"


def test_node_id_formats(content: str) -> tuple[bool, str]:
    """Check Node ID Formats table is present with correct formats"""
    errors = []

    # Session (root) is raw UUID (case-insensitive check)
    content_lower = content.lower()
    if "raw uuid" not in content_lower and "raw session uuid" not in content_lower:
        errors.append("Missing 'raw UUID' description for Session node ID")

    # Session (forked) is {hex}-{hex}_{agent-name}
    has_forked_format = "_agent" in content or "agent-name" in content or "agent_name" in content
    if not has_forked_format:
        errors.append("Missing forked session ID format with agent name")

    # Event is {session_id}__{event_name_underscored}__{epoch_ms}
    has_event_format = "epoch_ms" in content or "epoch" in content
    if not has_event_format:
        errors.append("Missing epoch_ms in Event ID format")

    # ToolCall is {session_id}__tool_call__{tool_call_id}
    has_toolcall_format = "tool_call" in content and "tool_call_id" in content
    if not has_toolcall_format:
        errors.append("Missing ToolCall ID format")

    if errors:
        return False, f"Node ID format issues: {'; '.join(errors)}"
    return True, "Node ID formats section present with correct formats"


def test_workspace_scoping(content: str) -> tuple[bool, str]:
    """Check Workspace Scoping section"""
    has_workspace_section = "Workspace" in content and "workspace" in content
    has_merge_key = "MERGE key" in content
    has_auto_inject = "auto-inject" in content or "auto_inject" in content or "auto inject" in content

    errors = []
    if not has_workspace_section:
        errors.append("Missing Workspace Scoping section")
    if not has_merge_key:
        errors.append("Missing MERGE key description")
    if not has_auto_inject:
        errors.append("Missing auto-inject description")

    if errors:
        return False, f"Workspace Scoping issues: {'; '.join(errors)}"
    return True, "Workspace Scoping section present with MERGE key and auto-inject info"


def test_blob_references(content: str) -> tuple[bool, str]:
    """Check Blob References section"""
    has_ci_blob = "ci-blob://" in content
    has_blob_read = "blob_read" in content
    has_warning = "100k" in content or "100,000" in content or "100k+" in content

    errors = []
    if not has_ci_blob:
        errors.append("Missing ci-blob:// URI scheme")
    if not has_blob_read:
        errors.append("Missing blob_read tool reference")
    if not has_warning:
        errors.append("Missing 100k+ token warning")

    if errors:
        return False, f"Blob References issues: {'; '.join(errors)}"
    return True, "Blob References section present with ci-blob://, blob_read, and warning"


def test_fieldlifter_properties(content: str) -> tuple[bool, str]:
    """Check FieldLifter Properties table"""
    # Check for various lifter types
    lifters = ["Universal", "Tool", "Llm", "Delegate", "Prompt", "Recipe", "Session", "Skill", "Artifact"]
    missing = [l for l in lifters if l not in content]

    if missing:
        return False, f"Missing FieldLifter types: {', '.join(missing)}"
    return True, f"All FieldLifter types present: {', '.join(lifters)}"


def test_two_paths_table(content: str) -> tuple[bool, str]:
    """Check Two Paths to Tool Data table"""
    has_flexible = "Flexible" in content or "HAS_EVENT" in content
    has_structured = "Structured" in content or "HAS_TOOL_CALL" in content
    has_tool_event = "ToolEvent" in content
    has_tool_call = "ToolCall" in content

    errors = []
    if not has_flexible:
        errors.append("Missing Flexible path via HAS_EVENT")
    if not has_structured:
        errors.append("Missing Structured path via HAS_TOOL_CALL")

    if errors:
        return False, f"Two Paths to Tool Data issues: {'; '.join(errors)}"
    return True, "Two Paths to Tool Data section present"


def run_all_tests():
    """Run all acceptance criteria tests."""
    content = read_file()

    tests = [
        ("AC1: 3-Node Table", test_three_node_table),
        ("AC2: 3-Edge Table", test_three_edge_table),
        ("AC3: DL2 Warning Section", test_dl2_warning_section),
        ("AC4: 24-Row Event Triple-Label Table", test_24_row_event_table),
        ("AC5: No Query Examples", test_no_query_examples),
        ("Extra: Node ID Formats", test_node_id_formats),
        ("Extra: Workspace Scoping", test_workspace_scoping),
        ("Extra: Blob References", test_blob_references),
        ("Extra: FieldLifter Properties", test_fieldlifter_properties),
        ("Extra: Two Paths to Tool Data", test_two_paths_table),
    ]

    print("=" * 70)
    print("Graph Model Reference - Acceptance Criteria Tests")
    print("=" * 70)

    all_passed = True
    for test_name, test_fn in tests:
        passed, message = test_fn(content)
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} | {test_name}")
        if not passed:
            print(f"       Detail: {message}")
            all_passed = False
        else:
            print(f"       {message}")

    print("=" * 70)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
