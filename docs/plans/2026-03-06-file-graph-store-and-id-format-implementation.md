# File-Based GraphStore and Universal ID Format Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Implement a file-based `GraphStore`, change node/edge ID formats to be filesystem-safe, restructure the config layout, and update all tests and documentation.

**Architecture:** New `FileGraphStore` implements the existing `GraphStore` protocol using flat JSON files in `nodes/` and `edges/` directories. `make_node_id` switches from colon separators to double-underscore separators. A new `make_edge_id` function generates deterministic edge filenames. The factory defaults to `"file"` type and passes type-specific config via `**kwargs`.

**Tech Stack:** Python 3.11+, pytest (asyncio_mode=auto), DuckDB (existing), JSON (new file store), no new dependencies.

**Design doc:** `docs/plans/2026-03-06-file-graph-store-and-id-format-design.md`

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

## Task 1: Update `make_node_id` format + add `make_edge_id`

**Files:**
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/utils.py`
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_utils.py`

### Step 1: Update test assertions for new `make_node_id` format

Open `TESTS/test_utils.py`. The `TestMakeNodeId` class has 7 tests that assert the old colon-based format. Replace the entire `TestMakeNodeId` class AND add a new `TestMakeEdgeId` class. Leave `TestHandlerLogger` and `TestEventLogContext` completely untouched.

Replace lines 1-53 of `tests/test_utils.py` (everything from the module docstring through the end of `TestMakeNodeId`) with:

```python
"""Tests for utils: make_node_id, make_edge_id, HandlerLogger, EventLogContext."""

from __future__ import annotations

import logging

from amplifier_module_hook_context_intelligence.utils import (
    EventLogContext,
    HandlerLogger,
    make_edge_id,
    make_node_id,
)


class TestMakeNodeId:
    """7 tests for the make_node_id utility — new __ separator format."""

    def test_basic_iso_timestamp(self):
        """Basic ISO-8601 with trailing Z produces correct epoch ms."""
        result = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        assert result == "s1__prompt_submit__1767225600000"

    def test_fractional_seconds(self):
        """Fractional seconds (.500) are preserved as milliseconds."""
        result = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00.500Z")
        assert result == "s1__prompt_submit__1767225600500"

    def test_timezone_offset(self):
        """Timezone offset +00:00 is handled correctly."""
        result = make_node_id("s1", "session:resume", "2026-01-01T02:00:00+00:00")
        assert result == "s1__session_resume__1767232800000"

    def test_deterministic(self):
        """Same inputs always produce the same output."""
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        assert a == b

    def test_different_events_produce_different_ids(self):
        """Different event names produce different node IDs."""
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s1", "session:start", "2026-01-01T00:00:00Z")
        assert a != b

    def test_different_sessions_produce_different_ids(self):
        """Different session IDs produce different node IDs."""
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s2", "prompt:submit", "2026-01-01T00:00:00Z")
        assert a != b

    def test_resume_pattern(self):
        """session:resume event follows the standard pattern, not the session exception."""
        result = make_node_id("sess-abc", "session:resume", "2026-01-01T02:00:00+00:00")
        assert result == "sess-abc__session_resume__1767232800000"


class TestMakeEdgeId:
    """5 tests for the make_edge_id utility."""

    def test_basic_construction(self):
        """Produces {source}==[{edge_type}]=={target} format."""
        result = make_edge_id("session-1", "node-2", "HAS_STEP")
        assert result == "session-1==[HAS_STEP]==node-2"

    def test_with_real_node_ids(self):
        """Works with realistic node IDs containing __ separators."""
        source = "6afb3613-7041-4735-9c0f-c2171452ed18"
        target = "6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000"
        result = make_edge_id(source, target, "HAS_STEP")
        expected = f"{source}==[HAS_STEP]=={target}"
        assert result == expected

    def test_parseable_back_to_components(self):
        """Edge ID can be split back into source, edge_type, target."""
        edge_id = make_edge_id("src-1", "tgt-2", "SUBSESSION_OF")
        # Parse: split on ==[ to get [source, rest], then split rest on ]== to get [type, target]
        source, rest = edge_id.split("==[", 1)
        edge_type, target = rest.split("]==", 1)
        assert source == "src-1"
        assert edge_type == "SUBSESSION_OF"
        assert target == "tgt-2"

    def test_deterministic(self):
        """Same inputs always produce the same output."""
        a = make_edge_id("s1", "t1", "HAS_STEP")
        b = make_edge_id("s1", "t1", "HAS_STEP")
        assert a == b

    def test_different_edge_types_produce_different_ids(self):
        """Different edge types produce different IDs."""
        a = make_edge_id("s1", "t1", "HAS_STEP")
        b = make_edge_id("s1", "t1", "SUBSESSION_OF")
        assert a != b
```

