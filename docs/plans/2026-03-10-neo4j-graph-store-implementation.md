# Neo4j Graph Store Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Add a fully conformant `Neo4jGraphStore` implementing the `QueryableStore` protocol, backed by Neo4j via the official async Python driver, with factory integration and a Cypher query skill.

**Architecture:** Buffer-then-batch pattern identical to `DuckDBGraphStore` — in-memory dict buffers for instant upserts, single-transaction `UNWIND`-based Cypher flush to Neo4j, buffer-first reads with Neo4j fallback. Forest scoping via `$graph_forest_name` parameter injection (not query rewriting).

**Tech Stack:** Python 3.11+, `neo4j` 6.x async driver, Neo4j 5.x+ (Docker), pytest with `asyncio_mode = "auto"`

---

## Scope

**In scope (this plan):**
- All 14 tasks — fully conformant `QueryableStore` with Cypher support
- Factory integration
- Comprehensive tests against live Neo4j container
- Cypher query skill

**Deferred (NOT in this plan):**
- User-level tenancy design
- FTS/search index equivalent for Neo4j
- Neo4j-specific features beyond the protocol (graph algorithms via APOC, etc.)
- Neo4j Aura cloud-specific configuration

---

## Paths Reference

All paths below are relative to the repository root.

| What | Path |
|------|------|
| Source dir | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/` |
| Test dir | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/` |
| Config | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/pyproject.toml` |
| Design doc | `amplifier-bundle-context-intelligence/docs/plans/2026-03-10-neo4j-graph-store-design.md` |
| DuckDB skill | `amplifier-bundle-context-intelligence/skills/context-intelligence-graph-search/SKILL.md` |
| New store file | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/neo4j_store.py` |
| New test file | `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_neo4j_store.py` |
| New skill file | `amplifier-bundle-context-intelligence/skills/context-intelligence-neo4j-search/SKILL.md` |

For brevity in task descriptions, `SRC` = the source dir path and `TESTS` = the test dir path above.

---

## Task 1: Docker Container Setup

**Goal:** Spin up a persistent Neo4j test container and verify it responds to Cypher queries.

**Files:**
- None (infrastructure only)

**Step 1: Create the Neo4j test container**

Use the `containers` tool with these exact parameters:

```
operation: create
name: neo4j-test-env
image: neo4j:5
purpose: general
persistent: true
ports: [{host: 7475, container: 7474}, {host: 7688, container: 7687}]
mounts: [{host: "~/neo4j-test-env-data", container: "/data", mode: "rw"}]
env: {NEO4J_AUTH: "neo4j/testpassword", NEO4J_ACCEPT_LICENSE_AGREEMENT: "yes"}
```

The container uses non-standard ports to avoid colliding with any local Neo4j:
- Bolt: `7688` (standard is `7687`)
- HTTP: `7475` (standard is `7474`)

Data volume is at `~/neo4j-test-env-data` so it persists across container restarts.

**Step 2: Wait for Neo4j to become healthy**

Neo4j takes 10–20 seconds to start. Run this inside the container to poll until ready:

```bash
# Inside the container (exec):
for i in $(seq 1 30); do
  if cypher-shell -u neo4j -p testpassword "RETURN 1 AS ok" 2>/dev/null; then
    echo "Neo4j is ready"
    break
  fi
  echo "Waiting for Neo4j... ($i/30)"
  sleep 2
done
```

If `cypher-shell` is not available in the container, use the HTTP API from the host:

```bash
curl -s -u neo4j:testpassword \
  -H "Content-Type: application/json" \
  -d '{"statements":[{"statement":"RETURN 1 AS ok"}]}' \
  http://localhost:7475/db/neo4j/tx/commit
```

**Step 3: Verify connectivity with a Cypher query**

Run from the host machine (requires `curl`):

```bash
curl -s -u neo4j:testpassword \
  -H "Content-Type: application/json" \
  -d '{"statements":[{"statement":"RETURN 1 AS ok"}]}' \
  http://localhost:7475/db/neo4j/tx/commit | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert data['results'][0]['data'][0]['row'] == [1], f'Unexpected: {data}'
print('Neo4j connectivity verified')
"
```

Expected: `Neo4j connectivity verified`

**Step 4: Commit**

Nothing to commit for this task — it's infrastructure only. Note these values for the rest of the plan:

| Setting | Value |
|---------|-------|
| Container name | `neo4j-test-env` |
| Bolt URI | `neo4j://localhost:7688` |
| HTTP URI | `http://localhost:7475` |
| Username | `neo4j` |
| Password | `testpassword` |
| Database | `neo4j` (default) |

---

## Task 2: Add `neo4j` Dependency

**Goal:** Add the `neo4j` async driver as a required dependency and verify it imports.

**Files:**
- Modify: `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/pyproject.toml`

**Step 1: Add `neo4j` to dependencies in `pyproject.toml`**

Open `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/pyproject.toml` and change the `dependencies` list from:

```toml
dependencies = [
    "duckdb==1.4.3",
]
```

to:

```toml
dependencies = [
    "duckdb==1.4.3",
    "neo4j>=5.0,<7.0",
]
```

We use a range `>=5.0,<7.0` rather than an exact pin because the neo4j driver maintains backwards compatibility within major versions. The latest stable is 6.1.0. This range accepts both 5.x and 6.x.

**Step 2: Install the dependency**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv sync
```

Expected: installs successfully, no errors.

**Step 3: Verify the import works**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run python -c "import neo4j; print(f'neo4j driver version: {neo4j.__version__}')"
```

Expected: prints something like `neo4j driver version: 6.1.0`

**Step 4: Verify async driver is available**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run python -c "from neo4j import AsyncGraphDatabase; print('AsyncGraphDatabase imported OK')"
```

Expected: `AsyncGraphDatabase imported OK`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add pyproject.toml uv.lock && git commit -m "feat(neo4j): add neo4j driver as required dependency"
```

---

## Task 3: `Neo4jGraphStore` Skeleton + Protocol Conformance

**Goal:** Create `neo4j_store.py` with every `QueryableStore` method stubbed out (`NotImplementedError`), and prove via test that it satisfies the protocol's `isinstance` check.

**Files:**
- Create: `SRC/neo4j_store.py`
- Create: `TESTS/test_neo4j_store.py`

**Step 1: Write the failing test**

Create `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_neo4j_store.py`:

```python
"""Tests for Neo4jGraphStore – buffer-first reads with async Neo4j persistence."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import (
    PROMPT_NODE_ID,
    RUN_NODE_ID,
    SESSION_ID,
    SESSION_NODE_ID,
    TOOL_NODE_ID,
    reference_edges,
    reference_nodes,
)

# ---------------------------------------------------------------------------
# Constants for this test module
# ---------------------------------------------------------------------------
NEO4J_URI = "neo4j://localhost:7688"
NEO4J_AUTH = ("neo4j", "testpassword")
NEO4J_DATABASE = "neo4j"


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------
class TestProtocolConformance:
    """Neo4jGraphStore must satisfy the QueryableStore runtime protocol."""

    def test_isinstance_graph_store(self):
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="test"
        )
        assert isinstance(store, GraphStore)

    def test_isinstance_queryable_store(self):
        from amplifier_module_hook_context_intelligence.graph_store import QueryableStore
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="test"
        )
        assert isinstance(store, QueryableStore)
```

