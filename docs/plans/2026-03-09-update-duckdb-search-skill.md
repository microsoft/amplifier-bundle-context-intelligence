# Update DuckDB Search Skill Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

> **Quality Review Note:** The quality review loop exhausted after 3 iterations.
> The final verdict was **APPROVED** (67/67 tests pass, all python_check clean),
> but the loop mechanism did not register the approval before hitting its max
> iteration count. The human reviewer should verify the implementation during the
> approval gate. The last quality review found zero critical or important issues.

**Goal:** Update the DuckDB search skill documentation (`SKILL.md`) to reflect the new `graph_forest_name` column on all three table schemas and add forest-scoped query examples.

**Architecture:** This is a documentation-only task. The `SKILL.md` file is the authoritative reference for agents querying the DuckDB-backed graph store. It must stay in sync with the DDL in `duckdb_store.py`. The skill file uses YAML frontmatter for metadata, markdown tables for schemas, and fenced code blocks for query examples. Tests validate the document structure by parsing the markdown.

**Tech Stack:** Markdown (SKILL.md), Python (pytest for doc validation tests), YAML frontmatter.

**Working directory for all commands:**
```
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence
```

---

### Task 1: Write Failing Tests for Schema Changes (AC-13)

**Files:**
- Modify: `tests/test_skill_graph_search.py`

**Step 1: Write the failing tests for `graph_forest_name` in all three tables**

Add a helper function and 9 new tests after the `# AC-12` comment (around line 523) in `tests/test_skill_graph_search.py`:

```python
# — AC-13: graph_forest_name column in all three tables ——————————


def _extract_table_section(content: str, table_heading: str) -> str:
    """Extract the content under a ### `table_name` heading up to the next ### or ##."""
    pattern = rf"^### `{re.escape(table_heading)}`\s*\n(.*?)(?=^###? |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def test_nodes_table_has_graph_forest_name() -> None:
    """nodes table schema includes graph_forest_name column."""
    nodes_section = _extract_table_section(_SKILL_CONTENT, "nodes")
    assert "graph_forest_name" in nodes_section, (
        "nodes table should have graph_forest_name column"
    )


def test_nodes_graph_forest_name_type_and_constraints() -> None:
    """nodes graph_forest_name has correct type and constraints."""
    nodes_section = _extract_table_section(_SKILL_CONTENT, "nodes")
    assert "VARCHAR" in nodes_section, "graph_forest_name should be VARCHAR"
    assert re.search(
        r"graph_forest_name.*NOT NULL.*DEFAULT.*'default'",
        nodes_section,
        re.DOTALL,
    ), "graph_forest_name should be NOT NULL DEFAULT 'default'"


def test_nodes_graph_forest_name_position() -> None:
    """graph_forest_name appears after node_id and before session_id in nodes."""
    nodes_section = _extract_table_section(_SKILL_CONTENT, "nodes")
    node_id_pos = nodes_section.find("node_id")
    forest_pos = nodes_section.find("graph_forest_name")
    session_id_pos = nodes_section.find("session_id")
    assert node_id_pos < forest_pos < session_id_pos, (
        "graph_forest_name must be after node_id and before session_id in nodes"
    )


def test_edges_table_has_graph_forest_name() -> None:
    """edges table schema includes graph_forest_name column."""
    edges_section = _extract_table_section(_SKILL_CONTENT, "edges")
    assert "graph_forest_name" in edges_section, (
        "edges table should have graph_forest_name column"
    )


def test_edges_graph_forest_name_type_and_constraints() -> None:
    """edges graph_forest_name has correct type and constraints."""
    edges_section = _extract_table_section(_SKILL_CONTENT, "edges")
    assert re.search(
        r"graph_forest_name.*NOT NULL.*DEFAULT.*'default'",
        edges_section,
        re.DOTALL,
    ), "edges graph_forest_name should be NOT NULL DEFAULT 'default'"


def test_edges_graph_forest_name_position() -> None:
    """graph_forest_name appears after edge_type and before session_id in edges."""
    edges_section = _extract_table_section(_SKILL_CONTENT, "edges")
    edge_type_pos = edges_section.find("edge_type")
    forest_pos = edges_section.find("graph_forest_name")
    session_id_pos = edges_section.find("session_id")
    assert edge_type_pos < forest_pos < session_id_pos, (
        "graph_forest_name must be after edge_type and before session_id in edges"
    )


def test_search_index_table_has_graph_forest_name() -> None:
    """search_index table schema includes graph_forest_name column."""
    search_section = _extract_table_section(_SKILL_CONTENT, "search_index")
    assert "graph_forest_name" in search_section, (
        "search_index table should have graph_forest_name column"
    )


def test_search_index_graph_forest_name_type_and_constraints() -> None:
    """search_index graph_forest_name has correct type and constraints."""
    search_section = _extract_table_section(_SKILL_CONTENT, "search_index")
    assert re.search(
        r"graph_forest_name.*NOT NULL.*DEFAULT.*'default'",
        search_section,
        re.DOTALL,
    ), "search_index graph_forest_name should be NOT NULL DEFAULT 'default'"


def test_search_index_graph_forest_name_position() -> None:
    """graph_forest_name appears after node_id and before session_id in search_index."""
    search_section = _extract_table_section(_SKILL_CONTENT, "search_index")
    node_id_pos = search_section.find("node_id")
    forest_pos = search_section.find("graph_forest_name")
    session_id_pos = search_section.find("session_id")
    assert node_id_pos < forest_pos < session_id_pos, (
        "graph_forest_name must be after node_id and before session_id in search_index"
    )
```

