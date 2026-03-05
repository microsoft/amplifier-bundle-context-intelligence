# GraphStore Protocol & SessionHandler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Define the async GraphStore protocol, adapt the in-memory GraphState to conform to it, and implement the SessionHandler that processes session lifecycle events into graph nodes and edges.

**Architecture:** A `GraphStore` Protocol class defines the async interface for graph storage. The existing `GraphState` class is adapted to satisfy this protocol (in-memory, no persistence). `SessionHandler` uses the protocol to build Session nodes and `SUBSESSION_OF` edges from 4 lifecycle events. DuckDB backend is deferred to a future pass.

**Tech Stack:** Python 3.11+, `typing.Protocol` with `@runtime_checkable`, pytest with `asyncio_mode = "auto"`, amplifier-core for `HookResult`.

---

## Orientation

**You are working inside:**
```
amplifier-bundle-context-intelligence/
  modules/hook-context-intelligence/
    amplifier_module_hook_context_intelligence/   ← source code
      handlers/                                   ← handler classes
      protocol.py                                 ← EventHandler protocol (already exists)
      services.py                                 ← GraphState, HookConfig, HookStateService
      mount.py                                    ← mount flow state machine
    tests/                                        ← all test files live here (flat)
      conftest.py                                 ← shared fixtures
    pyproject.toml                                ← project config
  behaviors/
    context-intelligence.yaml                     ← behavior YAML with exclude_events
```

**How to run tests** (from the module root):
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/ -v
```

**Key conventions:**
- `from __future__ import annotations` is the first import in every file
- `asyncio_mode = "auto"` in pyproject.toml — no `@pytest.mark.asyncio` decorator needed
- Test classes in `test_services.py` use inline imports inside each test method
- Test classes in `test_handlers.py` use top-level imports
- The `conftest.py` provides a `services()` fixture returning `HookStateService(raw_config={})`
- All handlers follow this constructor: `def __init__(self, services: HookStateService) -> None`
- All handlers have `handled_events: frozenset[str]` as a class-level attribute
- `__call__` signature: `async def __call__(self, event: str, data: dict[str, Any]) -> HookResult`

---

## Task 1: Empty the exclude_events list in behavior YAML

**Files:**
- Modify: `amplifier-bundle-context-intelligence/behaviors/context-intelligence.yaml`

This unblocks all session events from reaching handlers. Currently the YAML excludes 10 event patterns. We want to process everything.

**Step 1: Edit the YAML**

Replace the entire `exclude_events` block with an empty list. Keep `log_level` unchanged.

The file currently looks like this (lines 11-23):
```yaml
    config:
      exclude_events:
        - "content_block:delta"
        - "thinking:delta"
        - "session-naming:*"
        - "orchestrator:rate_limit_delay"
        - "provider:request"
        - "provider:response"
        - "provider:error"
        - "provider:tool_sequence_repaired"
        - "provider:background_status"
        - "provider:incomplete_continuation"
      log_level: "${CI_LOG_LEVEL:WARNING}"
```

Change it to:
```yaml
    config:
      exclude_events: []
      log_level: "${CI_LOG_LEVEL:WARNING}"
```

**Step 2: Validate the YAML parses**

Run:
```bash
cd amplifier-bundle-context-intelligence
uv run python -c "import yaml; yaml.safe_load(open('behaviors/context-intelligence.yaml'))"
```
Expected: No output (silent success, no exception).

**Step 3: Commit**

```bash
cd amplifier-bundle-context-intelligence
git add behaviors/context-intelligence.yaml
git commit -m "config: empty exclude_events list to process all events"
```

---

## Task 2: Define the GraphStore Protocol

**Files:**
- Create: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_store.py`
- Create: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_graph_store.py`

**Step 1: Write the failing tests**

Create `tests/test_graph_store.py` with protocol conformance tests. These follow the exact pattern established in `tests/test_protocol.py` — test that the protocol is runtime-checkable, that a conforming class passes `isinstance`, and that a non-conforming class fails.

```python
"""Tests for the GraphStore protocol."""

from __future__ import annotations

from typing import Any


