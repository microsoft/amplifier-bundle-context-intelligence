# Search Index & SQL/PGQ Skill Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Add a `search_index` table to `DuckDBGraphStore` with auto-population during `upsert_node`, create a SQL/PGQ skill that teaches LLMs how to query the graph, and register the skill in the behavior YAML.

**Architecture:** The search index is a DuckDB-internal concern — it lives entirely inside `DuckDBGraphStore`, not on the `GraphStore` protocol. When `upsert_node()` receives a node whose labels and properties match known indexable patterns (e.g., `PromptStep` with `prompt_text`), it automatically buffers a search index entry. The `flush()` method writes search entries in the same transaction as nodes and edges. A separate skill file teaches agents the full schema and query patterns.

**Tech Stack:** Python 3.11+, DuckDB, pytest, YAML, Markdown (Agent Skills spec)

**Design doc:** `docs/plans/2026-03-06-search-index-and-graph-query-skill-design.md`

---

## Orientation: What You Need to Know

**Project structure:**
```
amplifier-bundle-context-intelligence/          ← bundle root (git submodule)
├── bundle.md                                   ← bundle manifest
├── behaviors/context-intelligence.yaml         ← behavior YAML (hooks + tools)
├── skills/                                     ← NEW directory (Task 4)
│   └── context-intelligence-graph-search/
│       └── SKILL.md
├── docs/plans/                                 ← design docs
└── modules/hook-context-intelligence/          ← Python module
    ├── pyproject.toml
    ├── amplifier_module_hook_context_intelligence/
    │   ├── __init__.py
    │   ├── graph_store.py                      ← GraphStore protocol (DO NOT MODIFY)
    │   ├── duckdb_store.py                     ← DuckDBGraphStore (Tasks 1-3)
    │   ├── services.py                         ← HookStateService + GraphState
    │   ├── store_factory.py
    │   ├── utils.py
    │   └── handlers/
    │       ├── session.py
    │       └── orchestrator_run.py
    └── tests/
        ├── conftest.py                         ← shared fixtures
        ├── test_duckdb_store.py                ← DuckDB store tests (Tasks 1-2)
        ├── test_bundle.py                      ← bundle validation tests (Task 5)
        └── ...
```

**Key conventions:**
- `asyncio_mode = "auto"` in `pyproject.toml` — do NOT add `@pytest.mark.asyncio` to tests
- Test classes group related tests: `class TestSomething:`
- DuckDB unit tests create `DuckDBGraphStore(":memory:")` directly — no factory
- Imports follow: `from __future__ import annotations` → stdlib → third-party → `amplifier_core` → relative
- The `conftest.py` fixture `services()` returns a `HookStateService(raw_config={})` which uses `GraphState` (in-memory), NOT `DuckDBGraphStore`
- `flush()` snapshots buffers, clears them, then writes inside a `BEGIN/COMMIT` transaction with rollback-and-restore on failure

**Scope boundaries:**
- **IN:** search_index table, buffer, flush, auto-populate from upsert_node, standing rule docstring, skill file, skill registration
- **OUT:** FTS index rebuild (`PRAGMA create_fts_index`), GraphStore protocol changes, other handlers writing to search_index, DuckPGQ `CREATE PROPERTY GRAPH`

---

## Task 1: Add search_index Table, Buffer, and Flush

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py`
- Modify: `modules/hook-context-intelligence/tests/test_duckdb_store.py`

### Step 1: Write failing tests for search_index table creation and buffer

Open `modules/hook-context-intelligence/tests/test_duckdb_store.py` and add these test classes at the end of the file, after the existing `TestPersistence` class:

```python
# ---------------------------------------------------------------------------
# TestSearchIndexTable
# ---------------------------------------------------------------------------
class TestSearchIndexTable:
    """search_index table must exist after DuckDBGraphStore init."""

    def test_search_index_table_created_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        result = store._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = 'search_index'"
        ).fetchall()
        table_names = [row[0] for row in result]
        assert "search_index" in table_names

    def test_search_index_has_expected_columns(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        result = store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'search_index' ORDER BY ordinal_position"
        ).fetchall()
        columns = [row[0] for row in result]
        assert columns == ["node_id", "session_id", "field_name", "content", "occurred_at"]

    def test_search_buffer_exists_and_empty_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        assert hasattr(store, "_search_buffer")
        assert store._search_buffer == []


