# PGQ, FTS Lifecycle, and Reference Test Data Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement PGQ infrastructure, FTS lifecycle, and replace toy test data with reference data model fixtures in the DuckDB graph store.

**Architecture:** Lazy-init PGQ via `_ensure_pgq()` called only when `dialect="pgq"` is passed to `execute_query`. FTS rebuild is an explicit `rebuild_fts_index()` call — never automatic. Reference test fixtures use the real data model (Session, OrchestratorRun, PromptStep, ToolExecution) with `make_node_id`-style IDs so tests validate domain semantics, not just mechanics.

**Tech Stack:** Python 3.11, DuckDB ≥1.0, DuckPGQ extension, DuckDB FTS extension, pytest-asyncio

---

## Paths

All paths relative to **`/home/dicolomb/context-itelligence-bundle-v2`**.

| Alias | Full Path |
|-------|-----------|
| `SRC/` | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/` |
| `TESTS/` | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/` |
| `SKILL` | `amplifier-bundle-context-intelligence/skills/context-intelligence-graph-search/SKILL.md` |
| `AGENTS` | `AGENTS.md` |

All `uv run pytest` commands run from:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
```

---

### Task 1: Reference test data fixture — `conftest.py` graph seed

**Files:**
- Modify: `TESTS/conftest.py`

**Step 1: Add constants and `seed_reference_graph` fixture to conftest**

Open `TESTS/conftest.py` and replace the entire file with:

```python
"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService

# ---------------------------------------------------------------------------
# Reference data model constants
# ---------------------------------------------------------------------------
SESSION_ID = "55c8841a-test"
SESSION_NODE_ID = "55c8841a-test"
RUN_NODE_ID = "55c8841a-test__execution_start__1737972000000"
PROMPT_NODE_ID = "55c8841a-test__prompt_submit__1737972001000"
TOOL_NODE_ID = "55c8841a-test__tool_pre__1737972002000"


# ---------------------------------------------------------------------------
# Reference node/edge definitions
# ---------------------------------------------------------------------------
def _reference_nodes() -> list[tuple[str, set[str], dict[str, Any]]]:
    """Return (node_id, labels, properties) tuples for the reference graph."""
    return [
        (
            SESSION_NODE_ID,
            {"Session", "Root"},
            {
                "session_id": SESSION_ID,
                "status": "running",
                "started_at": "2026-01-15T10:00:00Z",
            },
        ),
        (
            RUN_NODE_ID,
            {"OrchestratorRun"},
            {
                "session_id": SESSION_ID,
                "run_number": 1,
                "status": "running",
                "started_at": "2026-01-15T10:00:00Z",
            },
        ),
        (
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "session_id": SESSION_ID,
                "iteration": 0,
                "prompt_text": "Help me refactor the authentication module",
                "prompt_preview": "Help me refactor the authentication module",
                "occurred_at": "2026-01-15T10:00:01Z",
            },
        ),
        (
            TOOL_NODE_ID,
            {"ToolExecution"},
            {
                "session_id": SESSION_ID,
                "tool_name": "read_file",
                "tool_call_id": "tc_001",
                "status": "complete",
            },
        ),
    ]