**Step 2: Run the test to verify it fails**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestProtocolConformance -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'amplifier_module_hook_context_intelligence.neo4j_store'`

**Step 3: Write the skeleton implementation**

Create `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/neo4j_store.py`:

```python
"""Neo4jGraphStore – buffer-first reads with async Neo4j persistence."""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncGraphDatabase

logger = logging.getLogger(__name__)


class Neo4jGraphStore:
    """Graph store backed by Neo4j with in-memory write buffer.

    Writes are buffered in Python dicts for instant access.  ``flush()``
    persists buffers to Neo4j in a single transaction using Cypher UNWIND.
    Reads check the buffer first, falling back to Neo4j only when the
    buffer has no entry.
    """

    def __init__(
        self,
        uri: str = "neo4j://localhost:7687",
        auth: tuple[str, str] = ("neo4j", "neo4j"),
        database: str = "neo4j",
        graph_forest_name: str = "default",
    ) -> None:
        self._uri = uri
        self._auth = auth
        self._database = database
        self._graph_forest_name = graph_forest_name
        self._driver = AsyncGraphDatabase.driver(uri, auth=auth)
        self._node_buffer: dict[str, dict[str, Any]] = {}
        self._edge_buffer: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._schema_initialized: bool = False

    @property
    def graph_forest_name(self) -> str:
        """The graph forest name for this store instance."""
        return self._graph_forest_name

    @property
    def supported_dialects(self) -> frozenset[str]:
        """The set of query dialects this backend can execute."""
        return frozenset({"cypher"})

    async def upsert_node(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> None:
        raise NotImplementedError

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        raise NotImplementedError

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def get_edge(
        self, source: str, target: str, edge_type: str
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    async def flush(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def execute_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
        graph_forest_name: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError
```

**Step 4: Run the test to verify it passes**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestProtocolConformance -v
```

Expected: both tests `PASSED`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "feat(neo4j): skeleton Neo4jGraphStore with QueryableStore conformance"
```

---

## Task 4: Constructor + `graph_forest_name` Property

**Goal:** Verify the constructor wires up the async driver correctly and `graph_forest_name` is a read-only property.

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test class)
- Modify: `SRC/neo4j_store.py` (no changes needed — already implemented in skeleton)

**Step 1: Write the tests**

Append to `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------
class TestConstructor:
    """Verify constructor wiring: driver creation and property access."""

    def test_graph_forest_name_returns_value(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="my-project"
        )
        assert store.graph_forest_name == "my-project"

    def test_graph_forest_name_defaults_to_default(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(uri=NEO4J_URI, auth=NEO4J_AUTH)
        assert store.graph_forest_name == "default"

    def test_graph_forest_name_is_readonly(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="test"
        )
        with pytest.raises(AttributeError):
            store.graph_forest_name = "nope"

    def test_driver_created_on_init(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="test"
        )
        assert store._driver is not None

    def test_buffers_empty_on_init(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="test"
        )
        assert store._node_buffer == {}
        assert store._edge_buffer == {}

    def test_schema_not_initialized_on_init(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore(
            uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="test"
        )
        assert store._schema_initialized is False
```

**Step 2: Run the tests**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestConstructor -v
```

Expected: all 6 tests `PASSED` (the skeleton already has the constructor implemented)

**Step 3: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add tests/test_neo4j_store.py && git commit -m "test(neo4j): constructor and graph_forest_name property tests"
```

---

## Task 5: Buffer Writes (`upsert_node`, `upsert_edge`)

**Goal:** Implement in-memory buffer writes with merge semantics. These are pure dict operations — no Neo4j interaction.

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test class)
- Modify: `SRC/neo4j_store.py` (implement `upsert_node`, `upsert_edge`)

**Step 1: Write the failing tests**

Append to `tests/test_neo4j_store.py`. First, add a `store` fixture at the top of the file, right after the constants block:

```python
# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def store():
    """Fresh Neo4jGraphStore for test isolation (buffer-only tests)."""
    from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

    return Neo4jGraphStore(
        uri=NEO4J_URI, auth=NEO4J_AUTH, graph_forest_name="test"
    )
```

Then append the test class:

```python
# ---------------------------------------------------------------------------
# TestBufferWrites
# ---------------------------------------------------------------------------
class TestBufferWrites:
    """upsert_node / upsert_edge write to in-memory buffers only."""

    async def test_upsert_node_writes_to_buffer(self, store):
        await store.upsert_node("n1", {"Label"}, {"key": "val"})
        assert "n1" in store._node_buffer

    async def test_upsert_node_buffer_shape(self, store):
        await store.upsert_node("n1", {"Session", "Root"}, {"status": "running"})
        entry = store._node_buffer["n1"]
        assert entry["id"] == "n1"
        assert entry["labels"] == {"Session", "Root"}
        assert entry["properties"] == {"status": "running"}

    async def test_upsert_edge_writes_to_buffer(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        assert ("a", "b", "KNOWS") in store._edge_buffer

    async def test_upsert_edge_buffer_shape(self, store):
        await store.upsert_edge("a", "b", "HAS_RUN", {"seq": 1})
        entry = store._edge_buffer[("a", "b", "HAS_RUN")]
        assert entry["source"] == "a"
        assert entry["target"] == "b"
        assert entry["type"] == "HAS_RUN"
        assert entry["properties"] == {"seq": 1}

    async def test_upsert_node_merges_labels(self, store):
        await store.upsert_node("n1", {"A"}, {})
        await store.upsert_node("n1", {"B"}, {})
        assert store._node_buffer["n1"]["labels"] == {"A", "B"}

    async def test_upsert_node_merges_properties(self, store):
        await store.upsert_node("n1", set(), {"a": 1})
        await store.upsert_node("n1", set(), {"b": 2})
        props = store._node_buffer["n1"]["properties"]
        assert props == {"a": 1, "b": 2}

    async def test_upsert_node_last_write_wins(self, store):
        await store.upsert_node("n1", set(), {"key": "old"})
        await store.upsert_node("n1", set(), {"key": "new"})
        assert store._node_buffer["n1"]["properties"]["key"] == "new"

    async def test_upsert_edge_merges_properties(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"x": 1})
        await store.upsert_edge("a", "b", "KNOWS", {"y": 2})
        props = store._edge_buffer[("a", "b", "KNOWS")]["properties"]
        assert props == {"x": 1, "y": 2}
```

**Step 2: Run tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestBufferWrites -v
```

Expected: all `FAILED` with `NotImplementedError`

**Step 3: Implement `upsert_node` and `upsert_edge`**

In `SRC/neo4j_store.py`, replace the two stub methods:

```python
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
```

**Step 4: Run tests to verify they pass**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestBufferWrites -v
```

Expected: all 8 tests `PASSED`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "feat(neo4j): buffer writes with merge semantics"
```

---

## Task 6: Buffer-First Reads (`get_node`, `get_edge`)

**Goal:** Implement buffer-first reads with Neo4j fallback. Check the in-memory buffer first; if not found, run a Cypher `MATCH` query against Neo4j.

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test class)
- Modify: `SRC/neo4j_store.py` (implement `get_node`, `get_edge`)

**Step 1: Add a Neo4j cleanup fixture**

Add this fixture to `tests/test_neo4j_store.py` right after the existing `store` fixture. This fixture cleans Neo4j data before each test that uses it, and creates a store connected to the live container:

```python
@pytest.fixture
async def neo4j_store():
    """Neo4jGraphStore connected to the live test container, with data cleanup."""
    from neo4j import AsyncGraphDatabase

    from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

    # Clean all data before the test
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    async with driver.session(database=NEO4J_DATABASE) as session:
        await session.run("MATCH (n) DETACH DELETE n")
    await driver.close()

    store = Neo4jGraphStore(
        uri=NEO4J_URI,
        auth=NEO4J_AUTH,
        database=NEO4J_DATABASE,
        graph_forest_name="test",
    )
    yield store
    await store.close()
```

**Step 2: Write the failing tests**

Append to `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestBufferFirstReads
# ---------------------------------------------------------------------------
class TestBufferFirstReads:
    """get_node / get_edge must reflect buffered state."""

    async def test_get_node_returns_buffered_data(self, store):
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "session_id": SESSION_ID,
                "iteration": 0,
                "prompt_text": "Hello, world!",
            },
        )
        node = await store.get_node(PROMPT_NODE_ID)
        assert node is not None
        assert node["id"] == PROMPT_NODE_ID
        assert node["labels"] == {"Step", "PromptStep"}
        assert node["properties"]["prompt_text"] == "Hello, world!"

    async def test_get_edge_returns_buffered_data(self, store):
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

    async def test_buffer_wins_over_stale_neo4j(self, neo4j_store):
        """Upsert after flush: buffer value should override stale Neo4j data."""
        await neo4j_store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await neo4j_store.flush()
        # Now upsert a newer version into buffer
        await neo4j_store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "completed"},
        )
        node = await neo4j_store.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["properties"]["status"] == "completed"
```

**Step 3: Run tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestBufferFirstReads -v
```

Expected: `FAILED` with `NotImplementedError`

**Step 4: Implement `get_node` and `get_edge`**

In `SRC/neo4j_store.py`, replace the two stub methods:

```python
    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        buffered = self._node_buffer.get(node_id)
        if buffered is not None:
            return buffered

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $node_id}) RETURN n",
                node_id=node_id,
            )
            record = await result.single()
            if record is None:
                return None
            neo4j_node = record["n"]
            # Reconstruct the buffer-compatible dict shape
            props = dict(neo4j_node)
            # Remove internal keys that we store as top-level fields
            props.pop("node_id", None)
            props.pop("graph_forest_name", None)
            return {
                "id": node_id,
                "labels": set(neo4j_node.labels),
                "properties": props,
            }

    async def get_edge(
        self, source: str, target: str, edge_type: str
    ) -> dict[str, Any] | None:
        key = (source, target, edge_type)
        buffered = self._edge_buffer.get(key)
        if buffered is not None:
            return buffered

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                "MATCH (s {node_id: $source})-[r]->(t {node_id: $target}) "
                "WHERE type(r) = $edge_type "
                "RETURN r",
                source=source,
                target=target,
                edge_type=edge_type,
            )
            record = await result.single()
            if record is None:
                return None
            rel = record["r"]
            props = dict(rel)
            props.pop("graph_forest_name", None)
            return {
                "source": source,
                "target": target,
                "type": edge_type,
                "properties": props,
            }