**Step 2: Run the tests to verify they fail**

Run:
```bash
pytest tests/test_skill_graph_search.py::test_nodes_table_has_graph_forest_name -v
```
Expected: FAIL — `graph_forest_name` not yet in the nodes table documentation.

---

### Task 2: Write Failing Tests for Forest-Scoped Queries Section (AC-14)

**Files:**
- Modify: `tests/test_skill_graph_search.py`

**Step 1: Write the failing tests for the Forest-Scoped Queries section**

Add a pre-extracted section variable and 8 new tests after the AC-13 tests:

```python
# — AC-14: Forest-Scoped Queries section ————————————————————————


_FOREST_SECTION: str = (
    _extract_section(_SKILL_CONTENT, "Forest-Scoped Queries") if _SKILL_CONTENT else ""
)


def test_forest_scoped_queries_section_exists() -> None:
    """Forest-Scoped Queries section exists."""
    assert "## Forest-Scoped Queries" in _SKILL_CONTENT, (
        "SKILL.md should have a '## Forest-Scoped Queries' section"
    )


def test_forest_scoped_queries_after_query_patterns() -> None:
    """Forest-Scoped Queries section comes after Query Patterns."""
    query_pos = _SKILL_CONTENT.find("## Query Patterns")
    forest_pos = _SKILL_CONTENT.find("## Forest-Scoped Queries")
    notes_pos = _SKILL_CONTENT.find("## Notes")
    assert query_pos < forest_pos < notes_pos, (
        "Forest-Scoped Queries must appear after Query Patterns and before Notes"
    )


def test_forest_default_query_example() -> None:
    """Example 1: default query (no param, scopes to own forest)."""
    assert re.search(
        r"default|no param|own forest",
        _FOREST_SECTION,
        re.IGNORECASE,
    ), "Should have a default query example (scopes to own forest)"


def test_forest_explicit_query_example() -> None:
    """Example 2: explicit forest query with graph_forest_name=\"other-project\"."""
    assert 'graph_forest_name="other-project"' in _FOREST_SECTION, (
        'Should have explicit forest query example with graph_forest_name="other-project"'
    )


def test_forest_cross_forest_query_example() -> None:
    """Example 3: cross-forest query with graph_forest_name=\"*\"."""
    assert 'graph_forest_name="*"' in _FOREST_SECTION, (
        'Should have cross-forest query example with graph_forest_name="*"'
    )


def test_forest_cte_wrapper_explanation() -> None:
    """Explains that forest filter is injected automatically as CTE wrappers."""
    assert re.search(r"CTE.*wrapper", _FOREST_SECTION, re.IGNORECASE), (
        "Should explain that forest filter is injected as CTE wrappers"
    )


def test_forest_raw_sql_example() -> None:
    """Shows raw SQL example with graph_forest_name='*' and manual WHERE clause."""
    assert "WHERE" in _FOREST_SECTION, "Should show raw SQL with manual WHERE clause"
    assert "graph_forest_name" in _FOREST_SECTION, (
        "Raw SQL example should reference graph_forest_name column"
    )


def test_forest_section_has_three_code_examples() -> None:
    """Forest-Scoped Queries section has at least 3 code blocks."""
    code_blocks = re.findall(r"```", _FOREST_SECTION)
    # Each code block has opening and closing ```, so pairs = len / 2
    assert len(code_blocks) >= 6, (
        f"Expected at least 3 code blocks (6 markers), found {len(code_blocks)} markers"
    )
