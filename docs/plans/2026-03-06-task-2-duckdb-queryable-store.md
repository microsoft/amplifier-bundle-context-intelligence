# DuckDBGraphStore QueryableStore Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

> **Quality Review Notice:** The automated quality review loop exhausted after 3 iterations
> without programmatic approval. The final (3rd) verdict was **APPROVED** with no critical or
> important issues. The reviewer noted the implementation is "clean, idiomatic, well-tested,
> and follows established patterns." A human reviewer should verify this task at the approval
> gate given the loop exhaustion. Key refinement during review: `if params:` was corrected to
> `if params is not None:` to properly distinguish `None` from `{}` per the type contract.

**Goal:** Update `DuckDBGraphStore` to implement the `QueryableStore` protocol by adding a `supported_dialects` property and updating `execute_query` to accept an optional `dialect` parameter with validation.

**Architecture:** `DuckDBGraphStore` already has `execute_query`. This task adds a `supported_dialects` property returning `frozenset({"sql"})` and extends `execute_query` with an optional `dialect` parameter. Invalid dialects raise `ValueError`. The inner `_query` closure pattern matches existing methods (`get_node`, `get_edge`, `flush`).

**Tech Stack:** Python 3.11+, pytest (asyncio_mode=auto), DuckDB, no new dependencies.

**Depends on:** Task 1 (`feat: split GraphStore into base + QueryableStore protocol`) must be committed first — `QueryableStore` must exist in `graph_store.py`.

---

## Notation

All file paths are relative to the workspace root `/home/dicolomb/context-itelligence-bundle-v2`.

The module source root is:
```
amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/
```
Abbreviated as `SRC/` below.

The test root is:
```
amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/
```
Abbreviated as `TESTS/` below.

All `uv run pytest` commands run from:
```
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
```

---

## Task 2A: Write failing tests for dialect support

**Files:**
- Modify: `TESTS/test_duckdb_store.py`

### Step 1: Replace the `TestExecuteQuery` class with dialect-aware tests

In `TESTS/test_duckdb_store.py`, find the existing `TestExecuteQuery` class (starts at approximately line 240 with the section header comment). Replace **the entire class and its section comment** (from `# ---------------------------------------------------------------------------` / `# TestExecuteQuery` through the end of the class) with:

```python
# ---------------------------------------------------------------------------
# TestExecuteQuery
# ---------------------------------------------------------------------------
class TestExecuteQuery:
    """execute_query returns list of dicts and supports dialect validation."""

    async def test_execute_query_returns_list_of_dicts(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        rows = await store.execute_query("SELECT node_id, labels FROM nodes")
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert "node_id" in rows[0]
        assert "labels" in rows[0]
        assert rows[0]["node_id"] == "n1"

    def test_supported_dialects_returns_frozenset(self, store):
        dialects = store.supported_dialects
        assert isinstance(dialects, frozenset)
        assert "sql" in dialects

    async def test_execute_query_with_explicit_sql_dialect(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        rows = await store.execute_query("SELECT node_id FROM nodes", dialect="sql")
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["node_id"] == "n1"

    async def test_execute_query_with_none_dialect_uses_default(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        rows = await store.execute_query("SELECT node_id FROM nodes", dialect=None)
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["node_id"] == "n1"

    async def test_execute_query_with_params(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.upsert_node("n2", {"Person"}, {"name": "Bob"})
        await store.flush()
        rows = await store.execute_query(
            "SELECT node_id FROM nodes WHERE node_id = $node_id",
            params={"node_id": "n1"},
        )
        assert len(rows) == 1
        assert rows[0]["node_id"] == "n1"

    async def test_execute_query_with_invalid_dialect_raises(self, store):
        with pytest.raises(ValueError, match="Unsupported dialect"):
            await store.execute_query("SELECT 1", dialect="cypher")
```

**Key conventions this follows** (verified from the existing file):
- All test methods use the shared `store` fixture from line 14 (not inline `DuckDBGraphStore()` instantiation)
- `pytest` is already imported at the top of the file (line 8)
- Async tests don't need decorators — `asyncio_mode = "auto"` is set in `pyproject.toml` line 37
- `test_supported_dialects_returns_frozenset` is sync (`def`, not `async def`) because the property is synchronous
- `test_execute_query_with_params` exercises the parameterized path with DuckDB's `$name` syntax
- Each test is independent and doesn't depend on state from other tests

### Step 2: Run the new tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestExecuteQuery -v
```

Expected failures:
- `test_supported_dialects_returns_frozenset` — FAIL: `AttributeError: 'DuckDBGraphStore' object has no attribute 'supported_dialects'`
- `test_execute_query_with_explicit_sql_dialect` — FAIL: `TypeError: execute_query() got an unexpected keyword argument 'dialect'`
- `test_execute_query_with_none_dialect_uses_default` — FAIL: same `TypeError`
- `test_execute_query_with_invalid_dialect_raises` — FAIL: same `TypeError`
- `test_execute_query_returns_list_of_dicts` — may PASS or FAIL depending on current `execute_query` return type
- `test_execute_query_with_params` — may PASS or FAIL depending on current `execute_query` params handling

---

## Task 2B: Implement `supported_dialects` property

**Files:**
- Modify: `SRC/duckdb_store.py`

### Step 1: Add the `supported_dialects` property

In `SRC/duckdb_store.py`, insert the following **after** the `_index_searchable_content` method (after the line containing the closing parenthesis of the last `self._search_buffer.append(...)` block, approximately line 114) and **before** the `upsert_node` method:

```python
    @property
    def supported_dialects(self) -> frozenset[str]:
        """The set of query dialects this backend can execute."""
        return frozenset({"sql"})
