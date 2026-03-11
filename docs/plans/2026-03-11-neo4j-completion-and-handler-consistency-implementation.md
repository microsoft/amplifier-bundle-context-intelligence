# Neo4j Completion and Handler Consistency — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Complete the Neo4j data model implementation end-to-end — eliminate stubs, fix timestamp types, ensure all 8 edge types carry `occurred_at`, and clean up handler file organization.

**Architecture:** Six independent tasks touching `neo4j_store.py` (indexes, comments, timestamp conversion, forest filter), `tool_execution.py` (two missing `occurred_at` properties), a file move for `logging_handler.py`, new integration tests for all 8 edge types, and a docs update to `AGENTS.md`. No new components — all changes within existing module structure.

**Tech Stack:** Python 3.11+, pytest with pytest-asyncio (mode=auto), Neo4j 5.x (test container at `localhost:7690`), neo4j Python driver.

**Design doc:** `docs/plans/2026-03-11-neo4j-completion-and-handler-consistency-design.md`

---

## Orientation

All paths below are relative to the **module root**:

```
modules/hook-context-intelligence/
```

The Python package inside is:

```
amplifier_module_hook_context_intelligence/
```

Tests live in:

```
tests/
```

Run all tests with:

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/ -v
```

The live Neo4j test container runs at `neo4j://localhost:7690` (Bolt) / `localhost:7480` (HTTP). Auth comes from env vars `NEO4J_USER`/`NEO4J_PASSWORD`, defaulting to `None` (no-auth mode).

---

## Task 1: Move `logging_handler.py` into `handlers/`

**Why:** Every other handler lives in `handlers/`. `logging_handler.py` sits alone at the module root next to infrastructure files. Move it for consistency.

**Dependencies:** None.

**Files:**
- Move: `amplifier_module_hook_context_intelligence/logging_handler.py` → `amplifier_module_hook_context_intelligence/handlers/logging_handler.py`
- Modify: `amplifier_module_hook_context_intelligence/__init__.py` (one import)
- Modify: `amplifier_module_hook_context_intelligence/handlers/__init__.py` (docstring)
- Verify: `tests/test_logging_handler.py` (imports use package path — check if they break)

### Step 1: Check existing test imports to understand what needs updating

Look at `tests/test_logging_handler.py`. Every test class uses inline imports like:

```python
from amplifier_module_hook_context_intelligence.logging_handler import LoggingHandler
```

After the move, this path becomes:

```python
from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler
```

There are **14 occurrences** of this import scattered across the test file (one per test method, inline). You need to update all of them.

### Step 2: Move the file

```bash
cd modules/hook-context-intelligence
mv amplifier_module_hook_context_intelligence/logging_handler.py \
   amplifier_module_hook_context_intelligence/handlers/logging_handler.py
```

### Step 3: Update the import in `__init__.py`

In `amplifier_module_hook_context_intelligence/__init__.py`, line 86, change:

```python
    from .logging_handler import LoggingHandler
```

to:

```python
    from .handlers.logging_handler import LoggingHandler
```

### Step 4: Update the `handlers/__init__.py` docstring

Replace the entire content of `amplifier_module_hook_context_intelligence/handlers/__init__.py` with:

```python
"""Event handlers for the context-intelligence hook module.

Eight handlers, each conforming to the EventHandler protocol:
- SessionHandler — :Session nodes
- OrchestratorRunHandler — :OrchestratorRun and :Step:PromptStep nodes
- StepHandler — :Step:AssistantStep nodes
- RecipeHandler — recipe orchestration events (:Event:RecipeStart, :Event:RecipeStep, etc.)
- ToolExecutionHandler — :ToolExecution nodes
- SystemEventHandler — :Event:ContextCompaction, :Event:CancelRequested, etc.
- DefaultHandler — :Event:{DerivedFullScope} (dynamic labels)
- LoggingHandler — always-on flat JSONL session file writer (no graph dependency)
"""
```

### Step 5: Update all test imports in `tests/test_logging_handler.py`

Find and replace **all** occurrences across the file:

Old:
```python
from amplifier_module_hook_context_intelligence.logging_handler import LoggingHandler
```

New:
```python
from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler
```

Also find and replace **all** occurrences of:

Old:
```python
from amplifier_module_hook_context_intelligence.logging_handler import (
    _sanitize_for_json,
)
```

New:
```python
from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _sanitize_for_json,
)
```

### Step 6: Run the logging handler tests to verify nothing broke

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_logging_handler.py -v
```

**Expected:** All 25 tests pass. Zero import errors.

### Step 7: Run the full test suite to catch any other import breakage

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/ -v
```

**Expected:** All tests pass.

### Step 8: Commit

```bash
cd modules/hook-context-intelligence
git add -A
git commit -m "refactor: move logging_handler.py into handlers/ for consistency"
```

---

## Task 2: Neo4j Store Polish — Stale Comments + Missing Indexes + `get_edge` Forest Filter

**Why:** Three small fixes: (a) misleading "stubbed" comments on fully-implemented methods, (b) missing indexes for 4 node labels the data model queries by, (c) `get_edge` Neo4j fallback doesn't filter the relationship's `graph_forest_name` property.

**Dependencies:** None.

**Files:**
- Modify: `amplifier_module_hook_context_intelligence/neo4j_store.py`
- Modify: `tests/test_neo4j_store.py` (new tests for indexes + forest filter)

### Step 1: Write tests for the 4 new indexes

Add these tests to the `TestSchemaInitialization` class in `tests/test_neo4j_store.py`, after the existing `test_forest_index_exists_after_flush` test (after line 627):

