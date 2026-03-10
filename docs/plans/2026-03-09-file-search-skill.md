# File Search Skill Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

> **Quality Review Note:** The quality review loop exhausted after 3 iterations
> without formal approval. The final verdict was **APPROVED** with only cosmetic
> suggestions (inconsistent subsection scoping in Patterns 3/4/5 and a fragile
> case-sensitive branch in test line 98-99). Neither issue causes functional
> failures. Human reviewer should verify these cosmetic items are acceptable
> before merging.

**Goal:** Create a skill file documenting 6 filesystem query patterns for the FileGraphStore flat JSON backend.

**Architecture:** A single Markdown skill file (`SKILL.md`) with YAML frontmatter, schema documentation derived from `file_store.py` and `utils.py`, and 6 copy-pasteable shell-based query patterns using `grep`, `jq`, `find`, and glob. A companion test file validates structure, frontmatter values, all schema sections, all 6 query patterns, and the notes section.

**Tech Stack:** Markdown with YAML frontmatter, pytest with pyyaml for validation.

---

## Context for Implementer

### Repo Layout

All work happens inside the `amplifier-bundle-context-intelligence/` submodule (a git submodule at the repo root). Paths below are relative to that submodule root.

```
amplifier-bundle-context-intelligence/
├── skills/
│   ├── context-intelligence-graph-search/SKILL.md   # ← existing sibling skill (pattern reference)
│   └── context-intelligence-file-search/             # ← CREATE this directory + SKILL.md
├── tests/
│   ├── test_skill_graph_search.py                    # ← existing test (pattern reference)
│   └── test_skill_file_search.py                     # ← CREATE this test file
├── modules/hook-context-intelligence/
│   └── amplifier_module_hook_context_intelligence/
│       ├── file_store.py                             # ← source of truth for directory layout
│       └── utils.py                                  # ← source of truth for ID formats
└── docs/plans/                                       # ← this plan lives here
```

### Key Source Facts (from `file_store.py` and `utils.py`)

These are the ground-truth values the skill must document accurately:

1. **Directory layout:** `{graph_store_root}/{graph_forest_name}/nodes/{node_id}.json` and `.../edges/{edge_id}.json`
2. **Node ID format** (`make_node_id` in `utils.py`): `{session_id}__{event_name}__{timestamp_ms}` — colons in event names become underscores
3. **Edge ID format** (`make_edge_id` in `utils.py`): `{source_id}==[{edge_type}]=={target_id}` — `==[` and `]==` never appear in node IDs
4. **Node JSON** (from `flush()` in `file_store.py`): `{"id": ..., "labels": [...], "properties": {...}}`
5. **Edge JSON** (from `flush()` in `file_store.py`): `{"source": ..., "target": ..., "type": ..., "properties": {...}}`
6. **FileGraphStore does NOT implement QueryableStore** — no `execute_query()`, no `supported_dialects`
7. **Default root:** `~/.amplifier/graphs`, **default forest:** `default`

### Testing Conventions

Tests follow the pattern in `tests/test_skill_graph_search.py`:
- Module-level file read with `try/except OSError`
- `_parse_frontmatter()` helper using `yaml.safe_load`
- `_extract_section()` for `##` headings, `_extract_subsection()` for `###` headings
- Section separators like `# — AC-1: File exists ———————`
- Every `assert` has a human-readable failure message
- Tests are pure (no fixtures, no I/O beyond the module-level read)

### Running Tests

From the submodule root (`amplifier-bundle-context-intelligence/`):

```bash
cd amplifier-bundle-context-intelligence
uv run pytest tests/test_skill_file_search.py -v
```

If `uv` is not available:
```bash
cd amplifier-bundle-context-intelligence
python -m pytest tests/test_skill_file_search.py -v
```

Note: `pyyaml` is a dev dependency declared in `modules/hook-context-intelligence/pyproject.toml`.

---

## Task 1: Write the Failing Test File

**Files:**
- Create: `tests/test_skill_file_search.py`

### Step 1: Write the test file

Create `tests/test_skill_file_search.py` with the complete content below. This file validates the SKILL.md that doesn't exist yet, so all 26 tests should fail.