Keep everything from `class TestHandlerLogger:` onward exactly as-is.

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_utils.py -v
```

Expected: `TestMakeNodeId` tests FAIL (old format still produces colons). `TestMakeEdgeId` tests FAIL (`make_edge_id` doesn't exist yet). `TestHandlerLogger` and `TestEventLogContext` still PASS.

### Step 3: Implement `make_node_id` format change and `make_edge_id`

Open `SRC/utils.py`. Replace the `make_node_id` function (lines 10-20) and add `make_edge_id` right after it.

Replace:
```python
def make_node_id(session_id: str, event_name: str, timestamp: str) -> str:
    """Generate a deterministic node ID from event data.

    Pattern: {session_id}:{event_name}:{timestamp_ms}

    Parses ISO-8601 timestamps (with fractional seconds and timezone offsets)
    and converts to epoch milliseconds.
    """
    dt = datetime.fromisoformat(timestamp)
    epoch_ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    return f"{session_id}:{event_name}:{epoch_ms}"
```

With:
```python
def make_node_id(session_id: str, event_name: str, timestamp: str) -> str:
    """Generate a deterministic, filesystem-safe node ID from event data.

    Pattern: {session_id}__{event_name}__{timestamp_ms}

    Colons in event_name are replaced with underscores so the ID is safe
    as a filename on all platforms (Linux, macOS, Windows).

    Session nodes do NOT use this function — they use the raw session_id
    (a UUID with hyphens, already filesystem-safe) as their node_id.

    Parses ISO-8601 timestamps (with fractional seconds and timezone offsets)
    and converts to epoch milliseconds.
    """
    dt = datetime.fromisoformat(timestamp)
    epoch_ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    safe_event = event_name.replace(":", "_")
    return f"{session_id}__{safe_event}__{epoch_ms}"


def make_edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    """Generate a deterministic, filesystem-safe edge ID.

    Pattern: {source_id}==[{edge_type}]=={target_id}

    The ``==[`` and ``]==`` separators never appear in node IDs (which use
    ``__``, ``_``, and ``-``), making the edge ID unambiguously parseable:

        source, rest = edge_id.split("==[", 1)
        edge_type, target = rest.split("]==", 1)

    All characters (``=``, ``[``, ``]``) are filesystem-safe on Linux, macOS,
    and Windows.
    """
    return f"{source_id}==[{edge_type}]=={target_id}"
```

### Step 4: Run tests to verify they pass

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_utils.py -v
```

Expected: ALL tests in `test_utils.py` PASS.