```python
    @pytest.mark.asyncio
    async def test_orchestrator_run_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on OrchestratorRun.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"OrchestratorRun"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "OrchestratorRun" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No OrchestratorRun index on node_id. Found: {records}"

    @pytest.mark.asyncio
    async def test_step_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on Step.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"Step"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "Step" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No Step index on node_id. Found: {records}"

    @pytest.mark.asyncio
    async def test_tool_execution_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on ToolExecution.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"ToolExecution"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "ToolExecution" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No ToolExecution index on node_id. Found: {records}"

    @pytest.mark.asyncio
    async def test_event_index_exists_after_flush(self, neo4j_store):
        """After flush, an index on Event.node_id must exist."""
        await neo4j_store.upsert_node("n1", {"Event"}, {"k": "v"})
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]

        found = any(
            record["labelsOrTypes"] is not None
            and "Event" in record["labelsOrTypes"]
            and record["properties"] is not None
            and "node_id" in record["properties"]
            for record in records
        )
        assert found, f"No Event index on node_id. Found: {records}"
```

### Step 2: Write a test for the `get_edge` forest filter

Add a new test class after `TestForestScoping` (after line 961) in `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestGetEdgeForestFilter
# ---------------------------------------------------------------------------
class TestGetEdgeForestFilter:
    """get_edge Neo4j fallback must filter by relationship's graph_forest_name."""

    @pytest.mark.asyncio
    async def test_get_edge_does_not_return_edge_from_other_forest(self):
        """An edge written in forest-a must NOT be visible via get_edge in forest-b."""
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store_a = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-a",
        )
        store_b = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="forest-b",
        )
        try:
            # Clean all data
            async with store_a._driver.session(database=store_a._database) as session:
                await session.run("MATCH (n) DETACH DELETE n")

            # Write nodes in BOTH forests (same node_id, different forest)
            await store_a.upsert_node("shared-src", {"X"}, {})
            await store_a.upsert_node("shared-tgt", {"X"}, {})
            await store_a.upsert_edge("shared-src", "shared-tgt", "LINKS", {"val": "from-a"})
            await store_a.flush()

            await store_b.upsert_node("shared-src", {"X"}, {})
            await store_b.upsert_node("shared-tgt", {"X"}, {})
            await store_b.flush()

            # get_edge from store_b should NOT see the edge written by store_a
            result = await store_b.get_edge("shared-src", "shared-tgt", "LINKS")
            assert result is None, (
                f"get_edge in forest-b should not see edge from forest-a, got: {result}"
            )
        finally:
            await store_a.close()
            await store_b.close()
```

### Step 3: Run the new tests to verify they fail

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_neo4j_store.py::TestSchemaInitialization::test_orchestrator_run_index_exists_after_flush tests/test_neo4j_store.py::TestSchemaInitialization::test_step_index_exists_after_flush tests/test_neo4j_store.py::TestSchemaInitialization::test_tool_execution_index_exists_after_flush tests/test_neo4j_store.py::TestSchemaInitialization::test_event_index_exists_after_flush tests/test_neo4j_store.py::TestGetEdgeForestFilter -v
```

**Expected:** 4 index tests FAIL (indexes don't exist yet). The forest filter test FAILS (edge from forest-a leaks to forest-b).

### Step 4: Fix stale comments in `neo4j_store.py`

In `amplifier_module_hook_context_intelligence/neo4j_store.py`:

**Line 66** — change:

```python
    # -- GraphStore methods (stubbed) ----------------------------------------
```

to:

```python
    # -- GraphStore methods --------------------------------------------------
```

**Line 292** — change:

```python
    # -- QueryableStore methods (stubbed) ------------------------------------
```

to:

```python
    # -- QueryableStore methods ----------------------------------------------
```

### Step 5: Add 4 new indexes to `_ensure_schema()`

In `amplifier_module_hook_context_intelligence/neo4j_store.py`, in the `_ensure_schema` method, after the existing 3 `session.run` calls (after line 279), add 4 more:

```python
                await session.run(
                    "CREATE INDEX idx_orchestrator_run_node_id IF NOT EXISTS "
                    "FOR (n:OrchestratorRun) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_step_node_id IF NOT EXISTS FOR (n:Step) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_tool_execution_node_id IF NOT EXISTS "
                    "FOR (n:ToolExecution) ON (n.node_id)"
                )
                await session.run(
                    "CREATE INDEX idx_event_node_id IF NOT EXISTS FOR (n:Event) ON (n.node_id)"
                )
```

### Step 6: Fix the `get_edge` forest filter

In `amplifier_module_hook_context_intelligence/neo4j_store.py`, in the `get_edge` method, replace the Neo4j fallback query (lines 136–144):

Old:

```python
            result = await session.run(
                "MATCH (s {node_id: $source, graph_forest_name: $gfn})"
                "-[r]->"
                "(t {node_id: $target, graph_forest_name: $gfn}) "
                "WHERE type(r) = $edge_type RETURN r",
                source=source,
                target=target,
                edge_type=edge_type,
                gfn=self._graph_forest_name,
            )
```

New:

```python
            result = await session.run(
                "MATCH (s {node_id: $source, graph_forest_name: $gfn})"
                "-[r]->"
                "(t {node_id: $target, graph_forest_name: $gfn}) "
                "WHERE type(r) = $edge_type AND r.graph_forest_name = $gfn RETURN r",
                source=source,
                target=target,
                edge_type=edge_type,
                gfn=self._graph_forest_name,
            )
```

The only change is adding `AND r.graph_forest_name = $gfn` to the `WHERE` clause.

### Step 7: Run the new tests to verify they pass

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_neo4j_store.py::TestSchemaInitialization::test_orchestrator_run_index_exists_after_flush tests/test_neo4j_store.py::TestSchemaInitialization::test_step_index_exists_after_flush tests/test_neo4j_store.py::TestSchemaInitialization::test_tool_execution_index_exists_after_flush tests/test_neo4j_store.py::TestSchemaInitialization::test_event_index_exists_after_flush tests/test_neo4j_store.py::TestGetEdgeForestFilter -v
```

