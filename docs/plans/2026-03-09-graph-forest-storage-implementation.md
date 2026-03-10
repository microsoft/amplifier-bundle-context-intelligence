# Graph Forest-Aware Storage — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Make the graph storage layer forest-aware so every store instance knows which named forest it belongs to, all writes are scoped to that forest, and queries can filter by forest.

**Architecture:** The `GraphStore` protocol gains a `graph_forest_name` read-only property. `QueryableStore.execute_query` gains an optional `graph_forest_name` parameter for cross-forest queries. The factory reads the forest name from config (defaulting to `"default"`) and passes it to every backend constructor. `FileGraphStore` uses `root/forest_name/` as its working directory. `DuckDBGraphStore` adds a `graph_forest_name` column to all tables and filters queries by forest.

**Tech Stack:** Python 3.11+, DuckDB 1.4.3, pytest + pytest-asyncio, `@runtime_checkable` Protocol classes.

**Working directory for all commands:**
```
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence
```

**Shorthand used below:**
- `SRC` = `amplifier_module_hook_context_intelligence`
- `TESTS` = `tests`

---

## Task 1: Add `graph_forest_name` Property to `GraphStore` Protocol

**Files:**
- Modify: `SRC/graph_store.py`
- Test: `TESTS/test_graph_store.py`

### Step 1: Write the failing test — property on protocol

Add a new test at the bottom of the `# GraphStore base protocol` section in `tests/test_graph_store.py`. Insert it right after `test_graph_state_conforms_to_graph_store`:

```python
def test_graph_store_protocol_has_graph_forest_name_property():
    """GraphStore protocol must declare graph_forest_name as a required member."""
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    # A class that implements all 6 base methods BUT lacks graph_forest_name
    # must fail isinstance if graph_forest_name is part of the protocol.
    class StoreWithoutForest:
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

    store = StoreWithoutForest()
    assert not isinstance(store, GraphStore), (
        "A class missing graph_forest_name should NOT satisfy GraphStore"
    )
```

### Step 2: Run the test to verify it fails

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py::test_graph_store_protocol_has_graph_forest_name_property -v
```

Expected: **FAIL** — `StoreWithoutForest` currently passes `isinstance(store, GraphStore)` because the protocol doesn't require `graph_forest_name` yet.

### Step 3: Add `graph_forest_name` property to `GraphStore` protocol

In `SRC/graph_store.py`, add the property to the `GraphStore` protocol class. Insert it as the first member, before `upsert_node`:

```python
@runtime_checkable
class GraphStore(Protocol):
    """Async protocol for graph storage backends.

    Implementations buffer writes in memory and expose buffer-first reads so
    that handlers always see a consistent, up-to-date view without waiting for
    I/O.  Persistence is driven by lifecycle triggers via ``flush()``.
    """

    @property
    def graph_forest_name(self) -> str:
        """The name of the graph forest this store instance belongs to.

        Set at construction time.  Immutable for the lifetime of the store.
        All writes are scoped to this forest.  Point lookups by ID are
        forest-agnostic (IDs are globally unique).
        """
        ...

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        # ... rest unchanged
```

Also update the module docstring to mention the property:

```python
"""GraphStore protocol — the async interface for graph storage backends.

Non-negotiable guarantees
-------------------------
1. upsert_node / upsert_edge MUST return immediately (buffer, no I/O).
2. get_node / get_edge MUST reflect buffered state (buffer-first reads).
3. flush() persists buffered writes (called by lifecycle triggers, not handlers).
4. close() MUST call flush() before releasing resources.
5. Flush failure MUST NOT propagate to handlers.

QueryableStore extension
------------------------
6. supported_dialects advertises the set of query languages the backend speaks.
7. execute_query runs a query in the specified (or default) dialect.
8. ValueError is raised when the requested dialect is not in supported_dialects.

Forest awareness
----------------
9. graph_forest_name is a read-only property set at construction time.
10. All writes are scoped to the store's forest.
11. Point lookups by ID are forest-agnostic (IDs are globally unique).
12. execute_query supports optional graph_forest_name for cross-forest queries.
"""
```

### Step 4: Run the test to verify it passes

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py::test_graph_store_protocol_has_graph_forest_name_property -v
```

Expected: **PASS**

### Step 5: Update the existing `test_conforming_class_passes_isinstance` test

The existing test uses a `FakeStore` class that now fails because it lacks `graph_forest_name`. Update it in `tests/test_graph_store.py`:

Add the property to `FakeStore` in `test_conforming_class_passes_isinstance`:

```python
def test_conforming_class_passes_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class FakeStore:
        @property
        def graph_forest_name(self) -> str:
            return "test"

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
```

Do the same for `StoreWithQuery` in `test_class_with_execute_query_still_passes_graph_store` — add the `graph_forest_name` property.

Do the same for `FakeQueryable` in `test_queryable_conforming_class_passes_isinstance` — add the `graph_forest_name` property.

Do the same for `BaseOnly` in `test_base_graph_store_is_not_queryable` — add the `graph_forest_name` property.

**Do NOT touch** the negative tests (`test_missing_upsert_node_fails_isinstance`, `test_missing_flush_fails_isinstance`) — they should still fail for the right reasons.

### Step 6: Run the full protocol test suite

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py -v
```

Expected: Some tests will still **FAIL** — specifically `test_graph_state_conforms_to_graph_store` and `test_duckdb_store_is_queryable` because `GraphState` and `DuckDBGraphStore` don't have the property yet. **This is expected.** We will fix those in Tasks 2 and 5.

Verify that these specific tests pass:
- `test_graph_store_is_runtime_checkable` ✓
- `test_conforming_class_passes_isinstance` ✓
- `test_class_with_execute_query_still_passes_graph_store` ✓
- `test_missing_upsert_node_fails_isinstance` ✓
- `test_missing_flush_fails_isinstance` ✓
- `test_graph_store_protocol_has_graph_forest_name_property` ✓
- `test_queryable_store_is_runtime_checkable` ✓
- `test_queryable_conforming_class_passes_isinstance` ✓
- `test_queryable_missing_supported_dialects_fails` ✓
- `test_base_graph_store_is_not_queryable` ✓

### Step 7: Commit

```bash
git add -A && git commit -m "feat(protocol): add graph_forest_name property to GraphStore protocol"
```

---

## Task 2: Add `graph_forest_name` to `QueryableStore.execute_query`

**Files:**
- Modify: `SRC/graph_store.py`
- Test: `TESTS/test_graph_store.py`

### Step 1: Write the failing test

Add to `tests/test_graph_store.py`, in the QueryableStore section:

```python
def test_queryable_execute_query_accepts_graph_forest_name_param():
    """execute_query must accept an optional graph_forest_name parameter."""
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    class QueryableWithForest:
        @property
        def graph_forest_name(self) -> str:
            return "test"

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

        @property
        def supported_dialects(self) -> frozenset[str]:
            return frozenset({"sql"})

        async def execute_query(
            self,
            query: str,
            params: dict[str, Any] | None = None,
            dialect: str | None = None,
            graph_forest_name: str | None = None,
        ) -> list[dict[str, Any]]: ...

    store = QueryableWithForest()
    assert isinstance(store, QueryableStore)
