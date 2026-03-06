# QueryableStore Protocol Split Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Split `GraphStore` into a base protocol (write/read/lifecycle) and an extended `QueryableStore` protocol (query + dialect discovery). Remove dead `execute_query` stubs from non-queryable stores. Update tests, docs, and standing rules.

**Architecture:** `GraphStore` keeps 6 methods (upsert/get/flush/close). New `QueryableStore(GraphStore, Protocol)` adds `supported_dialects` property and `execute_query` with optional `dialect` parameter. `DuckDBGraphStore` implements `QueryableStore`; `FileGraphStore` and `GraphState` implement only `GraphStore`. Skills are scoped per dialect.

**Tech Stack:** Python 3.11+, pytest (asyncio_mode=auto), DuckDB (existing), no new dependencies.

**Design doc:** `docs/plans/2026-03-06-queryable-store-protocol-split-design.md`

---

## Notation

All file paths are relative to the workspace root `/home/dicolomb/context-itelligence-bundle-v2`.

The module source root is:
```
amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/
```
Abbreviated as `SRC/` in commentary. The test root is:
```
amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/
```
Abbreviated as `TESTS/`.

All `uv run pytest` commands run from:
```
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
```

---

## Task 1: Split protocol — `GraphStore` base + `QueryableStore` extension

**Files:**
- Modify: `SRC/graph_store.py`
- Modify: `TESTS/test_graph_store.py`

### Step 1: Update protocol tests

Replace the entire contents of `TESTS/test_graph_store.py` with:

```python
"""Tests for the GraphStore and QueryableStore async protocols."""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# GraphStore (base protocol — no execute_query)
# ---------------------------------------------------------------------------


def test_graph_store_is_runtime_checkable():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    assert hasattr(GraphStore, "__protocol_attrs__") or hasattr(GraphStore, "_is_runtime_protocol")


def test_conforming_class_passes_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class FakeStore:
        async def upsert_node(
            self, node_id: str, labels: set[str], properties: dict[str, Any]
        ) -> None: ...
        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...
        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...
        async def flush(self) -> None: ...
        async def close(self) -> None: ...

    store = FakeStore()
    assert isinstance(store, GraphStore)


def test_class_with_execute_query_still_passes_graph_store():
    """A class with extra methods still satisfies the base protocol."""
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class StoreWithExtras:
        async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None: ...
        async def upsert_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any]) -> None: ...
        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
        async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None: ...
        async def flush(self) -> None: ...
        async def close(self) -> None: ...
        async def execute_query(self, query: str) -> list[dict[str, Any]]: ...

    assert isinstance(StoreWithExtras(), GraphStore)


def test_missing_upsert_node_fails_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class BadStore:
        async def upsert_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any]) -> None: ...
        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
        async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None: ...
        async def flush(self) -> None: ...
        async def close(self) -> None: ...

    store = BadStore()
    assert not isinstance(store, GraphStore)


def test_missing_flush_fails_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class BadStore:
        async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None: ...
        async def upsert_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any]) -> None: ...
        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
        async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None: ...
        async def close(self) -> None: ...

    store = BadStore()
    assert not isinstance(store, GraphStore)


def test_graph_state_conforms_to_graph_store():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore
    from amplifier_module_hook_context_intelligence.services import GraphState

    graph = GraphState()
    assert isinstance(graph, GraphStore)


# ---------------------------------------------------------------------------
# QueryableStore (extends GraphStore with query + dialect discovery)
# ---------------------------------------------------------------------------


def test_queryable_store_is_runtime_checkable():
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    assert hasattr(QueryableStore, "__protocol_attrs__") or hasattr(QueryableStore, "_is_runtime_protocol")


def test_queryable_conforming_class_passes_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    class FakeQueryable:
        async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None: ...
        async def upsert_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any]) -> None: ...
        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
        async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None: ...
        async def flush(self) -> None: ...
        async def close(self) -> None: ...

        @property
        def supported_dialects(self) -> frozenset[str]:
            return frozenset({"sql"})

        async def execute_query(
            self, query: str, params: dict[str, Any] | None = None, dialect: str | None = None
        ) -> list[dict[str, Any]]: ...

    assert isinstance(FakeQueryable(), QueryableStore)


def test_queryable_missing_supported_dialects_fails():
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    class MissingDialects:
        async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None: ...
        async def upsert_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any]) -> None: ...
        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
        async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None: ...
        async def flush(self) -> None: ...
        async def close(self) -> None: ...
        async def execute_query(self, query: str, params: dict[str, Any] | None = None, dialect: str | None = None) -> list[dict[str, Any]]: ...

    assert not isinstance(MissingDialects(), QueryableStore)


def test_base_graph_store_is_not_queryable():
    """GraphState satisfies GraphStore but NOT QueryableStore."""
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore
    from amplifier_module_hook_context_intelligence.services import GraphState

    assert not isinstance(GraphState(), QueryableStore)


def test_duckdb_store_is_queryable():
    from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    store = DuckDBGraphStore()
    assert isinstance(store, QueryableStore)
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py -v
```