**Expected:** All 5 tests PASS.

### Step 8: Run the full neo4j_store test suite to verify no regressions

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_neo4j_store.py -v
```

**Expected:** All tests pass (existing + new).

### Step 9: Commit

```bash
cd modules/hook-context-intelligence
git add -A
git commit -m "fix: neo4j store polish — remove stale comments, add missing indexes, fix get_edge forest filter"
```

---

## Task 3: Timestamp `*_at` Conversion in `flush()`

**Why:** All timestamps flow through as ISO-8601 strings from the kernel. Neo4j stores them as `String` type instead of native `DateTime`. This breaks temporal queries (`<`, `>`, `ORDER BY`, `duration.between()`). Fix by converting in the store layer before writing.

**Dependencies:** None.

**Files:**
- Modify: `amplifier_module_hook_context_intelligence/neo4j_store.py`
- Modify: `tests/test_neo4j_store.py` (new tests for timestamp conversion)

### Step 1: Write tests for timestamp conversion

Add a new test class at the end of `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestTimestampConversion
# ---------------------------------------------------------------------------
class TestTimestampConversion:
    """flush() must convert *_at string properties to native Neo4j DateTime."""

    @pytest.mark.asyncio
    async def test_node_occurred_at_stored_as_datetime(self, neo4j_store):
        """After flush, occurred_at must be stored as native DateTime, not String."""
        await neo4j_store.upsert_node(
            "ts-n1", {"Event"}, {"occurred_at": "2026-01-15T10:00:00+00:00", "name": "test"}
        )
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: 'ts-n1'}) "
                "RETURN apoc.meta.cypher.type(n.occurred_at) AS type",
            )
            record = await result.single()

        # If apoc is not installed, fall back to a different check
        if record is None or record["type"] is None:
            # Alternative: check Python driver returns datetime object
            async with neo4j_store._driver.session(database=neo4j_store._database) as session:
                result = await session.run(
                    "MATCH (n {node_id: 'ts-n1'}) RETURN n.occurred_at AS val"
                )
                record = await result.single()
            assert record is not None
            from neo4j.time import DateTime as Neo4jDateTime

            assert isinstance(record["val"], Neo4jDateTime), (
                f"Expected Neo4jDateTime, got {type(record['val'])}: {record['val']}"
            )
        else:
            assert "DATE_TIME" in record["type"].upper() or "DATETIME" in record["type"].upper(), (
                f"Expected DateTime type, got: {record['type']}"
            )

    @pytest.mark.asyncio
    async def test_node_started_at_stored_as_datetime(self, neo4j_store):
        """started_at must also be converted to native DateTime."""
        await neo4j_store.upsert_node(
            "ts-n2", {"Session"}, {"started_at": "2026-01-15T10:00:00Z", "status": "running"}
        )
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: 'ts-n2'}) RETURN n.started_at AS val"
            )
            record = await result.single()

        assert record is not None
        from neo4j.time import DateTime as Neo4jDateTime

        assert isinstance(record["val"], Neo4jDateTime), (
            f"Expected Neo4jDateTime, got {type(record['val'])}: {record['val']}"
        )

    @pytest.mark.asyncio
    async def test_edge_occurred_at_stored_as_datetime(self, neo4j_store):
        """Edge occurred_at must also be converted to native DateTime."""
        await neo4j_store.upsert_node("ts-src", {"A"}, {})
        await neo4j_store.upsert_node("ts-tgt", {"B"}, {})
        await neo4j_store.upsert_edge(
            "ts-src", "ts-tgt", "HAS_RUN", {"occurred_at": "2026-01-15T10:00:00Z", "seq": 1}
        )
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH ()-[r:HAS_RUN]->() RETURN r.occurred_at AS val"
            )
            record = await result.single()

        assert record is not None
        from neo4j.time import DateTime as Neo4jDateTime

        assert isinstance(record["val"], Neo4jDateTime), (
            f"Expected Neo4jDateTime, got {type(record['val'])}: {record['val']}"
        )

    @pytest.mark.asyncio
    async def test_non_at_properties_unchanged(self, neo4j_store):
        """Properties NOT ending in _at must not be converted."""
        await neo4j_store.upsert_node(
            "ts-n3", {"Tag"}, {"name": "Alice", "status": "running"}
        )
        await neo4j_store.flush()

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: 'ts-n3'}) RETURN n.name AS name, n.status AS status"
            )
            record = await result.single()

        assert record is not None
        assert isinstance(record["name"], str)
        assert record["name"] == "Alice"
        assert isinstance(record["status"], str)

    @pytest.mark.asyncio
    async def test_malformed_timestamp_passes_through_as_string(self, neo4j_store):
        """If a *_at value is not a valid ISO-8601 string, pass it through unchanged."""
        await neo4j_store.upsert_node(
            "ts-n4", {"Tag"}, {"occurred_at": "not-a-timestamp", "name": "test"}
        )
        await neo4j_store.flush()  # must not raise

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: 'ts-n4'}) RETURN n.occurred_at AS val"
            )
            record = await result.single()

        assert record is not None
        assert record["val"] == "not-a-timestamp"

    @pytest.mark.asyncio
    async def test_empty_string_timestamp_passes_through(self, neo4j_store):
        """Empty string *_at values pass through unchanged (handlers use '' as default)."""
        await neo4j_store.upsert_node(
            "ts-n5", {"Tag"}, {"occurred_at": "", "name": "test"}
        )
        await neo4j_store.flush()  # must not raise

        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: 'ts-n5'}) RETURN n.occurred_at AS val"
            )
            record = await result.single()

        assert record is not None
        assert record["val"] == ""
```

### Step 2: Run the new tests to verify they fail

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_neo4j_store.py::TestTimestampConversion -v
```