```

**Placement context** — the property goes between these two existing blocks:

```python
                )  # <-- end of _index_searchable_content method body

    @property                                    # <-- INSERT HERE
    def supported_dialects(self) -> frozenset[str]:
        """The set of query dialects this backend can execute."""
        return frozenset({"sql"})

    async def upsert_node(self, node_id: str, ...  # <-- existing method
```

### Step 2: Run the dialect property test to verify it passes

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestExecuteQuery::test_supported_dialects_returns_frozenset -v
```

Expected: PASS.

---

## Task 2C: Update `execute_query` with dialect parameter

**Files:**
- Modify: `SRC/duckdb_store.py`

### Step 1: Replace the `execute_query` method

Find the existing `execute_query` method in `SRC/duckdb_store.py` (in the `# QueryableStore` section, approximately lines 267-278). Replace the entire method with:

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
            # DuckDB requires omitting params arg when none provided
            if params is not None:
                result = self._conn.execute(query, params)
            else:
                result = self._conn.execute(query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]

        return await self._run(_query)
```

**Critical detail:** The params check MUST be `if params is not None:` (not `if params:`). This correctly distinguishes `None` (omit params entirely from `conn.execute()`) from `{}` (pass empty dict). DuckDB's `execute()` behaves differently when params are omitted vs passed as empty. The `dict[str, Any] | None` type signature requires this explicit None check.

**Section header context** — the method lives under this existing comment:

```python
    # ------------------------------------------------------------------
    # QueryableStore
    # ------------------------------------------------------------------

    async def execute_query(  # <-- THIS IS WHAT YOU'RE REPLACING
```

### Step 2: Run all `TestExecuteQuery` tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestExecuteQuery -v
```

Expected: ALL 6 PASS:
- `test_execute_query_returns_list_of_dicts` — PASS
- `test_supported_dialects_returns_frozenset` — PASS
- `test_execute_query_with_explicit_sql_dialect` — PASS
- `test_execute_query_with_none_dialect_uses_default` — PASS
- `test_execute_query_with_params` — PASS
- `test_execute_query_with_invalid_dialect_raises` — PASS

### Step 3: Run the full `test_duckdb_store.py` suite to verify no regressions

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: ALL 47 tests PASS. Zero failures, zero errors.

### Step 4: Run linter and type checker

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run ruff check amplifier_module_hook_context_intelligence/duckdb_store.py tests/test_duckdb_store.py && uv run ruff format --check amplifier_module_hook_context_intelligence/duckdb_store.py tests/test_duckdb_store.py
```

Expected: 0 errors, all files formatted. If formatting issues, run:
```bash
uv run ruff format amplifier_module_hook_context_intelligence/duckdb_store.py tests/test_duckdb_store.py
```

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/duckdb_store.py modules/hook-context-intelligence/tests/test_duckdb_store.py && git commit -m "feat: DuckDBGraphStore implements QueryableStore with dialect support"
```

---

## Acceptance Criteria Checklist

| Criterion | Verification |
|-----------|-------------|
| All tests in `test_duckdb_store.py` pass (47 total) | `uv run pytest tests/test_duckdb_store.py -v` shows 47 passed |
| `DuckDBGraphStore` has `supported_dialects` property | `test_supported_dialects_returns_frozenset` passes |
| `supported_dialects` returns `frozenset({"sql"})` | Test asserts `isinstance(dialects, frozenset)` and `"sql" in dialects` |
| `execute_query` accepts optional `dialect` parameter | `test_execute_query_with_explicit_sql_dialect` passes |
| `dialect='cypher'` raises `ValueError` with `"Unsupported dialect"` | `test_execute_query_with_invalid_dialect_raises` passes with `match="Unsupported dialect"` |
| `dialect=None` works correctly (default behavior) | `test_execute_query_with_none_dialect_uses_default` passes |
| `dialect='sql'` works correctly (explicit valid dialect) | `test_execute_query_with_explicit_sql_dialect` passes |
| `params` passthrough works with DuckDB `$name` syntax | `test_execute_query_with_params` passes |
| Commit message matches spec | `feat: DuckDBGraphStore implements QueryableStore with dialect support` |

## Files Changed Summary

| File | Action | Sub-task |
|------|--------|----------|
| `TESTS/test_duckdb_store.py` | Modify (replace `TestExecuteQuery` class) | 2A |
| `SRC/duckdb_store.py` | Modify (add `supported_dialects` property) | 2B |
| `SRC/duckdb_store.py` | Modify (replace `execute_query` method) | 2C |
