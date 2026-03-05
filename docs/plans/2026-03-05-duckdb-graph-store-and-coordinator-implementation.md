# DuckDB GraphStore & Coordinator Capture Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Replace the in-memory `GraphState` default with a DuckDB-backed `DuckDBGraphStore` as the production graph storage backend, driven by a factory pattern and nested config, and capture the coordinator reference on `HookStateService`.

**Architecture:** A `store_factory.py` creates the right `GraphStore` implementation based on `config.graph_store.type` (default: `"duckdb"`). `DuckDBGraphStore` buffers writes in-memory for non-blocking upserts, flushes to DuckDB in batched transactions via `run_in_executor`, and reads check the buffer first before querying DuckDB. The coordinator is captured on `HookStateService` during mount and passed through from `MountFlow.run()`.

**Tech Stack:** Python 3.11+, DuckDB ≥1.0, pytest + pytest-asyncio (auto mode), uv package manager

**Design docs:**
- `docs/plans/2026-03-05-duckdb-graph-store-implementation-design.md`
- `docs/plans/2026-03-05-capture-coordinator-design.md`

---

## Paths Reference

All paths below are relative to **`amplifier-bundle-context-intelligence/modules/hook-context-intelligence/`**. The full path from workspace root is `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/`.

| Shorthand | Full path |
|-----------|-----------|
| `SRC/` | `amplifier_module_hook_context_intelligence/` |
| `TESTS/` | `tests/` |
| `MODULE_ROOT/` | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/` |

**Commands** should be run from `MODULE_ROOT/`:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
```

---

## Task 1: Add DuckDB Runtime Dependency

**Files:**
- Modify: `pyproject.toml` (line 8)

**Step 1: Update pyproject.toml**

In `pyproject.toml`, change line 8 from:

```toml
dependencies = []
```

to:

```toml
dependencies = [
    "duckdb>=1.0",
]
```

Leave everything else in `pyproject.toml` unchanged.

**Step 2: Install and verify**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv sync
```

Expected: resolves successfully, installs `duckdb` package. Output includes something like `Resolved N packages` and `Installed duckdb`.

**Step 3: Verify duckdb imports**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run python -c "import duckdb; print(duckdb.__version__)"
```

Expected: prints a version number like `1.2.1` (any version ≥1.0).