def _reference_edges() -> list[tuple[str, str, str, dict[str, Any]]]:
    """Return (source, target, edge_type, properties) tuples for the reference graph."""
    return [
        (SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1}),
        (RUN_NODE_ID, PROMPT_NODE_ID, "HAS_STEP", {"seq": 0}),
        (PROMPT_NODE_ID, TOOL_NODE_ID, "TRIGGERED", {"seq": 1}),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService wired to an in-memory DuckDB store.

    Uses explicit config so the factory never tries to import file_store
    during DuckDB-focused tests.
    """
    return HookStateService(
        raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
    )


@pytest.fixture
async def seed_reference_graph(store):
    """Seed the store with reference data model entities and flush to DuckDB.

    After this fixture completes:
    - 4 nodes (Session, OrchestratorRun, PromptStep, ToolExecution) are in DuckDB
    - 3 edges (HAS_RUN, HAS_STEP, TRIGGERED) are in DuckDB
    - search_index has one entry for the PromptStep prompt_text
    - All buffers are empty (flushed)
    """
    for node_id, labels, properties in _reference_nodes():
        await store.upsert_node(node_id, labels, properties)
    for source, target, edge_type, properties in _reference_edges():
        await store.upsert_edge(source, target, edge_type, properties)
    await store.flush()
```

**Step 2: Run existing tests to verify conftest changes don't break anything**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py -v
```
Expected: ALL PASS (we only added new fixtures and constants; nothing removed yet)

**Step 3: Commit**

```bash
cd amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/tests/conftest.py
git commit -m "test: add reference data model fixtures to conftest"
```

---

### Task 2: Update existing tests to use reference data model

**Files:**
- Modify: `TESTS/test_duckdb_store.py`

These tests currently use toy data (`Alice`, `Bob`, `Person`, `KNOWS`, `n1`). We update the classes that benefit from domain-realistic data. `TestBufferWrites` keeps synthetic data — it tests pure buffer mechanics where domain labels are irrelevant.

**Step 1: Update `TestBufferFirstReads`**

Replace the `TestBufferFirstReads` class in `TESTS/test_duckdb_store.py` with:

```python
# ---------------------------------------------------------------------------
# TestBufferFirstReads
# ---------------------------------------------------------------------------
class TestBufferFirstReads:
    """get_node / get_edge must reflect buffered state."""

    async def test_get_node_returns_buffered_data(self, store):
        from tests.conftest import PROMPT_NODE_ID, SESSION_ID

        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {"session_id": SESSION_ID, "iteration": 0, "prompt_text": "Help me refactor"},
        )
        node = await store.get_node(PROMPT_NODE_ID)
        assert node is not None
        assert node["id"] == PROMPT_NODE_ID
        assert node["labels"] == {"Step", "PromptStep"}
        assert node["properties"]["session_id"] == SESSION_ID

    async def test_get_edge_returns_buffered_data(self, store):
        from tests.conftest import RUN_NODE_ID, SESSION_NODE_ID

        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        edge = await store.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"] == {"seq": 1}

    async def test_get_nonexistent_node_returns_none(self, store):
        result = await store.get_node("nope")
        assert result is None

    async def test_get_nonexistent_edge_returns_none(self, store):
        result = await store.get_edge("x", "y", "NOPE")
        assert result is None

    async def test_buffer_wins_over_stale_duckdb(self, store):
        """Upsert after flush: buffer value should override stale DuckDB data."""
        from tests.conftest import SESSION_NODE_ID

        await store.upsert_node(SESSION_NODE_ID, {"Session"}, {"status": "running"})
        await store.flush()
        # Now upsert a newer version into buffer
        await store.upsert_node(SESSION_NODE_ID, {"Session"}, {"status": "completed"})
        node = await store.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["properties"]["status"] == "completed"
```

**Step 2: Update `TestFlush`**

Replace the `TestFlush` class with:

```python
# ---------------------------------------------------------------------------
# TestFlush
# ---------------------------------------------------------------------------
class TestFlush:
    """flush() persists buffers to DuckDB and clears them."""

    async def test_flush_writes_nodes_to_duckdb(self, store):
        from tests.conftest import SESSION_NODE_ID

        await store.upsert_node(SESSION_NODE_ID, {"Session", "Root"}, {"status": "running"})
        await store.flush()
        row = store._conn.execute(
            "SELECT node_id FROM nodes WHERE node_id = ?", [SESSION_NODE_ID]
        ).fetchone()
        assert row is not None
        assert row[0] == SESSION_NODE_ID

    async def test_flush_writes_edges_to_duckdb(self, store):
        from tests.conftest import RUN_NODE_ID, SESSION_NODE_ID

        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.flush()
        row = store._conn.execute(
            "SELECT source, target, edge_type FROM edges "
            "WHERE source = ? AND target = ? AND edge_type = 'HAS_RUN'",
            [SESSION_NODE_ID, RUN_NODE_ID],
        ).fetchone()
        assert row is not None
        assert row == (SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")

    async def test_flush_clears_both_buffers(self, store):
        from tests.conftest import RUN_NODE_ID, SESSION_NODE_ID

        await store.upsert_node(SESSION_NODE_ID, {"Session"}, {})
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {})
        await store.flush()
        assert len(store._node_buffer) == 0
        assert len(store._edge_buffer) == 0

    async def test_get_node_from_duckdb_after_flush(self, store):
        from tests.conftest import SESSION_ID, SESSION_NODE_ID

        await store.upsert_node(
            SESSION_NODE_ID, {"Session", "Root"}, {"session_id": SESSION_ID, "status": "running"}
        )
        await store.flush()
        # Buffer is empty now; read must come from DuckDB
        assert len(store._node_buffer) == 0
        node = await store.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["id"] == SESSION_NODE_ID
        assert node["labels"] == {"Session", "Root"}
        assert node["properties"]["status"] == "running"

    async def test_get_edge_from_duckdb_after_flush(self, store):
        from tests.conftest import RUN_NODE_ID, SESSION_NODE_ID

        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.flush()
        assert len(store._edge_buffer) == 0
        edge = await store.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"] == {"seq": 1}

    async def test_flush_empty_buffer_is_noop(self, store):
        # Should not raise
        await store.flush()
        await store.flush()
```

**Step 3: Update `TestExecuteQuery`**

Replace the `TestExecuteQuery` class with:

```python
# ---------------------------------------------------------------------------
# TestExecuteQuery
# ---------------------------------------------------------------------------
class TestExecuteQuery:
    """execute_query returns list of dicts and supports dialect validation."""

    async def test_execute_query_returns_list_of_dicts(self, store, seed_reference_graph):
        rows = await store.execute_query("SELECT node_id, labels FROM nodes ORDER BY node_id")
        assert isinstance(rows, list)
        assert len(rows) == 4
        assert "node_id" in rows[0]
        assert "labels" in rows[0]

    def test_supported_dialects_returns_frozenset(self, store):
        dialects = store.supported_dialects
        assert isinstance(dialects, frozenset)
        assert "sql" in dialects

    async def test_execute_query_with_explicit_sql_dialect(self, store, seed_reference_graph):
        from tests.conftest import SESSION_NODE_ID

        rows = await store.execute_query(
            "SELECT node_id FROM nodes WHERE node_id = $nid",
            params={"nid": SESSION_NODE_ID},
            dialect="sql",
        )
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["node_id"] == SESSION_NODE_ID

    async def test_execute_query_with_none_dialect_uses_default(self, store, seed_reference_graph):
        from tests.conftest import SESSION_NODE_ID

        rows = await store.execute_query(
            "SELECT node_id FROM nodes WHERE node_id = $nid",
            params={"nid": SESSION_NODE_ID},
            dialect=None,
        )
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["node_id"] == SESSION_NODE_ID

    async def test_execute_query_with_params(self, store, seed_reference_graph):
        from tests.conftest import PROMPT_NODE_ID

        rows = await store.execute_query(
            "SELECT node_id FROM nodes WHERE node_id = $node_id",
            params={"node_id": PROMPT_NODE_ID},
        )
        assert len(rows) == 1
        assert rows[0]["node_id"] == PROMPT_NODE_ID

    async def test_execute_query_with_invalid_dialect_raises(self, store):
        with pytest.raises(ValueError, match="Unsupported dialect"):
            await store.execute_query("SELECT 1", dialect="cypher")
```

**Step 4: Update `TestClose`**

Replace the `TestClose` class with:

```python
# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() must flush before closing the connection."""

    async def test_close_flushes_before_closing(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from tests.conftest import SESSION_ID, SESSION_NODE_ID

        db_path = tmp_path / "close_test.db"
        store = DuckDBGraphStore(connection=str(db_path))
        await store.upsert_node(
            SESSION_NODE_ID, {"Session", "Root"}, {"session_id": SESSION_ID, "status": "running"}
        )
        await store.close()

        # Reopen and verify data was persisted
        import duckdb

        conn = duckdb.connect(str(db_path))
        row = conn.execute(
            "SELECT node_id FROM nodes WHERE node_id = ?", [SESSION_NODE_ID]
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == SESSION_NODE_ID
```

**Step 5: Update `TestPersistence`**

Replace the `TestPersistence` class with:

```python
# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------
class TestPersistence:
    """Data must survive close and reopen."""

    async def test_data_survives_close_and_reopen(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from tests.conftest import RUN_NODE_ID, SESSION_ID, SESSION_NODE_ID

        db_path = tmp_path / "persist_test.db"

        # Write data and close
        store = DuckDBGraphStore(connection=str(db_path))
        await store.upsert_node(
            SESSION_NODE_ID, {"Session", "Root"}, {"session_id": SESSION_ID, "status": "running"}
        )
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.close()

        # Reopen and read back
        store2 = DuckDBGraphStore(connection=str(db_path))
        node = await store2.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["id"] == SESSION_NODE_ID
        assert node["labels"] == {"Session", "Root"}
        assert node["properties"]["session_id"] == SESSION_ID

        edge = await store2.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"] == {"seq": 1}
        await store2.close()
```

**Step 6: Update `TestSearchIndexAutoPopulate` node IDs**

Replace the `TestSearchIndexAutoPopulate` class with:

```python
# ---------------------------------------------------------------------------
# TestSearchIndexAutoPopulate
# ---------------------------------------------------------------------------
class TestSearchIndexAutoPopulate:
    """upsert_node auto-populates _search_buffer for indexable PromptStep nodes."""

    async def test_promptstep_with_prompt_text_populates_search_buffer(self, store):
        """PromptStep with prompt_text populates search buffer with correct fields."""
        from tests.conftest import PROMPT_NODE_ID, SESSION_ID

        await store.upsert_node(
            PROMPT_NODE_ID,
            {"PromptStep"},
            {
                "prompt_text": "What is the meaning of life?",
                "session_id": SESSION_ID,
                "occurred_at": "2026-01-15T10:30:00",
            },
        )
        assert len(store._search_buffer) == 1
        entry = store._search_buffer[0]
        assert entry["node_id"] == PROMPT_NODE_ID
        assert entry["session_id"] == SESSION_ID
        assert entry["field_name"] == "prompt_text"
        assert entry["content"] == "What is the meaning of life?"
        assert entry["occurred_at"] == "2026-01-15T10:30:00"

    async def test_session_node_does_not_populate_search_buffer(self, store):
        """Session node does NOT populate search buffer."""
        from tests.conftest import SESSION_ID, SESSION_NODE_ID

        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session"},
            {"session_id": SESSION_ID, "prompt_text": "should not matter"},
        )
        assert len(store._search_buffer) == 0

    async def test_promptstep_without_prompt_text_does_not_populate(self, store):
        """PromptStep WITHOUT prompt_text does NOT populate search buffer."""
        from tests.conftest import PROMPT_NODE_ID, SESSION_ID

        await store.upsert_node(
            PROMPT_NODE_ID,
            {"PromptStep"},
            {"session_id": SESSION_ID, "occurred_at": "2026-01-15T10:30:00"},
        )
        assert len(store._search_buffer) == 0

    async def test_promptstep_with_empty_prompt_text_does_not_populate(self, store):
        """PromptStep with EMPTY prompt_text does NOT populate search buffer."""
        from tests.conftest import PROMPT_NODE_ID, SESSION_ID

        await store.upsert_node(
            PROMPT_NODE_ID,
            {"PromptStep"},
            {
                "prompt_text": "",
                "session_id": SESSION_ID,
                "occurred_at": "2026-01-15T10:30:00",
            },
        )
        assert len(store._search_buffer) == 0

    async def test_auto_populate_flows_through_to_duckdb_on_flush(self, store):
        """Auto-populated entries flow through flush() to DuckDB."""
        from tests.conftest import PROMPT_NODE_ID, SESSION_ID

        await store.upsert_node(
            PROMPT_NODE_ID,
            {"PromptStep"},
            {
                "prompt_text": "Tell me about AI",
                "session_id": SESSION_ID,
                "occurred_at": "2026-02-01T12:00:00",
            },
        )
        await store.flush()
        row = store._conn.execute(
            "SELECT node_id, session_id, field_name, content FROM search_index WHERE node_id = ?",
            [PROMPT_NODE_ID],
        ).fetchone()
        assert row is not None
        assert row[0] == PROMPT_NODE_ID
        assert row[1] == SESSION_ID
        assert row[2] == "prompt_text"
        assert row[3] == "Tell me about AI"

    async def test_upsert_existing_node_does_not_duplicate_search_entry(self, store):
        """Second upsert to same node_id does NOT add duplicate search entry."""
        from tests.conftest import PROMPT_NODE_ID, SESSION_ID

        await store.upsert_node(
            PROMPT_NODE_ID,
            {"PromptStep"},
            {
                "prompt_text": "Original prompt",
                "session_id": SESSION_ID,
                "occurred_at": "2026-03-01T08:00:00",
            },
        )
        assert len(store._search_buffer) == 1
        # Second upsert to same node_id - should NOT add another entry
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"PromptStep"},
            {"prompt_text": "Updated prompt"},
        )
        assert len(store._search_buffer) == 1