```

**Step 2: Run the tests to verify they fail**

Run:
```bash
pytest tests/test_skill_graph_search.py::test_forest_scoped_queries_section_exists -v
```
Expected: FAIL — no Forest-Scoped Queries section yet.

---

### Task 3: Write Failing Test for Version Bump

**Files:**
- Modify: `tests/test_skill_graph_search.py`

**Step 1: Verify the existing version test asserts 0.3.0**

The existing test at line 84 should already assert:

```python
def test_frontmatter_version() -> None:
    assert _FRONTMATTER["version"] == "0.3.0"
```

If it currently asserts `"0.2.0"`, update it to `"0.3.0"`.

**Step 2: Run the version test to verify it fails**

Run:
```bash
pytest tests/test_skill_graph_search.py::test_frontmatter_version -v
```
Expected: FAIL — version is still `0.2.0` in the SKILL.md frontmatter.

---

### Task 4: Update SKILL.md Schema Tables

**Files:**
- Modify: `skills/context-intelligence-graph-search/SKILL.md`

**Step 1: Add `graph_forest_name` to the `nodes` table**

In the `### \`nodes\`` section, insert a new row after `node_id` and before `session_id`:

```markdown
### `nodes`

| Column | Type | Constraints |
|--------|------|-------------|
| `node_id` | `VARCHAR` | `PRIMARY KEY` |
| `graph_forest_name` | `VARCHAR` | `NOT NULL DEFAULT 'default'` |
| `session_id` | `VARCHAR` | `DEFAULT ''` |
| `labels` | `VARCHAR[]` | |
| `occurred_at` | `TIMESTAMP` | |
| `properties` | `JSON` | |
```

**Step 2: Add `graph_forest_name` to the `edges` table**

In the `### \`edges\`` section, insert a new row after `edge_type` and before `session_id`:

```markdown
### `edges`

| Column | Type | Constraints |
|--------|------|-------------|
| `source` | `VARCHAR` | |
| `target` | `VARCHAR` | |
| `edge_type` | `VARCHAR` | |
| `graph_forest_name` | `VARCHAR` | `NOT NULL DEFAULT 'default'` |
| `session_id` | `VARCHAR` | `DEFAULT ''` |
| `occurred_at` | `TIMESTAMP` | |
| `seq` | `INTEGER` | |
| `properties` | `JSON` | |
```

**Step 3: Add `graph_forest_name` to the `search_index` table**

In the `### \`search_index\`` section, insert a new row after `node_id` and before `session_id`:

```markdown
### `search_index`

| Column | Type | Constraints |
|--------|------|-------------|
| `node_id` | `VARCHAR` | `NOT NULL` |
| `graph_forest_name` | `VARCHAR` | `NOT NULL DEFAULT 'default'` |
| `session_id` | `VARCHAR` | `NOT NULL` |
| `field_name` | `VARCHAR` | `NOT NULL` |
| `content` | `VARCHAR` | `NOT NULL` |
| `occurred_at` | `TIMESTAMP` | |
```

**Step 4: Run schema tests to verify they pass**

Run:
```bash
pytest tests/test_skill_graph_search.py -k "graph_forest_name" -v
```
Expected: All 9 AC-13 tests PASS.