**Step 4: Run existing tests to confirm nothing broke**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: all existing tests PASS. Adding a dependency should not break anything.

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add pyproject.toml uv.lock && git commit -m "feat: add duckdb>=1.0 as runtime dependency"
```

---

## Task 2: Capture Coordinator on HookStateService and MountFlow

**Files:**
- Modify: `SRC/services.py` (lines 85-90)
- Modify: `SRC/mount.py` (lines 51-54, 141-147)
- Create: `TESTS/test_coordinator_capture.py`

### Step 1: Write the failing tests

Create file `tests/test_coordinator_capture.py`:

```python
"""Tests for coordinator capture on HookStateService and MountFlow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from amplifier_module_hook_context_intelligence.mount import MountFlow
from amplifier_module_hook_context_intelligence.services import HookStateService


class TestHookStateServiceCoordinator:
    def test_coordinator_stored_when_passed(self):
        coordinator = MagicMock()
        service = HookStateService(raw_config={}, coordinator=coordinator)
        assert service.coordinator is coordinator

    def test_coordinator_defaults_to_none(self):
        service = HookStateService(raw_config={})
        assert service.coordinator is None


class TestMountFlowCoordinatorPassthrough:
    def test_create_services_passes_coordinator(self):
        coordinator = MagicMock()
        flow = MountFlow(config={})
        flow.create_services(coordinator)
        assert flow.services is not None
        assert flow.services.coordinator is coordinator

    def test_create_services_with_none_coordinator(self):
        flow = MountFlow(config={})
        flow.create_services(None)
        assert flow.services is not None
        assert flow.services.coordinator is None

    async def test_run_passes_coordinator_to_services(self):
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock(return_value=MagicMock())
        coordinator.collect_contributions = AsyncMock(return_value=[])
        coordinator.get_capability = MagicMock(return_value=None)

        flow = MountFlow(config={})
        await flow.run(coordinator)
        assert flow.services is not None
        assert flow.services.coordinator is coordinator
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_coordinator_capture.py -v
```

Expected: FAIL — `HookStateService.__init__()` does not accept `coordinator` keyword argument, and `MountFlow.create_services()` does not accept any positional arguments.

### Step 3: Modify services.py — add coordinator parameter

In `SRC/services.py`, change the `HookStateService` class (lines 85-90) from:

```python
class HookStateService:
    """Top-level service container shared across all handlers."""

    def __init__(self, raw_config: dict[str, Any]) -> None:
        self.config = HookConfig(raw_config)
        self.graph = GraphState()
```

to:

```python
class HookStateService:
    """Top-level service container shared across all handlers."""

    def __init__(self, raw_config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = HookConfig(raw_config)
        self.coordinator = coordinator
        self.graph = GraphState()
```

Note: `Any` is already imported on line 6. No new imports needed.

### Step 4: Modify mount.py — pass coordinator through create_services and run

In `SRC/mount.py`, change `create_services` (lines 51-54) from:

```python
    def create_services(self) -> None:
        """INIT → STATE_CREATED: Instantiate HookStateService from config."""
        self.services = HookStateService(self._config)
        self.state = MountState.STATE_CREATED
```

to:

```python
    def create_services(self, coordinator: Any) -> None:
        """INIT → STATE_CREATED: Instantiate HookStateService from config."""
        self.services = HookStateService(self._config, coordinator=coordinator)
        self.state = MountState.STATE_CREATED
```

Then change the `run` method (line 147) from:

```python
        self.create_services()
```

to:

```python
        self.create_services(coordinator)
```

No new imports needed — `Any` is already imported on line 8 and `Callable` on the same line.

### Step 5: Run the new tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_coordinator_capture.py -v
```

Expected: all 5 tests PASS.

### Step 6: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add -A && git commit -m "feat: capture coordinator on HookStateService and MountFlow"
```

**Note:** Some existing tests in `test_mount_flow.py` and `test_services.py` may now fail because `create_services()` requires a coordinator argument. That's expected — Task 6 fixes them. Do NOT fix them here.

---

## Task 3: Create DuckDBGraphStore Class

**Files:**
- Create: `SRC/duckdb_store.py`
- Create: `TESTS/test_duckdb_store.py`

### Step 1: Write the failing tests

Create file `tests/test_duckdb_store.py`:

```python
"""Tests for DuckDBGraphStore — buffer, flush, reads, protocol conformance."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest


class TestProtocolConformance:
    def test_isinstance_graph_store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = DuckDBGraphStore()
        assert isinstance(store, GraphStore)