```

**Step 7: Run the full test suite to verify refactor**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py -v
```
Expected: ALL PASS

**Step 8: Commit**

```bash
cd amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/tests/test_duckdb_store.py
git commit -m "test: replace toy data with reference data model entities"
```

---

### Task 3: Write PGQ tests (red)

**Files:**
- Modify: `TESTS/test_duckdb_store.py`

**Step 1: Add `TestPGQ` class**

Append to the end of `TESTS/test_duckdb_store.py`:

```python
# ---------------------------------------------------------------------------
# TestPGQ
# ---------------------------------------------------------------------------
class TestPGQ:
    """DuckPGQ property graph overlay: lazy init and structural queries."""

    def test_pgq_in_supported_dialects(self, store):
        assert "pgq" in store.supported_dialects

    async def test_ensure_pgq_creates_property_graph(self, store, seed_reference_graph):
        """After _ensure_pgq(), GRAPH_TABLE queries work."""
        store._ensure_pgq()
        result = store._conn.execute(
            "SELECT step_id FROM GRAPH_TABLE(context_graph "
            "MATCH (s:Session)-[hr:HAS_RUN]->(r)-[hs:HAS_STEP]->(step) "
            "WHERE s.node_id = '55c8841a-test' "
            "COLUMNS (step.node_id AS step_id))"
        ).fetchall()
        assert len(result) >= 1

    async def test_pgq_dialect_triggers_ensure_pgq(self, store, seed_reference_graph):
        """execute_query with dialect='pgq' auto-triggers _ensure_pgq."""
        rows = await store.execute_query(
            "SELECT step_id FROM GRAPH_TABLE(context_graph "
            "MATCH (s:Session)-[hr:HAS_RUN]->(r)-[hs:HAS_STEP]->(step) "
            "WHERE s.node_id = '55c8841a-test' "
            "COLUMNS (step.node_id AS step_id))",
            dialect="pgq",
        )
        assert len(rows) >= 1
        assert rows[0]["step_id"] == "55c8841a-test__prompt_submit__1737972001000"

    async def test_pgq_structural_query_steps_in_session(self, store, seed_reference_graph):
        """Pattern 3 from SKILL.md: find all steps in a session's runs."""
        rows = await store.execute_query(
            "SELECT step_id FROM GRAPH_TABLE(context_graph "
            "MATCH (s:Session)-[hr:HAS_RUN]->(r)-[hs:HAS_STEP]->(step) "
            "WHERE s.node_id = '55c8841a-test' "
            "COLUMNS (step.node_id AS step_id))",
            dialect="pgq",
        )
        step_ids = [r["step_id"] for r in rows]
        assert "55c8841a-test__prompt_submit__1737972001000" in step_ids

    async def test_pgq_triggered_tools_query(self, store, seed_reference_graph):
        """Query tools triggered by a step via TRIGGERED edge."""
        rows = await store.execute_query(
            "SELECT tool_id FROM GRAPH_TABLE(context_graph "
            "MATCH (step)-[t:TRIGGERED]->(tool) "
            "WHERE step.node_id = '55c8841a-test__prompt_submit__1737972001000' "
            "COLUMNS (tool.node_id AS tool_id))",
            dialect="pgq",
        )
        tool_ids = [r["tool_id"] for r in rows]
        assert "55c8841a-test__tool_pre__1737972002000" in tool_ids

    async def test_ensure_pgq_idempotent(self, store, seed_reference_graph):
        """Calling _ensure_pgq twice does not error."""
        store._ensure_pgq()
        store._ensure_pgq()
        # If we get here without exception, idempotent
        result = store._conn.execute(
            "SELECT step_id FROM GRAPH_TABLE(context_graph "
            "MATCH (s:Session)-[hr:HAS_RUN]->(r)-[hs:HAS_STEP]->(step) "
            "WHERE s.node_id = '55c8841a-test' "
            "COLUMNS (step.node_id AS step_id))"
        ).fetchall()
        assert len(result) >= 1
```