### Step 5: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/utils.py modules/hook-context-intelligence/tests/test_utils.py && git commit -m "feat: filesystem-safe node IDs (__ separator) and make_edge_id"
```

---

## Task 2: Update config layout and factory

**Files:**
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/store_factory.py`
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_store_factory.py`
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/conftest.py`
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_services.py`

### Step 1: Update the factory tests for new config shape

Replace the entire contents of `TESTS/test_store_factory.py` with:

```python
"""Tests for store_factory – factory function for graph store backends."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.store_factory import create_graph_store


class TestCreateGraphStore:
    """Verify create_graph_store dispatches correctly with nested config."""

    def test_returns_duckdb_store_for_explicit_type(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = create_graph_store({"type": "duckdb", "config": {"connection": ":memory:"}})
        assert isinstance(store, DuckDBGraphStore)

    def test_default_type_is_file(self, tmp_path):
        """Empty config defaults to type 'file' — requires location."""
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

        location = str(tmp_path / "graph")
        store = create_graph_store({"config": {"location": location}})
        assert isinstance(store, FileGraphStore)

    def test_duckdb_default_connection_is_memory(self):
        store = create_graph_store({"type": "duckdb"})
        assert store._connection_str == ":memory:"  # type: ignore[attr-defined]

    def test_passes_connection_string_through(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store = create_graph_store({"type": "duckdb", "config": {"connection": db_path}})
        assert store._connection_str == db_path  # type: ignore[attr-defined]

    def test_raises_for_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown graph_store type: neo4j"):
            create_graph_store({"type": "neo4j"})

    def test_duckdb_conforms_to_graph_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = create_graph_store({"type": "duckdb"})
        assert isinstance(store, GraphStore)

    def test_file_conforms_to_graph_store_protocol(self, tmp_path):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        location = str(tmp_path / "graph")
        store = create_graph_store({"type": "file", "config": {"location": location}})
        assert isinstance(store, GraphStore)

    def test_file_store_missing_location_raises(self):
        """File store with no location in config raises TypeError."""
        with pytest.raises(TypeError):
            create_graph_store({"type": "file"})


class TestHookStateServiceIntegration:
    """Verify HookStateService uses the store factory with nested config."""

    def test_explicit_duckdb_config_creates_duckdb_store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
        )
        assert isinstance(service.graph, DuckDBGraphStore)

    async def test_nested_graph_store_config_passed_through(self, tmp_path):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        db_path = str(tmp_path / "test.db")
        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": db_path}}}
        )
        try:
            assert service.graph._connection_str == db_path  # type: ignore[attr-defined]
        finally:
            await service.graph.close()

    def test_unknown_store_type_raises(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        with pytest.raises(ValueError, match="Unknown graph_store type"):
            HookStateService(raw_config={"graph_store": {"type": "bogus"}})
```

### Step 2: Update `conftest.py` to use explicit DuckDB config

Replace the entire contents of `TESTS/conftest.py` with:

```python
"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService


@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService with DuckDB in-memory for testing."""
    return HookStateService(
        raw_config={
            "graph_store": {
                "type": "duckdb",
                "config": {"connection": ":memory:"},
            }
        }
    )
```

### Step 3: Update `test_services.py` for new config shape

In `TESTS/test_services.py`, the `TestHookStateService` class (starting at line 134) has tests that create `HookStateService(raw_config={})`. Those will fail because the default type is now `"file"` which requires `location`. Update only the `TestHookStateService` class. Leave `TestHookConfig` and `TestGraphState` completely untouched.

Replace the `TestHookStateService` class (lines 134-157) with:

```python
class TestHookStateService:
    def test_construction(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.services import (
            HookConfig,
            HookStateService,
        )

        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
        )
        assert isinstance(service.graph, GraphStore)
        assert isinstance(service.config, HookConfig)

    async def test_graph_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(
            raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
        )
        await service.graph.upsert_node("test", labels={"Test"}, properties={})
        assert await service.graph.get_node("test") is not None

    def test_config_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(
            raw_config={
                "exclude_events": ["foo:bar"],
                "graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}},
            }
        )
        assert service.config.is_excluded("foo:bar") is True
```

### Step 4: Run updated tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py tests/test_services.py tests/conftest.py -v
```

Expected: FAIL — factory still defaults to `"duckdb"`, doesn't read nested `config`, and `file_store` module doesn't exist yet.

### Step 5: Update the factory implementation

Replace the entire contents of `SRC/store_factory.py` with:

```python
"""Factory for graph store backends."""

from __future__ import annotations

from typing import Any

from .graph_store import GraphStore


def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    """Create a graph store from configuration.

    Parameters
    ----------
    store_config:
        Dictionary with optional ``type`` (default ``"file"``) and a nested
        ``config`` dict whose keys are passed as ``**kwargs`` to the
        implementation constructor.

    Examples
    --------
    File store (default)::

        {"type": "file", "config": {"location": "/path/to/graph"}}

    DuckDB store::

        {"type": "duckdb", "config": {"connection": ":memory:"}}
    """
    store_type = store_config.get("type", "file")
    impl_config = store_config.get("config", {})

    if store_type == "file":
        from .file_store import FileGraphStore

        return FileGraphStore(**impl_config)

    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore

        return DuckDBGraphStore(**impl_config)

    raise ValueError(f"Unknown graph_store type: {store_type}")
```