```

**Step 5: Run tests to verify they pass**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestBufferFirstReads -v
```

Expected: all 5 tests `PASSED`

Note: The `test_buffer_wins_over_stale_neo4j` test depends on `flush()` (Task 7). If running tasks strictly in order, this test will fail with `NotImplementedError` on `flush()`. That's expected — it will pass after Task 7. You can skip it for now and re-run after Task 7, or implement Tasks 6 and 7 together as a pair.

**Step 6: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "feat(neo4j): buffer-first reads with Neo4j fallback"
```

---

## Task 7: `flush()` — Batch Write to Neo4j

**Goal:** Implement `flush()` using UNWIND-based Cypher in a single async transaction. On failure, restore buffers (swallow exception).

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test class)
- Modify: `SRC/neo4j_store.py` (implement `flush`)

**Step 1: Write the failing tests**

Append to `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestFlush
# ---------------------------------------------------------------------------
class TestFlush:
    """flush() persists buffers to Neo4j and clears them."""

    async def test_flush_writes_nodes_to_neo4j(self, neo4j_store):
        await neo4j_store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await neo4j_store.flush()
        # Verify via raw Cypher
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n.node_id AS node_id",
                nid=SESSION_NODE_ID,
            )
            record = await result.single()
        assert record is not None
        assert record["node_id"] == SESSION_NODE_ID

    async def test_flush_writes_edges_to_neo4j(self, neo4j_store):
        # Upsert source and target nodes first (edges need endpoints)
        await neo4j_store.upsert_node(SESSION_NODE_ID, {"Session"}, {})
        await neo4j_store.upsert_node(RUN_NODE_ID, {"OrchestratorRun"}, {})
        await neo4j_store.upsert_edge(
            SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1}
        )
        await neo4j_store.flush()
        # Verify via raw Cypher
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run(
                "MATCH (s {node_id: $src})-[r:HAS_RUN]->(t {node_id: $tgt}) "
                "RETURN r.seq AS seq",
                src=SESSION_NODE_ID,
                tgt=RUN_NODE_ID,
            )
            record = await result.single()
        assert record is not None
        assert record["seq"] == 1

    async def test_flush_clears_node_buffer(self, neo4j_store):
        await neo4j_store.upsert_node(SESSION_NODE_ID, {"Session"}, {})
        await neo4j_store.flush()
        assert len(neo4j_store._node_buffer) == 0

    async def test_flush_clears_edge_buffer(self, neo4j_store):
        await neo4j_store.upsert_node("a", {"Node"}, {})
        await neo4j_store.upsert_node("b", {"Node"}, {})
        await neo4j_store.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        await neo4j_store.flush()
        assert len(neo4j_store._edge_buffer) == 0

    async def test_get_node_from_neo4j_after_flush(self, neo4j_store):
        await neo4j_store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await neo4j_store.flush()
        # Buffer is empty; read must come from Neo4j
        assert len(neo4j_store._node_buffer) == 0
        node = await neo4j_store.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["id"] == SESSION_NODE_ID
        assert "Session" in node["labels"]
        assert "Root" in node["labels"]
        assert node["properties"]["session_id"] == SESSION_ID

    async def test_get_edge_from_neo4j_after_flush(self, neo4j_store):
        await neo4j_store.upsert_node(SESSION_NODE_ID, {"Session"}, {})
        await neo4j_store.upsert_node(RUN_NODE_ID, {"OrchestratorRun"}, {})
        await neo4j_store.upsert_edge(
            SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1}
        )
        await neo4j_store.flush()
        assert len(neo4j_store._edge_buffer) == 0
        edge = await neo4j_store.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"]["seq"] == 1

    async def test_flush_empty_buffer_is_noop(self, neo4j_store):
        await neo4j_store.flush()
        await neo4j_store.flush()

    async def test_flush_restores_buffers_on_failure(self, store):
        """If Neo4j write fails, buffers should be restored for retry."""
        await store.upsert_node("n1", {"Label"}, {"key": "val"})

        # Sabotage the driver to simulate failure
        original_driver = store._driver
        store._driver = None  # type: ignore[assignment]

        await store.flush()  # Should not raise (swallowed)

        store._driver = original_driver  # restore
        assert "n1" in store._node_buffer