# ---------------------------------------------------------------------------
# TestSearchIndexFlush
# ---------------------------------------------------------------------------
class TestSearchIndexFlush:
    """flush() must persist search_index buffer entries to DuckDB."""

    @pytest.fixture
    def store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        return DuckDBGraphStore()

    async def test_flush_writes_search_entries_to_duckdb(self, store):
        store._search_buffer.append({
            "node_id": "n1",
            "session_id": "s1",
            "field_name": "prompt_text",
            "content": "hello world",
            "occurred_at": "2026-03-06T00:00:00Z",
        })
        # flush requires at least one node/edge or search entry to do work
        await store.upsert_node("n1", {"PromptStep"}, {"session_id": "s1"})
        await store.flush()
        rows = store._conn.execute(
            "SELECT node_id, session_id, field_name, content FROM search_index"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0] == ("n1", "s1", "prompt_text", "hello world")

    async def test_flush_clears_search_buffer(self, store):
        store._search_buffer.append({
            "node_id": "n1",
            "session_id": "s1",
            "field_name": "prompt_text",
            "content": "hello",
            "occurred_at": "2026-03-06T00:00:00Z",
        })
        await store.upsert_node("n1", {"PromptStep"}, {"session_id": "s1"})
        await store.flush()
        assert store._search_buffer == []

    async def test_flush_empty_search_buffer_is_noop(self, store):
        await store.flush()
        rows = store._conn.execute("SELECT * FROM search_index").fetchall()
        assert rows == []

    async def test_flush_writes_multiple_search_entries(self, store):
        store._search_buffer.append({
            "node_id": "n1",
            "session_id": "s1",
            "field_name": "prompt_text",
            "content": "first prompt",
            "occurred_at": "2026-03-06T00:00:00Z",
        })
        store._search_buffer.append({
            "node_id": "n2",
            "session_id": "s1",
            "field_name": "prompt_text",
            "content": "second prompt",
            "occurred_at": "2026-03-06T00:01:00Z",
        })
        await store.upsert_node("n1", {"PromptStep"}, {"session_id": "s1"})
        await store.upsert_node("n2", {"PromptStep"}, {"session_id": "s1"})
        await store.flush()
        rows = store._conn.execute("SELECT node_id FROM search_index ORDER BY node_id").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "n1"
        assert rows[1][0] == "n2"

    async def test_flush_restores_search_buffer_on_failure(self, store):
        """If flush fails, search_buffer must be restored for retry."""
        store._search_buffer.append({
            "node_id": "n1",
            "session_id": "s1",
            "field_name": "prompt_text",
            "content": "hello",
            "occurred_at": "2026-03-06T00:00:00Z",
        })
        await store.upsert_node("n1", {"PromptStep"}, {"session_id": "s1"})
        # Close the connection to force a failure
        store._conn.close()
        import duckdb

        store._conn = duckdb.connect(":memory:")
        store._conn.execute("DROP TABLE IF EXISTS nodes")
        store._conn.execute("DROP TABLE IF EXISTS edges")
        # flush should fail because tables are missing, and restore buffers
        await store.flush()
        assert len(store._search_buffer) >= 1
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestSearchIndexTable -v && uv run pytest tests/test_duckdb_store.py::TestSearchIndexFlush -v
```

Expected: All tests FAIL — `search_index` table doesn't exist, `_search_buffer` attribute doesn't exist.

### Step 3: Implement search_index table, buffer, and flush

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py`.