**Step 2: Run PGQ tests to verify they fail**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py::TestPGQ -v
```
Expected: FAIL — `"pgq"` not in `supported_dialects`, `_ensure_pgq` does not exist

**Step 3: Commit (red tests)**

```bash
cd amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/tests/test_duckdb_store.py
git commit -m "test(red): add PGQ tests for lazy init and structural queries"
```

---

### Task 4: Implement PGQ infrastructure (green)

**Files:**
- Modify: `SRC/duckdb_store.py`

**Step 1: Add `_CREATE_PROPERTY_GRAPH` constant**

Add the following constant after the existing `_CREATE_SEARCH_INDEX` constant (after line 61) and before the `_INDEXABLE_FIELDS` dict:

```python
_CREATE_PROPERTY_GRAPH = """\
CREATE PROPERTY GRAPH context_graph
VERTEX TABLES (
    nodes
        KEY (node_id)
        LABEL Session   IN labels ('Session'),
        LABEL Root       IN labels ('Root'),
        LABEL Step       IN labels ('Step'),
        LABEL PromptStep IN labels ('PromptStep'),
        LABEL Event      IN labels ('Event')
)
EDGE TABLES (
    edges
        SOURCE KEY (source) REFERENCES nodes (node_id)
        DESTINATION KEY (target) REFERENCES nodes (node_id)
        LABEL HAS_RUN        WHERE edge_type = 'HAS_RUN',
        LABEL HAS_STEP       WHERE edge_type = 'HAS_STEP',
        LABEL NEXT           WHERE edge_type = 'NEXT',
        LABEL TRIGGERED      WHERE edge_type = 'TRIGGERED',
        LABEL PARALLEL_WITH  WHERE edge_type = 'PARALLEL_WITH',
        LABEL SPAWNED        WHERE edge_type = 'SPAWNED',
        LABEL SUBSESSION_OF  WHERE edge_type = 'SUBSESSION_OF',
        LABEL HAS_EVENT      WHERE edge_type = 'HAS_EVENT'
);
"""
```

**Step 2: Add `_pgq_ready` attribute to `__init__`**

In the `__init__` method, add this line after `self._search_buffer`:

```python
        self._pgq_ready: bool = False