```

**Step 2: Run tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestFlush -v
```

Expected: `FAILED` with `NotImplementedError`

**Step 3: Implement `flush()`**

In `SRC/neo4j_store.py`, replace the `flush` stub with:

```python
    async def flush(self) -> None:
        # Phase 1: Snapshot and optimistically clear
        forest = self._graph_forest_name
        nodes = self._node_buffer
        edges = self._edge_buffer
        self._node_buffer = {}
        self._edge_buffer = {}

        # Phase 2: Early exit
        if not nodes and not edges:
            return

        # Phase 3: Write with rollback + restore on failure
        try:
            await self._ensure_schema()

            async with self._driver.session(database=self._database) as session:
                async with await session.begin_transaction() as tx:
                    # Batch upsert nodes via UNWIND
                    if nodes:
                        node_records = []
                        for node in nodes.values():
                            node_records.append({
                                "node_id": node["id"],
                                "labels": list(node["labels"]),
                                "properties": node["properties"],
                                "graph_forest_name": forest,
                            })
                        await tx.run(
                            "UNWIND $nodes AS n "
                            "MERGE (node {node_id: n.node_id}) "
                            "SET node += n.properties, "
                            "    node.node_id = n.node_id, "
                            "    node.graph_forest_name = n.graph_forest_name",
                            nodes=node_records,
                        )
                        # Apply labels in a second pass (UNWIND + dynamic labels
                        # requires APOC or per-node calls; we do per-distinct-labelset)
                        label_groups: dict[frozenset[str], list[str]] = {}
                        for node in nodes.values():
                            key = frozenset(node["labels"])
                            label_groups.setdefault(key, []).append(node["id"])
                        for label_set, node_ids in label_groups.items():
                            if not label_set:
                                continue
                            label_clause = ":".join(
                                f"`{lbl}`" for lbl in sorted(label_set)
                            )
                            await tx.run(
                                f"UNWIND $ids AS nid "
                                f"MATCH (node {{node_id: nid}}) "
                                f"SET node:{label_clause}",
                                ids=node_ids,
                            )

                    # Batch upsert edges via UNWIND
                    if edges:
                        # Group edges by type (relationship type must be literal)
                        edge_type_groups: dict[str, list[dict[str, Any]]] = {}
                        for edge in edges.values():
                            edge_type_groups.setdefault(edge["type"], []).append({
                                "source": edge["source"],
                                "target": edge["target"],
                                "properties": edge["properties"],
                                "graph_forest_name": forest,
                            })
                        for edge_type, edge_records in edge_type_groups.items():
                            await tx.run(
                                f"UNWIND $edges AS e "
                                f"MATCH (s {{node_id: e.source}}) "
                                f"MATCH (t {{node_id: e.target}}) "
                                f"MERGE (s)-[r:`{edge_type}`]->(t) "
                                f"SET r += e.properties, "
                                f"    r.graph_forest_name = e.graph_forest_name",
                                edges=edge_records,
                            )

                    await tx.commit()
        except Exception:
            # Restore buffers for retry
            self._node_buffer.update(nodes)
            self._edge_buffer.update(edges)
            logger.warning("flush failed; buffers restored for retry", exc_info=True)
```

Also add the `_ensure_schema` helper method (used by `flush`, fully implemented in Task 8). For now, add a minimal stub so flush works:

```python
    async def _ensure_schema(self) -> None:
        """Ensure indexes and constraints exist (idempotent). Full impl in Task 8."""
        pass
```

**Step 4: Run tests to verify they pass**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestFlush -v
```

Expected: all 8 tests `PASSED`

Also re-run the `test_buffer_wins_over_stale_neo4j` test from Task 6 that depends on flush:

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestBufferFirstReads::test_buffer_wins_over_stale_neo4j -v
```

Expected: `PASSED`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "feat(neo4j): flush with UNWIND batch write and buffer restore on failure"
```

---

## Task 8: Schema Initialization

**Goal:** Implement idempotent index/constraint creation on first flush via the `_schema_initialized` flag.

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test class)
- Modify: `SRC/neo4j_store.py` (implement `_ensure_schema`)

**Step 1: Write the failing tests**

Append to `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestSchemaInitialization
# ---------------------------------------------------------------------------
class TestSchemaInitialization:
    """Schema indexes and constraints are created idempotently on first flush."""

    async def test_schema_initialized_after_first_flush(self, neo4j_store):
        await neo4j_store.upsert_node("n1", {"Session"}, {"key": "val"})
        await neo4j_store.flush()
        assert neo4j_store._schema_initialized is True

    async def test_schema_flag_prevents_rerun(self, neo4j_store):
        await neo4j_store.upsert_node("n1", {"Session"}, {"key": "val"})
        await neo4j_store.flush()
        # Second flush should not fail even with schema already existing
        await neo4j_store.upsert_node("n2", {"Session"}, {"key": "val2"})
        await neo4j_store.flush()
        assert neo4j_store._schema_initialized is True

    async def test_node_id_index_exists_after_flush(self, neo4j_store):
        await neo4j_store.upsert_node("n1", {"Session"}, {})
        await neo4j_store.flush()
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]
        # Look for an index on node_id
        index_names = [r["name"] for r in records]
        assert any("node_id" in str(r["properties"]) for r in records)

    async def test_forest_index_exists_after_flush(self, neo4j_store):
        await neo4j_store.upsert_node("n1", {"Session"}, {})
        await neo4j_store.flush()
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run("SHOW INDEXES YIELD name, labelsOrTypes, properties")
            records = [record async for record in result]
        assert any("graph_forest_name" in str(r["properties"]) for r in records)
```

**Step 2: Run tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestSchemaInitialization -v
```

Expected: `FAILED` — `_schema_initialized` is never set to `True` (the stub `_ensure_schema` does nothing)

**Step 3: Implement `_ensure_schema`**

In `SRC/neo4j_store.py`, replace the stub `_ensure_schema` method:

```python
    async def _ensure_schema(self) -> None:
        """Create indexes and constraints if not already done (idempotent)."""
        if self._schema_initialized:
            return

        async with self._driver.session(database=self._database) as session:
            # Index on node_id for fast lookups
            await session.run(
                "CREATE INDEX idx_node_id IF NOT EXISTS FOR (n:Node) ON (n.node_id)"
            )
            # Index on graph_forest_name for forest-scoped queries
            await session.run(
                "CREATE INDEX idx_forest IF NOT EXISTS FOR (n:Node) ON (n.graph_forest_name)"
            )
            # Range index on node_id across all nodes (label-free)
            await session.run(
                "CREATE INDEX idx_node_id_any IF NOT EXISTS "
                "FOR (n:Session) ON (n.node_id)"
            )

        self._schema_initialized = True
```

**Step 4: Run tests to verify they pass**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestSchemaInitialization -v
```

Expected: all 4 tests `PASSED`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "feat(neo4j): idempotent schema initialization on first flush"
```

---