Expected: FAIL — `QueryableStore` doesn't exist yet. Tests referencing it will error on import.

### Step 3: Implement the protocol split

Replace the entire contents of `SRC/graph_store.py` with:

```python
"""GraphStore and QueryableStore protocols — async interfaces for graph storage.

Non-negotiable guarantees (GraphStore)
--------------------------------------
1. upsert_node / upsert_edge MUST return immediately (buffer, no I/O).
2. get_node / get_edge MUST reflect buffered state (buffer-first reads).
3. flush() persists buffered writes (called by lifecycle triggers, not handlers).
4. close() MUST call flush() before releasing resources.
5. Flush failure MUST NOT propagate to handlers.

QueryableStore extension
------------------------
Stores that support structured queries extend GraphStore with:
- supported_dialects: declares what query languages the store accepts.
- execute_query: runs a query in the specified (or default) dialect.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphStore(Protocol):
    """Async protocol for graph storage backends.

    Implementations buffer writes in memory and expose buffer-first reads so
    that handlers always see a consistent, up-to-date view without waiting for
    I/O.  Persistence is driven by lifecycle triggers via ``flush()``.
    """

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        """Insert or update a node.

        Merge semantics: new properties merge with existing.  New keys added,
        existing overwritten, unmentioned preserved.  Labels unioned.
        """
        ...

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        """Insert or update an edge.

        Identity is (source, target, edge_type).  Same merge semantics as nodes.
        """
        ...

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node by ID.  Must reflect buffered state."""
        ...

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        """Retrieve an edge by composite key.  Must reflect buffered state."""
        ...

    async def flush(self) -> None:
        """Persist buffered writes."""
        ...

    async def close(self) -> None:
        """Shut down the store.  Must call flush() before releasing resources."""
        ...


@runtime_checkable
class QueryableStore(GraphStore, Protocol):
    """Extended protocol for stores that support structured queries.

    ``supported_dialects`` advertises which query languages the store accepts
    (e.g. ``{"sql"}``, ``{"cypher"}``).  ``execute_query`` runs a query in
    the specified dialect, or the store's default if ``dialect`` is ``None``.
    """

    @property
    def supported_dialects(self) -> frozenset[str]:
        """Query dialects this store supports (e.g. frozenset({"sql"}))."""
        ...

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None, dialect: str | None = None
    ) -> list[dict[str, Any]]:
        """Execute a query in the given dialect.

        If ``dialect`` is ``None``, use the store's default.
        If ``dialect`` is not in ``supported_dialects``, raise ``ValueError``.
        """
        ...
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py -v
```