**3a.** Add the table DDL constant after the existing `_CREATE_EDGES` constant (after line 36):

```python
_CREATE_SEARCH_INDEX = """
CREATE TABLE IF NOT EXISTS search_index (
    node_id     VARCHAR NOT NULL,
    session_id  VARCHAR NOT NULL,
    field_name  VARCHAR NOT NULL,
    content     VARCHAR NOT NULL,
    occurred_at TIMESTAMP
)
"""
```

**3b.** In `__init__`, execute the new DDL and add the search buffer. After the line `self._conn.execute(_CREATE_EDGES)` (line 55), add:

```python
        self._conn.execute(_CREATE_SEARCH_INDEX)
```

After the line `self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}` (line 57), add:

```python
        self._search_buffer: list[dict[str, Any]] = []
```

**3c.** Update `flush()` to include search_index writes and the search buffer in the empty-check and rollback logic.

Replace the entire `flush` method (lines 149-197) with:

```python
    async def flush(self) -> None:
        # Snapshot and clear
        nodes = self._node_buffer
        edges = self._edge_buffer
        search = self._search_buffer
        self._node_buffer = {}
        self._edge_buffer = {}
        self._search_buffer = []

        if not nodes and not edges and not search:
            return

        def _write() -> None:
            try:
                self._conn.execute("BEGIN TRANSACTION")
                for node in nodes.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO nodes (node_id, session_id, labels, properties) "
                        "VALUES (?, ?, ?, ?)",
                        [
                            node["id"],
                            "",
                            list(node["labels"]),
                            json.dumps(node["properties"]),
                        ],
                    )
                for edge in edges.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO edges "
                        "(source, target, edge_type, session_id, properties) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [
                            edge["source"],
                            edge["target"],
                            edge["type"],
                            "",
                            json.dumps(edge["properties"]),
                        ],
                    )
                for entry in search:
                    self._conn.execute(
                        "INSERT INTO search_index "
                        "(node_id, session_id, field_name, content, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [
                            entry["node_id"],
                            entry["session_id"],
                            entry["field_name"],
                            entry["content"],
                            entry["occurred_at"],
                        ],
                    )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:
                    pass
                # Put items back for retry
                self._node_buffer.update(nodes)
                self._edge_buffer.update(edges)
                self._search_buffer.extend(search)
                logger.warning("flush failed; buffers restored for retry", exc_info=True)

        await self._run(_write)
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: ALL tests pass — both the new `TestSearchIndexTable` / `TestSearchIndexFlush` tests and all existing tests.

### Step 5: Update existing table-creation test

The existing `TestConstructor.test_tables_created_on_init` checks for `nodes` and `edges`. Update it to also verify `search_index`. In `tests/test_duckdb_store.py`, find the `test_tables_created_on_init` method and replace it with:

```python
    def test_tables_created_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        # Query information_schema to verify tables exist
        result = store._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' "
            "AND table_name IN ('nodes', 'edges', 'search_index') "
            "ORDER BY table_name"
        ).fetchall()
        table_names = [row[0] for row in result]
        assert "edges" in table_names
        assert "nodes" in table_names
        assert "search_index" in table_names
```

### Step 6: Run full DuckDB store tests

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: ALL tests pass.

### Step 7: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py modules/hook-context-intelligence/tests/test_duckdb_store.py && git commit -m "feat: add search_index table, buffer, and flush to DuckDBGraphStore"
```

---

