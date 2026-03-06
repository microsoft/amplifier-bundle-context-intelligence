# Remove Dead `execute_query` Stubs Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Remove the dead `execute_query` stubs from `FileGraphStore` and `GraphState` that exist only to raise `NotImplementedError`, completing the ISP cleanup from the QueryableStore protocol split.
**Architecture:** After the protocol split (task-1), `execute_query` belongs exclusively to `QueryableStore`. `FileGraphStore` and `GraphState` conform only to the base `GraphStore` protocol, so their `execute_query` stubs are dead code. We delete them and their tests.
**Tech Stack:** Python 3.11+, pytest 8+ with pytest-asyncio (asyncio_mode = "auto")
**Dependency:** Requires task-1-split-protocol to be completed first (the `GraphStore` protocol must already exclude `execute_query`).

> **Spec Review Warning:** The spec review loop exhausted 3 iterations before reaching approval.
> The final verdict was APPROVED with all checks passing, but the iteration count warrants
> human reviewer attention during the approval gate. All deletions, test results, and commit
> message were verified correct in the final review.

---

**Working directory for all commands:**
```
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
```

All file paths below are relative to this working directory.

---

### Task 1: Verify green baseline

**Files:** (none modified)

**Step 1: Run the full test suite to confirm green starting state**

```bash
.venv/bin/python -m pytest tests/test_file_store.py tests/test_services.py tests/test_graph_store.py -v
```

Expected: All tests PASS (approximately 54 tests). If any test fails, stop and investigate before proceeding.

---

### Task 2: Remove `execute_query` from `FileGraphStore`

**Files:**
- Modify: `amplifier_module_hook_context_intelligence/file_store.py`
- Modify: `tests/test_file_store.py`

**Step 1: Delete the `execute_query` method from `FileGraphStore`**

In `amplifier_module_hook_context_intelligence/file_store.py`, delete the entire `# Query` section and `execute_query` method. The block to remove sits between the end of `flush()` (the `await self._run(_write)` line) and the `# Lifecycle` section comment.

Delete these exact lines:

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

After deletion, the `# Lifecycle` section should immediately follow `await self._run(_write)` with one blank line between them.

The end of the file should look like this after the edit:

```python
        await self._run(_write)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Shut down the store.  Flushes pending data first."""
        await self.flush()
```

**Step 2: Delete the `TestExecuteQuery` class from `test_file_store.py`**

In `tests/test_file_store.py`, delete the entire section 6 block (the `# 6. execute_query` comment, the `TestExecuteQuery` class, and all its contents):

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

**Step 3: Renumber the remaining test sections**

After the deletion, renumber the section comments to maintain sequential numbering:

- Change `# 7. close` to `# 6. close`
- Change `# 8. Persistence (close + reopen)` to `# 7. Persistence (close + reopen)`

The result should read:

```python
# ---------------------------------------------------------------------------
# 6. close
# ---------------------------------------------------------------------------
```

and:

```python
# ---------------------------------------------------------------------------
# 7. Persistence (close + reopen)
# ---------------------------------------------------------------------------
```

**Step 4: Run file_store tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_file_store.py -v
```

Expected: All tests PASS. The `TestExecuteQuery` class should no longer appear in the output. You should see tests from: `TestProtocolConformance`, `TestConstructor`, `TestBufferWrites`, `TestBufferFirstReads`, `TestFlush`, `TestClose`, `TestPersistence`.

---

### Task 3: Remove `execute_query` from `GraphState`

**Files:**
- Modify: `amplifier_module_hook_context_intelligence/services.py`
- Modify: `tests/test_services.py`

**Step 1: Delete the `execute_query` method from `GraphState`**

In `amplifier_module_hook_context_intelligence/services.py`, delete the `execute_query` method from the `GraphState` class. The block to remove sits between the end of `upsert_edge()` (the `self._edges[key] = edge` line) and the `flush()` method.

Delete these exact lines:

```python
    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "In-memory GraphState does not support execute_query. "
            "Use a DuckDB-backed GraphStore for query support."
        )
```

After deletion, the `flush()` method should immediately follow `self._edges[key] = edge` with one blank line between them.

The result should look like:

```python
        self._edges[key] = edge

    async def flush(self) -> None:
        pass
```

**Step 2: Delete the `test_execute_query_raises_not_implemented` method from `test_services.py`**

In `tests/test_services.py`, delete the entire test method from the `TestGraphState` class:

```python
    async def test_execute_query_raises_not_implemented(self):
        import pytest

        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        with pytest.raises(NotImplementedError):
            await graph.execute_query("MATCH (n) RETURN n")
```

After deletion, the `TestGraphState` class should end at `test_close_is_noop`, and `TestHookStateService` should follow after a blank line.

**Step 3: Run services tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_services.py -v
```

Expected: All tests PASS. The `test_execute_query_raises_not_implemented` method should no longer appear in the output.

---

### Task 4: Final verification and commit

**Files:** (none modified)

**Step 1: Verify no stale `execute_query` references on non-queryable stores**

```bash
grep -rn "execute_query" amplifier_module_hook_context_intelligence/ tests/
```

Expected: `execute_query` should appear ONLY in:
- `graph_store.py` — the `QueryableStore` protocol definition
- `duckdb_store.py` — the DuckDB implementation of `QueryableStore`
- `test_graph_store.py` — protocol conformance tests for `QueryableStore`
- `test_duckdb_store.py` — DuckDB-specific `execute_query` tests

It must NOT appear in `file_store.py`, `services.py`, `test_file_store.py`, or `test_services.py`.

**Step 2: Run the full acceptance test suite**

```bash
.venv/bin/python -m pytest tests/test_file_store.py tests/test_services.py tests/test_graph_store.py -v
```

Expected: All tests PASS (approximately 54 tests), zero failures, zero errors.

**Step 3: Run code quality checks**

```bash
.venv/bin/python -m ruff check amplifier_module_hook_context_intelligence/ tests/
.venv/bin/python -m ruff format --check amplifier_module_hook_context_intelligence/ tests/
```

Expected: No errors.

**Step 4: Commit**

```bash
git add amplifier_module_hook_context_intelligence/file_store.py amplifier_module_hook_context_intelligence/services.py tests/test_file_store.py tests/test_services.py
git commit -m 'refactor: remove execute_query stubs from FileGraphStore and GraphState'
```

Expected: Clean commit with exactly 4 files changed (deletions only, plus section renumbering in test_file_store.py).