## Task 9: `close()`

**Goal:** Implement `close()` — flush remaining buffers, then close the async driver.

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test classes)
- Modify: `SRC/neo4j_store.py` (implement `close`)

**Step 1: Write the failing tests**

Append to `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() must flush before closing the driver."""

    async def test_close_flushes_before_closing(self):
        from neo4j import AsyncGraphDatabase

        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        # Clean data
        driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        async with driver.session(database=NEO4J_DATABASE) as session:
            await session.run("MATCH (n) DETACH DELETE n")
        await driver.close()

        store = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="test",
        )
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.close()

        # Verify data was persisted by opening a fresh driver
        driver2 = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        async with driver2.session(database=NEO4J_DATABASE) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n.node_id AS node_id",
                nid=SESSION_NODE_ID,
            )
            record = await result.single()
        await driver2.close()
        assert record is not None
        assert record["node_id"] == SESSION_NODE_ID


# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------
class TestPersistence:
    """Data must survive close and reopen."""

    async def test_data_survives_close_and_reopen(self):
        from neo4j import AsyncGraphDatabase

        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        # Clean data
        driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        async with driver.session(database=NEO4J_DATABASE) as session:
            await session.run("MATCH (n) DETACH DELETE n")
        await driver.close()

        # Write and close
        store = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="test",
        )
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        # Need target node to exist for edge
        await store.upsert_node(RUN_NODE_ID, {"OrchestratorRun"}, {})
        await store.close()

        # Reopen and read
        store2 = Neo4jGraphStore(
            uri=NEO4J_URI,
            auth=NEO4J_AUTH,
            database=NEO4J_DATABASE,
            graph_forest_name="test",
        )
        node = await store2.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["id"] == SESSION_NODE_ID
        assert "Session" in node["labels"]
        assert "Root" in node["labels"]

        edge = await store2.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"]["seq"] == 1
        await store2.close()
```

**Step 2: Run tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestClose tests/test_neo4j_store.py::TestPersistence -v
```

Expected: `FAILED` with `NotImplementedError`

**Step 3: Implement `close()`**

In `SRC/neo4j_store.py`, replace the `close` stub:

```python
    async def close(self) -> None:
        await self.flush()
        await self._driver.close()
```

**Step 4: Run tests to verify they pass**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestClose tests/test_neo4j_store.py::TestPersistence -v
```

Expected: all tests `PASSED`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "feat(neo4j): close() flushes then closes driver"
```

---

## Task 10: `execute_query` + `supported_dialects`

**Goal:** Implement raw Cypher query execution with dialect validation and `$graph_forest_name` parameter injection.

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test class)
- Modify: `SRC/neo4j_store.py` (implement `execute_query`)

**Step 1: Write the failing tests**

Append to `tests/test_neo4j_store.py`. First add a helper fixture that seeds the reference graph into Neo4j:

```python
@pytest.fixture
async def seeded_neo4j_store(neo4j_store):
    """Neo4j store with the reference graph flushed."""
    for node_id, labels, props in reference_nodes():
        await neo4j_store.upsert_node(node_id, labels, props)
    for src, tgt, etype, props in reference_edges():
        await neo4j_store.upsert_edge(src, tgt, etype, props)
    await neo4j_store.flush()
    return neo4j_store
```

Then append:

```python
# ---------------------------------------------------------------------------
# TestExecuteQuery
# ---------------------------------------------------------------------------
class TestExecuteQuery:
    """execute_query returns list of dicts and supports dialect validation."""

    def test_supported_dialects_returns_frozenset(self, store):
        dialects = store.supported_dialects
        assert isinstance(dialects, frozenset)
        assert "cypher" in dialects

    async def test_execute_query_returns_list_of_dicts(self, seeded_neo4j_store):
        rows = await seeded_neo4j_store.execute_query(
            "MATCH (n) RETURN n.node_id AS node_id LIMIT 10"
        )
        assert isinstance(rows, list)
        assert len(rows) >= 1
        assert "node_id" in rows[0]

    async def test_execute_query_with_explicit_cypher_dialect(self, seeded_neo4j_store):
        rows = await seeded_neo4j_store.execute_query(
            "MATCH (n) RETURN n.node_id AS node_id",
            dialect="cypher",
        )
        assert isinstance(rows, list)
        assert len(rows) >= 1

    async def test_execute_query_with_none_dialect_uses_default(self, seeded_neo4j_store):
        rows = await seeded_neo4j_store.execute_query(
            "MATCH (n) RETURN n.node_id AS node_id",
            dialect=None,
        )
        assert isinstance(rows, list)
        assert len(rows) >= 1

    async def test_execute_query_with_params(self, seeded_neo4j_store):
        rows = await seeded_neo4j_store.execute_query(
            "MATCH (n {node_id: $node_id}) RETURN n.node_id AS node_id",
            params={"node_id": SESSION_NODE_ID},
        )
        assert len(rows) == 1
        assert rows[0]["node_id"] == SESSION_NODE_ID

    async def test_execute_query_with_invalid_dialect_raises(self, store):
        with pytest.raises(ValueError, match="Unsupported dialect"):
            await store.execute_query("RETURN 1", dialect="sql")

    async def test_execute_query_injects_graph_forest_name_param(self, seeded_neo4j_store):
        """$graph_forest_name should be available in the query params."""
        rows = await seeded_neo4j_store.execute_query(
            "MATCH (n) WHERE n.graph_forest_name = $graph_forest_name "
            "RETURN n.node_id AS node_id",
        )
        assert len(rows) >= 1
        # All returned nodes belong to the store's forest
        for row in rows:
            assert row["node_id"] is not None
```

**Step 2: Run tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestExecuteQuery -v
```

Expected: `FAILED` with `NotImplementedError`

**Step 3: Implement `execute_query`**

In `SRC/neo4j_store.py`, replace the `execute_query` stub:

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

        # Resolve forest: caller override > instance default
        forest = self._graph_forest_name if graph_forest_name is None else graph_forest_name

        # Build params with forest injection
        p = dict(params) if params is not None else {}
        if forest != "*":
            p["graph_forest_name"] = forest

        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, p)
            records = [record async for record in result]
            return [dict(record) for record in records]
```

**Step 4: Run tests to verify they pass**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestExecuteQuery -v
```

Expected: all 7 tests `PASSED`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "feat(neo4j): execute_query with dialect validation and forest param injection"
```

---

## Task 11: Forest Scoping

**Goal:** Verify that `graph_forest_name` is stamped on all writes during flush, and that two stores with different forest names writing to the same Neo4j database have isolated data.

**Files:**
- Modify: `TESTS/test_neo4j_store.py` (add test classes)

**Step 1: Write the tests**

Append to `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestForestWrites
# ---------------------------------------------------------------------------
class TestForestWrites:
    """flush() must stamp graph_forest_name on all nodes and edges."""

    async def test_flush_stamps_forest_on_nodes(self, neo4j_store):
        await neo4j_store.upsert_node("n1", {"Session"}, {"key": "val"})
        await neo4j_store.flush()
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run(
                "MATCH (n {node_id: 'n1'}) RETURN n.graph_forest_name AS forest"
            )
            record = await result.single()
        assert record is not None
        assert record["forest"] == "test"

    async def test_flush_stamps_forest_on_edges(self, neo4j_store):
        await neo4j_store.upsert_node("a", {"Node"}, {})
        await neo4j_store.upsert_node("b", {"Node"}, {})
        await neo4j_store.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        await neo4j_store.flush()
        async with neo4j_store._driver.session(database=NEO4J_DATABASE) as session:
            result = await session.run(
                "MATCH ()-[r:KNOWS]->() RETURN r.graph_forest_name AS forest"
            )
            record = await result.single()
        assert record is not None
        assert record["forest"] == "test"