def test_graph_store_is_runtime_checkable():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    assert hasattr(GraphStore, "__protocol_attrs__") or hasattr(
        GraphStore, "_is_runtime_protocol"
    )


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

        async def execute_query(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

    store = FakeStore()
    assert isinstance(store, GraphStore)


def test_missing_upsert_node_fails_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class BadStore:
        async def upsert_edge(
            self, source: str, target: str, edge_type: str, properties: dict[str, Any]
        ) -> None: ...

        async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

        async def get_edge(
            self, source: str, target: str, edge_type: str
        ) -> dict[str, Any] | None: ...

        async def execute_query(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]: ...

        async def flush(self) -> None: ...

        async def close(self) -> None: ...

    store = BadStore()
    assert not isinstance(store, GraphStore)


def test_missing_flush_fails_isinstance():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore

    class BadStore:
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

        async def execute_query(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]: ...

        async def close(self) -> None: ...

    store = BadStore()
    assert not isinstance(store, GraphStore)
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_graph_store.py -v
```
Expected: FAIL — `ModuleNotFoundError` or `ImportError` because `graph_store.py` doesn't exist yet.

**Step 3: Write the GraphStore protocol**

Create `amplifier_module_hook_context_intelligence/graph_store.py`:

```python
"""GraphStore protocol — async graph storage interface for context-intelligence."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphStore(Protocol):
    """Abstract graph storage for the context-intelligence hook.

    Handlers write through this protocol; tools and analysis code read from it.
    The protocol enforces non-blocking writes as a core contract requirement.

    Non-negotiable guarantees:
    1. upsert_node/upsert_edge MUST return immediately (buffer, no I/O).
    2. get_node/get_edge MUST reflect buffered state (buffer-first reads).
    3. flush() persists buffered writes (called by lifecycle triggers, not handlers).
    4. close() MUST call flush() before releasing resources.
    5. Flush failure MUST NOT propagate to handlers.
    """

    async def upsert_node(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> None:
        """Insert or update a node. MUST return immediately (non-blocking).

        Merge semantics: new properties merge with existing. New keys are added,
        existing keys are overwritten, unmentioned keys are preserved.
        Labels are unioned with existing labels.
        """
        ...

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        """Insert or update an edge. MUST return immediately (non-blocking).

        Edge identity is (source, target, edge_type). Merge semantics same as upsert_node.
        """
        ...

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node by ID. MUST reflect buffered state."""
        ...

    async def get_edge(
        self, source: str, target: str, edge_type: str
    ) -> dict[str, Any] | None:
        """Retrieve an edge by composite key. MUST reflect buffered state."""
        ...

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a backend-specific query against the persisted store."""
        ...

    async def flush(self) -> None:
        """Persist buffered writes. Called by lifecycle triggers, never by handlers."""
        ...

    async def close(self) -> None:
        """Shut down the store. MUST call flush() first. No silent data loss."""
        ...
```

**Step 4: Run tests to verify they pass**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_graph_store.py -v
```
Expected: 4 tests PASS.

**Step 5: Run ALL existing tests to verify nothing broke**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/ -v
```
Expected: All existing tests still pass. The new file is additive — it doesn't touch anything existing.

**Step 6: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
git add amplifier_module_hook_context_intelligence/graph_store.py tests/test_graph_store.py
git commit -m "feat: define GraphStore async protocol with runtime-checkable interface"
```

---

## Task 3: Adapt GraphState to conform to GraphStore

**Files:**
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py`
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_graph_store.py`

We make all `GraphState` methods async, change `upsert_node`/`upsert_edge` to return `None` instead of the node/edge dict, and add the three new methods (`execute_query`, `flush`, `close`). **This WILL break existing tests** — that's expected and fixed in Task 4.

**Step 1: Add a conformance test to `test_graph_store.py`**

Append this test to the end of `tests/test_graph_store.py`:

```python
def test_graph_state_conforms_to_graph_store():
    from amplifier_module_hook_context_intelligence.graph_store import GraphStore
    from amplifier_module_hook_context_intelligence.services import GraphState

    graph = GraphState()
    assert isinstance(graph, GraphStore)
```

**Step 2: Run the new test to verify it fails**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_graph_store.py::test_graph_state_conforms_to_graph_store -v
```
Expected: FAIL — `GraphState` methods are not async and it's missing `execute_query`, `flush`, `close`.

**Step 3: Modify GraphState in services.py**

Replace the entire `GraphState` class in `services.py` (lines 27-72) with:

```python
class GraphState:
    """In-memory property graph state. Conforms to GraphStore protocol.

    All methods are async to satisfy the GraphStore contract.
    For this in-memory implementation:
    - upsert_node/upsert_edge write to dicts (non-blocking by nature)
    - flush() and close() are no-ops (no backing store)
    - execute_query() raises NotImplementedError (no query engine)
    """

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.current_session: str | None = None
        self.current_run: str | None = None
        self.current_step: str | None = None
        self.step_counter: int = 0
        self.pending_delegate_tool_call_id: str | None = None

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self._nodes.get(node_id)

    async def upsert_node(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> None:
        existing = self._nodes.get(node_id)
        if existing is not None:
            existing["labels"] |= labels
            existing["properties"].update(properties)
            return
        self._nodes[node_id] = {
            "id": node_id,
            "labels": set(labels),
            "properties": dict(properties),
        }

    async def get_edge(
        self, source: str, target: str, edge_type: str
    ) -> dict[str, Any] | None:
        return self._edges.get((source, target, edge_type))

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        key = (source, target, edge_type)
        existing = self._edges.get(key)
        if existing is not None:
            existing["properties"].update(properties)
            return
        self._edges[key] = {
            "source": source,
            "target": target,
            "type": edge_type,
            "properties": dict(properties),
        }

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "In-memory GraphState does not support execute_query. "
            "Use a DuckDB-backed GraphStore for query support."
        )

    async def flush(self) -> None:
        """No-op for in-memory store — nothing to persist."""

    async def close(self) -> None:
        """No-op for in-memory store — nothing to release."""
```

**Important:** Leave `HookConfig` and `HookStateService` exactly as they are. Only replace the `GraphState` class body.

**Step 4: Run the conformance test to verify it passes**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_graph_store.py -v
```
Expected: All 5 tests PASS (4 protocol tests + 1 conformance test).

**Step 5: Verify existing tests are now broken** (expected!)

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_services.py -v 2>&1 | head -40
```
Expected: Multiple failures — tests call `graph.upsert_node(...)` synchronously but it now returns a coroutine. This is expected and will be fixed in Task 4.

**Step 6: Commit (with broken tests acknowledged)**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
git add amplifier_module_hook_context_intelligence/services.py tests/test_graph_store.py
git commit -m "feat: make GraphState async and conform to GraphStore protocol

BREAKING: All GraphState methods are now async. upsert_node/upsert_edge
return None instead of the node/edge dict. Tests will be fixed in next commit."
```

---

## Task 4: Fix broken tests

**Files:**
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_services.py`
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_handlers.py`

All `GraphState` methods are now async. Tests that call them need `await`, and test methods that use `await` must be `async def`. The project has `asyncio_mode = "auto"` in `pyproject.toml`, so no `@pytest.mark.asyncio` decorator is needed.

**Step 1: Fix `test_services.py`**

Replace the entire contents of `tests/test_services.py` with:

```python
"""Tests for HookStateService, GraphState, and HookConfig."""

from __future__ import annotations


class TestHookConfig:
    def test_construction_with_empty_config(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={})
        assert config.exclude_events == set()

    def test_construction_with_exclude_events(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(
            raw_config={"exclude_events": ["content_block:delta", "thinking:delta"]}
        )
        assert config.exclude_events == {"content_block:delta", "thinking:delta"}

    def test_is_excluded_exact_match(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={"exclude_events": ["session:start"]})
        assert config.is_excluded("session:start") is True
        assert config.is_excluded("session:end") is False

    def test_is_excluded_wildcard_match(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={"exclude_events": ["session-naming:*"]})
        assert config.is_excluded("session-naming:foo") is True
        assert config.is_excluded("session-naming:bar") is True
        assert config.is_excluded("session:start") is False


class TestGraphState:
    def test_construction(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph.current_session is None
        assert graph.current_run is None
        assert graph.current_step is None
        assert graph.step_counter == 0

    async def test_upsert_node_creates_node(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_node("s1", labels={"Session"}, properties={"started": True})
        node = await graph.get_node("s1")
        assert node is not None
        assert node["labels"] == {"Session"}
        assert node["properties"]["started"] is True

    async def test_upsert_node_updates_existing(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_node("s1", labels={"Session"}, properties={"started": True})
        await graph.upsert_node("s1", labels={"Session"}, properties={"ended": True})
        node = await graph.get_node("s1")
        assert node["properties"]["started"] is True
        assert node["properties"]["ended"] is True

    async def test_upsert_node_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        result = await graph.upsert_node("s1", labels={"Session"}, properties={})
        assert result is None

    async def test_upsert_node_merges_labels(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_node("s1", labels={"Session", "Root"}, properties={})
        await graph.upsert_node("s1", labels={"Resumed"}, properties={})
        node = await graph.get_node("s1")
        assert node["labels"] == {"Session", "Root", "Resumed"}

    async def test_upsert_edge_creates_edge(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.upsert_edge("s1", "r1", edge_type="CONTAINS_RUN", properties={})
        edge = await graph.get_edge("s1", "r1", edge_type="CONTAINS_RUN")
        assert edge is not None

    async def test_upsert_edge_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        result = await graph.upsert_edge("s1", "r1", edge_type="X", properties={})
        assert result is None

    async def test_get_nonexistent_node_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert await graph.get_node("nonexistent") is None

    async def test_get_nonexistent_edge_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert await graph.get_edge("a", "b", edge_type="X") is None

    async def test_flush_is_noop(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.flush()  # should not raise

    async def test_close_is_noop(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        await graph.close()  # should not raise

    async def test_execute_query_raises_not_implemented(self):
        import pytest

        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        with pytest.raises(NotImplementedError):
            await graph.execute_query("SELECT 1")


class TestHookStateService:
    def test_construction(self):
        from amplifier_module_hook_context_intelligence.services import (
            GraphState,
            HookConfig,
            HookStateService,
        )

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphState)
        assert isinstance(service.config, HookConfig)

    async def test_graph_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        await service.graph.upsert_node("test", labels={"Test"}, properties={})
        assert await service.graph.get_node("test") is not None

    def test_config_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={"exclude_events": ["foo:bar"]})
        assert service.config.is_excluded("foo:bar") is True
```

**Step 2: Fix `test_handlers.py`**

Only one test in `test_handlers.py` is async and it already uses `await` correctly (the `test_handler_returns_hook_result` method on line 50-56). However, the `TestHookStateService.test_graph_accessible` test in `test_services.py` was the only one calling graph methods directly from tests.

Check `test_handlers.py` for any sync calls to graph methods:

The existing `test_handlers.py` does NOT call `graph.upsert_node()` or `graph.get_node()` directly — it only tests protocol conformance, event claims, and `derive_label()`. **No changes are needed to `test_handlers.py`.**

BUT — the `test_handler_returns_hook_result` test calls `await handler(event, {...})` which internally now calls `await self.services.graph.upsert_node(...)` in the SessionHandler. Since the SessionHandler `__call__` currently just returns `HookResult(action="continue")` without calling any graph methods, this test still passes as-is. It will work fine.

**Step 3: Run ALL tests to verify everything passes**

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/ -v
```
Expected: ALL tests pass. Zero failures.

**Step 4: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
git add tests/test_services.py
git commit -m "fix: update tests for async GraphState API

All graph method calls now use await. Added tests for new methods:
flush (no-op), close (no-op), execute_query (raises NotImplementedError),
upsert returns None, label merging on upsert."
```

---

## Task 5: Implement SessionHandler

**Files:**
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/session.py`
- Create: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_session_handler.py`

The SessionHandler currently has a stub `__call__` that just returns `HookResult(action="continue")`. We need to fill it in with actual graph mutations for 4 session lifecycle events.

### Step 1: Write the test file

Create `tests/test_session_handler.py`. This uses top-level imports (matching `test_handlers.py` convention) and the `services` fixture from `conftest.py`.

```python
"""Tests for SessionHandler — session lifecycle event-to-graph mapping."""

from __future__ import annotations

import pytest
from amplifier_core.models import HookResult

from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.services import HookStateService


class TestSessionStart:
    """session:start creates a Session node. Root if no parent, Subsession if parent."""

    async def test_root_session(self, services: HookStateService):
        handler = SessionHandler(services)
        result = await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
            "metadata": {"project": "test"},
        })

        assert result == HookResult(action="continue")
        node = await services.graph.get_node("abc-123")
        assert node is not None
        assert node["labels"] == {"Session", "Root"}
        assert node["properties"]["started_at"] == "2026-03-05T10:00:00Z"
        assert node["properties"]["status"] == "running"
        assert node["properties"]["metadata"] == {"project": "test"}

    async def test_root_session_no_subsession_edge(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
        })

        edge = await services.graph.get_edge("abc-123", "", "SUBSESSION_OF")
        assert edge is None

    async def test_subsession(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "child-1",
            "parent_id": "parent-1",
            "timestamp": "2026-03-05T10:01:00Z",
        })

        node = await services.graph.get_node("child-1")
        assert node is not None
        assert node["labels"] == {"Session", "Subsession"}
        assert node["properties"]["started_at"] == "2026-03-05T10:01:00Z"
        assert node["properties"]["status"] == "running"

    async def test_subsession_edge(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "child-1",
            "parent_id": "parent-1",
            "timestamp": "2026-03-05T10:01:00Z",
        })

        edge = await services.graph.get_edge("child-1", "parent-1", "SUBSESSION_OF")
        assert edge is not None

    async def test_missing_metadata_defaults_to_empty_dict(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
        })

        node = await services.graph.get_node("abc-123")
        assert node["properties"]["metadata"] == {}


class TestSessionStartParentIdEdgeCases:
    """parent_id must handle null, empty string, whitespace-only → all produce Root."""

    @pytest.mark.parametrize("parent_id", [None, "", "   ", "\t", "\n"])
    async def test_falsy_parent_id_produces_root(
        self, services: HookStateService, parent_id
    ):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": parent_id,
            "timestamp": "2026-03-05T10:00:00Z",
        })

        node = await services.graph.get_node("abc-123")
        assert "Root" in node["labels"]
        assert "Subsession" not in node["labels"]

    async def test_missing_parent_id_key_produces_root(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "abc-123",
            "timestamp": "2026-03-05T10:00:00Z",
        })

        node = await services.graph.get_node("abc-123")
        assert "Root" in node["labels"]


class TestSessionFork:
    """session:fork creates a ForkedSession node with SUBSESSION_OF edge."""

    async def test_fork_labels(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:fork", {
            "session_id": "fork-1",
            "parent": "parent-1",
            "timestamp": "2026-03-05T10:02:00Z",
            "metadata": {},
        })

        node = await services.graph.get_node("fork-1")
        assert node is not None
        assert node["labels"] == {"Session", "Subsession", "ForkedSession"}
        assert node["properties"]["started_at"] == "2026-03-05T10:02:00Z"
        assert node["properties"]["status"] == "running"

    async def test_fork_edge(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:fork", {
            "session_id": "fork-1",
            "parent": "parent-1",
            "timestamp": "2026-03-05T10:02:00Z",
        })

        edge = await services.graph.get_edge("fork-1", "parent-1", "SUBSESSION_OF")
        assert edge is not None

    async def test_fork_missing_parent_degrades_to_root(self, services: HookStateService):
        """A fork without parent is structurally invalid — degrade gracefully."""
        handler = SessionHandler(services)
        await handler("session:fork", {
            "session_id": "fork-1",
            "timestamp": "2026-03-05T10:02:00Z",
        })

        node = await services.graph.get_node("fork-1")
        assert node is not None
        # Still gets ForkedSession label, but falls back to Root since no parent
        assert "Session" in node["labels"]


class TestSessionEnd:
    """session:end merges ended_at and status onto the existing Session node."""

    async def test_end_merges_properties(self, services: HookStateService):
        handler = SessionHandler(services)
        # First, start the session
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
        })
        # Then, end it
        await handler("session:end", {
            "session_id": "abc-123",
            "timestamp": "2026-03-05T10:05:00Z",
            "status": "completed",
        })

        node = await services.graph.get_node("abc-123")
        assert node["properties"]["ended_at"] == "2026-03-05T10:05:00Z"
        assert node["properties"]["status"] == "completed"

    async def test_end_preserves_existing_labels(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
        })
        await handler("session:end", {
            "session_id": "abc-123",
            "timestamp": "2026-03-05T10:05:00Z",
            "status": "completed",
        })

        node = await services.graph.get_node("abc-123")
        assert "Session" in node["labels"]
        assert "Root" in node["labels"]

    async def test_end_without_prior_start(self, services: HookStateService):
        """session:end on unknown session creates node via upsert semantics."""
        handler = SessionHandler(services)
        await handler("session:end", {
            "session_id": "orphan-1",
            "timestamp": "2026-03-05T10:05:00Z",
            "status": "completed",
        })

        node = await services.graph.get_node("orphan-1")
        assert node is not None
        assert "Session" in node["labels"]
        assert node["properties"]["status"] == "completed"


class TestSessionResume:
    """session:resume adds Resumed label and creates an Event node with HAS_EVENT edge."""

    async def test_resume_adds_label(self, services: HookStateService):
        handler = SessionHandler(services)
        # Start session first
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
        })
        # Resume it
        await handler("session:resume", {
            "session_id": "abc-123",
            "timestamp": "2026-03-05T11:00:00Z",
        })

        node = await services.graph.get_node("abc-123")
        assert "Resumed" in node["labels"]
        # Original labels preserved
        assert "Session" in node["labels"]
        assert "Root" in node["labels"]

    async def test_resume_creates_event_node(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
        })
        await handler("session:resume", {
            "session_id": "abc-123",
            "timestamp": "2026-03-05T11:00:00Z",
        })

        event_id = "abc-123:event:session_resume:2026-03-05T11:00:00Z"
        event_node = await services.graph.get_node(event_id)
        assert event_node is not None
        assert event_node["labels"] == {"Event", "SessionResume"}

    async def test_resume_creates_has_event_edge(self, services: HookStateService):
        handler = SessionHandler(services)
        await handler("session:start", {
            "session_id": "abc-123",
            "parent_id": None,
            "timestamp": "2026-03-05T10:00:00Z",
        })
        await handler("session:resume", {
            "session_id": "abc-123",
            "timestamp": "2026-03-05T11:00:00Z",
        })

        event_id = "abc-123:event:session_resume:2026-03-05T11:00:00Z"
        edge = await services.graph.get_edge("abc-123", event_id, "HAS_EVENT")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == "2026-03-05T11:00:00Z"

    async def test_resume_without_prior_start(self, services: HookStateService):
        """Resume on unknown session still works via upsert semantics."""
        handler = SessionHandler(services)
        await handler("session:resume", {
            "session_id": "orphan-1",
            "timestamp": "2026-03-05T11:00:00Z",
        })

        node = await services.graph.get_node("orphan-1")
        assert node is not None
        assert "Session" in node["labels"]
        assert "Resumed" in node["labels"]
```

### Step 2: Run the tests to verify they fail

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_session_handler.py -v
```
Expected: Most tests FAIL — the current `SessionHandler.__call__` just returns `HookResult(action="continue")` without doing any graph work, so `get_node` returns `None`.

### Step 3: Implement SessionHandler

Replace the entire contents of `handlers/session.py` with:

```python
"""SessionHandler — owns :Session node lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService

logger = logging.getLogger(__name__)


class SessionHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "session:start",
            "session:fork",
            "session:end",
            "session:resume",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            logger.error("SessionHandler: missing session_id in %s event, skipping", event)
            return HookResult(action="continue")

        if event == "session:start":
            await self._handle_start(data, session_id)
        elif event == "session:fork":
            await self._handle_fork(data, session_id)
        elif event == "session:end":
            await self._handle_end(data, session_id)
        elif event == "session:resume":
            await self._handle_resume(data, session_id)

        return HookResult(action="continue")

    async def _handle_start(self, data: dict[str, Any], session_id: str) -> None:
        parent_id = (data.get("parent_id") or "").strip()

        if parent_id:
            labels = {"Session", "Subsession"}
        else:
            labels = {"Session", "Root"}

        await self.services.graph.upsert_node(
            node_id=session_id,
            labels=labels,
            properties={
                "started_at": data.get("timestamp"),
                "status": "running",
                "metadata": data.get("metadata", {}),
            },
        )

        if parent_id:
            await self.services.graph.upsert_edge(
                source=session_id,
                target=parent_id,
                edge_type="SUBSESSION_OF",
                properties={"occurred_at": data.get("timestamp")},
            )

    async def _handle_fork(self, data: dict[str, Any], session_id: str) -> None:
        parent_id = (data.get("parent") or "").strip()

        if parent_id:
            labels = {"Session", "Subsession", "ForkedSession"}
        else:
            # Fork without parent is structurally invalid — degrade gracefully
            logger.warning(
                "SessionHandler: session:fork for %s has no parent, treating as root",
                session_id,
            )
            labels = {"Session", "Root", "ForkedSession"}

        await self.services.graph.upsert_node(
            node_id=session_id,
            labels=labels,
            properties={
                "started_at": data.get("timestamp"),
                "status": "running",
                "metadata": data.get("metadata", {}),
            },
        )

        if parent_id:
            await self.services.graph.upsert_edge(
                source=session_id,
                target=parent_id,
                edge_type="SUBSESSION_OF",
                properties={"occurred_at": data.get("timestamp")},
            )

    async def _handle_end(self, data: dict[str, Any], session_id: str) -> None:
        await self.services.graph.upsert_node(
            node_id=session_id,
            labels={"Session"},
            properties={
                "ended_at": data.get("timestamp"),
                "status": data.get("status", "completed"),
            },
        )

    async def _handle_resume(self, data: dict[str, Any], session_id: str) -> None:
        timestamp = data.get("timestamp")

        # Add Resumed label to existing session node
        await self.services.graph.upsert_node(
            node_id=session_id,
            labels={"Session", "Resumed"},
            properties={},
        )

        # Create Event node for this resume occurrence
        event_node_id = f"{session_id}:event:session_resume:{timestamp}"
        await self.services.graph.upsert_node(
            node_id=event_node_id,
            labels={"Event", "SessionResume"},
            properties={"occurred_at": timestamp},
        )

        # Link session to event
        await self.services.graph.upsert_edge(
            source=session_id,
            target=event_node_id,
            edge_type="HAS_EVENT",
            properties={"occurred_at": timestamp},
        )
```

### Step 4: Run the SessionHandler tests

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/test_session_handler.py -v
```
Expected: ALL tests PASS.

### Step 5: Run ALL tests to verify nothing is broken

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/ -v
```
Expected: ALL tests pass — including the existing handler protocol conformance tests, service tests, mount flow tests, and the new session handler tests.

### Step 6: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
git add amplifier_module_hook_context_intelligence/handlers/session.py tests/test_session_handler.py
git commit -m "feat: implement SessionHandler with session lifecycle graph mutations

Handles session:start (Root/Subsession), session:fork (ForkedSession),
session:end (merge ended_at/status), session:resume (Resumed label + Event node).
parent_id validated for null/empty/whitespace. Edge: SUBSESSION_OF (child→parent)."
```

---

## Verification Checklist

After completing all 5 tasks, run the full test suite one final time:

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pytest tests/ -v --tb=short
```

Expected: **All tests pass.** The count should be higher than before (new tests from `test_graph_store.py`, updated `test_services.py`, and new `test_session_handler.py`).

Then verify there are no type errors:

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
uv run pyright amplifier_module_hook_context_intelligence/
```

Expected: No errors (or only pre-existing ones unrelated to our changes).

---

## What We Built

| Component | File | Purpose |
|-----------|------|---------|
| GraphStore Protocol | `graph_store.py` | Async interface with 7 methods, `@runtime_checkable` |
| GraphState (adapted) | `services.py` | In-memory implementation conforming to GraphStore |
| SessionHandler | `handlers/session.py` | Processes 4 session lifecycle events into graph nodes/edges |
| Behavior config | `context-intelligence.yaml` | Empty exclude list — all events processed |

## What We Deferred

- **DuckDB backend** — no `duckdb` dependency added, no `DuckDBGraphStore` class
- **Background flush mechanism** — `flush()` is a no-op in the in-memory store
- **Other handlers** (OrchestratorRun, Step, Tool, SystemEvent) — still stubs
- **Parquet export** — depends on DuckDB
- **`session:resume` semantics** — open question for Brian (see design doc)