Expected: ALL PASS.

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_store.py modules/hook-context-intelligence/tests/test_graph_store.py && git commit -m "feat: split GraphStore into base + QueryableStore protocol"
```

---

## Task 2: Update `DuckDBGraphStore` to implement `QueryableStore`

**Files:**
- Modify: `SRC/duckdb_store.py`
- Modify: `TESTS/test_duckdb_store.py`

### Step 1: Add new tests for dialect support

In `TESTS/test_duckdb_store.py`, find the `TestExecuteQuery` class (line 240). Replace it with:

```python
class TestExecuteQuery:
    """execute_query returns list of dicts using column names."""

    async def test_execute_query_returns_list_of_dicts(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        rows = await store.execute_query("SELECT node_id, labels FROM nodes")
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert "node_id" in rows[0]
        assert "labels" in rows[0]
        assert rows[0]["node_id"] == "n1"

    async def test_supported_dialects_returns_frozenset(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        dialects = store.supported_dialects
        assert isinstance(dialects, frozenset)
        assert "sql" in dialects

    async def test_execute_query_with_explicit_sql_dialect(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", {"X"}, {})
        await store.flush()
        rows = await store.execute_query("SELECT node_id FROM nodes", dialect="sql")
        assert len(rows) == 1

    async def test_execute_query_with_none_dialect_uses_default(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", {"X"}, {})
        await store.flush()
        rows = await store.execute_query("SELECT node_id FROM nodes", dialect=None)
        assert len(rows) == 1

    async def test_execute_query_with_invalid_dialect_raises(self):
        import pytest

        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        with pytest.raises(ValueError, match="Unsupported dialect"):
            await store.execute_query("MATCH (n) RETURN n", dialect="cypher")
```

### Step 2: Run new tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestExecuteQuery -v
```

Expected: FAIL — `supported_dialects` property doesn't exist, `dialect` parameter not accepted.

### Step 3: Update `DuckDBGraphStore` implementation

In `SRC/duckdb_store.py`, make two changes:

**Change A:** Add `supported_dialects` property after `_index_searchable_content` method (after line 114), before the `upsert_node` method:

Insert between line 114 and 116:

```python
    @property
    def supported_dialects(self) -> frozenset[str]:
        """Query dialects supported by this store."""
        return frozenset({"sql"})

```

**Change B:** Replace the `execute_query` method (lines 267-278) with:

```python
    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None, dialect: str | None = None
    ) -> list[dict[str, Any]]:
        if dialect is not None and dialect not in self.supported_dialects:
            raise ValueError(
                f"Unsupported dialect: {dialect!r}. "
                f"Supported: {', '.join(sorted(self.supported_dialects))}"
            )

        def _query() -> list[dict[str, Any]]:
            if params:
                result = self._conn.execute(query, params)
            else:
                result = self._conn.execute(query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]

        return await self._run(_query)
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: ALL PASS.

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py modules/hook-context-intelligence/tests/test_duckdb_store.py && git commit -m "feat: DuckDBGraphStore implements QueryableStore with dialect support"
```

---

## Task 3: Remove `execute_query` from `FileGraphStore` and `GraphState`

**Files:**
- Modify: `SRC/file_store.py`
- Modify: `SRC/services.py`
- Modify: `TESTS/test_file_store.py`
- Modify: `TESTS/test_services.py`

### Step 1: Remove `execute_query` from `FileGraphStore`

In `SRC/file_store.py`, delete lines 211-221 (the entire `# Query` section and `execute_query` method):

```python
    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Not supported — use grep/jq on the JSON files directly."""
        raise NotImplementedError(
            "FileGraphStore does not support execute_query. Use grep/jq on the JSON files directly."
        )
```

### Step 2: Remove `execute_query` from `GraphState`

In `SRC/services.py`, delete lines 72-78 (the `execute_query` method on `GraphState`):

```python
    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "In-memory GraphState does not support execute_query. "
            "Use a DuckDB-backed GraphStore for query support."
        )
```

### Step 3: Remove `TestExecuteQuery` from `test_file_store.py`

In `TESTS/test_file_store.py`, delete lines 275-286 (the `# 6. execute_query` comment block and entire `TestExecuteQuery` class):

```python
# ---------------------------------------------------------------------------
# 6. execute_query
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    """execute_query is not supported for file store."""

    async def test_raises_not_implemented(self, tmp_path: Path) -> None:
        store = FileGraphStore(location=str(tmp_path / "graph"))
        with pytest.raises(NotImplementedError, match="grep.*jq"):
            await store.execute_query("SELECT 1")
```

### Step 4: Remove `test_execute_query_raises_not_implemented` from `test_services.py`

In `TESTS/test_services.py`, delete lines 124-131 (the `test_execute_query_raises_not_implemented` method):

```python
    async def test_execute_query_raises_not_implemented(self):
        import pytest

        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        with pytest.raises(NotImplementedError):
            await graph.execute_query("MATCH (n) RETURN n")
```

### Step 5: Run tests to verify everything passes

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_file_store.py tests/test_services.py tests/test_graph_store.py -v
```

Expected: ALL PASS. No test references `execute_query` on non-queryable stores.

### Step 6: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/file_store.py modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py modules/hook-context-intelligence/tests/test_file_store.py modules/hook-context-intelligence/tests/test_services.py && git commit -m "refactor: remove execute_query stubs from FileGraphStore and GraphState"
```

---

## Task 4: Update SKILL.md to scope as SQL dialect skill

**Files:**
- Modify: `amplifier-bundle-context-intelligence/skills/context-intelligence-graph-search/SKILL.md`

### Step 1: Update the skill header and description

In `skills/context-intelligence-graph-search/SKILL.md`, replace lines 1-12 (the frontmatter and opening paragraph) with:

```markdown
---
name: context-intelligence-graph-search
description: SQL and PGQ query patterns for QueryableStore backends reporting 'sql' in supported_dialects
version: 0.2.0
license: MIT
---

# Context Intelligence Graph Search (SQL Dialect)

**Dialect scope:** This skill applies to `QueryableStore` backends that report `"sql"` in `supported_dialects` (currently: DuckDB).

Query patterns for searching and traversing the context-intelligence graph stored in DuckDB.
Covers plain SQL, DuckDB full-text search (FTS), and ISO/IEC SQL/PGQ property-graph queries
via the DuckPGQ extension.
```

Also replace the "Multiple Storage Backends" subsection (lines 39-41) with:

```markdown
### Multiple Storage Backends

The graph can be stored in multiple backends. Query capability is declared via the `QueryableStore` protocol — check `store.supported_dialects` to discover what's available. This skill covers the `"sql"` dialect (DuckDB). Future dialect-specific skills will cover other backends.

The DuckDB schema below applies only to the DuckDB backend.
```

### Step 2: Commit

```bash
cd amplifier-bundle-context-intelligence && git add skills/context-intelligence-graph-search/SKILL.md && git commit -m "docs: scope SKILL.md as SQL dialect skill for QueryableStore"
```

---

## Task 5: Update AGENTS.md standing rules

**Files:**
- Modify: `/home/dicolomb/context-itelligence-bundle-v2/AGENTS.md`

### Step 1: Update Schema-Skill Synchronization rule

In `AGENTS.md`, replace lines 145-151 with:

```markdown
### Standing Rule: Schema-Skill Synchronization

Any change to a storage backend's schema MUST be accompanied by an update to the *relevant dialect skill*. Each skill is scoped to a query dialect (e.g., SQL, Cypher, grep/jq) and declares which `QueryableStore.supported_dialects` value it covers. Currently: `skills/context-intelligence-graph-search/SKILL.md` covers the `"sql"` dialect (DuckDB backend).

This includes: new tables, column changes, property graph definition changes, new label types, new edge types, new query patterns, new `field_name` values in search_index.

This rule is permanently enforced via docstrings in store implementations and cross-references in each dialect skill. This AGENTS.md note is for workspace visibility only.
```

### Step 2: Update Storage Implementation Parity rule

In `AGENTS.md`, replace lines 153-162 with:

```markdown
### Standing Rule: Storage Implementation Parity

With multiple `GraphStore` implementations (DuckDB, File-based), we must ensure:

- **Format parity**: Node IDs and edge IDs are generated by shared functions in `utils.py` (`make_node_id`, `make_edge_id`). ALL implementations use the same IDs. This enables reconciliation between stores.
- **Write/Read parity**: All implementations MUST support the full `GraphStore` protocol (`upsert_node`, `upsert_edge`, `get_node`, `get_edge`, `flush`, `close`) with identical merge-on-upsert semantics (labels unioned, properties updated).
- **Query capabilities are opt-in**: Only stores implementing `QueryableStore` support `execute_query`. Check `isinstance(store, QueryableStore)` and `store.supported_dialects` before querying.
- **Non-blocking writes**: Core protocol requirement for ALL implementations. No exceptions.

When adding a new `GraphStore` implementation, verify it passes the same behavioral tests as existing implementations (write, read, merge, flush, close). If it supports queries, it must also implement `QueryableStore` with accurate `supported_dialects`.
```

### Step 3: Commit

```bash
cd /home/dicolomb/context-itelligence-bundle-v2 && git add AGENTS.md && git commit -m "docs: update standing rules for QueryableStore protocol split"
```

---

## Task 6: Full test suite verification

**Files:** None (verification only)

### Step 1: Run the full test suite

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: ALL tests PASS. Zero failures.

### Step 2: Run type checker

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pyright amplifier_module_hook_context_intelligence/
```

Expected: 0 errors. Warnings are OK.

### Step 3: Run linter

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run ruff check amplifier_module_hook_context_intelligence/ tests/
```

Expected: 0 errors.

### Step 4: Run formatter check

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run ruff format --check amplifier_module_hook_context_intelligence/ tests/
```

Expected: All files formatted. If not, run `uv run ruff format amplifier_module_hook_context_intelligence/ tests/` and add the changes.

### Step 5: Fix any issues and commit

If any checks failed, fix the issues and commit. Then re-run all checks.

```bash
cd amplifier-bundle-context-intelligence && git add -A && git commit -m "chore: fix lint/type issues from QueryableStore split"
```

Only commit this if there were actual fixes needed. If everything was clean, skip this step.

---

## Summary of all files changed

| File | Action | Task |
|------|--------|------|
| `SRC/graph_store.py` | Modify | 1 |
| `TESTS/test_graph_store.py` | Modify | 1 |
| `SRC/duckdb_store.py` | Modify | 2 |
| `TESTS/test_duckdb_store.py` | Modify | 2 |
| `SRC/file_store.py` | Modify | 3 |
| `SRC/services.py` | Modify | 3 |
| `TESTS/test_file_store.py` | Modify | 3 |
| `TESTS/test_services.py` | Modify | 3 |
| `skills/context-intelligence-graph-search/SKILL.md` | Modify | 4 |
| `AGENTS.md` (workspace root) | Modify | 5 |

## Commit sequence

1. `feat: split GraphStore into base + QueryableStore protocol`
2. `feat: DuckDBGraphStore implements QueryableStore with dialect support`
3. `refactor: remove execute_query stubs from FileGraphStore and GraphState`
4. `docs: scope SKILL.md as SQL dialect skill for QueryableStore`
5. `docs: update standing rules for QueryableStore protocol split`
6. `chore: fix lint/type issues from QueryableStore split` (only if needed)