**Expected:** The first 3 tests FAIL (timestamps stored as strings, not DateTime). The last 3 should PASS even before the change (pass-through behavior).

### Step 3: Implement timestamp conversion in `flush()`

In `amplifier_module_hook_context_intelligence/neo4j_store.py`, add this import at the top of the file (after the existing imports, after line 4):

```python
from datetime import datetime, timezone
```

Then add this private helper method to the `Neo4jGraphStore` class, right after the `_BASE_LABEL = "Node"` line (after line 96):

```python
    @staticmethod
    def _convert_timestamps(props: dict[str, Any]) -> dict[str, Any]:
        """Convert *_at string properties to datetime objects for native Neo4j DateTime storage.

        Any property whose key ends with '_at' and whose value is a non-empty string
        is parsed via datetime.fromisoformat(). If parsing fails, the original string
        is kept and a warning is logged.
        """
        converted = dict(props)
        for key, value in converted.items():
            if key.endswith("_at") and isinstance(value, str) and value:
                try:
                    converted[key] = datetime.fromisoformat(value)
                except (ValueError, TypeError):
                    logger.warning("Failed to parse timestamp %r=%r, keeping as string", key, value)
        return converted
```

Now modify the `flush()` method to call `_convert_timestamps` on properties before writing.

**For nodes** — in the `flush()` method, inside the node_rows loop, change (around line 192):

Old:

```python
                        row: dict[str, Any] = {
                            "node_id": node_id,
                            "props": {
                                **entry["properties"],
                                "node_id": node_id,
                                "graph_forest_name": self._graph_forest_name,
                            },
                            "labels": list(entry["labels"]),
                        }
```

New:

```python
                        row: dict[str, Any] = {
                            "node_id": node_id,
                            "props": {
                                **self._convert_timestamps(entry["properties"]),
                                "node_id": node_id,
                                "graph_forest_name": self._graph_forest_name,
                            },
                            "labels": list(entry["labels"]),
                        }
```

**For edges** — in the `flush()` method, inside the edge_type_groups loop, change (around line 233):

Old:

```python
                        edge_type_groups.setdefault(etype, []).append(
                            {
                                "source": entry["source"],
                                "target": entry["target"],
                                "props": {
                                    **entry["properties"],
                                    "graph_forest_name": self._graph_forest_name,
                                },
                            }
                        )
```

New:

```python
                        edge_type_groups.setdefault(etype, []).append(
                            {
                                "source": entry["source"],
                                "target": entry["target"],
                                "props": {
                                    **self._convert_timestamps(entry["properties"]),
                                    "graph_forest_name": self._graph_forest_name,
                                },
                            }
                        )
```

### Step 4: Run the timestamp tests to verify they pass

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_neo4j_store.py::TestTimestampConversion -v
```

**Expected:** All 6 tests PASS.

### Step 5: Run the full test suite to verify no regressions

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_neo4j_store.py -v
```

**Expected:** All tests pass. Some existing tests pass string timestamps — they now get converted to datetime, but the existing assertions still hold because they check property values (not types).

### Step 6: Commit

```bash
cd modules/hook-context-intelligence
git add -A
git commit -m "feat: convert *_at string properties to native Neo4j DateTime in flush()"
```

---

## Task 4: Add `occurred_at` to `PARALLEL_WITH` and `SPAWNED` Edges

**Why:** 6 of 8 edge types already carry `occurred_at`. `PARALLEL_WITH` and `SPAWNED` pass empty `{}`. For consistency, all 8 edge types get `occurred_at`.

**Dependencies:** None.

**Files:**
- Modify: `amplifier_module_hook_context_intelligence/handlers/tool_execution.py`
- Modify: `tests/test_neo4j_store.py` (or a handler-level test — we'll add integration coverage in Task 5)

### Step 1: Write a focused unit test for the two edges

We'll test this through the handler by checking what gets buffered. Add to the end of `tests/test_neo4j_store.py` (we're testing the handler output through the graph store buffer):

> **Note:** We test these at the integration level in Task 5. For now, a focused check in `test_neo4j_store.py` confirms the properties are set.

Actually, these edges are created by the `ToolExecutionHandler` which requires `HookStateService` wiring. It's cleaner to verify these in Task 5's integration tests. For this task, we'll just make the code change and verify it didn't break existing tests.

### Step 2: Add `occurred_at` to `PARALLEL_WITH` edge

In `amplifier_module_hook_context_intelligence/handlers/tool_execution.py`, in the `_handle_tool_pre` method, change the `PARALLEL_WITH` edge creation (lines 92–97):

Old:

```python
                await self.services.graph.upsert_edge(
                    te_id,
                    existing_te_id,
                    "PARALLEL_WITH",
                    {},
                )
```

New:

```python
                await self.services.graph.upsert_edge(
                    te_id,
                    existing_te_id,
                    "PARALLEL_WITH",
                    {"occurred_at": timestamp},
                )
```

### Step 3: Add `occurred_at` to `SPAWNED` edge

In the same file, in the `_handle_delegate_agent_spawned` method, change the `SPAWNED` edge creation (lines 196–201):

Old:

```python
            await self.services.graph.upsert_edge(
                te_id,
                child_session_id,
                "SPAWNED",
                {},
            )
```

New:

```python
            await self.services.graph.upsert_edge(
                te_id,
                child_session_id,
                "SPAWNED",
                {"occurred_at": data.get("timestamp", "")},
            )
```

> **Note:** In `_handle_delegate_agent_spawned`, `timestamp` is not extracted as a local variable (unlike `_handle_tool_pre`). Use `data.get("timestamp", "")` directly.