### Step 6: Run tests that don't need `FileGraphStore` to exist yet

At this point `FileGraphStore` doesn't exist, so only run the DuckDB-specific tests and conftest-dependent tests:

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_services.py tests/test_session_handler.py tests/test_prompt_step_handler.py -v
```

Expected: ALL PASS. The `conftest.py` fixture now passes `type: "duckdb"` explicitly so the factory never tries to import `file_store`.

**Do NOT run the full `test_store_factory.py` yet** — two tests reference `FileGraphStore` which doesn't exist. That's expected. They will pass after Task 3.

### Step 7: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/store_factory.py modules/hook-context-intelligence/tests/conftest.py modules/hook-context-intelligence/tests/test_store_factory.py modules/hook-context-intelligence/tests/test_services.py && git commit -m "feat: nested config layout, factory defaults to file type"
```

---

## Task 3: Create `FileGraphStore`

**Files:**
- Create: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/file_store.py`
- Create: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_file_store.py`

### Step 1: Write the test file

Create `TESTS/test_file_store.py` with:

```python
"""Tests for FileGraphStore – buffer-first reads with async JSON file persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def store(tmp_path: Path):
    """Fresh FileGraphStore using a temp directory."""
    from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

    return FileGraphStore(location=str(tmp_path / "graph"))


@pytest.fixture
def graph_path(tmp_path: Path) -> Path:
    """Return the graph root directory (matches store fixture)."""
    return tmp_path / "graph"


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------
class TestProtocolConformance:
    """FileGraphStore must satisfy the GraphStore runtime protocol."""

    def test_isinstance_graph_store(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = FileGraphStore(location=str(tmp_path / "graph"))
        assert isinstance(store, GraphStore)


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------
class TestConstructor:
    """Verify constructor wiring: directory creation and buffer init."""

    def test_creates_nodes_and_edges_dirs(self, store, graph_path: Path):
        assert (graph_path / "nodes").is_dir()
        assert (graph_path / "edges").is_dir()

    def test_expands_tilde_in_location(self, tmp_path: Path, monkeypatch):
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

        monkeypatch.setenv("HOME", str(tmp_path))
        store = FileGraphStore(location="~/test-graph")
        assert (tmp_path / "test-graph" / "nodes").is_dir()

    def test_empty_buffers_on_init(self, store):
        assert store._node_buffer == {}
        assert store._edge_buffer == {}

    def test_location_required(self):
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

        with pytest.raises(TypeError):
            FileGraphStore()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# TestBufferWrites
# ---------------------------------------------------------------------------
class TestBufferWrites:
    """upsert_node / upsert_edge write to in-memory buffers only."""

    async def test_upsert_node_writes_to_buffer(self, store, graph_path: Path):
        await store.upsert_node("n1", {"Label"}, {"key": "val"})
        assert "n1" in store._node_buffer
        # No file on disk yet
        assert not (graph_path / "nodes" / "n1.json").exists()

    async def test_upsert_edge_writes_to_buffer(self, store, graph_path: Path):
        await store.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        assert ("a", "b", "KNOWS") in store._edge_buffer
        # No file on disk yet
        assert len(list((graph_path / "edges").iterdir())) == 0

    async def test_upsert_node_merges_labels(self, store):
        await store.upsert_node("n1", {"A"}, {})
        await store.upsert_node("n1", {"B"}, {})
        assert store._node_buffer["n1"]["labels"] == {"A", "B"}

    async def test_upsert_node_merges_properties(self, store):
        await store.upsert_node("n1", set(), {"a": 1})
        await store.upsert_node("n1", set(), {"b": 2})
        props = store._node_buffer["n1"]["properties"]
        assert props == {"a": 1, "b": 2}

    async def test_upsert_edge_merges_properties(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"x": 1})
        await store.upsert_edge("a", "b", "KNOWS", {"y": 2})
        props = store._edge_buffer[("a", "b", "KNOWS")]["properties"]
        assert props == {"x": 1, "y": 2}


# ---------------------------------------------------------------------------
# TestBufferFirstReads
# ---------------------------------------------------------------------------
class TestBufferFirstReads:
    """get_node / get_edge must reflect buffered state."""

    async def test_get_node_returns_buffered_data(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        node = await store.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Person"}
        assert node["properties"] == {"name": "Alice"}

    async def test_get_edge_returns_buffered_data(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"since": 2020})
        edge = await store.get_edge("a", "b", "KNOWS")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["target"] == "b"
        assert edge["type"] == "KNOWS"
        assert edge["properties"] == {"since": 2020}

    async def test_get_nonexistent_node_returns_none(self, store):
        result = await store.get_node("nope")
        assert result is None

    async def test_get_nonexistent_edge_returns_none(self, store):
        result = await store.get_edge("x", "y", "NOPE")
        assert result is None

    async def test_buffer_wins_over_stale_disk(self, store):
        """Upsert after flush: buffer value should override stale disk data."""
        await store.upsert_node("n1", {"V1"}, {"version": 1})
        await store.flush()
        await store.upsert_node("n1", {"V2"}, {"version": 2})
        node = await store.get_node("n1")
        assert node is not None
        assert node["labels"] == {"V2"}
        assert node["properties"] == {"version": 2}


# ---------------------------------------------------------------------------
# TestFlush
# ---------------------------------------------------------------------------
class TestFlush:
    """flush() persists buffers to JSON files and clears them."""

    async def test_flush_writes_node_files(self, store, graph_path: Path):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        node_file = graph_path / "nodes" / "n1.json"
        assert node_file.exists()
        data = json.loads(node_file.read_text())
        assert data["id"] == "n1"
        assert "Person" in data["labels"]
        assert data["properties"]["name"] == "Alice"

    async def test_flush_writes_edge_files(self, store, graph_path: Path):
        await store.upsert_edge("a", "b", "KNOWS", {"w": 1})
        await store.flush()
        edge_files = list((graph_path / "edges").iterdir())
        assert len(edge_files) == 1
        data = json.loads(edge_files[0].read_text())
        assert data["source"] == "a"
        assert data["target"] == "b"
        assert data["type"] == "KNOWS"
        assert data["properties"] == {"w": 1}

    async def test_flush_clears_both_buffers(self, store):
        await store.upsert_node("n1", {"X"}, {})
        await store.upsert_edge("a", "b", "R", {})
        await store.flush()
        assert len(store._node_buffer) == 0
        assert len(store._edge_buffer) == 0

    async def test_get_node_from_disk_after_flush(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        assert len(store._node_buffer) == 0
        node = await store.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Person"}
        assert node["properties"] == {"name": "Alice"}

    async def test_get_edge_from_disk_after_flush(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"w": 1})
        await store.flush()
        assert len(store._edge_buffer) == 0
        edge = await store.get_edge("a", "b", "KNOWS")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["target"] == "b"
        assert edge["type"] == "KNOWS"
        assert edge["properties"] == {"w": 1}

    async def test_flush_empty_buffer_is_noop(self, store):
        await store.flush()
        await store.flush()

    async def test_flush_merges_with_existing_file(self, store, graph_path: Path):
        """Second flush merges labels and properties with existing file."""
        await store.upsert_node("n1", {"Session"}, {"started": True})
        await store.flush()
        await store.upsert_node("n1", {"Root"}, {"ended": True})
        await store.flush()
        data = json.loads((graph_path / "nodes" / "n1.json").read_text())
        assert "Session" in data["labels"]
        assert "Root" in data["labels"]
        assert data["properties"]["started"] is True
        assert data["properties"]["ended"] is True


# ---------------------------------------------------------------------------
# TestExecuteQuery
# ---------------------------------------------------------------------------
class TestExecuteQuery:
    """execute_query raises NotImplementedError — file store doesn't support SQL."""

    async def test_execute_query_raises_not_implemented(self, store):
        with pytest.raises(NotImplementedError):
            await store.execute_query("SELECT * FROM nodes")


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() must flush before releasing resources."""

    async def test_close_flushes_pending_data(self, store, graph_path: Path):
        await store.upsert_node("n1", {"X"}, {"val": 42})
        await store.close()
        assert (graph_path / "nodes" / "n1.json").exists()


# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------
class TestPersistence:
    """Data must survive close and reopen."""

    async def test_data_survives_close_and_reopen(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.file_store import FileGraphStore

        location = str(tmp_path / "graph")

        store = FileGraphStore(location=location)
        await store.upsert_node("n1", {"Person"}, {"name": "Bob"})
        await store.upsert_edge("n1", "n2", "KNOWS", {"since": 2021})
        await store.close()

        store2 = FileGraphStore(location=location)
        node = await store2.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Person"}
        assert node["properties"] == {"name": "Bob"}

        edge = await store2.get_edge("n1", "n2", "KNOWS")
        assert edge is not None
        assert edge["source"] == "n1"
        assert edge["target"] == "n2"
        assert edge["type"] == "KNOWS"
        assert edge["properties"] == {"since": 2021}
        await store2.close()
```