## Task 2: Auto-Populate search_index During upsert_node

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py`
- Modify: `modules/hook-context-intelligence/tests/test_duckdb_store.py`

### Step 1: Write failing tests

Open `modules/hook-context-intelligence/tests/test_duckdb_store.py` and add this test class at the end of the file:

```python
# ---------------------------------------------------------------------------
# TestSearchIndexAutoPopulate
# ---------------------------------------------------------------------------
class TestSearchIndexAutoPopulate:
    """upsert_node must auto-populate search_index for known indexable patterns."""

    @pytest.fixture
    def store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        return DuckDBGraphStore()

    async def test_prompt_step_with_prompt_text_populates_search_buffer(self, store):
        await store.upsert_node(
            "n1",
            {"Step", "PromptStep"},
            {"prompt_text": "hello world", "session_id": "s1", "occurred_at": "2026-03-06T00:00:00Z"},
        )
        assert len(store._search_buffer) == 1
        entry = store._search_buffer[0]
        assert entry["node_id"] == "n1"
        assert entry["session_id"] == "s1"
        assert entry["field_name"] == "prompt_text"
        assert entry["content"] == "hello world"
        assert entry["occurred_at"] == "2026-03-06T00:00:00Z"

    async def test_session_node_does_not_populate_search_buffer(self, store):
        await store.upsert_node(
            "s1",
            {"Session", "Root"},
            {"started_at": "2026-03-06T00:00:00Z", "status": "running"},
        )
        assert len(store._search_buffer) == 0

    async def test_prompt_step_without_prompt_text_does_not_populate(self, store):
        await store.upsert_node(
            "n1",
            {"Step", "PromptStep"},
            {"session_id": "s1", "occurred_at": "2026-03-06T00:00:00Z"},
        )
        assert len(store._search_buffer) == 0

    async def test_prompt_step_with_empty_prompt_text_does_not_populate(self, store):
        await store.upsert_node(
            "n1",
            {"Step", "PromptStep"},
            {"prompt_text": "", "session_id": "s1", "occurred_at": "2026-03-06T00:00:00Z"},
        )
        assert len(store._search_buffer) == 0

    async def test_auto_populate_flows_through_to_duckdb_on_flush(self, store):
        await store.upsert_node(
            "n1",
            {"Step", "PromptStep"},
            {"prompt_text": "search me", "session_id": "s1", "occurred_at": "2026-03-06T00:00:00Z"},
        )
        await store.flush()
        rows = store._conn.execute(
            "SELECT node_id, field_name, content FROM search_index"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0] == ("n1", "prompt_text", "search me")

    async def test_upsert_existing_node_does_not_duplicate_search_entry(self, store):
        """Second upsert to same node merges properties but should not add a second search entry."""
        await store.upsert_node(
            "n1",
            {"Step", "PromptStep"},
            {"prompt_text": "hello", "session_id": "s1", "occurred_at": "2026-03-06T00:00:00Z"},
        )
        # Second upsert merges properties (e.g., adding iteration count)
        await store.upsert_node("n1", set(), {"iteration": 1})
        # Should still be only 1 search entry — the second upsert has no prompt_text
        assert len(store._search_buffer) == 1
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestSearchIndexAutoPopulate -v
```

Expected: All tests FAIL — `upsert_node` doesn't auto-populate `_search_buffer` yet.

### Step 3: Implement auto-populate in upsert_node

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py`.

**3a.** Add an indexable-fields mapping after the `_CREATE_SEARCH_INDEX` constant. This maps `(label, property_name)` pairs that should be indexed:

```python
# Maps (label, property_key) pairs to search_index field_name values.
# When upsert_node receives a NEW node matching one of these patterns,
# a search_index entry is auto-buffered.
_INDEXABLE_FIELDS: dict[tuple[str, str], str] = {
    ("PromptStep", "prompt_text"): "prompt_text",
}
```

**3b.** Replace the existing `upsert_node` method (lines 71-81) with:

```python
    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        existing = self._node_buffer.get(node_id)
        if existing is not None:
            existing["labels"] |= labels
            existing["properties"].update(properties)
            return
        self._node_buffer[node_id] = {
            "id": node_id,
            "labels": set(labels),
            "properties": dict(properties),
        }
        self._index_searchable_content(node_id, labels, properties)
```