### Step 4: Run existing tests to verify no regressions

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/ -v
```

**Expected:** All tests pass. No handler tests currently assert on `PARALLEL_WITH` or `SPAWNED` edge properties, so nothing breaks.

### Step 5: Commit

```bash
cd modules/hook-context-intelligence
git add -A
git commit -m "feat: add occurred_at to PARALLEL_WITH and SPAWNED edges for consistency"
```

---

## Task 5: Integration Tests for All 8 Edge Types

**Why:** The existing reference graph covers 3 edge types (`HAS_RUN`, `HAS_STEP`, `TRIGGERED`). We need end-to-end verification of all 8 edge types with `occurred_at` and native `DateTime` storage.

**Dependencies:** Tasks 2, 3, 4 must be completed first (needs indexes, native timestamps, and `occurred_at` on all edges).

**Files:**
- Modify: `tests/conftest.py` (expand reference graph with nodes and edges for all 8 types)
- Create: `tests/test_neo4j_edge_types.py` (new integration test file)

### Step 1: Expand reference graph in `conftest.py`

Replace the content of `tests/conftest.py` with the following. This adds new reference IDs, expands `reference_nodes()` to 9 nodes, and expands `reference_edges()` to cover all 8 edge types:

```python
"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

import os
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService

# ---------------------------------------------------------------------------
# Neo4j test connection constants (shared across test modules)
# ---------------------------------------------------------------------------
NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j://localhost:7690")
_neo4j_user = os.environ.get("NEO4J_USER")
_neo4j_pass = os.environ.get("NEO4J_PASSWORD")
NEO4J_AUTH = (_neo4j_user, _neo4j_pass) if _neo4j_user and _neo4j_pass else None
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# ---------------------------------------------------------------------------
# Reference IDs – mirror the make_node_id format used by handlers
# ---------------------------------------------------------------------------
SESSION_ID = "55c8841a-test"
SESSION_NODE_ID = "55c8841a-test"
RUN_NODE_ID = "55c8841a-test__execution_start__1737972000000"
PROMPT_NODE_ID = "55c8841a-test__prompt_submit__1737972001000"
TOOL_NODE_ID = "55c8841a-test__tool_pre__1737972002000"

# New reference IDs for expanded graph
ASSISTANT_STEP_NODE_ID = "55c8841a-test__provider_request__1737972003000"
TOOL_NODE_2_ID = "55c8841a-test__tool_pre__1737972004000"
DELEGATION_TE_NODE_ID = "55c8841a-test__tool_pre__1737972005000"
CHILD_SESSION_ID = "child-session-001"
CHILD_SESSION_NODE_ID = "child-session-001"
EVENT_NODE_ID = "55c8841a-test__context_compaction__1737972006000"

# Reference timestamp used across all edges
REF_TIMESTAMP = "2026-01-15T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Reference graph helpers (public API used by tests)
# ---------------------------------------------------------------------------
def reference_nodes() -> list[tuple[str, set[str], dict[str, Any]]]:
    """Return the 9 canonical node tuples for the expanded reference session graph."""
    return [
        # 1. Root session
        (
            SESSION_NODE_ID,
            {"Session", "Root"},
            {
                "session_id": SESSION_ID,
                "status": "running",
                "started_at": REF_TIMESTAMP,
            },
        ),
        # 2. OrchestratorRun
        (
            RUN_NODE_ID,
            {"OrchestratorRun"},
            {
                "session_id": SESSION_ID,
                "run_number": 1,
                "status": "running",
                "started_at": REF_TIMESTAMP,
            },
        ),
        # 3. PromptStep
        (
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "session_id": SESSION_ID,
                "iteration": 0,
                "prompt_text": "Help me refactor the authentication module",
                "prompt_preview": "Help me refactor the authentication module",
                "occurred_at": REF_TIMESTAMP,
            },
        ),
        # 4. ToolExecution (first)
        (
            TOOL_NODE_ID,
            {"ToolExecution"},
            {
                "session_id": SESSION_ID,
                "tool_name": "read_file",
                "tool_call_id": "call_001",
                "parallel_group_id": "pg_001",
                "status": "complete",
                "started_at": REF_TIMESTAMP,
            },
        ),
        # 5. AssistantStep (for NEXT edge: PromptStep -> AssistantStep)
        (
            ASSISTANT_STEP_NODE_ID,
            {"Step", "AssistantStep"},
            {
                "session_id": SESSION_ID,
                "iteration": 1,
                "provider": "anthropic",
                "request_at": REF_TIMESTAMP,
                "occurred_at": REF_TIMESTAMP,
            },
        ),
        # 6. ToolExecution (second, same parallel_group for PARALLEL_WITH)
        (
            TOOL_NODE_2_ID,
            {"ToolExecution"},
            {
                "session_id": SESSION_ID,
                "tool_name": "grep",
                "tool_call_id": "call_002",
                "parallel_group_id": "pg_001",
                "status": "complete",
                "started_at": REF_TIMESTAMP,
            },
        ),
        # 7. Delegation ToolExecution (for SPAWNED edge)
        (
            DELEGATION_TE_NODE_ID,
            {"ToolExecution", "Delegation"},
            {
                "session_id": SESSION_ID,
                "tool_name": "delegate",
                "tool_call_id": "call_003",
                "child_session_id": CHILD_SESSION_ID,
                "child_agent": "code-review",
                "started_at": REF_TIMESTAMP,
            },
        ),
        # 8. Child session (for SPAWNED + SUBSESSION_OF edges)
        (
            CHILD_SESSION_NODE_ID,
            {"Session", "Subsession"},
            {
                "session_id": CHILD_SESSION_ID,
                "status": "running",
                "started_at": REF_TIMESTAMP,
            },
        ),
        # 9. Event node (for HAS_EVENT edge)
        (
            EVENT_NODE_ID,
            {"Event", "ContextCompaction"},
            {
                "event_name": "context:compaction",
                "occurred_at": REF_TIMESTAMP,
            },
        ),
    ]