---

### Task 5: Add Forest-Scoped Queries Section

**Files:**
- Modify: `skills/context-intelligence-graph-search/SKILL.md`

**Step 1: Add the Forest-Scoped Queries section**

Insert the following section after the `---` that closes `## Query Patterns` (after line 257 in the original file) and before `## Notes`:

```markdown
## Forest-Scoped Queries

Every query is scoped to a **graph forest** — an isolated partition of the
graph identified by the `graph_forest_name` column present on `nodes`,
`edges`, and `search_index`. The forest filter is injected automatically
as CTE wrappers around the raw tables before your SQL executes, so most
queries need no changes.

### 1. Default query (own forest)

When no `graph_forest_name` parameter is supplied, queries are automatically
scoped to the caller's own forest (the default forest):

```sql
-- No forest parameter needed — CTE wrappers scope to own forest automatically
SELECT node_id, labels, occurred_at
  FROM nodes
 WHERE properties->>'status' = 'completed';
```

### 2. Explicit forest query

Pass `graph_forest_name="other-project"` to query a specific forest:

```sql
-- graph_forest_name="other-project"
-- CTE wrappers inject: WHERE graph_forest_name = 'other-project'
SELECT node_id, labels, occurred_at
  FROM nodes
 WHERE properties->>'status' = 'completed';
```

### 3. Cross-forest query

Pass `graph_forest_name="*"` to query across all forests:

```sql
-- graph_forest_name="*"
-- CTE wrappers are omitted — full table access
SELECT n.graph_forest_name, n.node_id, n.labels
  FROM nodes n
 ORDER BY n.graph_forest_name, n.occurred_at DESC;
```

### How CTE wrappers work

When `graph_forest_name` is set to a specific value (including the default),
the query engine wraps each table reference in a CTE that filters by forest:

```sql
-- Raw SQL equivalent of a specific-forest query (e.g. graph_forest_name="my-project")
WITH nodes AS (
    SELECT * FROM nodes WHERE graph_forest_name = 'my-project'
),
edges AS (
    SELECT * FROM edges WHERE graph_forest_name = 'my-project'
),
search_index AS (
    SELECT * FROM search_index WHERE graph_forest_name = 'my-project'
)
SELECT node_id, labels
  FROM nodes
 WHERE properties->>'status' = 'completed';
```

When `graph_forest_name="*"`, no CTE wrappers are injected and queries see
all forests. Use a manual `WHERE` clause to filter as needed:

```sql
-- graph_forest_name="*" — no automatic filtering
SELECT n.node_id, n.graph_forest_name, n.labels
  FROM nodes n
 WHERE n.graph_forest_name IN ('project-a', 'project-b')
 ORDER BY n.occurred_at DESC;
```
```

**Step 2: Run the forest query tests to verify they pass**

Run:
```bash
pytest tests/test_skill_graph_search.py -k "forest" -v
```
Expected: All 8 AC-14 tests PASS.

---

### Task 6: Bump Version and Final Verification

**Files:**
- Modify: `skills/context-intelligence-graph-search/SKILL.md`

**Step 1: Update the YAML frontmatter version**

Change line 4 of `skills/context-intelligence-graph-search/SKILL.md` from:

```yaml
version: 0.2.0
```

to:

```yaml
version: 0.3.0
```

**Step 2: Run the version test**

Run:
```bash
pytest tests/test_skill_graph_search.py::test_frontmatter_version -v
```
Expected: PASS

**Step 3: Run the full test suite**

Run:
```bash
pytest tests/test_skill_graph_search.py -v
```
Expected: 67 passed (0.07s)

**Step 4: Run code quality checks on the test file**

Run:
```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence && ruff check tests/test_skill_graph_search.py && ruff format --check tests/test_skill_graph_search.py
```
Expected: No issues.

**Step 5: Commit**

```bash
git add skills/context-intelligence-graph-search/SKILL.md tests/test_skill_graph_search.py && git commit -m "docs(skill): update DuckDB skill with graph_forest_name column and query examples"
```