class TestConstructor:
    def test_default_connection_is_memory(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        assert store._connection_str == ":memory:"

    def test_file_path_expands_tilde(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        # Use a real temp path instead of tilde to avoid messing with home dir
        db_path = tmp_path / "subdir" / "test.duckdb"
        store = DuckDBGraphStore(connection=str(db_path))
        assert db_path.parent.exists()
        store._conn.close()

    def test_creates_parent_directories(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "deep" / "nested" / "dir" / "graph.duckdb"
        store = DuckDBGraphStore(connection=str(db_path))
        assert db_path.parent.exists()
        store._conn.close()

    def test_tables_created_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        tables = store._conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        table_names = {row[0] for row in tables}
        assert "nodes" in table_names
        assert "edges" in table_names


class TestBufferWrites:
    async def test_upsert_node_writes_to_buffer(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"Session"}, properties={"key": "val"})
        assert "n1" in store._node_buffer
        # DuckDB should have nothing yet
        rows = store._conn.execute("SELECT * FROM nodes").fetchall()
        assert len(rows) == 0

    async def test_upsert_edge_writes_to_buffer(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_edge("a", "b", edge_type="LINKS", properties={"w": 1})
        assert ("a", "b", "LINKS") in store._edge_buffer
        rows = store._conn.execute("SELECT * FROM edges").fetchall()
        assert len(rows) == 0

    async def test_upsert_node_merges_labels(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"Session", "Root"}, properties={})
        await store.upsert_node("n1", labels={"Resumed"}, properties={})
        buffered = store._node_buffer["n1"]
        assert buffered["labels"] == {"Session", "Root", "Resumed"}

    async def test_upsert_node_merges_properties(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"X"}, properties={"a": 1})
        await store.upsert_node("n1", labels={"X"}, properties={"b": 2})
        buffered = store._node_buffer["n1"]
        assert buffered["properties"]["a"] == 1
        assert buffered["properties"]["b"] == 2

    async def test_upsert_edge_merges_properties(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_edge("a", "b", edge_type="X", properties={"k1": "v1"})
        await store.upsert_edge("a", "b", edge_type="X", properties={"k2": "v2"})
        buffered = store._edge_buffer[("a", "b", "X")]
        assert buffered["properties"]["k1"] == "v1"
        assert buffered["properties"]["k2"] == "v2"


class TestBufferFirstReads:
    async def test_get_node_returns_buffered_data(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"Session"}, properties={"started": True})
        node = await store.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Session"}
        assert node["properties"]["started"] is True

    async def test_get_edge_returns_buffered_data(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_edge("a", "b", edge_type="LINKS", properties={"w": 1})
        edge = await store.get_edge("a", "b", edge_type="LINKS")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["target"] == "b"
        assert edge["type"] == "LINKS"
        assert edge["properties"]["w"] == 1

    async def test_get_nonexistent_node_returns_none(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        assert await store.get_node("nonexistent") is None

    async def test_get_nonexistent_edge_returns_none(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        assert await store.get_edge("a", "b", edge_type="X") is None

    async def test_buffer_wins_over_stale_duckdb_data(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"Session"}, properties={"status": "running"})
        await store.flush()

        # Now upsert new data to buffer (not flushed)
        await store.upsert_node("n1", labels={"Session"}, properties={"status": "completed"})

        node = await store.get_node("n1")
        assert node is not None
        assert node["properties"]["status"] == "completed"


class TestFlush:
    async def test_flush_writes_nodes_to_duckdb(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"Session"}, properties={"key": "val"})
        await store.flush()

        rows = store._conn.execute("SELECT * FROM nodes WHERE node_id = 'n1'").fetchall()
        assert len(rows) == 1

    async def test_flush_writes_edges_to_duckdb(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_edge("a", "b", edge_type="LINKS", properties={"w": 1})
        await store.flush()

        rows = store._conn.execute(
            "SELECT * FROM edges WHERE source = 'a' AND target = 'b' AND edge_type = 'LINKS'"
        ).fetchall()
        assert len(rows) == 1

    async def test_flush_clears_buffer(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"X"}, properties={})
        await store.upsert_edge("a", "b", edge_type="Y", properties={})
        await store.flush()

        assert len(store._node_buffer) == 0
        assert len(store._edge_buffer) == 0

    async def test_get_node_reads_from_duckdb_after_flush(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"Session", "Root"}, properties={"a": 1})
        await store.flush()

        # Buffer is now empty — read must come from DuckDB
        assert len(store._node_buffer) == 0
        node = await store.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Session", "Root"}
        assert node["properties"]["a"] == 1

    async def test_get_edge_reads_from_duckdb_after_flush(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_edge("a", "b", edge_type="LINKS", properties={"w": 42})
        await store.flush()

        assert len(store._edge_buffer) == 0
        edge = await store.get_edge("a", "b", edge_type="LINKS")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["target"] == "b"
        assert edge["type"] == "LINKS"
        assert edge["properties"]["w"] == 42

    async def test_flush_empty_buffer_is_noop(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.flush()  # should not raise


class TestExecuteQuery:
    async def test_execute_query_after_flush(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", labels={"Session"}, properties={"status": "running"})
        await store.upsert_node("n2", labels={"Run"}, properties={"status": "done"})
        await store.flush()

        results = await store.execute_query("SELECT node_id FROM nodes ORDER BY node_id")
        assert len(results) == 2
        assert results[0]["node_id"] == "n1"
        assert results[1]["node_id"] == "n2"


class TestClose:
    async def test_close_flushes_before_closing(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
            db_path = f.name

        store = DuckDBGraphStore(connection=db_path)
        await store.upsert_node("n1", labels={"X"}, properties={"y": 1})
        await store.close()

        # Reopen and verify data was flushed
        import duckdb

        conn = duckdb.connect(db_path)
        rows = conn.execute("SELECT * FROM nodes WHERE node_id = 'n1'").fetchall()
        conn.close()
        assert len(rows) == 1

        Path(db_path).unlink(missing_ok=True)


class TestPersistence:
    async def test_data_survives_close_and_reopen(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
            db_path = f.name

        # Write and close
        store1 = DuckDBGraphStore(connection=db_path)
        await store1.upsert_node("n1", labels={"Session"}, properties={"status": "running"})
        await store1.upsert_edge("n1", "n2", edge_type="CONTAINS", properties={"seq": 1})
        await store1.close()

        # Reopen and read
        store2 = DuckDBGraphStore(connection=db_path)
        node = await store2.get_node("n1")
        assert node is not None
        assert node["labels"] == {"Session"}
        assert node["properties"]["status"] == "running"

        edge = await store2.get_edge("n1", "n2", edge_type="CONTAINS")
        assert edge is not None
        assert edge["properties"]["seq"] == 1
        await store2.close()

        Path(db_path).unlink(missing_ok=True)
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: FAIL — `ModuleNotFoundError` because `duckdb_store.py` does not exist yet.

### Step 3: Write the DuckDBGraphStore implementation

Create file `SRC/duckdb_store.py`:

```python
"""DuckDBGraphStore — DuckDB-backed graph storage with buffer-first reads."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id     VARCHAR PRIMARY KEY,
    session_id  VARCHAR NOT NULL DEFAULT '',
    labels      VARCHAR[] NOT NULL,
    occurred_at TIMESTAMP,
    properties  JSON
);
"""

_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    source      VARCHAR NOT NULL,
    target      VARCHAR NOT NULL,
    edge_type   VARCHAR NOT NULL,
    session_id  VARCHAR NOT NULL DEFAULT '',
    occurred_at TIMESTAMP,
    seq         INTEGER,
    properties  JSON,
    PRIMARY KEY (source, target, edge_type)
);
"""


class DuckDBGraphStore:
    """DuckDB-backed GraphStore with in-memory buffer and buffer-first reads.

    Writes go to an in-memory buffer (non-blocking).  ``flush()`` drains the
    buffer into DuckDB in a single transaction.  Reads check the buffer first,
    falling back to DuckDB for data that has been flushed.
    """

    def __init__(self, connection: str = ":memory:") -> None:
        self._connection_str = connection

        if connection != ":memory:" and connection:
            expanded = Path(connection).expanduser()
            expanded.parent.mkdir(parents=True, exist_ok=True)
            connection = str(expanded)

        self._conn = duckdb.connect(connection)
        self._conn.execute(_CREATE_NODES)
        self._conn.execute(_CREATE_EDGES)

        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def _run(self, fn: Any) -> Any:
        """Run a blocking callable in the default executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    # -- Non-blocking writes (core protocol requirement) ----------------------

    async def upsert_node(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> None:
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

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        key = (source, target, edge_type)
        existing = self._edge_buffer.get(key)
        if existing is not None:
            existing["properties"].update(properties)
            return
        self._edge_buffer[key] = {
            "source": source,
            "target": target,
            "type": edge_type,
            "properties": dict(properties),
        }

    # -- Buffer-first reads ---------------------------------------------------

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        buffered = self._node_buffer.get(node_id)
        if buffered is not None:
            return buffered

        def _query() -> dict[str, Any] | None:
            row = self._conn.execute(
                "SELECT node_id, labels, properties FROM nodes WHERE node_id = ?",
                [node_id],
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "labels": set(row[1]) if row[1] else set(),
                "properties": json.loads(row[2]) if row[2] else {},
            }

        return await self._run(_query)

    async def get_edge(
        self, source: str, target: str, edge_type: str
    ) -> dict[str, Any] | None:
        key = (source, target, edge_type)
        buffered = self._edge_buffer.get(key)
        if buffered is not None:
            return buffered

        def _query() -> dict[str, Any] | None:
            row = self._conn.execute(
                "SELECT source, target, edge_type, properties FROM edges "
                "WHERE source = ? AND target = ? AND edge_type = ?",
                [source, target, edge_type],
            ).fetchone()
            if row is None:
                return None
            return {
                "source": row[0],
                "target": row[1],
                "type": row[2],
                "properties": json.loads(row[3]) if row[3] else {},
            }

        return await self._run(_query)

    # -- Flush ----------------------------------------------------------------

    async def flush(self) -> None:
        nodes = dict(self._node_buffer)
        edges = dict(self._edge_buffer)
        self._node_buffer.clear()
        self._edge_buffer.clear()

        if not nodes and not edges:
            return

        def _write() -> None:
            try:
                self._conn.execute("BEGIN TRANSACTION")
                for node_id, node in nodes.items():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO nodes (node_id, session_id, labels, properties) "
                        "VALUES (?, '', ?, ?)",
                        [node_id, list(node["labels"]), json.dumps(node["properties"])],
                    )
                for (src, tgt, etype), edge in edges.items():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO edges (source, target, edge_type, session_id, properties) "
                        "VALUES (?, ?, ?, '', ?)",
                        [src, tgt, etype, json.dumps(edge["properties"])],
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        try:
            await self._run(_write)
        except Exception:
            logger.warning("DuckDBGraphStore flush failed, returning items to buffer", exc_info=True)
            # Put items back for retry
            for node_id, node in nodes.items():
                if node_id not in self._node_buffer:
                    self._node_buffer[node_id] = node
            for key, edge in edges.items():
                if key not in self._edge_buffer:
                    self._edge_buffer[key] = edge

    # -- Query ----------------------------------------------------------------

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            if params:
                result = self._conn.execute(query, params)
            else:
                result = self._conn.execute(query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]

        return await self._run(_query)

    # -- Close ----------------------------------------------------------------

    async def close(self) -> None:
        await self.flush()
        self._conn.close()
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: all tests PASS.

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add -A && git commit -m "feat: add DuckDBGraphStore with buffer-first reads and async flush"
```

---

## Task 4: Create store_factory.py

**Files:**
- Create: `SRC/store_factory.py`
- Create: `TESTS/test_store_factory.py`

### Step 1: Write the failing tests

Create file `tests/test_store_factory.py`:

```python
"""Tests for the graph store factory function."""

from __future__ import annotations

import pytest


class TestCreateGraphStore:
    def test_returns_duckdb_store_for_explicit_type(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.store_factory import create_graph_store

        store = create_graph_store({"type": "duckdb"})
        assert isinstance(store, DuckDBGraphStore)

    def test_returns_duckdb_store_for_empty_config(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.store_factory import create_graph_store

        store = create_graph_store({})
        assert isinstance(store, DuckDBGraphStore)

    def test_default_connection_is_memory(self):
        from amplifier_module_hook_context_intelligence.store_factory import create_graph_store

        store = create_graph_store({})
        assert store._connection_str == ":memory:"

    def test_passes_connection_string_through(self, tmp_path):
        from amplifier_module_hook_context_intelligence.store_factory import create_graph_store

        db_path = str(tmp_path / "test.duckdb")
        store = create_graph_store({"type": "duckdb", "connection": db_path})
        assert store._connection_str == db_path
        store._conn.close()

    def test_raises_for_unknown_type(self):
        from amplifier_module_hook_context_intelligence.store_factory import create_graph_store

        with pytest.raises(ValueError, match="Unknown graph_store type: neo4j"):
            create_graph_store({"type": "neo4j"})

    def test_result_conforms_to_graph_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.store_factory import create_graph_store

        store = create_graph_store({})
        assert isinstance(store, GraphStore)
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py -v
```

Expected: FAIL — `ModuleNotFoundError` because `store_factory.py` does not exist yet.

### Step 3: Write the factory implementation

Create file `SRC/store_factory.py`:

```python
"""Factory for creating GraphStore implementations from config."""

from __future__ import annotations

from typing import Any

from .graph_store import GraphStore


def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    """Create a GraphStore implementation based on config.

    Config shape::

        graph_store:
            type: "duckdb"           # default
            connection: ":memory:"   # default

    Unknown types raise ``ValueError`` — fail loud, not silent.
    """
    store_type = store_config.get("type", "duckdb")
    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore

        connection = store_config.get("connection", ":memory:")
        return DuckDBGraphStore(connection=connection)
    raise ValueError(f"Unknown graph_store type: {store_type}")
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py -v
```

Expected: all 6 tests PASS.

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add -A && git commit -m "feat: add store_factory with lazy DuckDB import"
```

---

## Task 5: Wire HookStateService to Use the Factory

**Files:**
- Modify: `SRC/services.py` (lines 85-90)

### Step 1: Write the failing test

Add this test to the **existing** file `tests/test_store_factory.py` (append at the bottom):

```python
class TestHookStateServiceIntegration:
    def test_default_config_creates_duckdb_store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, DuckDBGraphStore)

    def test_nested_graph_store_config_passed_through(self, tmp_path):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        db_path = str(tmp_path / "custom.duckdb")
        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "connection": db_path}}
        )
        assert service.graph._connection_str == db_path
        service.graph._conn.close()

    def test_unknown_store_type_raises(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        with pytest.raises(ValueError, match="Unknown graph_store type"):
            HookStateService(raw_config={"graph_store": {"type": "bogus"}})
```

### Step 2: Run the new tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py::TestHookStateServiceIntegration -v
```

Expected: FAIL — `HookStateService` still creates `GraphState()`, not `DuckDBGraphStore`.

### Step 3: Modify services.py to use the factory

In `SRC/services.py`, add this import after the existing imports (after line 6):

```python
from .store_factory import create_graph_store
```

Then change the `HookStateService.__init__` method from:

```python
    def __init__(self, raw_config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = HookConfig(raw_config)
        self.coordinator = coordinator
        self.graph = GraphState()
```

to:

```python
    def __init__(self, raw_config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = HookConfig(raw_config)
        self.coordinator = coordinator
        store_config = raw_config.get("graph_store", {})
        self.graph = create_graph_store(store_config)
```

**Important:** Do NOT remove `GraphState` from the file. It's still used by handler unit tests directly and by `test_graph_store.py`.

### Step 4: Run the integration tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py -v
```

Expected: all 9 tests PASS (6 from Task 4 + 3 new).

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add -A && git commit -m "feat: wire HookStateService to use store factory (DuckDB default)"
```

---

## Task 6: Fix All Broken Tests

After Tasks 2–5, some existing tests are broken. This task fixes them all.

**Files:**
- Modify: `TESTS/test_services.py` (line 136-144)
- Modify: `TESTS/test_mount_flow.py` (many calls to `create_services()`)

### Step 1: Identify broken tests

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/ -v 2>&1 | tail -40
```

Expected failures:
1. `test_services.py::TestHookStateService::test_construction` — asserts `isinstance(service.graph, GraphState)` but graph is now `DuckDBGraphStore`
2. `test_mount_flow.py` — multiple tests call `flow.create_services()` with no args, but signature is now `create_services(coordinator)`

### Step 2: Fix test_services.py

In `tests/test_services.py`, find the `TestHookStateService::test_construction` method (lines 135-144):

```python
    def test_construction(self):
        from amplifier_module_hook_context_intelligence.services import (
            GraphState,
            HookConfig,
            HookStateService,
        )

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphState)
        assert isinstance(service.config, HookConfig)
```

Replace it with:

```python
    def test_construction(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import (
            HookConfig,
            HookStateService,
        )

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphStore)
        assert isinstance(service.config, HookConfig)
```

This changes the assertion from checking for `GraphState` specifically to checking for the `GraphStore` protocol — which `DuckDBGraphStore` satisfies.

### Step 3: Fix test_mount_flow.py

In `tests/test_mount_flow.py`, every call to `flow.create_services()` (with no arguments) needs to become `flow.create_services(None)`. The coordinator is `None` in these unit tests because they don't need a real coordinator for the state transitions being tested.

Find and replace **all** occurrences of `flow.create_services()` with `flow.create_services(None)`. There are many — here is the complete list of lines to change:

**`TestInitToStateCreated::test_create_services`** (line 49):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestStateCreatedToHandlersInstantiated::test_instantiate_handlers`** (line 58):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestStateCreatedToHandlersInstantiated::test_all_handlers_conform_to_protocol`** (line 65):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestStateCreatedToHandlersInstantiated::test_claimed_events_computed`** (line 73):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestStateCreatedToHandlersInstantiated::test_default_handler_starts_empty`** (line 79):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestHandlersInstantiatedToEventsDiscovered`** — all 6 tests (lines 92, 101, 108, 113, 123, 131, 136, 144):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestEventsDiscoveredToSpecificRegistered`** — both tests (lines 158, 167):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestSpecificRegisteredToReady`** — both tests (lines 194, 207):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**`TestKeyInvariant`** — both tests (lines 232, 244):
```python
        flow.create_services()
```
→
```python
        flow.create_services(None)
```

**Total: replace every `flow.create_services()` with `flow.create_services(None)` throughout the file.** There are no calls to `create_services()` in the `TestFullMount` class because those tests use `flow.run(coordinator)` which calls `create_services` internally.

A quick way to do this: use a find-and-replace of `flow.create_services()` → `flow.create_services(None)` across the file.

### Step 4: Run the full test suite

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: **ALL tests PASS**. This includes:
- `test_services.py` — `TestHookConfig` (unchanged), `TestGraphState` (unchanged, tests GraphState directly), `TestHookStateService` (fixed)
- `test_mount_flow.py` — all state transition tests (fixed)
- `test_handlers.py` — protocol conformance, event claims (unchanged, uses `services` fixture which now gives DuckDBGraphStore — but handlers only use `GraphStore` protocol methods so they work)
- `test_session_handler.py` — all handler tests (unchanged, uses `services` fixture — DuckDBGraphStore buffer-first reads return same format as GraphState)
- `test_graph_store.py` — protocol tests (unchanged)
- `test_duckdb_store.py` — new DuckDB tests (from Task 3)
- `test_store_factory.py` — factory + integration tests (from Tasks 4-5)
- `test_coordinator_capture.py` — coordinator capture tests (from Task 2)
- `test_mount.py` — mount entry point tests (unchanged)
- `test_bundle.py` — bundle validation tests (unchanged)
- `test_protocol.py` — EventHandler protocol tests (unchanged)
- `test_module_loading.py` — module loading tests (unchanged)

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add -A && git commit -m "fix: update tests for DuckDBGraphStore default and coordinator parameter"
```

---

## Summary

| Task | What | New files | Modified files |
|------|------|-----------|----------------|
| 1 | DuckDB dependency | — | `pyproject.toml` |
| 2 | Coordinator capture | `tests/test_coordinator_capture.py` | `SRC/services.py`, `SRC/mount.py` |
| 3 | DuckDBGraphStore | `SRC/duckdb_store.py`, `tests/test_duckdb_store.py` | — |
| 4 | Store factory | `SRC/store_factory.py`, `tests/test_store_factory.py` | — |
| 5 | Wire factory | — | `SRC/services.py`, `tests/test_store_factory.py` |
| 6 | Fix broken tests | — | `tests/test_services.py`, `tests/test_mount_flow.py` |

**After all 6 tasks:** `uv run pytest tests/ -v` — all tests green.