def reference_edges() -> list[tuple[str, str, str, dict[str, Any]]]:
    """Return the 8 canonical edge tuples covering all 8 edge types."""
    return [
        # 1. HAS_RUN: Session -> OrchestratorRun
        (SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1, "occurred_at": REF_TIMESTAMP}),
        # 2. HAS_STEP: OrchestratorRun -> PromptStep
        (RUN_NODE_ID, PROMPT_NODE_ID, "HAS_STEP", {"seq": 0, "occurred_at": REF_TIMESTAMP}),
        # 3. TRIGGERED: PromptStep -> ToolExecution
        (PROMPT_NODE_ID, TOOL_NODE_ID, "TRIGGERED", {"seq": 1, "occurred_at": REF_TIMESTAMP}),
        # 4. NEXT: PromptStep -> AssistantStep
        (PROMPT_NODE_ID, ASSISTANT_STEP_NODE_ID, "NEXT", {"occurred_at": REF_TIMESTAMP}),
        # 5. PARALLEL_WITH: ToolExecution <-> ToolExecution (same parallel_group)
        (TOOL_NODE_ID, TOOL_NODE_2_ID, "PARALLEL_WITH", {"occurred_at": REF_TIMESTAMP}),
        # 6. SPAWNED: Delegation ToolExecution -> Child Session
        (DELEGATION_TE_NODE_ID, CHILD_SESSION_NODE_ID, "SPAWNED", {"occurred_at": REF_TIMESTAMP}),
        # 7. SUBSESSION_OF: Child Session -> Root Session
        (CHILD_SESSION_NODE_ID, SESSION_NODE_ID, "SUBSESSION_OF", {"occurred_at": REF_TIMESTAMP}),
        # 8. HAS_EVENT: Session -> Event
        (SESSION_NODE_ID, EVENT_NODE_ID, "HAS_EVENT", {"occurred_at": REF_TIMESTAMP}),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService using default GraphState (no external store)."""
    return HookStateService(raw_config={})


@pytest.fixture
async def seed_reference_graph(store: Any) -> None:
    """Upsert all reference nodes and edges into *store*, then flush."""
    for node_id, labels, props in reference_nodes():
        await store.upsert_node(node_id, labels, props)
    for src, tgt, etype, props in reference_edges():
        await store.upsert_edge(src, tgt, etype, props)
    await store.flush()
```

### Step 2: Create the new integration test file

Create `tests/test_neo4j_edge_types.py`:

```python
"""Integration tests — all 8 edge types verified end-to-end against live Neo4j.

