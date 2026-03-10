# Neo4j Graph Store Design

> **Goal:** Add a Neo4j backend to the context-intelligence graph store layer, implementing the
> `QueryableStore` protocol to provide production persistence, advanced Cypher graph queries,
> and multi-tenant off-machine storage.

**Date:** 2026-03-10

---

## Background

The graph store layer currently has two backends: `FileGraphStore` (basic persistence) and
`DuckDBGraphStore` (queryable, SQL-based). Both work well for single-process, local-machine
scenarios. However, production deployments need:

1. **Production persistence** — a durable, networked graph database for deployments where
   DuckDB's single-process model is limiting.
2. **Advanced graph queries** — full Cypher query language and Neo4j's graph algorithms
   (shortest path, community detection, etc.) that go beyond what SQL offers.
3. **Multi-tenant, off-machine storage** — centralized analysis across teams/users. User-level
   tenancy will be addressed in a separate design. `graph_forest_name` stays as-is, grouping
   sessions within a project.

Neo4j is a natural fit because the existing data model is already a labeled property graph —
nodes with labels and properties, edges with types and properties. The mapping is 1:1.

## Approach

**Buffer-then-batch** — same pattern as DuckDB. `upsert_node` and `upsert_edge` write to
in-memory dicts. `flush()` sends everything to Neo4j in a single transaction using Cypher
`UNWIND` for batch efficiency. `get_node`/`get_edge` check the buffer first, then fall back
to Neo4j. This is consistent with the existing backends, minimizes network round-trips, and
honors the protocol's non-blocking upsert requirement. A single batched transaction is
atomic — if flush fails, nothing is partially written.

---

## Architecture & Protocol Conformance

`Neo4jGraphStore` is a new class implementing `QueryableStore` — the same extended protocol
that `DuckDBGraphStore` implements. It lives in a new file `neo4j_store.py` alongside the
existing store files.

### Protocol Surface

| Method / Property | Behavior |
|---|---|
| `graph_forest_name: str` | Set at construction, read-only property |
| `upsert_node(node_id, labels, properties)` | Non-blocking, writes to in-memory buffer |
| `upsert_edge(source, target, edge_type, properties)` | Non-blocking, writes to in-memory buffer |
| `get_node(node_id)` | Buffer-first, then falls back to Neo4j query |
| `get_edge(source, target, edge_type)` | Buffer-first, then falls back to Neo4j query |
| `flush()` | Batch-writes all buffered data to Neo4j in a single transaction |
| `close()` | Calls flush, then closes the async driver |
| `supported_dialects: frozenset[str]` | Returns `frozenset({"cypher"})` |
| `execute_query(query, params, dialect, graph_forest_name)` | Runs raw Cypher, with forest scoping |

### Dependency

The `neo4j` package is a **required dependency** in `pyproject.toml` alongside `duckdb==1.4.3`.
No lazy import guards, no optional dependency handling.

### Protocol Guarantees

All 12 protocol guarantees from `graph-store-protocol.md` apply identically. The Neo4j backend
must pass the same protocol conformance tests as the other backends.

---

## Neo4j Data Model Mapping

The existing graph data model maps naturally to Neo4j's native labeled property graph.

### Nodes

Each node becomes a Neo4j node with its labels applied directly. The existing hierarchical
label system (`Session:Root`, `Step:PromptStep`, etc.) maps 1:1 to Neo4j's multi-label
support. Every node also gets:

- `node_id` as a property (used for lookups and the protocol's ID contract)
- `graph_forest_name` as a property (for forest-scoped queries)
- `session_id`, `occurred_at`, and all other properties from the `properties` dict merged
  as top-level Neo4j properties

### Edges

Each edge becomes a Neo4j relationship with the `edge_type` as the relationship type
(`HAS_RUN`, `NEXT`, `SPAWNED`, etc.). Edge properties (`occurred_at`, `seq`, etc.) are set
as relationship properties. `graph_forest_name` is also stamped on edges, consistent with
the DuckDB approach.

### Indexes

On first flush, the store ensures the following indexes and constraints exist:

- **Uniqueness constraint** on `node_id` for each known label
- **Index** on `graph_forest_name` for nodes and edges
- **Composite index** on `(source, target, edge_type)` for edge lookups

All created using `CREATE CONSTRAINT IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`
(idempotent).

### Flush Cypher Pattern

The batch write uses `UNWIND` for both nodes and edges:

```cypher
UNWIND $nodes AS n
MERGE (node {node_id: n.node_id})
SET node += n.properties, node:Label1:Label2
```

And similarly for edges. Single transaction, atomic.

---

## Buffering & Write Path

### Buffers

Same structure as DuckDB:

- `_node_buffer: dict[str, dict]` — keyed by `node_id`, value is the full node dict
  (labels, properties)
- `_edge_buffer: dict[tuple[str, str, str], dict]` — keyed by `(source, target, edge_type)`,
  value is the edge dict (properties)

### upsert_node / upsert_edge

Purely in-memory dict writes. Non-blocking per protocol requirement. Repeated upserts to the
same key merge properties (last-write-wins), exactly like the DuckDB backend.

### flush()

Executes inside a single Neo4j async transaction:

1. Batch all buffered nodes into a single `UNWIND` Cypher query that `MERGE`s on `node_id`
   and sets labels + properties + `graph_forest_name`.
2. Batch all buffered edges into a single `UNWIND` Cypher query that `MATCH`es source/target
   by `node_id` and `MERGE`s the relationship with its type and properties +
   `graph_forest_name`.
3. On success, clear both buffers.
4. On failure, raise — buffers are **NOT** cleared, so the next flush retries the same data.

### get_node / get_edge

Buffer-first reads. Check the in-memory buffer; if not found, fall back to a Cypher `MATCH`
query against Neo4j. This satisfies protocol guarantee #6 (handlers always see their own
buffered writes).

