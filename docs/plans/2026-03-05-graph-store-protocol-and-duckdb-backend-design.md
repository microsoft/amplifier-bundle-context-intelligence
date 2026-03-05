# GraphStore Protocol and DuckDB Backend Design

## Goal

Design the GraphStore protocol and a DuckDB-backed implementation for the context-intelligence hook module. The GraphStore abstracts graph storage so handlers write through it and tools/analysis code reads from it. The DuckDB implementation persists session graphs to a single file that spans multiple sessions over time.

## Background

The context-intelligence hook observes every event in an Amplifier session and builds a property graph representing the session's structure: sessions, orchestrator runs, steps, tool executions, delegations, and events. This graph needs a storage layer that:

- Handles writes in the event hot path without blocking handlers
- Persists data across sessions in a single embedded database
- Supports graph-aware queries for analysis
- Remains fully embedded (no external services) and MIT licensed

## Chosen Approach

- **DuckDB** as the storage backend (MIT licensed, truly embedded, single file, columnar)
- **DuckPGQ extension** for SQL/PGQ graph queries at analysis time (MIT licensed, SQL:2023 standard pattern matching)
- **Non-blocking writes** as a core protocol requirement (not an implementation detail)
- **In-memory write buffer** with background flush to DuckDB
- **Parquet export** capability for portable analysis

## Architecture

```
Events from Coordinator
        |
        v
   Handler.__call__()
        |
        v
   GraphStore Protocol (async interface)
        |
        +---> upsert_node / upsert_edge  (non-blocking, buffer only)
        +---> get_node / get_edge         (buffer-first, then backing store)
        +---> execute_query               (backing store, SQL/PGQ)
        +---> flush                       (persist buffer to backing store)
        +---> close                       (final flush + release)
        |
        v
   DuckDBGraphStore (implementation)
        |
        +---> In-Memory Buffer (hot path, microseconds)
        +---> Background Flush Task (batch writes, milliseconds)
        +---> DuckDB File (graph.duckdb, persistent)
```

## Components

### Component 1: GraphStore Protocol

The GraphStore is a `runtime_checkable` Protocol class. Any implementation must satisfy these method signatures and honor the guarantees below.

```python
@runtime_checkable
class GraphStore(Protocol):
    # Write operations -- MUST be non-blocking.
    # Implementations MUST buffer writes and return immediately.
    # Callers are in the event hot path.
    async def upsert_node(self, node_id: str, labels: set[str],
                          properties: dict[str, Any]) -> None: ...
    async def upsert_edge(self, source: str, target: str, edge_type: str,
                          properties: dict[str, Any]) -> None: ...

    # Read operations -- check buffer first, then backing store.
    async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
    async def get_edge(self, source: str, target: str,
                       edge_type: str) -> dict[str, Any] | None: ...

    # Query -- runs against persisted store (post-flush).
    async def execute_query(self, query: str,
                            params: dict[str, Any] | None = None
                            ) -> list[dict[str, Any]]: ...

    # Flush -- persist buffered writes. Called by lifecycle triggers,
    # not by handlers.
    async def flush(self) -> None: ...

    # Lifecycle
    async def close(self) -> None: ...
```

#### Protocol-Level Guarantees

These are non-negotiable. Any implementation must honor all five:

1. **`upsert_node`/`upsert_edge` MUST return immediately.** They buffer the operation and return. No disk I/O, no network, no blocking. This is a core requirement, not a workaround, not an optimization -- no exceptions.
2. **`get_node`/`get_edge` MUST reflect buffered state.** Handlers see the latest state even before flush.
3. **`flush()` persists buffered writes to the backing store.** Called by lifecycle triggers (`orchestrator:complete`, `session:end`, threshold), never by handlers directly.
4. **`close()` MUST call `flush()` before releasing resources.** No silent data loss.
5. **Flush failure MUST NOT propagate to handlers.** Log and retry on next trigger. The buffer is the safety net.

#### Labels

`set[str]` -- always includes the base type (e.g., `{"Step", "PromptStep"}`). Derived from event data by handlers using conditions from the data model. Event nodes always include `"Event"` plus the `derive_label()` output (e.g., `{"Event", "ContextCompaction"}`). The protocol doesn't validate labels -- that's the handler's job.

#### Properties

`dict[str, Any]` -- open-ended. The DuckDB implementation lifts `session_id` to a real column. Everything else goes into a JSON properties column initially, and we promote fields to real columns as needed.