```

### Step 2: Run the test to verify it fails

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py::test_queryable_execute_query_accepts_graph_forest_name_param -v
```

Expected: **PASS** actually — Python protocol `isinstance` checks don't validate parameter names. But the point of this test is to document the expected signature. If it passes, that's fine. Move on.

### Step 3: Update `QueryableStore.execute_query` signature

In `SRC/graph_store.py`, update the `execute_query` method signature on `QueryableStore`:

```python
    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
        graph_forest_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a query in the given dialect.

        Parameters
        ----------
        query:
            The query string.
        params:
            Optional bind parameters.
        dialect:
            Which query language to use.  ``None`` means the backend's default.
            Raises ``ValueError`` if *dialect* is not in ``supported_dialects``.
        graph_forest_name:
            Forest scope for the query.  ``None`` (default) scopes to the
            store's own forest.  An explicit string scopes to that forest.
            The special value ``"*"`` disables forest filtering (cross-forest).
        """
        ...
```

### Step 4: Run the protocol tests again

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py -v
```

Expected: Same results as before — the new test passes, existing results unchanged.

### Step 5: Commit

```bash
git add -A && git commit -m "feat(protocol): add graph_forest_name param to QueryableStore.execute_query"
```

---

## Task 3: Add `graph_forest_name` to `GraphState`

**Files:**
- Modify: `SRC/services.py`
- Test: `TESTS/test_services.py`

### Step 1: Write the failing test — constructor param and property

Add to `tests/test_services.py`, inside `TestGraphState`:

```python
    def test_graph_forest_name_default(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph.graph_forest_name == "default"

    def test_graph_forest_name_explicit(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState(graph_forest_name="my-project")
        assert graph.graph_forest_name == "my-project"

    def test_graph_forest_name_is_readonly(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState(graph_forest_name="test")
        with pytest.raises(AttributeError):
            graph.graph_forest_name = "other"  # type: ignore[misc]
```

You will also need to add the `import pytest` at the top of the file (it doesn't currently import it):

```python
import pytest
```

### Step 2: Run the tests to verify they fail

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_services.py::TestGraphState::test_graph_forest_name_default -v
```

Expected: **FAIL** — `GraphState.__init__()` doesn't accept `graph_forest_name`.

### Step 3: Implement `graph_forest_name` on `GraphState`

In `SRC/services.py`, update `GraphState.__init__` and add the property:

```python
class GraphState:
    """In-memory property graph state conforming to the GraphStore protocol."""

    def __init__(self, graph_forest_name: str = "default") -> None:
        self._graph_forest_name = graph_forest_name
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.current_session: str | None = None
        self.current_run: str | None = None
        self.current_step: str | None = None
        self.step_counter: int = 0
        self.pending_delegate_tool_call_id: str | None = None

    @property
    def graph_forest_name(self) -> str:
        """The name of the graph forest this store instance belongs to."""
        return self._graph_forest_name
```

### Step 4: Run the tests to verify they pass

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_services.py::TestGraphState -v
```

Expected: **ALL PASS** (including the existing tests — `GraphState()` still works with no args because the default is `"default"`).

### Step 5: Verify protocol conformance is restored

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py::test_graph_state_conforms_to_graph_store -v
```

Expected: **PASS** — `GraphState` now has the `graph_forest_name` property.

### Step 6: Commit

```bash
git add -A && git commit -m "feat(services): add graph_forest_name to GraphState"
```

---

## Task 4: Update Factory to Read and Pass `graph_forest_name`

**Files:**
- Modify: `SRC/store_factory.py`
- Test: `TESTS/test_store_factory.py`

### Step 1: Write the failing tests

Add to `tests/test_store_factory.py`, inside `TestCreateGraphStore`:

```python
    def test_graph_forest_name_defaults_to_default(self):
        """When graph_forest_name is absent from config, it defaults to 'default'."""
        store = create_graph_store({"type": "duckdb", "config": {"connection": ":memory:"}})
        assert store.graph_forest_name == "default"

    def test_graph_forest_name_passed_to_duckdb(self):
        """Explicit graph_forest_name is passed to the DuckDB backend."""
        store = create_graph_store({
            "type": "duckdb",
            "graph_forest_name": "my-project",
            "config": {"connection": ":memory:"},
        })
        assert store.graph_forest_name == "my-project"

    def test_graph_forest_name_passed_to_file(self, tmp_path):
        """Explicit graph_forest_name is passed to the file backend."""
        store = create_graph_store({
            "type": "file",
            "graph_forest_name": "my-project",
            "config": {"graph_store_root": str(tmp_path)},
        })
        assert store.graph_forest_name == "my-project"
```

### Step 2: Run the tests to verify they fail

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py::TestCreateGraphStore::test_graph_forest_name_defaults_to_default -v
```

Expected: **FAIL** — `DuckDBGraphStore` doesn't have `graph_forest_name` yet. Also the factory doesn't read it from config yet.

**Note:** These tests will keep failing until we also complete Tasks 5 (DuckDB) and 6 (FileGraphStore). That's fine. We implement the factory logic now so it's ready when the backends are updated.

### Step 3: Rewrite the factory

Replace the entire contents of `SRC/store_factory.py`:

```python
"""Factory for graph store backends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .graph_store import GraphStore

_DEFAULT_FILE_ROOT = str(Path("~/.amplifier/graphs"))
_DEFAULT_FOREST_NAME = "default"


def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    """Create a graph store from configuration.

    Parameters
    ----------
    store_config:
        Dictionary with optional ``type`` (default ``"file"``), optional
        ``graph_forest_name`` (default ``"default"``), and optional
        ``config`` dict containing backend-specific kwargs.

        ``graph_forest_name`` is a cross-protocol concept: it lives at
        the ``graph_store`` config level, not inside the backend-specific
        ``config``.  The factory reads it once and passes it to every
        backend constructor.

        Example::

            {
                "type": "duckdb",
                "graph_forest_name": "my-project",
                "config": {"connection": ":memory:"}
            }
    """
    store_type = store_config.get("type", "file")
    impl_config = store_config.get("config", {})
    forest_name = store_config.get("graph_forest_name", _DEFAULT_FOREST_NAME)

    if store_type == "file":
        from .file_store import FileGraphStore

        root = impl_config.get("graph_store_root", _DEFAULT_FILE_ROOT)
        return FileGraphStore(graph_store_root=root, graph_forest_name=forest_name)

    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore

        connection = impl_config.get("connection", ":memory:")
        return DuckDBGraphStore(connection=connection, graph_forest_name=forest_name)

    raise ValueError(f"Unknown graph_store type: {store_type}")
```

### Step 4: Run the full factory test suite to see what breaks

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py -v
```

Expected: **Many failures** — `FileGraphStore` and `DuckDBGraphStore` constructors don't accept the new arguments yet. This is expected — Tasks 5 and 6 will fix them.

### Step 5: Commit (partial — factory is ready, backends not yet)

```bash
git add SRC/store_factory.py && git commit -m "feat(factory): read graph_forest_name from config and pass to backends

Backends not yet updated — will be done in subsequent tasks."
```

(Replace `SRC` with the actual path: `amplifier_module_hook_context_intelligence/store_factory.py`)

---

## Task 5: Update `FileGraphStore` for Forest Awareness

**Files:**
- Modify: `SRC/file_store.py`
- Test: `TESTS/test_file_store.py`

### Step 1: Write the failing tests for the new constructor

Replace the `TestConstructor` and `TestProtocolConformance` classes in `tests/test_file_store.py`. Here are the new tests to add (keep all other test classes unchanged for now — they will be fixed in Task 8):

Add a new test class after the existing `TestProtocolConformance`:

```python
class TestForestAwareness:
    """FileGraphStore is forest-aware via graph_store_root + graph_forest_name."""

    def test_constructor_accepts_root_and_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="my-project"
        )
        assert store.graph_forest_name == "my-project"

    def test_working_directory_is_root_slash_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="my-project"
        )
        expected = tmp_path / "my-project"
        assert store._location == expected

    def test_nodes_dir_inside_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="my-project"
        )
        assert store._nodes_dir == tmp_path / "my-project" / "nodes"

    def test_edges_dir_inside_forest(self, tmp_path: Path) -> None:
        store = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="my-project"
        )
        assert store._edges_dir == tmp_path / "my-project" / "edges"

    def test_dirs_created_on_construction(self, tmp_path: Path) -> None:
        FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="my-project"
        )
        assert (tmp_path / "my-project" / "nodes").is_dir()
        assert (tmp_path / "my-project" / "edges").is_dir()

    def test_tilde_expansion_in_root(self) -> None:
        import shutil

        expected = Path.home() / "test-graph-forest-store" / "test-forest"
        try:
            store = FileGraphStore(
                graph_store_root="~/test-graph-forest-store",
                graph_forest_name="test-forest",
            )
            assert store._location == expected
        finally:
            shutil.rmtree(Path.home() / "test-graph-forest-store", ignore_errors=True)

    def test_graph_forest_name_is_readonly(self, tmp_path: Path) -> None:
        store = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="test"
        )
        with pytest.raises(AttributeError):
            store.graph_forest_name = "other"  # type: ignore[misc]

    async def test_data_isolated_between_forests(self, tmp_path: Path) -> None:
        """Two stores with different forest names share a root but not data."""
        store_a = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="forest-a"
        )
        store_b = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="forest-b"
        )
        await store_a.upsert_node("n1", {"Label"}, {"source": "a"})
        await store_a.flush()

        # store_b should not see store_a's data
        result = await store_b.get_node("n1")
        assert result is None

    async def test_persistence_across_forest_reopen(self, tmp_path: Path) -> None:
        """Data survives close and reopen of the same forest."""
        store1 = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="my-forest"
        )
        await store1.upsert_node("n1", {"A"}, {"k": "v"})
        await store1.close()

        store2 = FileGraphStore(
            graph_store_root=str(tmp_path), graph_forest_name="my-forest"
        )
        node = await store2.get_node("n1")
        assert node is not None
        assert node["properties"]["k"] == "v"
```

### Step 2: Run the tests to verify they fail

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_file_store.py::TestForestAwareness::test_constructor_accepts_root_and_forest -v
```

Expected: **FAIL** — `FileGraphStore.__init__()` doesn't accept `graph_store_root`.

### Step 3: Rewrite `FileGraphStore.__init__` and add property

In `SRC/file_store.py`, change the constructor and add the property:

```python
class FileGraphStore:
    """Graph store backed by flat JSON files with in-memory write buffers.

    Writes are buffered in Python dicts for instant access.  ``flush()``
    persists buffers to JSON files in ``{graph_store_root}/{graph_forest_name}/nodes/``
    and ``{graph_store_root}/{graph_forest_name}/edges/`` directories.  Reads
    check the buffer first, falling back to disk only when the buffer has no entry.
    """

    def __init__(self, graph_store_root: str, graph_forest_name: str) -> None:
        self._graph_store_root = Path(graph_store_root).expanduser()
        self._graph_forest_name = graph_forest_name
        self._location = self._graph_store_root / graph_forest_name
        self._nodes_dir = self._location / "nodes"
        self._edges_dir = self._location / "edges"
        self._nodes_dir.mkdir(parents=True, exist_ok=True)
        self._edges_dir.mkdir(parents=True, exist_ok=True)
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}

    @property
    def graph_forest_name(self) -> str:
        """The name of the graph forest this store instance belongs to."""
        return self._graph_forest_name
```

**Everything else in the file stays exactly the same** — the `_location`, `_nodes_dir`, `_edges_dir` private fields are unchanged in name, just computed differently.

### Step 4: Run the forest awareness tests

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_file_store.py::TestForestAwareness -v
```

Expected: **ALL PASS**

### Step 5: Commit

```bash
git add -A && git commit -m "feat(file-store): replace location with graph_store_root + graph_forest_name"
```

---

## Task 6: Update `DuckDBGraphStore` for Forest Awareness

This is the largest single task. It has three sub-parts: (A) constructor + property, (B) schema + writes, (C) query filtering.

**Files:**
- Modify: `SRC/duckdb_store.py`
- Test: `TESTS/test_duckdb_store.py`

### Sub-task 6A: Constructor and Property

#### Step 1: Write the failing test

Add to `tests/test_duckdb_store.py`, in `TestConstructor`:

```python
    def test_graph_forest_name_default(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="my-project")
        assert store.graph_forest_name == "my-project"

    def test_graph_forest_name_is_readonly(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        with pytest.raises(AttributeError):
            store.graph_forest_name = "other"  # type: ignore[misc]
```

You'll need to add `import pytest` at the top of the file if it's not there. (It's already imported — check line 8.)

#### Step 2: Run the test to verify it fails

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestConstructor::test_graph_forest_name_default -v
```

Expected: **FAIL** — `DuckDBGraphStore.__init__()` doesn't accept `graph_forest_name`.

#### Step 3: Add `graph_forest_name` to constructor

In `SRC/duckdb_store.py`, update the constructor:

```python
    def __init__(self, connection: str = ":memory:", graph_forest_name: str = "default") -> None:
        self._connection_str = connection
        self._graph_forest_name = graph_forest_name
        if connection != ":memory:":
            path = Path(connection).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection_str = str(path)
        self._conn = duckdb.connect(self._connection_str)
        self._conn.execute(_CREATE_NODES)
        self._conn.execute(_CREATE_EDGES)
        self._conn.execute(_CREATE_SEARCH_INDEX)
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._search_buffer: list[dict[str, Any]] = []
        self._pgq_ready: bool = False
        self._fts_ready: bool = False

    @property
    def graph_forest_name(self) -> str:
        """The name of the graph forest this store instance belongs to."""
        return self._graph_forest_name
```

#### Step 4: Run the constructor tests

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestConstructor -v
```

Expected: **ALL PASS**

#### Step 5: Commit

```bash
git add -A && git commit -m "feat(duckdb): add graph_forest_name constructor param and property"
```

### Sub-task 6B: Schema — Add `graph_forest_name` Column

#### Step 1: Write the failing test

Add a new test class in `tests/test_duckdb_store.py`:

```python
class TestForestSchema:
    """Verify graph_forest_name column exists on all tables."""

    def test_nodes_has_graph_forest_name_column(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'nodes' AND column_name = 'graph_forest_name'"
        ).fetchall()
        assert len(result) == 1
        assert result[0][0] == "graph_forest_name"

    def test_edges_has_graph_forest_name_column(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'edges' AND column_name = 'graph_forest_name'"
        ).fetchall()
        assert len(result) == 1
        assert result[0][0] == "graph_forest_name"

    def test_search_index_has_graph_forest_name_column(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'search_index' AND column_name = 'graph_forest_name'"
        ).fetchall()
        assert len(result) == 1
        assert result[0][0] == "graph_forest_name"
```

#### Step 2: Run the tests to verify they fail

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestForestSchema -v
```

Expected: **FAIL** — column doesn't exist.

#### Step 3: Update the DDL constants

In `SRC/duckdb_store.py`, update the three `_CREATE_*` SQL constants:

```python
_CREATE_NODES = """\
CREATE TABLE IF NOT EXISTS nodes (
    node_id            VARCHAR PRIMARY KEY,
    graph_forest_name  VARCHAR NOT NULL DEFAULT 'default',
    session_id         VARCHAR DEFAULT '',
    labels             VARCHAR[],
    occurred_at        TIMESTAMP,
    properties         JSON
)
"""

_CREATE_EDGES = """\
CREATE TABLE IF NOT EXISTS edges (
    source             VARCHAR,
    target             VARCHAR,
    edge_type          VARCHAR,
    graph_forest_name  VARCHAR NOT NULL DEFAULT 'default',
    session_id         VARCHAR DEFAULT '',
    occurred_at        TIMESTAMP,
    seq                INTEGER,
    properties         JSON,
    PRIMARY KEY (source, target, edge_type)
)
"""

_CREATE_SEARCH_INDEX = """\
CREATE TABLE IF NOT EXISTS search_index (
    node_id            VARCHAR NOT NULL,
    graph_forest_name  VARCHAR NOT NULL DEFAULT 'default',
    session_id         VARCHAR NOT NULL,
    field_name         VARCHAR NOT NULL,
    content            VARCHAR NOT NULL,
    occurred_at        TIMESTAMP
)
"""
```

#### Step 4: Run the schema tests

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestForestSchema -v
```

Expected: **ALL PASS**

#### Step 5: Commit

```bash
git add -A && git commit -m "feat(duckdb): add graph_forest_name column to nodes, edges, search_index"
```

### Sub-task 6C: Stamp Forest on Writes

#### Step 1: Write the failing test

Add to `tests/test_duckdb_store.py`:

```python
class TestForestWrites:
    """flush() stamps graph_forest_name on all rows."""

    async def test_flush_stamps_forest_on_nodes(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="proj-a")
        await store.upsert_node("n1", {"Label"}, {"k": "v"})
        await store.flush()
        row = store._conn.execute(
            "SELECT graph_forest_name FROM nodes WHERE node_id = 'n1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "proj-a"

    async def test_flush_stamps_forest_on_edges(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="proj-a")
        await store.upsert_edge("a", "b", "REL", {"w": 1})
        await store.flush()
        row = store._conn.execute(
            "SELECT graph_forest_name FROM edges WHERE source = 'a'"
        ).fetchone()
        assert row is not None
        assert row[0] == "proj-a"

    async def test_flush_stamps_forest_on_search_index(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="proj-a")
        await store.upsert_node(
            "ps1",
            {"PromptStep"},
            {"prompt_text": "hello", "session_id": "s1"},
        )
        await store.flush()
        row = store._conn.execute(
            "SELECT graph_forest_name FROM search_index WHERE node_id = 'ps1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "proj-a"
```

#### Step 2: Run the tests to verify they fail

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestForestWrites -v
```

Expected: **FAIL** — the INSERT statements don't include `graph_forest_name`.

#### Step 3: Update `flush()` to stamp forest

In `SRC/duckdb_store.py`, update the `_write` function inside `flush()`:

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

        forest = self._graph_forest_name

        def _write() -> None:
            try:
                self._conn.execute("BEGIN TRANSACTION")
                for node in nodes.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO nodes "
                        "(node_id, graph_forest_name, session_id, labels, properties) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [
                            node["id"],
                            forest,
                            "",  # session_id: lives in properties; column reserved for future use
                            list(node["labels"]),
                            json.dumps(node["properties"]),
                        ],
                    )
                for edge in edges.values():
                    self._conn.execute(
                        "INSERT OR REPLACE INTO edges "
                        "(source, target, edge_type, graph_forest_name, session_id, properties) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            edge["source"],
                            edge["target"],
                            edge["type"],
                            forest,
                            "",  # session_id: lives in properties; column reserved for future use
                            json.dumps(edge["properties"]),
                        ],
                    )
                for entry in search:
                    self._conn.execute(
                        "INSERT INTO search_index "
                        "(node_id, graph_forest_name, session_id, field_name, content, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            entry["node_id"],
                            forest,
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
                    logger.warning("rollback also failed", exc_info=True)
                # Put items back for retry
                self._node_buffer.update(nodes)
                self._edge_buffer.update(edges)
                self._search_buffer.extend(search)
                logger.warning("flush failed; buffers restored for retry", exc_info=True)

        await self._run(_write)
```

#### Step 4: Run the write tests

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestForestWrites -v
```

Expected: **ALL PASS**

#### Step 5: Commit

```bash
git add -A && git commit -m "feat(duckdb): stamp graph_forest_name on all writes"
```

### Sub-task 6D: Forest Filtering on `execute_query`

#### Step 1: Write the failing tests

Add to `tests/test_duckdb_store.py`:

```python
class TestForestQueryFiltering:
    """execute_query filters by graph_forest_name."""

    async def _seed_two_forests(self):
        """Create two stores writing to the same DB, different forests."""
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        # We need a shared DB. Use a single connection string via file.
        # For in-memory, we create one store and write with different forests
        # by manipulating the forest name between flushes.
        store = DuckDBGraphStore(graph_forest_name="forest-a")
        await store.upsert_node("n1", {"Label"}, {"source": "a"})
        await store.flush()

        # Change forest and write again
        store._graph_forest_name = "forest-b"
        await store.upsert_node("n2", {"Label"}, {"source": "b"})
        await store.flush()

        # Reset to forest-a for querying
        store._graph_forest_name = "forest-a"
        return store

    async def test_default_scopes_to_own_forest(self):
        store = await self._seed_two_forests()
        rows = await store.execute_query("SELECT node_id FROM nodes")
        node_ids = [r["node_id"] for r in rows]
        assert "n1" in node_ids
        assert "n2" not in node_ids

    async def test_explicit_forest_scopes_to_that_forest(self):
        store = await self._seed_two_forests()
        rows = await store.execute_query(
            "SELECT node_id FROM nodes", graph_forest_name="forest-b"
        )
        node_ids = [r["node_id"] for r in rows]
        assert "n2" in node_ids
        assert "n1" not in node_ids

    async def test_star_returns_all_forests(self):
        store = await self._seed_two_forests()
        rows = await store.execute_query(
            "SELECT node_id FROM nodes", graph_forest_name="*"
        )
        node_ids = [r["node_id"] for r in rows]
        assert "n1" in node_ids
        assert "n2" in node_ids

    async def test_none_is_same_as_default(self):
        store = await self._seed_two_forests()
        rows_default = await store.execute_query("SELECT node_id FROM nodes")
        rows_none = await store.execute_query(
            "SELECT node_id FROM nodes", graph_forest_name=None
        )
        assert rows_default == rows_none
```

#### Step 2: Run the tests to verify they fail

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestForestQueryFiltering::test_default_scopes_to_own_forest -v
```

Expected: **FAIL** — `execute_query` doesn't filter by forest.

#### Step 3: Update `execute_query` to filter by forest

In `SRC/duckdb_store.py`, update the `execute_query` method:

```python
    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
        graph_forest_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if dialect is not None and dialect not in self.supported_dialects:
            raise ValueError(
                f"Unsupported dialect {dialect!r}; supported: {sorted(self.supported_dialects)}"
            )

        # Resolve forest scope
        forest = self._graph_forest_name if graph_forest_name is None else graph_forest_name

        def _query() -> list[dict[str, Any]]:
            if dialect == "pgq":
                self._ensure_pgq()

            # Wrap in a forest-scoped CTE unless cross-forest ("*")
            effective_query = query
            effective_params = params
            if forest != "*":
                effective_query, effective_params = _inject_forest_filter(
                    query, forest, params
                )

            # DuckDB requires omitting params arg when none provided
            if effective_params is not None:
                result = self._conn.execute(effective_query, effective_params)
            else:
                result = self._conn.execute(effective_query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]

        return await self._run(_query)
```

Now add the `_inject_forest_filter` helper function as a **module-level function** (not a method), placed right after the `_INDEXABLE_FIELDS` dict and before the `DuckDBGraphStore` class:

```python
def _inject_forest_filter(
    query: str,
    forest: str,
    params: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    """Wrap *query* so that ``nodes``, ``edges``, and ``search_index`` are
    filtered to a single forest.

    Uses CTEs that shadow the real table names so the caller's SQL is
    unchanged.  Returns ``(new_query, new_params)`` — the forest value is
    injected as a positional ``$forest`` parameter.
    """
    forest_ctes = (
        "WITH nodes AS (SELECT * FROM nodes WHERE graph_forest_name = $forest), "
        "edges AS (SELECT * FROM edges WHERE graph_forest_name = $forest), "
        "search_index AS (SELECT * FROM search_index WHERE graph_forest_name = $forest) "
    )
    merged_params = dict(params) if params else {}
    merged_params["forest"] = forest
    return forest_ctes + query, merged_params
```

#### Step 4: Run the query filtering tests

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py::TestForestQueryFiltering -v
```

Expected: **ALL PASS**

#### Step 5: Update PGQ materialization to scope by forest

In `SRC/duckdb_store.py`, update `_ensure_pgq` to filter by the store's own forest:

```python
    def _ensure_pgq(self) -> None:
        """Lazily load duckpgq and create the property graph (idempotent)."""
        if self._pgq_ready:
            return
        _install_err = (duckdb.CatalogException, duckdb.HTTPException, duckdb.IOException)
        try:
            self._conn.execute("INSTALL duckpgq; LOAD duckpgq;")
        except _install_err:
            try:
                self._conn.execute("INSTALL duckpgq FROM community; LOAD duckpgq;")
            except _install_err:
                self._conn.execute("LOAD duckpgq;")
        # Materialize per-edge-type tables for the property graph,
        # scoped to the store's own forest.
        forest = self._graph_forest_name
        for etype in _PGQ_EDGE_TYPES:
            tbl = f"pgq_e_{etype.lower()}"
            self._conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            self._conn.execute(
                f"CREATE TABLE {tbl} AS SELECT * FROM edges "
                f"WHERE edge_type = '{etype}' AND graph_forest_name = '{forest}'"
            )
        self._conn.execute("DROP PROPERTY GRAPH IF EXISTS context_graph")
        self._conn.execute(_build_create_property_graph())
        self._pgq_ready = True
```

#### Step 6: Commit

```bash
git add -A && git commit -m "feat(duckdb): forest-scoped execute_query and PGQ materialization"
```

---

## Task 7: Update Behavior YAML

**Files:**
- Modify: `behaviors/context-intelligence.yaml` (repo root, NOT inside modules/)

### Step 1: Update the YAML

Replace the entire `behaviors/context-intelligence.yaml` with:

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
      log_level: "WARNING"
      graph_store:
        type: "file"
        # graph_forest_name is a cross-protocol concept.
        # It lives at the graph_store level, NOT inside backend-specific config.
        # Default: "default". App-cli overrides via bundle overlay config.
        graph_forest_name: "default"
        config:
          graph_store_root: "~/.amplifier/graphs"

tools:
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-module-tool-skills@main
    config:
      skills:
        - git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills
        - git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=skills
```

Key changes:
- `log_level`: changed from `"${CI_LOG_LEVEL:WARNING}"` to plain `"WARNING"` (no env vars)
- `graph_store`: added `graph_forest_name: "default"` at the `graph_store` level
- `config.location` replaced with `config.graph_store_root: "~/.amplifier/graphs"` (note: `graphs` plural, not `graph`)

### Step 2: Commit

```bash
git add behaviors/context-intelligence.yaml && git commit -m "feat(config): update behavior YAML with forest-aware config, remove env vars"
```

---

## Task 8: Fix All Existing Tests Broken by Constructor Changes

Now that all backends have new constructors, every existing test that constructs a store directly needs updating. This task is mechanical — update constructor calls, nothing else.

**Files to modify:**
- `TESTS/test_file_store.py` — every `FileGraphStore(location=...)` call
- `TESTS/test_duckdb_store.py` — the `store` fixture and standalone constructors
- `TESTS/test_store_factory.py` — factory tests with old config shapes
- `TESTS/test_graph_store.py` — `DuckDBGraphStore(":memory:")` call
- `TESTS/conftest.py` — the `services` fixture (if config shape changed)
- `TESTS/test_services.py` — `TestSearchIndexTable` column assertion

### Sub-task 8A: Fix `test_file_store.py`

Every `FileGraphStore(location=str(...))` must become `FileGraphStore(graph_store_root=str(...), graph_forest_name="test")`.

The pattern is systematic. Here is the mapping:

**Old:** `FileGraphStore(location=str(tmp_path / "graph"))`
**New:** `FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="test")`

**Important:** The old `location` pointed to the final directory. The new `graph_store_root` is the parent, and the working directory is `root / forest_name`. So if the old code used `tmp_path / "graph"` as the location, the new equivalent is `graph_store_root=str(tmp_path)` with `graph_forest_name="graph"` (or just use `"test"` and adjust the assertion paths).

**Simplest approach:** Use `graph_store_root=str(tmp_path)` and `graph_forest_name="test"` everywhere. Then update the helper paths to use `tmp_path / "test"` instead of `tmp_path / "graph"`.

Update `_node_file` and `_edge_file` helpers to be used with the correct base path. The tests already compute the base path — just update the `loc` variable pattern.

Here is the **complete replacement pattern** for each test class:

#### `TestProtocolConformance`

```python
class TestProtocolConformance:
    """FileGraphStore must satisfy the GraphStore protocol."""

    def test_isinstance_check(self, tmp_path: Path) -> None:
        store = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="test")
        assert isinstance(store, GraphStore)
```

#### `TestConstructor`

```python
class TestConstructor:
    """Constructor creates directories and initialises empty buffers."""

    def test_creates_node_and_edge_dirs(self, tmp_path: Path) -> None:
        FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="test")
        assert (tmp_path / "test" / "nodes").is_dir()
        assert (tmp_path / "test" / "edges").is_dir()

    def test_tilde_expansion(self) -> None:
        import shutil

        expected = Path.home() / "test-graph-store" / "test"
        try:
            store = FileGraphStore(
                graph_store_root="~/test-graph-store", graph_forest_name="test"
            )
            assert store._location == expected
        finally:
            shutil.rmtree(Path.home() / "test-graph-store", ignore_errors=True)

    def test_empty_buffers(self, tmp_path: Path) -> None:
        store = FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="test")
        assert store._node_buffer == {}
        assert store._edge_buffer == {}

    def test_required_args(self) -> None:
        with pytest.raises(TypeError):
            FileGraphStore()  # type: ignore[call-arg]
```

#### All remaining test classes

Apply the same transformation: replace `FileGraphStore(location=str(loc))` or `FileGraphStore(location=str(tmp_path / "graph"))` with the two-arg form.

For every test that computes `loc = tmp_path / "graph"`, change to `loc = tmp_path / "test"` and construct with `FileGraphStore(graph_store_root=str(tmp_path), graph_forest_name="test")`.

For the `TestPersistence` class, both `store1` and `store2` must use the same `graph_store_root` and `graph_forest_name`.

**Run after all changes:**

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_file_store.py -v
```

Expected: **ALL PASS**

### Sub-task 8B: Fix `test_duckdb_store.py`

#### The `store` fixture

Update the `store` fixture at the top of the file:

```python
@pytest.fixture
def store():
    """Fresh in-memory DuckDBGraphStore for test isolation."""
    from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

    s = DuckDBGraphStore(graph_forest_name="test")
    yield s
    s._conn.close()
```

#### Standalone constructors

Anywhere in the file that calls `DuckDBGraphStore()` without args, add `graph_forest_name="test"`:

- `TestRunUsesGetRunningLoop.test_run_calls_get_running_loop`: `DuckDBGraphStore()` → `DuckDBGraphStore(graph_forest_name="test")`
- `TestProtocolConformance.test_isinstance_graph_store`: same
- `TestConstructor.test_default_connection_is_memory`: same
- `TestConstructor.test_tables_created_on_init`: same
- All `TestSearchIndexTable` tests: same

Anywhere that calls `DuckDBGraphStore(connection=str(db_path))`, add `graph_forest_name="test"`:

- `TestConstructor.test_file_path_expands_tilde`: `DuckDBGraphStore(connection=str(db_path))` → `DuckDBGraphStore(connection=str(db_path), graph_forest_name="test")`
- `TestConstructor.test_file_path_creates_parent_dirs`: same
- `TestClose.test_close_flushes_before_closing`: same
- `TestPersistence.test_data_survives_close_and_reopen`: both `store` and `store2`

#### `TestSearchIndexTable.test_search_index_has_expected_columns`

This test asserts the exact column list. Update to include `graph_forest_name`:

```python
    def test_search_index_has_expected_columns(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'search_index' ORDER BY ordinal_position"
        ).fetchall()
        column_names = [row[0] for row in result]
        assert column_names == [
            "node_id", "graph_forest_name", "session_id", "field_name", "content", "occurred_at"
        ]
```

**Run after all changes:**

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: **ALL PASS**

### Sub-task 8C: Fix `test_store_factory.py`

Update `test_default_type_is_file` — it creates a default file store which now needs a `graph_store_root`:

```python
    def test_default_type_is_file(self, tmp_path):
        """Empty config should default to type='file', not duckdb."""
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

        store = create_graph_store({"config": {"graph_store_root": str(tmp_path)}})
        assert isinstance(store, FileGraphStore)
```

Update `test_duckdb_default_connection_is_memory`:

```python
    def test_duckdb_default_connection_is_memory(self):
        store = create_graph_store({"type": "duckdb"})
        assert store._connection_str == ":memory:"  # type: ignore[attr-defined]
```

Update `test_file_missing_location_raises` — now we test for missing `graph_store_root`:

```python
    def test_file_missing_root_uses_default(self):
        """FileGraphStore without explicit root uses ~/.amplifier/graphs."""
        import shutil

        try:
            store = create_graph_store({"type": "file"})
            assert store.graph_forest_name == "default"
        finally:
            shutil.rmtree(
                Path("~/.amplifier/graphs/default").expanduser(), ignore_errors=True
            )
```

Update `test_file_conforms_to_graph_store_protocol`:

```python
    def test_file_conforms_to_graph_store_protocol(self, tmp_path):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = create_graph_store({"config": {"graph_store_root": str(tmp_path)}})
        assert isinstance(store, GraphStore)
```

Update `TestHookStateServiceIntegration` — these should still work as-is because they pass explicit duckdb config.

**Run after all changes:**

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py -v
```

Expected: **ALL PASS**

### Sub-task 8D: Fix `test_graph_store.py`

Update `test_duckdb_store_is_queryable`:

```python
def test_duckdb_store_is_queryable():
    from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
    from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

    store = DuckDBGraphStore(graph_forest_name="test")
    assert isinstance(store, QueryableStore)
```

**Run after all changes:**

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_graph_store.py -v
```

Expected: **ALL PASS**

### Sub-task 8E: Run the FULL test suite

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest -v
```

Expected: **ALL PASS** (or known pre-existing failures unrelated to our changes).

If any test fails, fix it by applying the same constructor pattern. The `conftest.py` `services` fixture passes `{"type": "duckdb", "config": {"connection": ":memory:"}}` to `HookStateService` — the factory will apply the default `graph_forest_name="default"`, so this should work without changes.

### Step: Commit

```bash
git add -A && git commit -m "fix(tests): update all tests for forest-aware constructors"
```

---

## Task 9: Update Context Documentation — Protocol Doc

**Files:**
- Modify: `context/graph-store-protocol.md` (repo root)

### Step 1: Add "Forest Awareness" section

Add a new section after "## Non-Negotiable Guarantees" and before "## Write Path". Insert:

```markdown
## Forest Awareness

A graph forest is a named collection of sessions sharing the same storage scope. Every `GraphStore` instance belongs to exactly one forest, identified by the `graph_forest_name` read-only property.

### Property Contract

```python
@property
def graph_forest_name(self) -> str:
    """Set at construction time. Immutable for the lifetime of the store."""
    ...
```

Every implementation (`FileGraphStore`, `DuckDBGraphStore`, `GraphState`) exposes this property. It is set at construction time via the factory, which reads `graph_store.graph_forest_name` from config (defaulting to `"default"`).

### Write Scoping

All writes (`upsert_node`, `upsert_edge`) are scoped to the store's forest. The forest value comes from the store instance, not from the caller — handlers never specify a forest.

- **FileGraphStore:** writes to `{graph_store_root}/{graph_forest_name}/nodes/` and `.../edges/`
- **DuckDBGraphStore:** stamps `graph_forest_name` column on every row in `nodes`, `edges`, and `search_index`

### Query Scoping

`QueryableStore.execute_query` accepts an optional `graph_forest_name` parameter:

| `graph_forest_name` value | Behavior |
|---------------------------|----------|
| `None` (default) | Scope to the store's own forest |
| Explicit string | Scope to that specific forest |
| `"*"` | All forests — no forest filter (cross-forest queries) |

### Point Lookups

`get_node` and `get_edge` are **forest-agnostic**. Node IDs are globally unique thanks to the `{session_id}__{event}__{epoch_ms}` format, so point lookups don't need forest filtering.
```

### Step 2: Update the Protocol Interface code block

Update the `execute_query` signature in the protocol code block to include the `graph_forest_name` parameter, and add the `graph_forest_name` property:

In the Protocol Interface code block, add `graph_forest_name` property before `upsert_node`, and add `graph_forest_name` param to `execute_query`.

### Step 3: Commit

```bash
git add context/graph-store-protocol.md && git commit -m "docs(protocol): add Forest Awareness section"
```

---

## Task 10: Update DOT Diagrams

**Files:**
- Modify: `context/graph-store-lifecycle.dot`
- Modify: `context/hook-event-discovery-and-dispatch.dot`
- Modify: `context/read-path.dot`
- Modify: `context/write-path.dot`

### Step 1: Update `graph-store-lifecycle.dot`

In the `opening` state, update the label to show forest name resolution:

```dot
    opening [label="Opening\nRead config → resolve\ngraph_forest_name\nCreate/connect backend\nCREATE IF NOT EXISTS"
             shape=box style=filled fillcolor="#D4E6F1"];
```

Update the transition from `unmounted -> opening`:

```dot
    unmounted -> opening [label="mount()\nfactory reads\ngraph_forest_name\nfrom config"];
```

### Step 2: Update `hook-event-discovery-and-dispatch.dot`

In the `cluster_mount` subgraph, update `config_include` to reference the new config structure and remove env var references:

```dot
        config_include [label="Config: graph_store\n\ngraph_forest_name: 'default'\ntype: 'file'\nconfig:\n  graph_store_root: ~/.amplifier/graphs\n\nNo env var interpolation\nPlain values from config"];
```

### Step 3: Update `read-path.dot`

Add forest context to the analysis query path:

```dot
    analysis [label="Analysis Tool\nexecute_query()\nSQL/PGQ\nforest-scoped"
              shape=box style=filled fillcolor="#E8DAEF"];
```

Add a note about forest filtering:

```dot
    note_forest [label="Forest filtering:\nNone → own forest\nexplicit → that forest\n'*' → cross-forest"
                  shape=note style=filled fillcolor="#FDEBD0"];
    analysis -> note_forest [style=invis];
```

### Step 4: Update `write-path.dot`

Update the DuckDB node to show forest stamping:

```dot
    duckdb [label="DuckDB\n(disk file)\ngraph.duckdb\n\ngraph_forest_name\nstamped on all rows"
            shape=cylinder style=filled fillcolor="#D5F5E3"];
```

### Step 5: Commit

```bash
git add context/*.dot && git commit -m "docs(diagrams): update DOT diagrams for forest-aware storage"
```

---

## Task 11: Update DuckDB Search Skill

**Files:**
- Modify: `skills/context-intelligence-graph-search/SKILL.md` (repo root)

### Step 1: Update the Schema section

Add the `graph_forest_name` column to all three table schemas:

#### `nodes` table

| Column | Type | Constraints |
|--------|------|-------------|
| `node_id` | `VARCHAR` | `PRIMARY KEY` |
| `graph_forest_name` | `VARCHAR` | `NOT NULL DEFAULT 'default'` |
| `session_id` | `VARCHAR` | `DEFAULT ''` |
| `labels` | `VARCHAR[]` | |
| `occurred_at` | `TIMESTAMP` | |
| `properties` | `JSON` | |

#### `edges` table

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

#### `search_index` table

| Column | Type | Constraints |
|--------|------|-------------|
| `node_id` | `VARCHAR` | `NOT NULL` |
| `graph_forest_name` | `VARCHAR` | `NOT NULL DEFAULT 'default'` |
| `session_id` | `VARCHAR` | `NOT NULL` |
| `field_name` | `VARCHAR` | `NOT NULL` |
| `content` | `VARCHAR` | `NOT NULL` |
| `occurred_at` | `TIMESTAMP` | |

### Step 2: Add Forest Query section

Add a new section after "## Query Patterns":

```markdown
### Forest-Scoped Queries

By default, `execute_query` scopes all queries to the store's own forest. You can override this with the `graph_forest_name` parameter.

```python
# Default: queries the store's own forest (no explicit param needed)
rows = await store.execute_query("SELECT node_id FROM nodes")

# Explicit: query a specific forest
rows = await store.execute_query(
    "SELECT node_id FROM nodes",
    graph_forest_name="other-project"
)

# Cross-forest: query all forests
rows = await store.execute_query(
    "SELECT graph_forest_name, node_id FROM nodes",
    graph_forest_name="*"
)
```

The forest filter is injected automatically as CTE wrappers around your query — you don't need to add `WHERE graph_forest_name = ...` yourself.

For raw SQL where you want manual control, use `graph_forest_name="*"` and add your own filter:

```sql
SELECT node_id, graph_forest_name
  FROM nodes
 WHERE graph_forest_name IN ('project-a', 'project-b')
```
```

### Step 3: Update the version

Change the YAML frontmatter version from `0.2.0` to `0.3.0`.

### Step 4: Commit

```bash
git add skills/context-intelligence-graph-search/SKILL.md && git commit -m "docs(skill): update DuckDB skill with graph_forest_name column and query examples"
```

---

## Task 12: Create File Search Skill

**Files:**
- Create: `skills/context-intelligence-file-search/SKILL.md` (repo root)

### Step 1: Create the skill file

Create the directory and file:

```bash
mkdir -p skills/context-intelligence-file-search
```

Write `skills/context-intelligence-file-search/SKILL.md`:

```markdown
---
name: context-intelligence-file-search
description: Filesystem query patterns for the FileGraphStore flat JSON backend
version: 0.1.0
license: MIT
---

# Context Intelligence File Search

This skill applies to the `FileGraphStore` backend — the flat JSON file
store that implements `GraphStore` but NOT `QueryableStore`. Agents using
this backend query the graph via filesystem operations: `grep`, `jq`,
and glob patterns.

---

## Schema

### Directory Layout

```
{graph_store_root}/{graph_forest_name}/
  nodes/
    {node_id}.json
  edges/
    {source}==[{edge_type}]=={target}.json
```

All paths below are relative to the resolved
`{graph_store_root}/{graph_forest_name}/` directory.

### Node ID Format

**Pattern:** `{session_id}__{event_name}__{timestamp_ms}`

- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` → `prompt_submit`
- Session nodes use the raw `session_id` (a UUID) as their node_id
- Example: `6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000`

### Edge ID Format

**Pattern:** `{source_id}==[{edge_type}]=={target_id}`

- `==[` and `]==` are the separators (never appear in node IDs)
- Example: `6afb3613-...==[HAS_STEP]==6afb3613-...__prompt_submit__1741270343000`

### Node JSON Structure

```json
{
  "id": "node_id_here",
  "labels": ["Session", "Root"],
  "properties": {
    "session_id": "6afb3613-...",
    "status": "running",
    "started_at": "2026-01-15T10:00:00Z"
  }
}
```

### Edge JSON Structure

```json
{
  "source": "source_node_id",
  "target": "target_node_id",
  "type": "HAS_RUN",
  "properties": {
    "seq": 1
  }
}
```

---

## Query Patterns

### Pattern 1: Find Nodes by Label

Find all session nodes:

```bash
grep -l '"Session"' nodes/*.json
```

Combine with `jq` for property filtering — find PromptStep nodes mentioning "auth":

```bash
grep -rl '"PromptStep"' nodes/ | xargs jq 'select(.properties.prompt_text | test("auth"))'
```

### Pattern 2: Find Edges by Type

Glob on the edge ID format — the `==[TYPE]==` pattern makes this trivial:

```bash
ls edges/*==[HAS_STEP]==*
```

Find all edge types present:

```bash
ls edges/ | grep -oP '==\[\K[^\]]+' | sort -u
```

### Pattern 3: Find Nodes for a Specific Session

Leverage the session prefix in node IDs:

```bash
ls nodes/{session_id}__*
```

Example:

```bash
ls nodes/6afb3613-7041-4735-9c0f-c2171452ed18__*
```

### Pattern 4: Traverse a Path

Walk from session → run → step using shell pipelines:

```bash
# 1. Find the session node
SESSION_ID="6afb3613-7041-4735-9c0f-c2171452ed18"

# 2. Find HAS_RUN edges from this session
ls edges/${SESSION_ID}==[HAS_RUN]==*

# 3. Extract run node IDs from those edge filenames
for edge in edges/${SESSION_ID}==[HAS_RUN]==*; do
  RUN_ID=$(basename "$edge" .json | sed 's/.*]==//');
  echo "Run: $RUN_ID"

  # 4. Find HAS_STEP edges from each run
  for step_edge in edges/${RUN_ID}==[HAS_STEP]==*; do
    STEP_ID=$(basename "$step_edge" .json | sed 's/.*]==//');
    echo "  Step: $STEP_ID"
    jq '.properties' "nodes/${STEP_ID}.json"
  done
done
```

### Pattern 5: Cross-Forest Queries

Navigate up to `graph_store_root/` and glob across forest subdirectories:

```bash
# Find a session across all forests
ls */nodes/{session_id}__*

# Find all PromptStep nodes across all forests
grep -rl '"PromptStep"' */nodes/

# List all forests
ls -d */
```

### Pattern 6: Full-Text Search Across Properties

Search for a term in all node properties within the current forest:

```bash
grep -rl "authentication" nodes/ | xargs jq '.properties'
```

Search with context (show the node ID too):

```bash
grep -rl "authentication" nodes/ | while read f; do
  echo "=== $(basename "$f" .json) ==="
  jq '.properties' "$f"
done
```

---

## Notes

### Path Resolution

All paths in this skill are relative to the resolved
`{graph_store_root}/{graph_forest_name}/` directory. The graph store root
and forest name come from the coordinator config at mount time — never
from environment variables, never hardcoded.

To find the actual path at runtime, check the behavior YAML:

```yaml
graph_store:
  graph_forest_name: "default"
  config:
    graph_store_root: "~/.amplifier/graphs"
```

The resolved path would be `~/.amplifier/graphs/default/`.

### FileGraphStore Does NOT Implement QueryableStore

The file backend implements `GraphStore` only. It does not support
`execute_query`, `supported_dialects`, or any SQL/PGQ operations.
All queries must use filesystem operations as shown above.

If you need SQL/PGQ queries, use the DuckDB backend and refer to the
`context-intelligence-graph-search` skill instead.
```

### Step 2: Commit

```bash
git add skills/context-intelligence-file-search/ && git commit -m "docs(skill): add file search skill with 6 query patterns"
```

---

## Task 13: Final Verification

### Step 1: Run the complete test suite

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest -v
```

Expected: **ALL PASS**

### Step 2: Run type checking

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pyright amplifier_module_hook_context_intelligence/
```

Expected: No new errors.

### Step 3: Run linting

```bash
cd /home/dicolomb/context-itelligence-bundle-v2-storage/amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run ruff check amplifier_module_hook_context_intelligence/ tests/
```

Expected: No new errors.

### Step 4: Final commit

```bash
git add -A && git commit -m "chore: final verification pass for graph forest storage"
```

---

## Dependency Graph

```
Task 1 (protocol property)
Task 2 (protocol execute_query param)
  │
  ├── Task 3 (GraphState) ──────────────────┐
  ├── Task 4 (Factory) ─────────────────────┤
  ├── Task 5 (FileGraphStore) ──────────────┤
  ├── Task 6A-D (DuckDBGraphStore) ─────────┤
  │                                          │
  ├── Task 7 (Behavior YAML) [independent]  │
  │                                          │
  └──────────────────────────── Task 8 (Fix all tests)
                                             │
                                Task 9  (Protocol doc)    [independent]
                                Task 10 (DOT diagrams)    [independent]
                                Task 11 (DuckDB skill)    [independent]
                                Task 12 (File search skill) [independent]
                                             │
                                Task 13 (Final verification)
```

---

## Summary of All Files Changed

### Source files (inside `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/`)

| File | Change |
|------|--------|
| `graph_store.py` | Add `graph_forest_name` property to `GraphStore`, add `graph_forest_name` param to `QueryableStore.execute_query` |
| `services.py` | Add `graph_forest_name` param + property to `GraphState` |
| `store_factory.py` | Rewrite to read `graph_forest_name` from config, pass to all backends |
| `file_store.py` | Replace `location` constructor with `graph_store_root` + `graph_forest_name` |
| `duckdb_store.py` | Add `graph_forest_name` constructor param, property, schema column, write stamping, query filtering, PGQ scoping |

### Test files (inside `modules/hook-context-intelligence/tests/`)

| File | Change |
|------|--------|
| `test_graph_store.py` | Add forest property test, update all `FakeStore` classes, fix `DuckDBGraphStore` construction |
| `test_services.py` | Add `graph_forest_name` tests to `TestGraphState` |
| `test_store_factory.py` | Add forest config tests, update existing constructor patterns |
| `test_file_store.py` | Add `TestForestAwareness`, update all constructor calls |
| `test_duckdb_store.py` | Add `TestForestSchema`, `TestForestWrites`, `TestForestQueryFiltering`, update all constructor calls |

### Config and docs (repo root)

| File | Change |
|------|--------|
| `behaviors/context-intelligence.yaml` | Remove env var, add `graph_forest_name`, change `location` to `graph_store_root` |
| `context/graph-store-protocol.md` | Add "Forest Awareness" section |
| `context/graph-store-lifecycle.dot` | Show forest name resolution in mount flow |
| `context/hook-event-discovery-and-dispatch.dot` | Show config flow, remove env var references |
| `context/read-path.dot` | Add forest filtering context |
| `context/write-path.dot` | Add forest stamping context |
| `skills/context-intelligence-graph-search/SKILL.md` | Add `graph_forest_name` column to schema, add forest query section |
| `skills/context-intelligence-file-search/SKILL.md` | **NEW** — 6 query patterns for flat JSON store |