```

**Step 3: Add `_ensure_pgq` method**

Add this method in the `DuckDBGraphStore` class, in the "Internal helpers" section (after `_run`):

```python
    def _ensure_pgq(self) -> None:
        """Load DuckPGQ extension and create the property graph (lazy, idempotent).

        Called inside the executor when dialect="pgq" is requested.
        Safe to call multiple times — exits immediately after first success.
        """
        if self._pgq_ready:
            return
        try:
            self._conn.execute("INSTALL duckpgq; LOAD duckpgq;")
        except duckdb.CatalogException:
            # Already installed/loaded in this process — try community registry
            try:
                self._conn.execute("INSTALL duckpgq FROM community; LOAD duckpgq;")
            except duckdb.CatalogException:
                # Already loaded, continue
                self._conn.execute("LOAD duckpgq;")
        self._conn.execute("DROP PROPERTY GRAPH IF EXISTS context_graph")
        self._conn.execute(_CREATE_PROPERTY_GRAPH)
        self._pgq_ready = True
```

**Step 4: Update `supported_dialects`**

Replace the `supported_dialects` property:

```python
    @property
    def supported_dialects(self) -> frozenset[str]:
        """The set of query dialects this backend can execute."""
        return frozenset({"sql", "pgq"})
```

**Step 5: Update `execute_query` to call `_ensure_pgq` for PGQ dialect**

Replace the `execute_query` method with:

```python
    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
    ) -> list[dict[str, Any]]:
        if dialect is not None and dialect not in self.supported_dialects:
            raise ValueError(
                f"Unsupported dialect {dialect!r}; supported: {sorted(self.supported_dialects)}"
            )

        def _query() -> list[dict[str, Any]]:
            if dialect == "pgq":
                self._ensure_pgq()
            # DuckDB requires omitting params arg when none provided
            if params is not None:
                result = self._conn.execute(query, params)
            else:
                result = self._conn.execute(query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]

        return await self._run(_query)