# ---------------------------------------------------------------------------
# TestForestIsolation
# ---------------------------------------------------------------------------
class TestForestIsolation:
    """Two stores with different forest names must have isolated data."""

    async def test_data_isolated_between_forests(self):
        from neo4j import AsyncGraphDatabase

        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        # Clean data
        driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        async with driver.session(database=NEO4J_DATABASE) as session:
            await session.run("MATCH (n) DETACH DELETE n")
        await driver.close()

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

        # Write n1 in forest-a
        await store_a.upsert_node("n1", {"Session"}, {"key": "from-a"})
        await store_a.flush()

        # Write n2 in forest-b
        await store_b.upsert_node("n2", {"Session"}, {"key": "from-b"})
        await store_b.flush()

        # Forest-a query should only see n1
        rows_a = await store_a.execute_query(
            "MATCH (n) WHERE n.graph_forest_name = $graph_forest_name "
            "RETURN n.node_id AS node_id ORDER BY n.node_id"
        )
        node_ids_a = [r["node_id"] for r in rows_a]
        assert node_ids_a == ["n1"]

        # Forest-b query should only see n2
        rows_b = await store_b.execute_query(
            "MATCH (n) WHERE n.graph_forest_name = $graph_forest_name "
            "RETURN n.node_id AS node_id ORDER BY n.node_id"
        )
        node_ids_b = [r["node_id"] for r in rows_b]
        assert node_ids_b == ["n2"]

        await store_a.close()
        await store_b.close()

    async def test_star_returns_all_forests(self):
        from neo4j import AsyncGraphDatabase

        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        # Clean data
        driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        async with driver.session(database=NEO4J_DATABASE) as session:
            await session.run("MATCH (n) DETACH DELETE n")
        await driver.close()

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

        await store_a.upsert_node("n1", {"Session"}, {"key": "from-a"})
        await store_a.flush()
        await store_b.upsert_node("n2", {"Session"}, {"key": "from-b"})
        await store_b.flush()

        # Wildcard query from store_a should see both
        rows = await store_a.execute_query(
            "MATCH (n) RETURN n.node_id AS node_id ORDER BY n.node_id",
            graph_forest_name="*",
        )
        node_ids = [r["node_id"] for r in rows]
        assert "n1" in node_ids
        assert "n2" in node_ids

        await store_a.close()
        await store_b.close()
```

**Step 2: Run the tests**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestForestWrites tests/test_neo4j_store.py::TestForestIsolation -v
```

Expected: all tests `PASSED` (forest stamping is already implemented in flush, and parameter injection in execute_query)

**Step 3: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add tests/test_neo4j_store.py && git commit -m "test(neo4j): forest scoping and isolation tests"
```

---

## Task 12: Factory Integration

**Goal:** Add `"neo4j"` as a recognized store type in `store_factory.py`. Fix the existing test that uses `"neo4j"` as the unknown type.

**Files:**
- Modify: `SRC/store_factory.py`
- Modify: `TESTS/test_store_factory.py`

**Step 1: Fix the breaking test**

In `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/tests/test_store_factory.py`, the test at line 35–37 currently reads:

```python
    def test_raises_for_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown graph_store type: neo4j"):
            create_graph_store({"type": "neo4j"})
```

Change it to:

```python
    def test_raises_for_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown graph_store type: bogus"):
            create_graph_store({"type": "bogus"})
```

**Step 2: Write the new factory tests**

Append to `tests/test_store_factory.py` inside the `TestCreateGraphStore` class:

```python
    def test_returns_neo4j_store_for_explicit_type(self):
        from amplifier_module_hook_context_intelligence.neo4j_store import Neo4jGraphStore

        store = create_graph_store({
            "type": "neo4j",
            "config": {
                "uri": "neo4j://localhost:7688",
                "auth": ("neo4j", "testpassword"),
            },
        })
        assert isinstance(store, Neo4jGraphStore)

    def test_neo4j_passes_config_through(self):
        store = create_graph_store({
            "type": "neo4j",
            "config": {
                "uri": "neo4j://localhost:7688",
                "auth": ("neo4j", "testpassword"),
                "database": "custom-db",
            },
        })
        assert store._database == "custom-db"  # type: ignore[attr-defined]

    def test_neo4j_graph_forest_name_passed_through(self):
        store = create_graph_store({
            "type": "neo4j",
            "graph_forest_name": "my-project",
            "config": {
                "uri": "neo4j://localhost:7688",
                "auth": ("neo4j", "testpassword"),
            },
        })
        assert store.graph_forest_name == "my-project"

    def test_neo4j_conforms_to_queryable_store_protocol(self):
        from amplifier_module_hook_context_intelligence.graph_store import QueryableStore

        store = create_graph_store({
            "type": "neo4j",
            "config": {
                "uri": "neo4j://localhost:7688",
                "auth": ("neo4j", "testpassword"),
            },
        })
        assert isinstance(store, QueryableStore)
```

**Step 3: Run the new tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py::TestCreateGraphStore::test_returns_neo4j_store_for_explicit_type -v
```

Expected: `FAILED` with `ValueError: Unknown graph_store type: neo4j`

**Step 4: Add the `"neo4j"` case to `store_factory.py`**

In `amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/store_factory.py`, add the following block right before the final `raise ValueError(...)` line:

```python
    if store_type == "neo4j":
        from .neo4j_store import Neo4jGraphStore

        uri = impl_config["uri"]
        auth = impl_config["auth"]
        database = impl_config.get("database", "neo4j")
        return Neo4jGraphStore(
            uri=uri, auth=auth, database=database, graph_forest_name=forest_name
        )
```

The full end of the function should now look like:

```python
    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore

        connection = impl_config.get("connection", ":memory:")
        return DuckDBGraphStore(connection=connection, graph_forest_name=forest_name)

    if store_type == "neo4j":
        from .neo4j_store import Neo4jGraphStore

        uri = impl_config["uri"]
        auth = impl_config["auth"]
        database = impl_config.get("database", "neo4j")
        return Neo4jGraphStore(
            uri=uri, auth=auth, database=database, graph_forest_name=forest_name
        )

    raise ValueError(f"Unknown graph_store type: {store_type}")
```

**Step 5: Run all factory tests**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_store_factory.py -v
```

Expected: all tests `PASSED` (including the updated `test_raises_for_unknown_type`)

**Step 6: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/store_factory.py tests/test_store_factory.py && git commit -m "feat(neo4j): factory integration with 'neo4j' store type"
```

---

## Task 13: Standing Rule Docstring

**Goal:** Add a standing rule to the `neo4j_store.py` module docstring, referencing the Cypher skill. Test it the same way DuckDB's `TestStandingRuleDocstring` does.