### Step 2: Run tests to verify they fail

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_file_store.py -v
```

Expected: ALL FAIL — `file_store` module doesn't exist.

### Step 3: Implement `FileGraphStore`

Create `SRC/file_store.py` with:

```python
"""FileGraphStore – buffer-first reads with async JSON file persistence.

STANDING RULE — Skill Synchronization
--------------------------------------
Any change to the node/edge ID format, new label types, or new edge types
MUST be accompanied by an update to the SQL/PGQ skill at
``skills/context-intelligence-graph-search/SKILL.md``.

The skill is the contract between this storage layer and agents that generate
queries.  Stale skill = broken agent query generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .utils import make_edge_id

logger = logging.getLogger(__name__)


class FileGraphStore:
    """Graph store backed by flat JSON files with in-memory write buffer.

    Writes are buffered in Python dicts for instant access.  ``flush()``
    persists buffers to JSON files in ``{location}/nodes/`` and
    ``{location}/edges/`` via ``run_in_executor``.  Reads check the buffer
    first, falling back to disk only when the buffer has no entry.

    Does NOT support ``execute_query`` — raises ``NotImplementedError``.
    Use grep/jq on the JSON files for ad-hoc queries.
    """

    def __init__(self, location: str) -> None:
        self._location = Path(location).expanduser()
        self._nodes_dir = self._location / "nodes"
        self._edges_dir = self._location / "edges"
        self._nodes_dir.mkdir(parents=True, exist_ok=True)
        self._edges_dir.mkdir(parents=True, exist_ok=True)
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, fn: Any) -> Any:  # noqa: ANN401
        """Run a blocking callable in the default executor."""
        return asyncio.get_running_loop().run_in_executor(None, fn)

    def _node_path(self, node_id: str) -> Path:
        return self._nodes_dir / f"{node_id}.json"

    def _edge_path(self, source: str, target: str, edge_type: str) -> Path:
        edge_id = make_edge_id(source, target, edge_type)
        return self._edges_dir / f"{edge_id}.json"

    # ------------------------------------------------------------------
    # Writes (buffer only, no I/O)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Reads (buffer-first, then disk)
    # ------------------------------------------------------------------

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        buffered = self._node_buffer.get(node_id)
        if buffered is not None:
            return buffered

        path = self._node_path(node_id)

        def _read() -> dict[str, Any] | None:
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return {
                "id": data["id"],
                "labels": set(data["labels"]),
                "properties": data["properties"],
            }

        return await self._run(_read)

    async def get_edge(
        self, source: str, target: str, edge_type: str
    ) -> dict[str, Any] | None:
        key = (source, target, edge_type)
        buffered = self._edge_buffer.get(key)
        if buffered is not None:
            return buffered

        path = self._edge_path(source, target, edge_type)

        def _read() -> dict[str, Any] | None:
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return {
                "source": data["source"],
                "target": data["target"],
                "type": data["type"],
                "properties": data["properties"],
            }

        return await self._run(_read)

    # ------------------------------------------------------------------
    # Flush (persist buffers to JSON files)
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        nodes = self._node_buffer
        edges = self._edge_buffer
        self._node_buffer = {}
        self._edge_buffer = {}

        if not nodes and not edges:
            return

        def _write() -> None:
            try:
                for node in nodes.values():
                    path = self._node_path(node["id"])
                    # Merge with existing file if present
                    if path.exists():
                        existing = json.loads(path.read_text())
                        merged_labels = set(existing.get("labels", []))
                        merged_labels |= node["labels"]
                        merged_props = existing.get("properties", {})
                        merged_props.update(node["properties"])
                        data = {
                            "id": node["id"],
                            "labels": sorted(merged_labels),
                            "properties": merged_props,
                        }
                    else:
                        data = {
                            "id": node["id"],
                            "labels": sorted(node["labels"]),
                            "properties": node["properties"],
                        }
                    path.write_text(json.dumps(data, indent=2))

                for (source, target, edge_type), edge in edges.items():
                    path = self._edge_path(source, target, edge_type)
                    if path.exists():
                        existing = json.loads(path.read_text())
                        merged_props = existing.get("properties", {})
                        merged_props.update(edge["properties"])
                        data = {
                            "source": edge["source"],
                            "target": edge["target"],
                            "type": edge["type"],
                            "properties": merged_props,
                        }
                    else:
                        data = {
                            "source": edge["source"],
                            "target": edge["target"],
                            "type": edge["type"],
                            "properties": edge["properties"],
                        }
                    path.write_text(json.dumps(data, indent=2))
            except Exception:
                # Restore buffers for retry
                self._node_buffer.update(nodes)
                self._edge_buffer.update(edges)
                logger.warning("flush failed; buffers restored for retry", exc_info=True)

        await self._run(_write)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "FileGraphStore does not support execute_query. "
            "Use grep/jq on the JSON files for ad-hoc queries."
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self.flush()
```

### Step 4: Run `FileGraphStore` tests

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_file_store.py -v
```

Expected: ALL PASS.

### Step 5: Run factory tests that depend on `FileGraphStore`

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py -v
```

Expected: ALL PASS (the `test_default_type_is_file` and `test_file_conforms_to_graph_store_protocol` tests now work).

### Step 6: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/file_store.py modules/hook-context-intelligence/tests/test_file_store.py && git commit -m "feat: FileGraphStore — JSON file-based GraphStore implementation"
```

---

## Task 4: Update DuckDB store and handler tests for new ID format

**Files:**
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_prompt_step_handler.py`

The DuckDB store itself (`duckdb_store.py`) needs NO changes — its constructor already takes `connection` as a kwarg, which works with `**impl_config`. Its tests (`test_duckdb_store.py`) use raw synthetic node IDs like `"n1"`, `"a"`, `"b"` — those don't reference the old colon format, so they need no changes.

The file that DOES need updating is `test_prompt_step_handler.py`, which has a hardcoded `EXPECTED_NODE_ID` using the old colon format.

### Step 1: Update the hardcoded expected node ID

In `TESTS/test_prompt_step_handler.py`, line 13 has:

```python
EXPECTED_NODE_ID = "s1:prompt:submit:1772758800000"
```

Replace it with:

```python
EXPECTED_NODE_ID = "s1__prompt_submit__1772758800000"
```

That's the only change in this file. Everything else references `EXPECTED_NODE_ID` by name.

### Step 2: Run prompt step handler tests

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_prompt_step_handler.py -v
```

Expected: ALL PASS.

### Step 3: Run DuckDB store tests to verify nothing broke

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_duckdb_store.py -v
```

Expected: ALL PASS.

### Step 4: Commit

```bash
cd amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/tests/test_prompt_step_handler.py && git commit -m "fix: update EXPECTED_NODE_ID to new __ separator format"
```

---

## Task 5: Update skill file

**Files:**
- Modify: `amplifier-bundle-context-intelligence/skills/context-intelligence-graph-search/SKILL.md`

### Step 1: Add Node ID and Edge ID format sections

Open `skills/context-intelligence-graph-search/SKILL.md`. After the `## Schema` heading (line 16) and before `### \`nodes\`` (line 18), insert these new sections:

```markdown

### Node ID Format

Node IDs are generated by `make_node_id()` in `utils.py` and are filesystem-safe on all platforms.

**Pattern:** `{session_id}__{event_name}__{timestamp_ms}`

- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` -> `prompt_submit`
- Session nodes use the raw `session_id` (a UUID) as their node_id -- no transformation
- Example: `6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000`

### Edge ID Format

Edge IDs are generated by `make_edge_id()` in `utils.py`. Used as filenames in the file-based store and as deterministic identifiers across all implementations.

**Pattern:** `{source_id}==[{edge_type}]=={target_id}`

- `==[` and `]==` are the separators (never appear in node IDs)
- Example: `6afb3613-...==[HAS_STEP]==6afb3613-...__prompt_submit__1741270343000`
- Parse: `source, rest = edge_id.split("==[", 1)` then `edge_type, target = rest.split("]==", 1)`

### Multiple Storage Backends

The graph can be stored in DuckDB (SQL/PGQ queries) or as flat JSON files (grep/jq queries). Node IDs and edge IDs are identical across backends -- generated by shared functions in `utils.py`. The DuckDB schema below applies only to the DuckDB backend. The file-based backend stores self-contained JSON files in `nodes/` and `edges/` directories.

```

### Step 2: Commit

```bash
cd amplifier-bundle-context-intelligence && git add skills/context-intelligence-graph-search/SKILL.md && git commit -m "docs: update skill with new ID formats and multi-backend note"
```

---

## Task 6: Update AGENTS.md

**Files:**
- Modify: `/home/dicolomb/context-itelligence-bundle-v2/AGENTS.md`

### Step 1: Add storage parity standing rule

Open `/home/dicolomb/context-itelligence-bundle-v2/AGENTS.md`. At the end of the file (after the existing "Standing Rule: Schema-Skill Synchronization" block and its closing paragraph, line 151), append:

```markdown

### Standing Rule: Storage Implementation Parity

With multiple `GraphStore` implementations (DuckDB, File-based), we must ensure:

- **Format parity**: Node IDs and edge IDs are generated by shared functions in `utils.py` (`make_node_id`, `make_edge_id`). ALL implementations use the same IDs. This enables reconciliation between stores.
- **Write/Read parity**: All implementations MUST support the full `upsert_node`, `upsert_edge`, `get_node`, `get_edge`, `flush`, `close` contract with identical merge-on-upsert semantics (labels unioned, properties updated).
- **Query capabilities may differ**: DuckDB supports `execute_query` (SQL/PGQ). File store raises `NotImplementedError` (use grep/jq). This is acceptable -- query is backend-specific.
- **Non-blocking writes**: Core protocol requirement for ALL implementations. No exceptions.

When adding a new `GraphStore` implementation, verify it passes the same behavioral tests as existing implementations (write, read, merge, flush, close).
```

### Step 2: Commit

```bash
cd /home/dicolomb/context-itelligence-bundle-v2 && git add AGENTS.md && git commit -m "docs: add storage implementation parity standing rule to AGENTS.md"
```

---

## Task 7: Full test suite verification

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
cd amplifier-bundle-context-intelligence && git add -A && git commit -m "chore: fix lint/type issues from file store implementation"
```

Only commit this if there were actual fixes needed. If everything was clean, skip this step.

---

## Summary of all files changed

| File | Action | Task |
|------|--------|------|
| `SRC/utils.py` | Modify | 1 |
| `TESTS/test_utils.py` | Modify | 1 |
| `SRC/store_factory.py` | Modify | 2 |
| `TESTS/test_store_factory.py` | Modify | 2 |
| `TESTS/conftest.py` | Modify | 2 |
| `TESTS/test_services.py` | Modify | 2 |
| `SRC/file_store.py` | Create | 3 |
| `TESTS/test_file_store.py` | Create | 3 |
| `TESTS/test_prompt_step_handler.py` | Modify | 4 |
| `skills/context-intelligence-graph-search/SKILL.md` | Modify | 5 |
| `/home/dicolomb/context-itelligence-bundle-v2/AGENTS.md` | Modify | 6 |

## Commit sequence

1. `feat: filesystem-safe node IDs (__ separator) and make_edge_id`
2. `feat: nested config layout, factory defaults to file type`
3. `feat: FileGraphStore — JSON file-based GraphStore implementation`
4. `fix: update EXPECTED_NODE_ID to new __ separator format`
5. `docs: update skill with new ID formats and multi-backend note`
6. `docs: add storage implementation parity standing rule to AGENTS.md`
7. `chore: fix lint/type issues from file store implementation` (only if needed)
