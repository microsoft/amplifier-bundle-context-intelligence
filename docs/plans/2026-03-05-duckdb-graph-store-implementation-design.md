# DuckDBGraphStore Implementation and Coordinator Capture Design

## Goal

Implement the DuckDB-backed GraphStore as the default storage backend for the context-intelligence hook module, driven by a connection string in config, and capture the coordinator reference on HookStateService for future lazy evaluation of settings.

## Background

The context-intelligence hook module needs a persistent, queryable graph store to back the `GraphStore` protocol. The existing in-memory `GraphState` served as a reference implementation during protocol design, but production use requires a real storage engine. DuckDB provides embedded columnar storage with SQL and PGQ graph query support -- ideal for an analytics-oriented graph store that runs in-process with no external server.

The coordinator reference is also needed on `HookStateService` so handlers can eventually resolve settings with a config-first, coordinator-fallback pattern.

## Approach

- **DuckDB is the default graph store** -- no in-memory fallback in production config. `GraphState` remains in the codebase for unit tests only.
- **Connection string in nested config** drives persistent vs ephemeral: file path = persistent, `:memory:` = ephemeral.
- **Factory pattern** for store creation -- lazy import of `duckdb`, clean separation from `HookStateService`.
- **Coordinator captured** on `HookStateService` for future config-first, coordinator-fallback resolution.
- **Non-blocking write path** via in-memory buffer + background flush (core protocol requirement).

## Architecture

```
behavior.yaml
    └─ config.graph_store.{type, connection}
            │
            ▼
    ┌──────────────────┐
    │  store_factory.py │  ← create_graph_store(store_config)
    │  (lazy import)    │
    └────────┬─────────┘
             │ returns GraphStore
             ▼
    ┌──────────────────┐         ┌──────────────────┐
    │ HookStateService │────────▶│ DuckDBGraphStore  │
    │   .graph         │         │   (buffer + db)   │
    │   .coordinator   │         └──────────────────┘
    └──────────────────┘
             ▲
             │
        MountFlow.create_services(coordinator)
```

Handlers write nodes/edges to the buffer (non-blocking). Flush drains the buffer into DuckDB in a single transaction. Reads check the buffer first, then fall through to DuckDB.

## Components

### Config Structure with Nested Storage Settings

Config in behavior YAML:

```yaml
hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      graph_store:
        type: "duckdb"                # which GraphStore implementation
        connection: "~/.amplifier/context-intelligence/graph.duckdb"  # persistent
        # connection: ":memory:"      # in-memory, ephemeral
        # connection: ""              # (or omitted) defaults to :memory:
```

Resolution rules:

- `config.graph_store.type` selects the implementation class. Default is `"duckdb"`.
- `config.graph_store.connection` and any other keys under `graph_store` are backend-specific -- passed to that implementation's constructor.
- If `graph_store` is omitted entirely, defaults to DuckDB with `":memory:"` connection.
- If `type` is `"duckdb"` but `connection` is omitted/empty, defaults to `":memory:"`.
- Unknown types raise `ValueError` -- fail loud, not silent.
- Future backends (e.g., GraphForge) would use their own keys under `graph_store`.

Path handling for file-based connections:

- Expand `~` in paths.
- Create parent directories if they don't exist.

### Factory-based GraphStore Creation

**New file:** `store_factory.py` alongside `services.py`, `graph_store.py`, `protocol.py`.

```python
def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    store_type = store_config.get("type", "duckdb")  # default is duckdb
    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore
        connection = store_config.get("connection", ":memory:")
        return DuckDBGraphStore(connection=connection)
    raise ValueError(f"Unknown graph_store type: {store_type}")
```

`HookStateService` stays clean -- no knowledge of DuckDB or any specific backend:

```python
class HookStateService:
    def __init__(self, raw_config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = HookConfig(raw_config)
        self.coordinator = coordinator
        store_config = raw_config.get("graph_store", {})
        self.graph = create_graph_store(store_config)
```

Benefits:

- Lazy import of `duckdb` stays in the factory -- only triggered when `type: "duckdb"` is configured.
- `HookStateService` doesn't know about DuckDB or any specific backend.
- Factory is the single place to add new backend types later.
- Easy to test in isolation -- mock the factory or pass config directly.
- `GraphState` stays in the codebase for unit tests only -- handlers can test against it directly.