```python
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


# — AC-1: File exists ——————————————————————————————————————


def test_skill_file_exists() -> None:
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


def test_skill_directory_exists() -> None:
    assert SKILL_DIR.is_dir(), f"Skill directory not found at {SKILL_DIR}"


# — AC-2: YAML frontmatter ———————————————————————————————


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


# — AC-3: Schema section — directory layout ———————————————


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


# — AC-4: Node ID Format ————————————————————————————————


def test_node_id_format_pattern() -> None:
    """Documents the {session_id}__{event_name}__{timestamp_ms} pattern."""
    assert "{session_id}__{event_name}__{timestamp_ms}" in _SCHEMA_SECTION, (
        "Node ID Format should document the pattern"
    )


# — AC-5: Edge ID Format ————————————————————————————————


def test_edge_id_format_pattern() -> None:
    """Documents the {source_id}==[{edge_type}]=={target_id} pattern."""
    assert "{source_id}==[{edge_type}]=={target_id}" in _SCHEMA_SECTION, (
        "Edge ID Format should document the pattern"
    )


# — AC-6: Node JSON structure ———————————————————————————


def test_node_json_structure() -> None:
    """Documents the JSON structure of a node file."""
    for field in ["id", "labels", "properties"]:
        assert field in _SCHEMA_SECTION, (
            f"Node JSON structure should document '{field}' field"
        )


# — AC-7: Edge JSON structure ———————————————————————————


def test_edge_json_structure() -> None:
    """Documents the JSON structure of an edge file."""
    for field in ["source", "target", "type", "properties"]:
        assert field in _EDGE_JSON_SUBSECTION, (
            f"Edge JSON structure should document '{field}' field"
        )


# — AC-8: 6 Query Patterns ——————————————————————————————


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


# — AC-9: Notes section —————————————————————————————————


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


# — AC-10: Code examples contain shell commands ——————————


def test_query_patterns_have_code_blocks() -> None:
    """Query patterns section should have code blocks."""
    code_blocks = re.findall(r"```", _QUERY_SECTION)
    # Each code block has opening and closing ```, so pairs = len / 2
    assert len(code_blocks) >= 12, (
        f"Expected at least 6 code blocks (12 markers), found {len(code_blocks)} markers"
    )
```

### Step 2: Run the tests to verify they fail

Run:
```bash
cd amplifier-bundle-context-intelligence && uv run pytest tests/test_skill_file_search.py -v
```

Expected: All 26 tests FAIL. `test_skill_file_exists` fails with "SKILL.md not found". The remaining tests fail because `_SKILL_CONTENT` is empty (the file doesn't exist yet), causing all assertions against frontmatter, sections, and patterns to fail.

### Step 3: Commit

```
git add tests/test_skill_file_search.py
git commit -m "test: add 26 tests for file search skill (all failing — TDD red phase)"
```

---

## Task 2: Create the SKILL.md File

**Files:**
- Create: `skills/context-intelligence-file-search/SKILL.md`

### Step 1: Create the skill directory and file

Create `skills/context-intelligence-file-search/SKILL.md` with the complete content below. Every value in this file is derived from the source code in `file_store.py` and `utils.py` — do not invent fields or formats.

````markdown
---
name: context-intelligence-file-search
description: Filesystem query patterns for the FileGraphStore flat JSON backend
version: 0.1.0
license: MIT
---

# Context Intelligence File Search (Filesystem Dialect)

This skill applies to the FileGraphStore backend, which persists graph data as
flat JSON files on the local filesystem. Unlike the DuckDB backend, FileGraphStore
does not implement the QueryableStore protocol — queries are performed using
standard shell tools (find, glob, grep, jq) rather than SQL.

---

## Schema

### Directory Layout

```
{graph_store_root}/{graph_forest_name}/nodes/{node_id}.json
{graph_store_root}/{graph_forest_name}/edges/{source}==[{edge_type}]=={target}.json
```

- `graph_store_root` — configurable root directory (default: `~/.amplifier/graphs`)
- `graph_forest_name` — isolated partition name (default: `default`)
- `nodes/` — one JSON file per node, named `{node_id}.json`
- `edges/` — one JSON file per edge, named `{source_id}==[{edge_type}]=={target_id}.json`

### Node ID Format

Node IDs are generated by `make_node_id()` in `utils.py` and are filesystem-safe on all platforms.

**Pattern:** `{session_id}__{event_name}__{timestamp_ms}`

- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` -> `prompt_submit`
- Session nodes use the raw `session_id` (a UUID) as their node_id — no transformation
- Example: `6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000`

### Edge ID Format

Edge IDs are generated by `make_edge_id()` in `utils.py`. Used as filenames in the file-based store.