---

## Driver Lifecycle & Connection Management

### Construction

`Neo4jGraphStore` takes `uri`, `auth`, `database`, and `graph_forest_name`. The constructor
creates an `AsyncDriver` via `neo4j.AsyncGraphDatabase.driver(uri, auth=(...))`. The driver
manages its own connection pool internally — no manual pool management needed.

### Schema Initialization

On first `flush()` (or via an explicit `initialize()` call), the store ensures the necessary
indexes and constraints exist. This is a one-time idempotent operation using
`CREATE CONSTRAINT IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`.

### close()

Calls `flush()` to drain any remaining buffers, then calls `await driver.close()` to cleanly
shut down the connection pool. After close, any further operations raise.

### Error Handling

Neo4j driver exceptions (connectivity, transaction failures) propagate as-is during `flush()`
and `execute_query()`. The protocol doesn't define custom exception types.
`neo4j.exceptions.*` propagate directly, consistent with how DuckDB lets `duckdb.Error`
bubble up and FileStore lets `OSError` bubble up.

---

## Query Execution & Forest Scoping

### execute_query

`execute_query(query, params, dialect, graph_forest_name)` runs raw Cypher queries through
the async driver. The `dialect` parameter must be `"cypher"` — anything else raises
`ValueError`, consistent with how DuckDB rejects unknown dialects.

### Forest Scoping

Rather than fragile query rewriting, forest scoping is the caller's responsibility. The
`execute_query` method passes `graph_forest_name` as a parameter (`$graph_forest_name`)
available in the query's parameter map, so callers write:

```cypher
MATCH (s:Session {graph_forest_name: $graph_forest_name})-[:HAS_RUN]->(r)
RETURN s, r
```

The wildcard `"*"` skips adding the parameter, letting queries span all forests. This is
cleaner, more predictable, and avoids brittle query rewriting. Skill documentation will
provide forest-scoped query patterns, just like the DuckDB skill does today.

---

## Factory Integration

`store_factory.py` gets a third case: `"neo4j"`. It reads `uri`, `auth`, and `database` from
the `config` dict, constructs `Neo4jGraphStore(uri, auth, database, graph_forest_name)`, and
returns it. Same dispatch pattern as the existing two backends.

Config shape:

```python
{
    "type": "neo4j",
    "graph_forest_name": "my-project",
    "config": {
        "uri": "neo4j://localhost:7687",
        "auth": {"username": "neo4j", "password": "..."},
        "database": "neo4j"
    }
}
```

---

## Testing Strategy

### Three Tiers

1. **Protocol conformance tests** — `Neo4jGraphStore` plugs into the same protocol conformance
   suite that all backends use. Verifies all 12 guarantees: buffer-first reads, non-blocking
   upserts, flush atomicity, close-calls-flush, etc.

2. **Neo4j-specific unit tests** — Test Cypher generation (UNWIND batching, MERGE patterns,
   label application), schema initialization (idempotent index creation), and forest parameter
   injection. These can be structured so Cypher construction is testable independently of a
   live connection.

3. **Integration tests** — End-to-end: create store, upsert nodes/edges, flush, run Cypher
   queries, verify results. These require a running Neo4j instance.

### Test Infrastructure

- A **persistent** Docker container named `neo4j-test-env`.
- Non-standard ports: **`7688`** for Bolt, **`7475`** for HTTP (to avoid conflicts with any
  local Neo4j instance).
- Data volume mapped to **`~/neo4j-test-env-data`**.
- The container **stays running** between test runs — tests clean up data before or after
  runs rather than tearing down the container.
- A pytest fixture handles data cleanup (e.g., `MATCH (n) DETACH DELETE n` or scoped to the
  test's forest name) to ensure a clean slate.

---

## Open Questions

- Specific `neo4j` driver version to pin in `pyproject.toml` (will determine during
  implementation).
- User-level tenancy design (deferred to a future design).
- Whether to create a Cypher-focused skill alongside the existing DuckDB and file search
  skills (likely yes, during or after implementation).