**3c.** Add the `_index_searchable_content` helper in the "Internal helpers" section, after the `_run` method:

```python
    def _index_searchable_content(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> None:
        """Auto-buffer search_index entries for known indexable label+property pairs."""
        for (label, prop_key), field_name in _INDEXABLE_FIELDS.items():
            if label in labels:
                content = properties.get(prop_key)
                if content:
                    self._search_buffer.append({
                        "node_id": node_id,
                        "session_id": properties.get("session_id", ""),
                        "field_name": field_name,
                        "content": content,
                        "occurred_at": properties.get("occurred_at"),
                    })
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: ALL tests pass — both new `TestSearchIndexAutoPopulate` tests and all existing tests.

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py modules/hook-context-intelligence/tests/test_duckdb_store.py && git commit -m "feat: auto-populate search_index from upsert_node for PromptStep nodes"
```

---

## Task 3: Add Standing Rule Docstring to duckdb_store.py

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py`

### Step 1: Update the module docstring

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py`.

Replace the first line:

```python
"""DuckDBGraphStore – buffer-first reads with async DuckDB persistence."""
```

With:

```python
"""DuckDBGraphStore – buffer-first reads with async DuckDB persistence.

STANDING RULE — Skill Synchronization
--------------------------------------
Any change to the schema (tables, columns, property graph definition,
search_index, FTS indexes, new label types, new edge types, new
field_name values in search_index, _INDEXABLE_FIELDS entries) MUST be
accompanied by an update to the SQL/PGQ skill at:

    skills/context-intelligence-graph-search/SKILL.md

The skill is the contract between this storage layer and agents that
generate queries.  Stale skill = broken agent query generation.
"""
```

### Step 2: Run tests to verify nothing broke

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: ALL tests still pass.

### Step 3: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py && git commit -m "docs: add standing rule docstring to duckdb_store.py for skill synchronization"
```

---

## Task 4: Create the SQL/PGQ Skill

**Files:**
- Create: `skills/context-intelligence-graph-search/SKILL.md`

### Step 1: Create the skills directory

```bash
mkdir -p amplifier-bundle-context-intelligence/skills/context-intelligence-graph-search
```

### Step 2: Write the skill file

Create `skills/context-intelligence-graph-search/SKILL.md` with this exact content:

````markdown
---
name: context-intelligence-graph-search
description: >
  SQL and PGQ query patterns for the context-intelligence property graph
  stored in DuckDB. Covers the full schema (nodes, edges, search_index),
  the label system, edge types, full-text search with BM25 ranking, and
  DuckPGQ graph traversal. Use this skill to generate queries against
  captured session data.
version: 0.1.0
license: MIT
---

# Context Intelligence Graph Search

Query patterns for the context-intelligence session graph in DuckDB.

> **Schema source of truth:** `duckdb_store.py` in the `hook-context-intelligence` module.
> If this skill's schema does not match `duckdb_store.py`, **the skill is stale**.

## Schema

### nodes

```sql
CREATE TABLE nodes (
    node_id     VARCHAR PRIMARY KEY,
    session_id  VARCHAR DEFAULT '',
    labels      VARCHAR[],
    occurred_at TIMESTAMP,
    properties  JSON
);
```

### edges

```sql
CREATE TABLE edges (
    source      VARCHAR,
    target      VARCHAR,
    edge_type   VARCHAR,
    session_id  VARCHAR DEFAULT '',
    occurred_at TIMESTAMP,
    seq         INTEGER,
    properties  JSON,
    PRIMARY KEY (source, target, edge_type)
);
```

### search_index

```sql
CREATE TABLE search_index (
    node_id     VARCHAR NOT NULL,
    session_id  VARCHAR NOT NULL,
    field_name  VARCHAR NOT NULL,
    content     VARCHAR NOT NULL,
    occurred_at TIMESTAMP
);
```

### Property Graph Overlay (DuckPGQ)

```sql
INSTALL duckpgq FROM community;
LOAD duckpgq;