Tests run against the neo4j-test-env container at localhost:7690.
Each test seeds the expanded reference graph, flushes to Neo4j, then verifies
edge existence, direction, properties, and native DateTime storage via raw Cypher.
"""

from __future__ import annotations

import pytest
from neo4j.time import DateTime as Neo4jDateTime

from tests.conftest import (
    ASSISTANT_STEP_NODE_ID,
    CHILD_SESSION_NODE_ID,
    DELEGATION_TE_NODE_ID,
    EVENT_NODE_ID,
    NEO4J_AUTH,
    NEO4J_DATABASE,
    NEO4J_URI,
    PROMPT_NODE_ID,
    RUN_NODE_ID,
    SESSION_NODE_ID,
    TOOL_NODE_2_ID,
    TOOL_NODE_ID,
    reference_edges,
    reference_nodes,
)


# ---------------------------------------------------------------------------
# Shared fixture: seeded Neo4j store with full reference graph
# ---------------------------------------------------------------------------
@pytest.fixture
async def seeded_store():
    """Neo4j store with all 9 reference nodes and 8 reference edges flushed."""
    from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH, database=NEO4J_DATABASE)
    try:
        # Clean all data
        async with store._driver.session(database=store._database) as session:
            await session.run("MATCH (n) DETACH DELETE n")

        # Seed reference graph
        for node_id, labels, props in reference_nodes():
            await store.upsert_node(node_id, labels, props)
        for src, tgt, etype, props in reference_edges():
            await store.upsert_edge(src, tgt, etype, props)
        await store.flush()

        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Helper: verify edge via raw Cypher
# ---------------------------------------------------------------------------
async def _assert_edge_exists(
    store,
    source_id: str,
    target_id: str,
    edge_type: str,
    *,
    expect_occurred_at: bool = True,
    expect_seq: int | None = None,
):
    """Verify edge exists in Neo4j with correct direction and properties."""
    async with store._driver.session(database=store._database) as session:
        result = await session.run(
            f"MATCH (s {{node_id: $src}})-[r:`{edge_type}`]->(t {{node_id: $tgt}}) "  # noqa: S608
            "RETURN r, type(r) AS rel_type",
            src=source_id,
            tgt=target_id,
        )
        record = await result.single()

    assert record is not None, (
        f"Edge {edge_type} from {source_id} -> {target_id} not found in Neo4j"
    )
    assert record["rel_type"] == edge_type

    rel_props = dict(record["r"])

    # All edges must have occurred_at as native DateTime
    if expect_occurred_at:
        assert "occurred_at" in rel_props, (
            f"Edge {edge_type} missing occurred_at property. Props: {rel_props}"
        )
        assert isinstance(rel_props["occurred_at"], Neo4jDateTime), (
            f"Edge {edge_type} occurred_at should be Neo4jDateTime, "
            f"got {type(rel_props['occurred_at'])}: {rel_props['occurred_at']}"
        )

    # Some edges have seq
    if expect_seq is not None:
        assert rel_props.get("seq") == expect_seq, (
            f"Edge {edge_type} seq expected {expect_seq}, got {rel_props.get('seq')}"
        )

    return rel_props


# ---------------------------------------------------------------------------
# Tests — one per edge type
# ---------------------------------------------------------------------------
class TestEdgeTypeHasRun:
    """HAS_RUN: Session -> OrchestratorRun"""

    @pytest.mark.asyncio
    async def test_has_run_exists_with_seq_and_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            SESSION_NODE_ID,
            RUN_NODE_ID,
            "HAS_RUN",
            expect_seq=1,
        )


class TestEdgeTypeHasStep:
    """HAS_STEP: OrchestratorRun -> Step"""

    @pytest.mark.asyncio
    async def test_has_step_exists_with_seq_and_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            RUN_NODE_ID,
            PROMPT_NODE_ID,
            "HAS_STEP",
            expect_seq=0,
        )


class TestEdgeTypeTriggered:
    """TRIGGERED: Step -> ToolExecution"""

    @pytest.mark.asyncio
    async def test_triggered_exists_with_seq_and_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            PROMPT_NODE_ID,
            TOOL_NODE_ID,
            "TRIGGERED",
            expect_seq=1,
        )


class TestEdgeTypeNext:
    """NEXT: Step -> Step (sequential step ordering)"""

    @pytest.mark.asyncio
    async def test_next_exists_with_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            PROMPT_NODE_ID,
            ASSISTANT_STEP_NODE_ID,
            "NEXT",
        )


class TestEdgeTypeParallelWith:
    """PARALLEL_WITH: ToolExecution <-> ToolExecution (same parallel group)"""

    @pytest.mark.asyncio
    async def test_parallel_with_exists_with_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            TOOL_NODE_ID,
            TOOL_NODE_2_ID,
            "PARALLEL_WITH",
        )


class TestEdgeTypeSpawned:
    """SPAWNED: Delegation ToolExecution -> Child Session"""

    @pytest.mark.asyncio
    async def test_spawned_exists_with_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            DELEGATION_TE_NODE_ID,
            CHILD_SESSION_NODE_ID,
            "SPAWNED",
        )


class TestEdgeTypeSubsessionOf:
    """SUBSESSION_OF: Child Session -> Parent Session"""

    @pytest.mark.asyncio
    async def test_subsession_of_exists_with_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            CHILD_SESSION_NODE_ID,
            SESSION_NODE_ID,
            "SUBSESSION_OF",
        )


class TestEdgeTypeHasEvent:
    """HAS_EVENT: Session -> Event"""

    @pytest.mark.asyncio
    async def test_has_event_exists_with_datetime(self, seeded_store):
        await _assert_edge_exists(
            seeded_store,
            SESSION_NODE_ID,
            EVENT_NODE_ID,
            "HAS_EVENT",
        )


# ---------------------------------------------------------------------------
# Cross-cutting: all 8 edges present after single seed+flush
# ---------------------------------------------------------------------------
class TestAllEdgeTypesPresent:
    """After seeding and flushing, all 8 edge types must exist in Neo4j."""

    @pytest.mark.asyncio
    async def test_all_eight_edge_types_present(self, seeded_store):
        """Query all relationship types in Neo4j and verify all 8 are present."""
        async with seeded_store._driver.session(database=seeded_store._database) as session:
            result = await session.run(
                "MATCH ()-[r]->() RETURN DISTINCT type(r) AS rel_type ORDER BY rel_type"
            )
            records = [record async for record in result]

        rel_types = {r["rel_type"] for r in records}
        expected = {
            "HAS_RUN",
            "HAS_STEP",
            "TRIGGERED",
            "NEXT",
            "PARALLEL_WITH",
            "SPAWNED",
            "SUBSESSION_OF",
            "HAS_EVENT",
        }
        assert expected.issubset(rel_types), (
            f"Missing edge types: {expected - rel_types}. Found: {rel_types}"
        )

    @pytest.mark.asyncio
    async def test_all_edges_have_occurred_at_as_datetime(self, seeded_store):
        """Every edge in the reference graph must have occurred_at as native DateTime."""
        async with seeded_store._driver.session(database=seeded_store._database) as session:
            result = await session.run(
                "MATCH ()-[r]->() "
                "WHERE r.graph_forest_name = $gfn "
                "RETURN type(r) AS rel_type, r.occurred_at AS occurred_at",
                gfn=seeded_store.graph_forest_name,
            )
            records = [record async for record in result]

        assert len(records) == 8, f"Expected 8 edges, got {len(records)}"
        for record in records:
            assert record["occurred_at"] is not None, (
                f"Edge {record['rel_type']} missing occurred_at"
            )
            assert isinstance(record["occurred_at"], Neo4jDateTime), (
                f"Edge {record['rel_type']} occurred_at is {type(record['occurred_at'])}, "
                f"expected Neo4jDateTime"
            )
```

### Step 3: Run the new edge type tests

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/test_neo4j_edge_types.py -v
```

**Expected:** All 10 tests PASS (8 individual edge tests + 2 cross-cutting tests).

### Step 4: Run the FULL test suite to verify no regressions

The expanded `conftest.py` changes `reference_nodes()` and `reference_edges()` — existing tests import these. Verify nothing broke:

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/ -v
```

**Expected:** All tests pass. The existing `test_neo4j_store.py` tests that use `reference_nodes()` and `reference_edges()` (like `seeded_neo4j_store` fixture) now seed a larger graph, but their assertions are on specific nodes/edges that are still present.

### Step 5: Commit

```bash
cd modules/hook-context-intelligence
git add -A
git commit -m "test: integration tests for all 8 edge types with native DateTime verification"
```

---

## Task 6: AGENTS.md Update

**Why:** The workspace `AGENTS.md` references DuckDB, file-based stores, multiple `GraphStore` implementations, and `"sql"` dialect. All of that is gone — Neo4j is the sole graph backend. Update to reflect reality.

**Dependencies:** None.

**Files:**
- Modify: `/home/dicolomb/context-itelligence-bundle-v2/AGENTS.md` (workspace root, NOT inside the bundle)

### Step 1: Update the Workspace Layout section

In the `## Workspace Layout` section, add the `agents/` and `skills/` directories to the tree. Replace the entire code block (lines 17-31) with:

````markdown
```
./
├── AGENTS.md                                          # This file
├── amplifier-bundle-context-intelligence/             # THE BUNDLE WE ARE BUILDING
│   ├── bundle.md                                      # Thin bundle (includes behavior)
│   ├── behaviors/context-intelligence.yaml            # Hook module declaration
│   ├── modules/hook-context-intelligence/             # The hook module
│   ├── agents/                                        # Agent definitions
│   │   └── context-intelligence-analyst.md            # Graph analysis agent
│   ├── skills/                                        # Cypher query skill
│   └── docs/                                          # Design docs, DOT diagrams, plans
├── amplifier-event-and-data-model-for-context-intelligence/  # RESEARCH (read-only reference)
│   ├── graph-data-model.md                            # Property graph specification
│   ├── 13-navigation-graph-model.dot                  # Visual ontology
│   ├── 14-session-instance-55c8841a.dot               # Real session as property graph
│   └── navigation-model-evolution.md                  # Model evolution tracking
├── amplifier/                                         # Submodule (reference)
├── amplifier-core/                                    # Submodule (reference)
└── amplifier-foundation/                              # Submodule (reference)
```
````

### Step 2: Update the Schema-Skill Synchronization standing rule

Replace the entire `### Standing Rule: Schema-Skill Synchronization` section (lines 145-151) with:

```markdown
### Standing Rule: Schema-Skill Synchronization

Any change to the Neo4j storage backend's schema MUST be accompanied by an update to the Cypher dialect skill at `skills/context-intelligence-neo4j-search/SKILL.md`. The skill covers the `"cypher"` dialect (Neo4j backend).

This includes: new node labels, relationship types, property key changes, graph_forest_name scoping, new index definitions in `_ensure_schema()`, new query patterns.

This rule is permanently enforced via docstrings in `neo4j_store.py` and cross-references in the skill. This AGENTS.md note is for workspace visibility only.
```

### Step 3: Replace the Storage Implementation Parity standing rule

Replace the entire `### Standing Rule: Storage Implementation Parity` section (lines 153-162) with:

```markdown
### Standing Rule: Neo4j is the Sole Graph Backend

DuckDB, file-based, and composite stores have been removed. Neo4j is the sole graph backend. The in-memory `GraphState` remains as a fallback when `enable_graph=false`, but it is not a persistence layer.

All CRITICAL and HIGH upstream gaps are resolved:
- CP-1 (G1): `session:end` now emitted (amplifier-core v1.0.11)
- CP-4 (G3): Delegate `tool_call_id` enrichment re-landed (amplifier-foundation commit `f70646b`)
- CP-5 (G4): Recipe events now visible (amplifier-bundle-recipes PR #46)
- CP-6 (G5): `execution:start/end` present in loop-basic
- CP-7 (G2): `execution:end` on CancelledError paths fixed (amplifier-module-loop-streaming commit `7b953f2`)

The event stream is now complete for full graph population. The `context-intelligence-analyst` agent in `agents/` provides graph analysis capabilities using the Cypher skill.
```

### Step 4: Verify the file looks correct

Read the updated file and confirm it makes sense:

```bash
cat /home/dicolomb/context-itelligence-bundle-v2/AGENTS.md
```

### Step 5: Commit

```bash
cd /home/dicolomb/context-itelligence-bundle-v2
git add AGENTS.md
git commit -m "docs: update AGENTS.md — Neo4j-only, upstream gaps resolved, analyst agent documented"
```

---

## Verification Checklist

After all 6 tasks are complete, run the full test suite one final time:

```bash
cd modules/hook-context-intelligence
.venv/bin/python -m pytest tests/ -v
```

**Expected results:**
- `test_logging_handler.py` — all 25 tests pass (imports updated)
- `test_neo4j_store.py` — all existing tests + 11 new tests pass (indexes, forest filter, timestamps)
- `test_neo4j_edge_types.py` — all 10 new tests pass (8 edge types + 2 cross-cutting)
- All other existing test files — pass unchanged

**Manual spot-check:**
1. Verify `logging_handler.py` no longer exists at the module root (only in `handlers/`)
2. Verify Neo4j stores `DateTime` not `String` for timestamp properties:
   ```bash
   cd modules/hook-context-intelligence
   .venv/bin/python -c "
   import asyncio
   from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore
   async def check():
       store = Neo4jGraphStore(uri='neo4j://localhost:7690', auth=None)
       await store.upsert_node('check-1', {'Test'}, {'occurred_at': '2026-01-15T10:00:00Z'})
       await store.flush()
       async with store._driver.session(database='neo4j') as session:
           result = await session.run('MATCH (n {node_id: \"check-1\"}) RETURN n.occurred_at AS val')
           record = await result.single()
           print(f'Type: {type(record[\"val\"])}, Value: {record[\"val\"]}')
       await store.close()
   asyncio.run(check())
   "
   ```
   **Expected:** `Type: <class 'neo4j.time.DateTime'>, Value: ...`

---

## Out of Scope

These items were explicitly excluded from this plan:

- **SystemEventHandler completion** — stays as no-op (simplicity). DefaultHandler catches those events generically.
- **Upstream repo changes** — all CPs are resolved. No changes to amplifier-core, amplifier-foundation, etc.
- **DuckDB/file-store anything** — deleted. Gone. Do not resurrect.
- **README.md updates** — not requested.
- **New query patterns in the Cypher skill** — the skill update is triggered by the Standing Rule but is a separate task (if schema changes warrant it, the implementer should update the skill per the standing rule docstring in `neo4j_store.py`).