### Coordinator Capture

`HookStateService` gains a `coordinator` parameter:

```python
class HookStateService:
    def __init__(self, raw_config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = HookConfig(raw_config)
        self.coordinator = coordinator
        store_config = raw_config.get("graph_store", {})
        self.graph = create_graph_store(store_config)
```

`MountFlow.create_services()` receives and passes coordinator:

```python
def create_services(self, coordinator: Any) -> None:
    self.services = HookStateService(self._config, coordinator=coordinator)
    self.state = MountState.STATE_CREATED
```

`MountFlow.run()` passes coordinator to `create_services()`:

```python
async def run(self, coordinator: Any) -> Callable:
    self.create_services(coordinator)
    self.instantiate_handlers()
    await self.discover_events(coordinator)
    ...
```

Key decisions:

- `coordinator: Any = None` default -- backward compatible, existing tests that create `HookStateService(raw_config={})` don't break.
- Handlers access coordinator via `self.services.coordinator`.
- Resolution pattern for later: config first, coordinator fallback.
- No code uses the coordinator yet -- captured for future lazy evaluation.

### DuckDB as Runtime Dependency

Since DuckDB is the default store (not optional), `duckdb` becomes a runtime dependency:

```toml
[project]
dependencies = [
    "duckdb>=1.0",
]
```

Not in `[dependency-groups] dev` -- in `[project] dependencies`. Anyone who composes this bundle gets DuckDB installed.

### DuckDBGraphStore Class Implementation

**File:** `duckdb_store.py` -- new file alongside `services.py`.

#### Constructor

- Takes `connection: str = ":memory:"`.
- Expands `~` in path, creates parent directories for file paths.
- Opens `duckdb.connect(connection)`.
- Runs `CREATE TABLE IF NOT EXISTS` for both tables.
- Initializes empty buffer dicts.

#### Buffer Structure

Same shape as `GraphState` internals:

- `_node_buffer: dict[str, dict[str, Any]]` -- keyed by `node_id`.
- `_edge_buffer: dict[tuple[str, str, str], dict[str, Any]]` -- keyed by `(source, target, edge_type)`.

#### Async Wrapping Pattern

```python
async def _run(self, fn):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)
```

All DuckDB calls go through `_run()` -- keeps the event loop free.

#### Non-Blocking Writes (Core Protocol Requirement)

- `upsert_node`/`upsert_edge` write to the in-memory buffer and return immediately.
- No DuckDB I/O in the hot path.
- Buffer uses merge-on-upsert semantics: existing labels are unioned, existing properties are updated.

#### Buffer-First Reads

- `get_node`/`get_edge` check buffer first (sync, instant).
- If not in buffer, query DuckDB via `_run()`.
- Reconstruct dict format from DuckDB row (deserialize JSON properties, convert `VARCHAR[]` to set for labels).

#### Flush

- `flush()` drains the buffer into DuckDB in a single transaction via `_run()`.
- `BEGIN TRANSACTION`.
- Batch `INSERT OR REPLACE INTO nodes` from buffer.
- Batch `INSERT OR REPLACE INTO edges` from buffer.
- `COMMIT`.
- Buffer cleared after successful flush.
- If flush fails, log warning, put items back in buffer for retry.

#### Query

- `execute_query()` runs SQL/PGQ against DuckDB via `_run()`.
- Operates on flushed data only (analysis-time queries).

#### Close

- `close()` calls `flush()`, then closes the DuckDB connection.

## Data Flow

```
Handler.handle(event, services)
    │
    ├─ services.graph.upsert_node(...)   ← writes to buffer (sync, instant)
    ├─ services.graph.upsert_edge(...)   ← writes to buffer (sync, instant)
    │
    ▼  (on session:end / orchestrator:complete / explicit flush)
    │
    services.graph.flush()
    │
    ├─ _run(lambda: BEGIN TRANSACTION)
    ├─ _run(lambda: INSERT OR REPLACE INTO nodes ...)
    ├─ _run(lambda: INSERT OR REPLACE INTO edges ...)
    └─ _run(lambda: COMMIT)
              │
              ▼
         DuckDB storage (file or :memory:)
```