#### Merge Semantics on Upsert

New properties merge with existing. Callers can update a node incrementally across multiple events (e.g., `tool:pre` creates the ToolExecution node, `tool:post` updates its `status` and `ended_at`).

### Component 2: DuckDB Storage Backend

The `DuckDBGraphStore` implements the GraphStore protocol with DuckDB as the storage engine.

#### Single DuckDB File, Multi-Session Graph

All sessions that compose this hook write to the same database. `session_id` scopes data; the graph grows over time. The hook is not in the scope of a single session -- it is composed into any session that uses the bundle, and the graph spans all of them.

#### File Location

`config.storage_path` (default: `~/.amplifier/context-intelligence/graph.duckdb`).

#### Schema

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

#### Schema Design Decisions

| Column / Type | Rationale |
|---------------|-----------|
| `session_id` real column | Every node and edge belongs to a session. Primary scoping/filtering dimension. |
| `labels VARCHAR[]` | DuckDB native list type. Supports `list_contains(labels, 'PromptStep')`. Maps to PGQ discriminator pattern. |
| `properties JSON` | Open-ended. Holds everything type-specific (status, run_number, tool_name, metadata, token counts, etc.). Promote to real columns later as query patterns emerge. |
| `occurred_at TIMESTAMP` | Nearly every node and edge has timing. Worth a real column for ordering and range queries. |
| `seq INTEGER` on edges | Ordering field from the data model (`HAS_RUN`, `HAS_STEP`, `TRIGGERED` all use seq). |
| `CREATE IF NOT EXISTS` | Idempotent. First session creates the tables, subsequent sessions reuse them. |

#### DuckPGQ Overlay

Defined on demand for analysis queries:

```sql
CREATE PROPERTY GRAPH session_graph
VERTEX TABLES (nodes)
EDGE TABLES (
    edges SOURCE KEY (source) REFERENCES nodes (node_id)
          DESTINATION KEY (target) REFERENCES nodes (node_id)
);
```

Recreated when needed for analysis. Most queries happen at analysis time, not real-time, so the CSR cache staleness issue is not a concern.

#### Parquet Export

```sql
COPY nodes TO 'sessions.parquet' (FORMAT PARQUET);
```

For portable analysis. Can filter by session, date range, labels, etc. DuckDB's native Parquet export makes the graph data instantly portable to Pandas, Polars, Spark, or any data warehouse.

#### Async Wrapping

DuckDB is synchronous. The implementation wraps calls in `asyncio.get_event_loop().run_in_executor()`.

#### Future Query Options

The protocol's `execute_query` method takes backend-specific query strings. For DuckDB, this is SQL/PGQ. In the future, a GraphForge backend could accept Cypher queries through the same protocol method. GraphForge (MIT licensed, pure Python, truly embedded, openCypher-compatible, SQLite-backed) was evaluated as a potential future Cypher query adapter on top of DuckDB data.

### Component 3: Non-Blocking Write Path

Non-blocking writes are a **core protocol requirement**, not a DuckDB implementation detail. Any implementation of GraphStore must guarantee that `upsert_node`/`upsert_edge` are non-blocking from the caller's perspective. This is part of the protocol contract, not an optimization. No workaround, no exception.

#### In-Memory Write Buffer + Background Flush

```
Handler calls upsert_node/upsert_edge
        |
        v
  In-Memory Buffer (dict-based, non-blocking, returns immediately)
        |
        |  background asyncio task
        v
  DuckDB file (batch INSERT/UPDATE periodically or on flush triggers)
```

#### How It Works

1. **`upsert_node`/`upsert_edge` write to an in-memory buffer and return immediately.** No awaiting disk I/O. The handler's hot path touches only a Python dict.
2. **A background task flushes the buffer to DuckDB on triggers:**
   - After each `orchestrator:complete` (natural boundary between runs)
   - On `session:end` (final flush)
   - When the buffer exceeds a size threshold (e.g., 100 pending operations)
   - On explicit `flush()` call
3. **Batch writes** -- the flush collects all pending upserts and writes them to DuckDB in a single transaction.
4. **Read operations** (`get_node`/`get_edge`) check the buffer first, then DuckDB. Handlers always see the latest state, even if it hasn't been flushed yet.
5. **`execute_query`** (SQL/PGQ) runs against DuckDB only -- analysis queries see flushed data. This is fine since most queries happen at analysis time.
6. **If a background flush fails**, log a warning and keep the data in the buffer for the next attempt. The in-memory buffer is the safety net. Worst case (crash before flush), you lose the last batch but `events.jsonl` is the authoritative source and the graph can be rebuilt.