**Pattern:** `{source_id}==[{edge_type}]=={target_id}`

- `==[` and `]==` are the separators (never appear in node IDs)
- Example: `6afb3613-...==[HAS_STEP]==6afb3613-...__prompt_submit__1741270343000`
- Parse: `source, rest = edge_id.split("==[", 1)` then `edge_type, target = rest.split("]==", 1)`

### Node JSON Structure

Each node file (`nodes/{node_id}.json`) contains:

```json
{
  "id": "6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000",
  "labels": ["PromptStep", "Step"],
  "properties": {
    "event_name": "prompt:submit",
    "occurred_at": "1741270343000",
    "prompt_text": "Help me refactor auth",
    "prompt_preview": "Help me refactor auth"
  }
}
```

- `id` — the node ID (same as the filename without `.json`)
- `labels` — sorted array of label strings
- `properties` — arbitrary key-value map of node data

### Edge JSON Structure

Each edge file (`edges/{source_id}==[{edge_type}]=={target_id}.json`) contains:

```json
{
  "source": "6afb3613-7041-4735-9c0f-c2171452ed18",
  "target": "6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000",
  "type": "HAS_STEP",
  "properties": {
    "seq": 1
  }
}
```

- `source` — the source node ID
- `target` — the target node ID
- `type` — the edge type string
- `properties` — arbitrary key-value map of edge data

---

## Query Patterns

### Pattern 1: Find Nodes by Label

Use grep and jq to find all nodes with a specific label in their `labels` array.

```bash
# Find all PromptStep nodes in a forest
FOREST="$GRAPH_STORE_ROOT/$GRAPH_FOREST_NAME"
grep -rl '"PromptStep"' "$FOREST/nodes/" | while read f; do
  jq '{id, labels, prompt: .properties.prompt_preview}' "$f"
done
```

```bash
# Find all Session nodes
grep -rl '"Session"' "$FOREST/nodes/" --include='*.json'
```

### Pattern 2: Find Edges by Type

Use glob patterns on the `==[TYPE]==` portion of edge filenames to find edges of a specific type.

```bash
# Find all HAS_STEP edges
ls "$FOREST/edges/"*'==[HAS_STEP]=='*.json

# Find all SPAWNED edges (delegation chains)
ls "$FOREST/edges/"*'==[SPAWNED]=='*.json
```

```bash
# Extract edge details with jq
for f in "$FOREST/edges/"*'==[HAS_RUN]=='*.json; do
  jq '{source, target, type}' "$f"
done
```

### Pattern 3: Find Nodes for Specific Session

Use the session ID prefix to locate all nodes belonging to a specific session.

```bash
# List all nodes for a session (session_id is the prefix before the first __)
SESSION_ID="6afb3613-7041-4735-9c0f-c2171452ed18"
ls "$FOREST/nodes/${SESSION_ID}"__*.json

# The session node itself uses the raw UUID as filename
cat "$FOREST/nodes/${SESSION_ID}.json"
```

```bash
# Get all node details for a session
for f in "$FOREST/nodes/${SESSION_ID}"*.json; do
  jq '{id, labels}' "$f"
done
```

### Pattern 4: Traverse a Path

Use a shell pipeline to traverse session→run→step by chaining edge lookups.

```bash
# Given a session, find its runs, then the steps within each run
SESSION_ID="6afb3613-7041-4735-9c0f-c2171452ed18"
FOREST="$GRAPH_STORE_ROOT/$GRAPH_FOREST_NAME"

# Step 1: Find HAS_RUN edges from this session
for run_edge in "$FOREST/edges/${SESSION_ID}==[HAS_RUN]=="*.json; do
  RUN_ID=$(jq -r '.target' "$run_edge")
  echo "Run: $RUN_ID"

  # Step 2: Find HAS_STEP edges from this run
  for step_edge in "$FOREST/edges/${RUN_ID}==[HAS_STEP]=="*.json; do
    STEP_ID=$(jq -r '.target' "$step_edge")
    echo "  Step: $STEP_ID"

    # Step 3: Read the step node
    jq '.properties.prompt_preview // .properties.event_name' \
      "$FOREST/nodes/${STEP_ID}.json"
  done
done
```

### Pattern 5: Cross-Forest Queries

Navigate up to the `graph_store_root` directory and glob across all forest subdirectories.