Reads follow a buffer-first pattern:

```
services.graph.get_node(node_id)
    │
    ├─ check _node_buffer → hit? return immediately
    │
    └─ miss → _run(lambda: SELECT ... FROM nodes WHERE node_id = ?)
                  │
                  ▼
              deserialize JSON properties, VARCHAR[] → set
```

## Schema

```sql
CREATE TABLE IF NOT EXISTS nodes (
    node_id     VARCHAR PRIMARY KEY,
    session_id  VARCHAR NOT NULL,
    labels      VARCHAR[] NOT NULL,
    occurred_at TIMESTAMP,
    properties  JSON
);

CREATE TABLE IF NOT EXISTS edges (
    source      VARCHAR NOT NULL,
    target      VARCHAR NOT NULL,
    edge_type   VARCHAR NOT NULL,
    session_id  VARCHAR NOT NULL,
    occurred_at TIMESTAMP,
    seq         INTEGER,
    properties  JSON,
    PRIMARY KEY (source, target, edge_type)
);
```

## Error Handling

- **Unknown store type:** `ValueError` raised from the factory -- fail loud, not silent.
- **Flush failure:** Log warning, put items back in buffer for retry on next flush call.
- **Connection failure:** DuckDB raises on `connect()` -- propagates up through `create_graph_store()` at service init time.
- **Path creation failure:** `os.makedirs` failure propagates -- if we can't create the directory, we can't store data.

## Testing Strategy

- **`GraphState` for handler unit tests:** Handlers test against the in-memory `GraphState` directly -- fast, no DuckDB dependency in handler tests.
- **`DuckDBGraphStore` unit tests:** Test with `:memory:` connection -- buffer behavior, flush, reads, upsert merge semantics.
- **Factory tests:** Mock or pass config dicts directly, verify correct store type is returned.
- **Integration tests:** Full config → factory → DuckDB store → handler → flush cycle.

## File Layout After Implementation

```
amplifier_module_hook_context_intelligence/
    __init__.py           # mount() entry point
    protocol.py           # EventHandler protocol
    graph_store.py        # GraphStore protocol
    services.py           # HookConfig, GraphState, HookStateService
    store_factory.py      # create_graph_store() factory (NEW)
    duckdb_store.py       # DuckDBGraphStore (NEW)
    mount.py              # MountFlow state machine
    handlers/
        __init__.py
        session.py
        orchestrator_run.py
        step.py
        recipe_step.py
        tool_execution.py
        event.py
        default.py
```

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| DuckDB is the default store | No in-memory fallback in production. `GraphState` for unit tests only. |
| Connection string in nested config `graph_store.connection` | Backend-specific settings under `graph_store` namespace, doesn't pollute top-level hook config. |
| Default type is `"duckdb"`, default connection is `":memory:"` | Zero-config works -- you get DuckDB in-memory without specifying anything. |
| Unknown types raise `ValueError` | Fail loud, not silent fallback. |
| Factory with lazy import | `duckdb` only imported when type is `"duckdb"` -- though it's always duckdb for now, this pattern is clean for future backends. |
| `duckdb>=1.0` as runtime dependency | DuckDB is the default, not optional. Must be in `[project] dependencies`. |
| Coordinator captured on `HookStateService` | Future lazy evaluation: config first, coordinator fallback. |
| `coordinator: Any = None` default | Backward compatible with existing tests. |
| Buffer same shape as `GraphState` | Familiar pattern, merge-on-upsert semantics work the same way. |
| `INSERT OR REPLACE` for flush | Handles both new and updated nodes/edges in one statement. |

## Open Questions

1. **DuckPGQ extension loading** -- do we `INSTALL duckpgq FROM community; LOAD duckpgq;` at connection time, or lazily on first `execute_query`? Probably lazy.
2. **Flush threshold tuning** -- 100 pending ops? Time-based? Both? Start with on-demand only (`orchestrator:complete`, `session:end`, explicit `flush()`), tune later.
3. **Schema evolution** -- when to promote JSON properties to real columns. Not now.
4. **Parquet export** -- tool affordance for later, not part of this implementation.