## Data Flow

See the DOT diagrams in the bundle's `context/` directory for formalized visual representations:

- `context/write-path.dot` -- hot path through buffer, background flush to DuckDB
- `context/read-path.dot` -- dual source reads (buffer-first for handlers, DuckDB-only for analysis)
- `context/graph-store-lifecycle.dot` -- state machine from mount to close

## Error Handling

- **Flush failure**: Log warning, retain data in buffer, retry on next trigger. Never propagate to handlers.
- **DuckDB connection failure on open**: Fail the hook mount. The hook cannot function without storage.
- **Crash before flush**: Data in the buffer is lost. `events.jsonl` is the authoritative event source; the graph can be rebuilt by replaying events.
- **Close without flush**: `close()` always calls `flush()` first. No silent data loss under normal shutdown.

## Corrections to Research Documents

Two corrections to the research data model (`graph-data-model.md`) were agreed during this design:

1. **`:Prompt` label on Step nodes should be `:PromptStep`** for consistency with `:AssistantStep` and `:RecipeStep`. All sub-labels of `:Step` end in `Step`.

2. **Event sub-labels should use `derive_label()` for ALL events**, not special-cased abbreviated names. The `:Custom` category is removed. Every Event node gets `:Event` plus the `derive_label()` output:
   - `context:compaction` -> `{Event, ContextCompaction}` (not `{Event, Compaction}`)
   - `cancel:requested` -> `{Event, CancelRequested}` (not `{Event, Cancellation}`)
   - `cancel:completed` -> `{Event, CancelCompleted}`
   - `session:resume` -> `{Event, SessionResume}` (not `{Event, Resume}`)
   - `skill:loaded` -> `{Event, SkillLoaded}` (not `{Event, Custom}`)
   - Any event name -> `{Event, <derive_label(event_name)>}`

`derive_label()` already exists in the codebase (`DefaultHandler`) -- splits on `:` and `_`, PascalCase joins.

## Decisions Made During Design

| Decision | Rationale |
|----------|-----------|
| DuckDB over SQLite | SQL/PGQ graph queries, columnar engine, native Parquet export, `VARCHAR[]` for labels, MIT licensed |
| DuckDB over FalkorDB Lite | FalkorDB engine is SSPL licensed -- incompatible with MIT ecosystem. SSPL not recognized as open source by OSI. Downstream user surprise risk. |
| DuckDB over GraphForge | GraphForge is MIT and has Cypher, but is 5 weeks old and alpha. DuckDB is production-grade. GraphForge remains a future option as an optional Cypher query adapter. |
| Single file, multi-session | The hook is composed into any session, not scoped to one. Graph spans all sessions. `session_id` column scopes data. Enables cross-session analysis. |
| Non-blocking writes as protocol requirement | The hook is in the hot path of every event. Blocking writes would slow down the entire session. This is a contract, not an optimization. |
| In-memory buffer + background flush | Decouples handler hot path from disk I/O. Flush on natural boundaries (`orchestrator:complete`, `session:end`). |
| Labels as `VARCHAR[]` | DuckDB native list type. Supports `list_contains()` queries. Maps to PGQ discriminator pattern. |
| `session_id` and `occurred_at` lifted to schema | Guaranteed on every node and edge. Primary scoping and ordering dimensions. |
| Properties as JSON | Open-ended. Promote to real columns as query patterns emerge. |
| Empty `exclude_events` list | Start with no exclusions. Tune later. |
| `derive_label()` for all Event sub-labels | Consistent, predictable, extensible. No special-cased Custom bucket. |

## Testing Strategy

Deferred. Handler implementations and tests will be designed one at a time, starting with `SessionHandler`, grounded in real event data once the gap-closing work provides actual test fixtures.

## Open Questions

1. **Handler implementation details** -- each handler's exact event-to-graph mapping. Start with SessionHandler, one at a time, grounded in real event data.
2. **Exact flush threshold tuning** -- 100 pending ops? Time-based? Both?
3. **DuckPGQ property graph recreation strategy** for analysis queries.
4. **Tool affordances** for querying the graph from within sessions.
5. **Schema evolution strategy** -- when and how to promote JSON properties to real columns.