```bash
# List all forests
ls -d "$GRAPH_STORE_ROOT"/*/

# Find PromptStep nodes across ALL forests
grep -rl '"PromptStep"' "$GRAPH_STORE_ROOT"/*/nodes/ --include='*.json'
```

```bash
# Count nodes per forest
for forest_dir in "$GRAPH_STORE_ROOT"/*/; do
  FOREST_NAME=$(basename "$forest_dir")
  NODE_COUNT=$(ls "$forest_dir/nodes/"*.json 2>/dev/null | wc -l)
  echo "$FOREST_NAME: $NODE_COUNT nodes"
done
```

### Pattern 6: Full-Text Search Across Properties

Use grep and jq to search across all node and edge properties for arbitrary text.

```bash
# Search for "authentication" in any node property value
grep -rl 'authentication' "$FOREST/nodes/" --include='*.json' | while read f; do
  jq '{id, labels, match: .properties | to_entries[] | select(.value | tostring | test("authentication"; "i"))}' "$f"
done
```

```bash
# Search for a term across both nodes and edges
TERM="refactor"
echo "=== Nodes ==="
grep -rl "$TERM" "$FOREST/nodes/" --include='*.json'
echo "=== Edges ==="
grep -rl "$TERM" "$FOREST/edges/" --include='*.json'
```

---

## Notes

### Path resolution from config

The `graph_store_root` path is resolved from the store configuration passed to
`create_graph_store()` in `store_factory.py`. The default is `~/.amplifier/graphs`.
The `graph_forest_name` is an isolated partition within the root (default: `default`).

Full resolved path: `~/.amplifier/graphs/default/nodes/` and `.../edges/`.

Configuration example:

```python
store_config = {
    "type": "file",
    "graph_forest_name": "my-project",
    "config": {
        "graph_store_root": "~/.amplifier/graphs"
    }
}
```

### FileGraphStore does NOT implement QueryableStore

FileGraphStore is a simple persistence layer. It does NOT implement the
QueryableStore protocol — there is no `execute_query()` method, no
`supported_dialects`, and no SQL or PGQ capability. All queries must be
performed externally using filesystem tools (find, grep, jq, shell globbing)
as shown in the query patterns above.

For SQL/PGQ query capability, use the DuckDB backend and the
`context-intelligence-graph-search` skill instead.
````

### Step 2: Run the tests to verify they pass

Run:
```bash
cd amplifier-bundle-context-intelligence && uv run pytest tests/test_skill_file_search.py -v
```

Expected: All 26 tests PASS (0 failures).

### Step 3: Run code quality checks on the test file

Run:
```bash
cd amplifier-bundle-context-intelligence && uv run python -m ruff check tests/test_skill_file_search.py && uv run python -m ruff format --check tests/test_skill_file_search.py
```

Expected: No issues.

### Step 4: Commit

```
git add skills/context-intelligence-file-search/SKILL.md tests/test_skill_file_search.py
git commit -m "docs(skill): add file search skill with 6 query patterns"
```

This is the commit message specified in the acceptance criteria.

---

## Acceptance Criteria Checklist

| # | Criterion | Verified By |
|---|-----------|------------|
| AC-1 | File exists at `skills/context-intelligence-file-search/SKILL.md` | `test_skill_file_exists`, `test_skill_directory_exists` |
| AC-2 | YAML frontmatter: name, description, version=0.1.0, license=MIT | `test_frontmatter_name`, `test_frontmatter_description`, `test_frontmatter_version`, `test_frontmatter_license` |
| AC-3 | Schema section with directory layout | `test_schema_section_exists`, `test_schema_directory_layout`, `test_schema_node_file_pattern`, `test_schema_edge_file_pattern` |
| AC-4 | Node ID format documented | `test_node_id_format_pattern` |
| AC-5 | Edge ID format documented | `test_edge_id_format_pattern` |
| AC-6 | Node JSON structure | `test_node_json_structure` |
| AC-7 | Edge JSON structure | `test_edge_json_structure` |
| AC-8 | 6 query patterns | `test_query_patterns_section_exists`, `test_six_query_patterns_exist`, plus individual pattern tests |
| AC-9 | Notes section (path resolution, not QueryableStore) | `test_notes_section_exists`, `test_notes_path_resolution`, `test_notes_not_queryablestore` |
| AC-10 | Code blocks in query patterns | `test_query_patterns_have_code_blocks` |
| — | Commit message matches spec | Manual: `docs(skill): add file search skill with 6 query patterns` |