```

**Step 6: Run PGQ tests to verify they pass**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py::TestPGQ -v
```
Expected: ALL PASS

**Step 7: Run full suite to verify no regressions**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py -v
```
Expected: ALL PASS

**Step 8: Commit**

```bash
cd amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py
git commit -m "feat: add lazy PGQ infrastructure with _ensure_pgq and property graph DDL"
```

---

### Task 5: Write FTS tests (red)

**Files:**
- Modify: `TESTS/test_duckdb_store.py`

**Step 1: Add `TestFTS` class**

Append to the end of `TESTS/test_duckdb_store.py`:

```python
# ---------------------------------------------------------------------------
# TestFTS
# ---------------------------------------------------------------------------
class TestFTS:
    """FTS lifecycle: explicit rebuild_fts_index, BM25 queries."""

    async def test_rebuild_fts_index_creates_index(self, store, seed_reference_graph):
        """After rebuild, BM25 query returns results for seeded content."""
        await store.rebuild_fts_index()
        rows = await store.execute_query(
            "SELECT si.node_id, score "
            "FROM ("
            "  SELECT *, fts_main_search_index.match_bm25(rowid, 'authentication') AS score"
            "  FROM search_index"
            ") si "
            "WHERE score IS NOT NULL"
        )
        assert len(rows) >= 1

    async def test_bm25_query_pattern_1(self, store, seed_reference_graph):
        """Pattern 1 from SKILL.md: direct FTS with BM25."""
        await store.rebuild_fts_index()
        rows = await store.execute_query(
            "SELECT si.node_id, si.session_id, si.field_name, si.content, score "
            "FROM ("
            "  SELECT *, fts_main_search_index.match_bm25(rowid, 'authentication') AS score"
            "  FROM search_index"
            ") si "
            "WHERE score IS NOT NULL "
            "ORDER BY score DESC"
        )
        assert len(rows) >= 1
        assert rows[0]["node_id"] == "55c8841a-test__prompt_submit__1737972001000"
        assert rows[0]["field_name"] == "prompt_text"

    async def test_fts_without_rebuild_has_no_results(self, store, seed_reference_graph):
        """Without rebuild_fts_index, BM25 queries return nothing."""
        # Do NOT call rebuild_fts_index — FTS index does not exist
        # The fts_main_search_index.match_bm25 function won't exist, so this should error
        # or return empty. We test that FTS is not auto-built.
        with pytest.raises(Exception):
            await store.execute_query(
                "SELECT *, fts_main_search_index.match_bm25(rowid, 'authentication') AS score "
                "FROM search_index "
                "WHERE score IS NOT NULL"
            )

    async def test_rebuild_fts_index_idempotent(self, store, seed_reference_graph):
        """Calling rebuild_fts_index twice does not error."""
        await store.rebuild_fts_index()
        await store.rebuild_fts_index()
        rows = await store.execute_query(
            "SELECT si.node_id, score "
            "FROM ("
            "  SELECT *, fts_main_search_index.match_bm25(rowid, 'authentication') AS score"
            "  FROM search_index"
            ") si "
            "WHERE score IS NOT NULL"
        )
        assert len(rows) >= 1

    async def test_fts_finds_prompt_text_content(self, store, seed_reference_graph):
        """Search for 'refactor' finds the reference prompt node."""
        await store.rebuild_fts_index()
        rows = await store.execute_query(
            "SELECT si.node_id, score "
            "FROM ("
            "  SELECT *, fts_main_search_index.match_bm25(rowid, 'refactor') AS score"
            "  FROM search_index"
            ") si "
            "WHERE score IS NOT NULL"
        )
        assert len(rows) >= 1
        node_ids = [r["node_id"] for r in rows]
        assert "55c8841a-test__prompt_submit__1737972001000" in node_ids