CREATE PROPERTY GRAPH session_graph
    VERTEX TABLES (nodes LABEL session_node)
    EDGE TABLES (
        edges SOURCE KEY (source) REFERENCES nodes (node_id)
              DESTINATION KEY (target) REFERENCES nodes (node_id)
              LABEL session_edge
    );
```

The property graph is created on demand before graph traversal queries, not at startup.

## Label System

Nodes are typed via the `labels` column (`VARCHAR[]`). Use `list_contains(labels, 'X')` to filter by type.

| Label | Meaning |
|-------|---------|
| `Session` | A session (root, subsession, or fork) |
| `Root` | A root (top-level) session |
| `Subsession` | A child session spawned from a parent |
| `ForkedSession` | A session created via fork |
| `Resumed` | A session that has been resumed at least once |
| `OrchestratorRun` | An orchestrator execution run within a session |
| `Step` | Base label for all steps |
| `PromptStep` | A user prompt or delegation instruction |
| `AssistantStep` | An assistant response |
| `RecipeStep` | A recipe execution step |
| `ToolExecution` | A tool call and its result |
| `Delegation` | A sub-agent delegation |
| `Event` | A lifecycle event node |

Derived labels (from `derive_label()`) convert event names like `session:resume` into `SessionResume`.

## Edge Types

| Edge Type | From → To | Meaning |
|-----------|-----------|---------|
| `HAS_RUN` | Session → OrchestratorRun | Session contains this run |
| `HAS_STEP` | Session/OrchestratorRun → Step | Contains this step |
| `NEXT` | Step → Step | Sequential ordering |
| `TRIGGERED` | Step → ToolExecution | Step triggered this tool call |
| `PARALLEL_WITH` | ToolExecution ↔ ToolExecution | Concurrent tool calls |
| `SPAWNED` | ToolExecution → Session | Tool call spawned a subsession |
| `SUBSESSION_OF` | Session → Session | Child → parent relationship |
| `HAS_EVENT` | any scope → Event | Scope owns this event |

## Search Index

The `search_index` table stores searchable text content extracted from graph nodes. Each row maps one `field_name` to the text `content` from a specific `node_id`.

### field_name Values

| field_name | Source Label | Meaning |
|------------|-------------|---------|
| `prompt_text` | `PromptStep` | User prompt or delegation instruction |

Future values (not yet implemented): `response_text`, `tool_result`, `thinking`.

## Query Patterns

### Pattern 1: Direct FTS (No Graph Traversal)

**When to use:** The answer is already in `search_index` — e.g., "find sessions where the prompt mentioned X."

```sql
SELECT session_id, node_id, field_name,
       fts_main_search_index.match_bm25(rowid, 'comic-strip-bundle') AS score
FROM search_index
WHERE score IS NOT NULL
  AND field_name = 'prompt_text'
ORDER BY score DESC;
```

PGQ is not needed because the answer IS the `session_id`.

### Pattern 2: FTS + PGQ Traversal

**When to use:** FTS finds candidates but the answer requires walking graph relationships — e.g., "find the root session that had any descendant session matching X."

```sql
WITH matches AS (
    SELECT session_id,
           fts_main_search_index.match_bm25(rowid, 'comic-strip-bundle') AS score
    FROM search_index
    WHERE score IS NOT NULL AND field_name = 'prompt_text'
)
SELECT DISTINCT graph_result.root_session_id, m.score
FROM matches m,
     GRAPH_TABLE (session_graph
         MATCH (child:session_node)-[:session_edge*]->(root:session_node)
         WHERE child.node_id = m.session_id
           AND list_contains(root.labels, 'Root')
         COLUMNS (root.node_id AS root_session_id)
     ) graph_result