**Files:**
- Modify: `SRC/neo4j_store.py` (update module docstring)
- Modify: `TESTS/test_neo4j_store.py` (add test class)

**Step 1: Write the failing tests**

Append to `tests/test_neo4j_store.py`:

```python
# ---------------------------------------------------------------------------
# TestStandingRuleDocstring
# ---------------------------------------------------------------------------
class TestStandingRuleDocstring:
    """Module docstring must contain the standing rule for skill synchronization."""

    def test_docstring_contains_standing_rule_section(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None, "Module docstring must not be None"
        assert "STANDING RULE" in doc

    def test_docstring_references_skill_path(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None
        assert "skills/context-intelligence-neo4j-search/SKILL.md" in doc

    def test_docstring_lists_schema_triggers(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None
        required_triggers = [
            "node labels",
            "relationship types",
            "properties",
            "graph_forest_name",
            "indexes",
        ]
        for trigger in required_triggers:
            assert trigger in doc, f"Docstring missing trigger: {trigger!r}"

    def test_docstring_preserves_original_description(self):
        import amplifier_module_hook_context_intelligence.neo4j_store as mod

        doc = mod.__doc__
        assert doc is not None
        assert "Neo4jGraphStore" in doc
        assert "buffer-first reads with async Neo4j persistence" in doc
```

**Step 2: Run tests to verify they fail**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestStandingRuleDocstring -v
```

Expected: `FAILED` — the current docstring doesn't contain `STANDING RULE`

**Step 3: Update the module docstring**

In `SRC/neo4j_store.py`, replace the module docstring (the very first string in the file) with:

```python
"""Neo4jGraphStore – buffer-first reads with async Neo4j persistence.

STANDING RULE — Skill Synchronization
--------------------------------------
Any change to the Neo4j schema (node labels, relationship types, properties,
graph_forest_name scoping, indexes, constraints, new label types, new edge types,
new property keys on nodes or edges)
MUST be accompanied by an update to the Cypher skill at
``skills/context-intelligence-neo4j-search/SKILL.md``.

The skill is the contract between this storage layer and agents that generate
Cypher queries.  Stale skill = broken agent query generation.
"""
```

**Step 4: Run tests to verify they pass**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestStandingRuleDocstring -v
```

Expected: all 4 tests `PASSED`

**Step 5: Commit**

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/neo4j_store.py tests/test_neo4j_store.py && git commit -m "docs(neo4j): standing rule docstring referencing Cypher skill"
```

---

## Task 14: Cypher Query Skill

**Goal:** Create a comprehensive Cypher query skill documenting the Neo4j schema, query patterns, and forest scoping. Mirror the structure and completeness of the existing DuckDB skill at `skills/context-intelligence-graph-search/SKILL.md`.

**Files:**
- Create: `amplifier-bundle-context-intelligence/skills/context-intelligence-neo4j-search/SKILL.md`

**Step 1: Create the skill file**

Create `amplifier-bundle-context-intelligence/skills/context-intelligence-neo4j-search/SKILL.md`:

```markdown
---
name: context-intelligence-neo4j-search
description: Cypher query patterns for QueryableStore backends reporting cypher in supported_dialects
version: 0.1.0
license: MIT
---

# Context Intelligence Neo4j Search (Cypher Dialect)

This skill applies to QueryableStore backends that report "cypher" in
supported_dialects (currently: Neo4j).

Query patterns for searching and traversing the context-intelligence graph
using the Cypher query language against a Neo4j database.

---

## Schema

### Node ID Format

Node IDs are generated by `make_node_id()` in `utils.py` and are filesystem-safe on all platforms.

**Pattern:** `{session_id}__{event_name}__{timestamp_ms}`

- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` -> `prompt_submit`
- Session nodes use the raw `session_id` (a UUID) as their node_id -- no transformation
- Example: `6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000`

### Edge ID Format

Edge IDs are generated by `make_edge_id()` in `utils.py`. Used as deterministic identifiers across all implementations.

**Pattern:** `{source_id}==[{edge_type}]=={target_id}`

- `==[` and `]==` are the separators (never appear in node IDs)
- Example: `6afb3613-...==[HAS_STEP]==6afb3613-...__prompt_submit__1741270343000`

### Multiple Storage Backends

The graph can be stored in multiple backends. Query capability is declared via the `QueryableStore` protocol — check `store.supported_dialects` to discover what's available. Neo4j reports `{"cypher"}`. This skill covers the Cypher dialect. For SQL/PGQ patterns, see the `context-intelligence-graph-search` skill (DuckDB backend).

### Neo4j Node Properties

Every node in Neo4j has these properties:

| Property | Type | Description |
|----------|------|-------------|
| `node_id` | `String` | Unique identifier (same format as other backends) |
| `graph_forest_name` | `String` | Forest partition this node belongs to |
| All properties from the `properties` dict | Various | Merged as top-level Neo4j properties |

Properties from the original `properties` dict (e.g., `session_id`, `status`, `occurred_at`, `prompt_text`) are stored as top-level Neo4j properties on the node — they are NOT nested inside a JSON blob.

### Neo4j Node Labels

Node labels are applied directly as Neo4j labels. A node can have multiple labels.

| Label | Meaning |
|-------|---------|
| `Session` | Fundamental execution boundary; one Amplifier session |
| `Root` | Top-level session with no parent |
| `Subsession` | Child session with a parent |
| `ForkedSession` | Session created via `session:fork` (inherits parent context) |
| `OrchestratorRun` | One `execution:start` to `execution:end` bracket (one user turn) |
| `Step` | A unit of work within an OrchestratorRun |
| `PromptStep` | The causal trigger step (iteration 0); carries the user prompt or delegation instruction |
| `AssistantStep` | An LLM iteration step within an interactive OrchestratorRun |
| `RecipeStep` | An LLM iteration step within a recipe-spawned session |
| `ToolExecution` | One `tool:pre` to `tool:post` pair; a single tool invocation |
| `Delegation` | A ToolExecution that spawned a child session via the delegate tool |
| `Event` | Any lifecycle or custom event not part of the core structural chain |

### Neo4j Relationship Types

Edges are stored as Neo4j relationships with the edge type as the relationship type.

| Relationship Type | From | To | Meaning |
|-------------------|------|----|---------| 
| `HAS_RUN` | Session | OrchestratorRun | Session contains ordered orchestrator runs |
| `HAS_STEP` | OrchestratorRun | Step | Run contains ordered steps (LLM iterations) |
| `NEXT` | Step | Step | Sequential causal ordering within a run |
| `TRIGGERED` | Step | ToolExecution | Step triggered these tool executions |
| `PARALLEL_WITH` | ToolExecution | ToolExecution | Concurrent execution in same parallel group |
| `SPAWNED` | ToolExecution | Session | Delegation created a child session |
| `SUBSESSION_OF` | Session | Session | Child session to parent lineage |
| `HAS_EVENT` | Session / OrchestratorRun / Step | Event | Attaches lifecycle/custom events to their scope |

### Relationship Properties

All relationships carry:

| Property | Type | Description |
|----------|------|-------------|
| `graph_forest_name` | `String` | Forest partition this edge belongs to |
| All properties from the edge `properties` dict | Various | e.g., `seq`, `occurred_at` |

### Indexes

The following indexes are created automatically on first flush:

- Index on `node_id` for fast node lookups
- Index on `graph_forest_name` for forest-scoped queries

---

## Query Patterns

### Pattern 1: Node Lookup by ID

```cypher
MATCH (n {node_id: $node_id})
RETURN n
```

### Pattern 2: Find Nodes by Label

```cypher
-- Find all PromptStep nodes in a forest
MATCH (n:PromptStep)
WHERE n.graph_forest_name = $graph_forest_name
RETURN n.node_id AS node_id, n.prompt_text AS prompt_text
ORDER BY n.occurred_at DESC
```

### Pattern 3: Find All Sessions

```cypher
MATCH (s:Session:Root)
WHERE s.graph_forest_name = $graph_forest_name
RETURN s.node_id AS session_id, s.status AS status, s.started_at AS started_at
ORDER BY s.started_at DESC
```

### Pattern 4: Path Traversal — Session → Runs → Steps

```cypher
-- Find all steps in a session's runs
MATCH (s:Session {node_id: $session_id})-[:HAS_RUN]->(r:OrchestratorRun)-[:HAS_STEP]->(step)
WHERE s.graph_forest_name = $graph_forest_name
RETURN r.node_id AS run_id, step.node_id AS step_id, labels(step) AS step_labels
ORDER BY step.occurred_at
```

### Pattern 5: Tool Executions Triggered by a Step

```cypher
MATCH (step {node_id: $step_id})-[:TRIGGERED]->(tool:ToolExecution)
RETURN tool.node_id AS tool_id, tool.tool_name AS tool_name, tool.status AS status
```

### Pattern 6: Delegation Chains

```cypher
-- Find all delegation chains: parent session → tool → child session
MATCH (parent:Session)-[:HAS_RUN]->(run)-[:HAS_STEP]->(step)
      -[:TRIGGERED]->(te:ToolExecution)-[:SPAWNED]->(child:Session)