```

**Step 2: Run FTS tests to verify they fail**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py::TestFTS -v
```
Expected: FAIL — `rebuild_fts_index` does not exist

**Step 3: Commit (red tests)**

```bash
cd amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/tests/test_duckdb_store.py
git commit -m "test(red): add FTS lifecycle tests for rebuild and BM25 queries"
```

---

### Task 6: Implement FTS lifecycle (green)

**Files:**
- Modify: `SRC/duckdb_store.py`

**Step 1: Add `_fts_ready` attribute to `__init__`**

In the `__init__` method, add this line after `self._pgq_ready`:

```python
        self._fts_ready: bool = False
```

**Step 2: Add `build_fts_index` and `rebuild_fts_index` methods**

Add these methods to the `DuckDBGraphStore` class. Place them in a new section between the "QueryableStore" section and the "Lifecycle" section:

```python
    # ------------------------------------------------------------------
    # Full-Text Search
    # ------------------------------------------------------------------

    def build_fts_index(self) -> None:
        """Build (or rebuild) the FTS index on search_index.content.

        Synchronous — intended to be called inside the executor via rebuild_fts_index().
        Safe to call multiple times: drops existing index first (ignoring errors if absent).
        """
        try:
            self._conn.execute("PRAGMA drop_fts_index('search_index')")
        except Exception:
            pass  # Index may not exist yet — that's fine
        self._conn.execute("PRAGMA create_fts_index('search_index', 'content')")
        self._fts_ready = True

    async def rebuild_fts_index(self) -> None:
        """Rebuild the FTS index asynchronously.

        NOT called in flush() — this is an explicit action. Callers invoke it
        when they need search results to reflect recently flushed data.
        """
        await self._run(self.build_fts_index)
```

**Step 3: Run FTS tests to verify they pass**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py::TestFTS -v
```
Expected: ALL PASS

**Step 4: Run full suite to verify no regressions**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py
git commit -m "feat: add FTS lifecycle with build_fts_index and rebuild_fts_index"
```

---

### Task 7: FTS + PGQ combined query test

**Files:**
- Modify: `TESTS/test_duckdb_store.py`

**Step 1: Add `TestFTSPlusPGQ` class**

Append to the end of `TESTS/test_duckdb_store.py`:

```python
# ---------------------------------------------------------------------------
# TestFTSPlusPGQ
# ---------------------------------------------------------------------------
class TestFTSPlusPGQ:
    """Combined FTS + PGQ queries: text search feeds graph traversal."""

    async def test_pattern_2_fts_then_pgq_traversal(self, store, seed_reference_graph):
        """Pattern 2 from SKILL.md: FTS CTE + GRAPH_TABLE to walk from matched step to session.

        1. FTS finds the PromptStep via 'refactor auth'
        2. GRAPH_TABLE traverses from Session -> Run -> Step
        3. JOIN matches FTS hit to graph traversal result
        """
        await store.rebuild_fts_index()
        store._ensure_pgq()

        rows = await store.execute_query(
            "WITH hits AS ("
            "    SELECT node_id, fts_main_search_index.match_bm25(rowid, 'refactor') AS score"
            "    FROM search_index"
            "    WHERE score IS NOT NULL"
            ") "
            "SELECT hit_node, session_node, hits.score "
            "FROM hits, GRAPH_TABLE (context_graph "
            "    MATCH (s:Session)-[hr:HAS_RUN]->(r)-[hs:HAS_STEP]->(step) "
            "    WHERE step.node_id = hits.node_id "
            "    COLUMNS (step.node_id AS hit_node, s.node_id AS session_node) "
            ") gt "
            "JOIN hits ON gt.hit_node = hits.node_id "
            "ORDER BY hits.score DESC",
            dialect="pgq",
        )
        assert len(rows) >= 1
        assert rows[0]["hit_node"] == "55c8841a-test__prompt_submit__1737972001000"
        assert rows[0]["session_node"] == "55c8841a-test"
        assert rows[0]["score"] is not None
```