ORDER BY m.score DESC;
```

FTS finds candidates with BM25 ranking → PGQ walks the graph to find their root ancestor. Each layer does what it is good at.

### Pattern 3: Pure PGQ (No Text Search)

**When to use:** Graph structure queries — e.g., "show me the steps for session X."

```sql
FROM GRAPH_TABLE (session_graph
    MATCH (s:session_node)-[:session_edge]->(p:session_node)
    WHERE s.node_id = 'abc123'
      AND list_contains(p.labels, 'PromptStep')
    COLUMNS (s.node_id AS session_id, p.properties AS step_props)
)
```

### The Principle

FTS for text discovery, PGQ for graph traversal. Use PGQ only when you need to walk relationships that FTS cannot answer. Do not use PGQ to re-fetch data you already have from FTS.

## Notes

- **FTS index:** The FTS index (`PRAGMA create_fts_index('search_index', 'content')`) is rebuilt asynchronously, not during write operations. Between rebuilds, newly inserted content is not FTS-searchable. For recently inserted data, use `WHERE content LIKE '%term%'` as a fallback.
- **Property graph creation:** `CREATE PROPERTY GRAPH session_graph` must be executed before any `GRAPH_TABLE` query. It is created on demand, not at startup.
- **JSON properties:** Use `json_extract(properties, '$.key')` or `properties->>'key'` to access fields inside the `properties` JSON column.
````

### Step 3: Verify the file exists and is well-formed

```bash
head -10 amplifier-bundle-context-intelligence/skills/context-intelligence-graph-search/SKILL.md
```

Expected: The YAML frontmatter with `name: context-intelligence-graph-search`.

### Step 4: Commit

```bash
cd amplifier-bundle-context-intelligence && git add skills/ && git commit -m "feat: add SQL/PGQ skill for context-intelligence graph search"
```

---

## Task 5: Register Skill in Behavior YAML

**Files:**
- Modify: `behaviors/context-intelligence.yaml`
- Modify: `modules/hook-context-intelligence/tests/test_bundle.py`

### Step 1: Write a failing test for the tools section

Open `modules/hook-context-intelligence/tests/test_bundle.py` and add this test class at the end of the file:

```python
class TestSkillRegistration:
    """Validate skill registration in behavior YAML."""

    def _load_behavior(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_behavior_has_tools_section(self):
        data = self._load_behavior()
        assert "tools" in data, "Behavior YAML must have a tools: section for skill registration"

    def test_tools_section_has_tool_skills_module(self):
        data = self._load_behavior()
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "tool-skills" in tool_modules

    def test_tool_skills_config_has_skills_list(self):
        data = self._load_behavior()
        tool_skills = [t for t in data.get("tools", []) if t["module"] == "tool-skills"]
        assert len(tool_skills) == 1
        config = tool_skills[0].get("config", {})
        assert "skills" in config
        assert isinstance(config["skills"], list)
        assert len(config["skills"]) >= 2, (
            "skills list must include BOTH curated skills AND bundle skills "
            "(deep_merge replaces lists, so we must include both)"
        )

    def test_tool_skills_includes_curated_skills(self):
        data = self._load_behavior()
        tool_skills = [t for t in data.get("tools", []) if t["module"] == "tool-skills"][0]
        skills = tool_skills["config"]["skills"]
        assert any("amplifier-bundle-skills" in s for s in skills), (
            "Must include curated skills from amplifier-bundle-skills"
        )

    def test_tool_skills_includes_bundle_skills(self):
        data = self._load_behavior()
        tool_skills = [t for t in data.get("tools", []) if t["module"] == "tool-skills"][0]
        skills = tool_skills["config"]["skills"]
        assert any("context-intelligence" in s and "skills" in s for s in skills), (
            "Must include bundle skills directory"
        )

    def test_skill_directory_exists(self):
        assert (REPO_ROOT / "skills" / "context-intelligence-graph-search").is_dir()

    def test_skill_file_exists(self):
        assert (REPO_ROOT / "skills" / "context-intelligence-graph-search" / "SKILL.md").is_file()

    def test_skill_file_has_frontmatter(self):
        content = (
            REPO_ROOT / "skills" / "context-intelligence-graph-search" / "SKILL.md"
        ).read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"
        parts = content.split("---", 2)
        assert len(parts) >= 3, "SKILL.md must have YAML frontmatter between --- delimiters"
        fm = yaml.safe_load(parts[1])
        assert fm["name"] == "context-intelligence-graph-search"
        assert "description" in fm
        assert fm["license"] == "MIT"
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_bundle.py::TestSkillRegistration -v
```

Expected: `test_behavior_has_tools_section` FAILS (and others) — no `tools:` section in behavior YAML yet.

### Step 3: Update the behavior YAML

Open `behaviors/context-intelligence.yaml` and replace its entire content with:

```yaml
bundle:
  name: context-intelligence-behavior
  version: 0.1.0
  description: |
    Context intelligence hooks for building a property graph
    from orchestrator events.

hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      exclude_events: []
      log_level: "${CI_LOG_LEVEL:WARNING}"

tools:
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-module-tool-skills@main
    config:
      skills:
        - "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
        - "git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=skills"
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_bundle.py -v
```

Expected: ALL tests pass — both old `TestBundleRoot` / `TestBehaviorYaml` tests and new `TestSkillRegistration` tests.

**Important check:** Verify the existing `test_behavior_hook_is_in_hooks_section_not_tools` still passes. That test checks `hook-context-intelligence` is in `hooks:` not `tools:` — our new `tools:` section adds `tool-skills`, which is correct.

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence && git add behaviors/context-intelligence.yaml modules/hook-context-intelligence/tests/test_bundle.py && git commit -m "feat: register SQL/PGQ skill in behavior YAML with tool-skills module"
```

---

## Task 6: Verify Full Test Suite

**Files:** None (verification only)

### Step 1: Run the entire test suite

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: ALL tests pass. Zero failures.

### Step 2: Run type checking

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pyright amplifier_module_hook_context_intelligence/duckdb_store.py
```

Expected: No errors (warnings are OK).

### Step 3: Run linting

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run ruff check amplifier_module_hook_context_intelligence/ tests/
```

Expected: No errors. If ruff reports formatting issues, run `uv run ruff format amplifier_module_hook_context_intelligence/ tests/` and commit the result.

### Step 4: Final commit (if any lint/format fixes were needed)

```bash
cd amplifier-bundle-context-intelligence && git add -A && git commit -m "chore: lint and format fixes" --allow-empty
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `duckdb_store.py` | New `_CREATE_SEARCH_INDEX` DDL, `_search_buffer` list, `_INDEXABLE_FIELDS` mapping, `_index_searchable_content()` helper, updated `upsert_node()` to auto-index, updated `flush()` to write search entries, standing rule docstring |
| `test_duckdb_store.py` | `TestSearchIndexTable` (3 tests), `TestSearchIndexFlush` (5 tests), `TestSearchIndexAutoPopulate` (6 tests), updated `test_tables_created_on_init` |
| `skills/.../SKILL.md` | New skill file with full schema, label system, edge types, 3 query patterns, cross-reference |
| `context-intelligence.yaml` | Added `tools:` section with `tool-skills` config (both curated and bundle skills) |
| `test_bundle.py` | `TestSkillRegistration` (8 tests) |

**Commit sequence:**
1. `feat: add search_index table, buffer, and flush to DuckDBGraphStore`
2. `feat: auto-populate search_index from upsert_node for PromptStep nodes`
3. `docs: add standing rule docstring to duckdb_store.py for skill synchronization`
4. `feat: add SQL/PGQ skill for context-intelligence graph search`
5. `feat: register SQL/PGQ skill in behavior YAML with tool-skills module`
6. `chore: lint and format fixes` (if needed)