WHERE parent.graph_forest_name = $graph_forest_name
RETURN parent.node_id AS parent_id,
       te.node_id AS tool_id,
       child.node_id AS child_id
```

### Pattern 7: Subsession Lineage

```cypher
-- Walk the subsession chain upward
MATCH path = (child:Session)-[:SUBSESSION_OF*1..10]->(ancestor:Session)
WHERE child.node_id = $session_id
RETURN [n IN nodes(path) | n.node_id] AS lineage
```

### Pattern 8: Sequential Step Chain Within a Run

```cypher
-- Follow the NEXT chain from the first step in a run
MATCH (r:OrchestratorRun {node_id: $run_id})-[:HAS_STEP]->(first_step)
WHERE NOT ()-[:NEXT]->(first_step)
MATCH path = (first_step)-[:NEXT*0..50]->(step)
RETURN step.node_id AS step_id, labels(step) AS labels, step.occurred_at AS occurred_at
```

### Pattern 9: Search by Property Value

```cypher
-- Find nodes where a property matches a value
MATCH (n)
WHERE n.graph_forest_name = $graph_forest_name
  AND n.prompt_text CONTAINS $search_term
RETURN n.node_id AS node_id, n.prompt_text AS prompt_text
ORDER BY n.occurred_at DESC
```

### Pattern 10: Count Nodes by Label

```cypher
MATCH (n)
WHERE n.graph_forest_name = $graph_forest_name
RETURN labels(n) AS label_set, count(*) AS count
ORDER BY count DESC
```

---

## Forest-Scoped Queries

Every node and edge has a `graph_forest_name` property set during flush.
The `execute_query` method automatically injects `$graph_forest_name` as a
parameter (set to the store's own forest name by default).

### 1. Default query (own forest)

When no `graph_forest_name` argument is passed to `execute_query`, the
parameter `$graph_forest_name` is automatically set to the store's own forest.
Use it in WHERE clauses:

```cypher
-- $graph_forest_name is automatically set to the store's forest
MATCH (s:Session)
WHERE s.graph_forest_name = $graph_forest_name
RETURN s.node_id AS session_id, s.status AS status
```

### 2. Explicit forest query

Pass `graph_forest_name="other-project"` to query a different forest:

```cypher
-- graph_forest_name="other-project"
-- $graph_forest_name is set to "other-project"
MATCH (s:Session)
WHERE s.graph_forest_name = $graph_forest_name
RETURN s.node_id AS session_id
```

### 3. Cross-forest query

Pass `graph_forest_name="*"` to disable forest filtering. The
`$graph_forest_name` parameter is NOT injected, so queries see all data:

```cypher
-- graph_forest_name="*" — no automatic parameter
MATCH (n)
RETURN n.graph_forest_name AS forest, n.node_id AS node_id, labels(n) AS labels
ORDER BY n.graph_forest_name, n.occurred_at DESC
```

### Key difference from DuckDB

In DuckDB, forest scoping is done via CTE wrappers that automatically filter
all table references. In Neo4j, forest scoping is the **caller's
responsibility** — the store provides `$graph_forest_name` as a parameter,
and the caller adds `WHERE n.graph_forest_name = $graph_forest_name` to
their queries. This is more explicit but also more flexible.

---

## Notes

### Property access

Node and edge properties are stored as top-level Neo4j properties (not JSON blobs).
Access them directly:

```cypher
-- Direct property access
MATCH (n {node_id: $node_id})
RETURN n.status, n.session_id, n.prompt_text

-- Property existence check
MATCH (n)
WHERE n.prompt_text IS NOT NULL
RETURN n.node_id, n.prompt_text
```

### Relationship type in queries

Neo4j requires relationship types to be literal in MATCH patterns (not parameterized).
Use the exact type name:

```cypher
-- Correct: literal relationship type
MATCH (a)-[:HAS_RUN]->(b) RETURN b

-- WRONG: parameterized type (not supported in standard Cypher)
-- MATCH (a)-[$type]->(b) RETURN b
```

### Graph algorithms (future)

Neo4j supports advanced graph algorithms via the Graph Data Science (GDS) library:
shortest path, community detection, PageRank, node similarity, etc. These are
deferred to a future design but can be accessed via `execute_query` with raw
Cypher GDS calls once the library is installed in the Neo4j instance.

### Configuration

The Neo4j backend is configured via the store factory:

```python
store_config = {
    "type": "neo4j",
    "graph_forest_name": "my-project",
    "config": {
        "uri": "neo4j://localhost:7687",
        "auth": ("neo4j", "password"),
        "database": "neo4j"  # optional, defaults to "neo4j"
    }
}
```
```

**Step 2: Verify the file is well-formed**

```bash
head -5 amplifier-bundle-context-intelligence/skills/context-intelligence-neo4j-search/SKILL.md
```

Expected: shows the YAML frontmatter starting with `---`

**Step 3: Commit**

```bash
cd amplifier-bundle-context-intelligence && git add skills/context-intelligence-neo4j-search/SKILL.md && git commit -m "docs(neo4j): Cypher query skill for Neo4j graph search"
```

---

## Final Verification

After all 14 tasks are complete, run the full test suite:

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py tests/test_store_factory.py -v
```

Expected: all tests `PASSED`.

Then run the full module test suite to ensure nothing is broken:

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest -v
```

Expected: all tests across all test files `PASSED`.