**Step 2: Run to verify it passes (implementation already exists)**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_duckdb_store.py::TestFTSPlusPGQ -v
```
Expected: PASS

**Step 3: Commit**

```bash
cd amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/tests/test_duckdb_store.py
git commit -m "test: add FTS + PGQ combined query test (Pattern 2 from SKILL.md)"
```

---

### Task 8: Update SKILL.md — add `rebuild_fts_index` note

**Files:**
- Modify: `amplifier-bundle-context-intelligence/skills/context-intelligence-graph-search/SKILL.md`

**Step 1: Add rebuild_fts_index note to the existing Notes section**

The SKILL.md already has a "Notes" section with "FTS index rebuild timing" (lines 262-273). Add a paragraph about the programmatic API. Find the text block under `### FTS index rebuild timing` that ends with "batch rebuilds at query time if staleness is acceptable." and add after it:

```markdown

In the DuckDB backend, use `await store.rebuild_fts_index()` to trigger a
rebuild programmatically. This calls `PRAGMA drop_fts_index` then
`PRAGMA create_fts_index` inside the executor. It is NOT called
automatically by `flush()` — callers must invoke it explicitly when they
need search results to be current.
```

Also verify the property graph DDL in SKILL.md uses `context_graph` (it already does at line 99). No change needed there.

**Step 2: Commit**

```bash
cd amplifier-bundle-context-intelligence
git add skills/context-intelligence-graph-search/SKILL.md
git commit -m "docs: add rebuild_fts_index note to SKILL.md"
```

---

### Task 9: Update AGENTS.md standing rule

**Files:**
- Modify: `AGENTS.md` (workspace root)

**Step 1: Update the standing rule text**

In `AGENTS.md`, line 147 currently reads:

> Currently: `skills/context-intelligence-graph-search/SKILL.md` covers the `"sql"` dialect (DuckDB backend).

Replace that sentence with:

> Currently: `skills/context-intelligence-graph-search/SKILL.md` covers the `"sql"` and `"pgq"` dialects (DuckDB backend). The DuckDB backend reports `supported_dialects = {"sql", "pgq"}`.

**Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md standing rule for pgq dialect support"
```

---

### Task 10: Full suite verification

**Files:** None (verification only)

**Step 1: Run full pytest**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/ -v
```
Expected: ALL PASS

**Step 2: Run pyright type checking**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pyright amplifier_module_hook_context_intelligence/
```
Expected: 0 errors

**Step 3: Run ruff linting and formatting check**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run ruff check amplifier_module_hook_context_intelligence/ tests/
uv run ruff format --check amplifier_module_hook_context_intelligence/ tests/
```
Expected: No errors, no formatting issues

**Step 4: Fix any issues found**

If any checks fail, fix them and re-run. Common issues:
- `ruff format` — run `uv run ruff format amplifier_module_hook_context_intelligence/ tests/` to auto-fix
- `ruff check` — run `uv run ruff check --fix amplifier_module_hook_context_intelligence/ tests/`
- `pyright` — fix type annotations as needed

**Step 5: Final commit (if fixes were needed)**

```bash
cd amplifier-bundle-context-intelligence
git add -A
git commit -m "chore: code quality fixes from pyright/ruff"
```

---

## Implementation Checklist

| # | What | Status |
|---|------|--------|
| 1 | Reference test data fixture in `conftest.py` | ⬜ |
| 2 | Update existing tests to use reference data | ⬜ |
| 3 | PGQ tests (red) | ⬜ |
| 4 | PGQ implementation (green) | ⬜ |
| 5 | FTS tests (red) | ⬜ |
| 6 | FTS implementation (green) | ⬜ |
| 7 | FTS + PGQ combined test | ⬜ |
| 8 | Update SKILL.md | ⬜ |
| 9 | Update AGENTS.md | ⬜ |
| 10 | Full suite verification | ⬜ |

## Key Design Constraints

- **Graph name:** `context_graph` everywhere (matches SKILL.md)
- **`_ensure_pgq` is lazy:** Only called when `dialect="pgq"` is passed to `execute_query`
- **`rebuild_fts_index` is NOT in flush:** It's a separate explicit action
- **`TestBufferWrites` keeps synthetic data:** Pure mechanics test, domain labels irrelevant
- **`INSTALL duckpgq` may need fallback:** Handle `CatalogException` for already-installed extension
- **FTS index is static:** Must be rebuilt after new data is flushed to `search_index`
