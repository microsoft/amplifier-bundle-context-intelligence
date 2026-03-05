# GraphStore Protocol Reference

This document defines the GraphStore protocol contract. It is the authoritative reference for anyone implementing a new GraphStore backend (PostgreSQL, GraphForge, or any future storage engine). You should be able to write a compliant implementation using only this document and the DOT diagrams in this directory.

## Protocol Interface

```python
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class GraphStore(Protocol):
    """Abstract graph storage for the context-intelligence hook.

    Handlers write through this protocol; tools and analysis code read from it.
    The protocol enforces non-blocking writes as a core contract requirement.
    """

    async def upsert_node(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> None:
        """Insert or update a node in the graph.

        MUST return immediately. Buffer the operation in memory and return.
        No disk I/O, no network calls, no blocking of any kind.

        If a node with this node_id already exists, merge properties:
        new properties are added, existing properties are overwritten
        by the new values, unmentioned properties are preserved.

        Args:
            node_id: Unique identifier for the node.
            labels: Set of labels (e.g., {"Step", "PromptStep"}).
                    Always includes the base type plus any sub-type.
            properties: Open-ended key-value pairs. Callers may update
                        a node incrementally across multiple events.
        """
        ...

    async def upsert_edge(
        self, source: str, target: str, edge_type: str,
        properties: dict[str, Any]
    ) -> None:
        """Insert or update an edge in the graph.

        MUST return immediately. Same non-blocking guarantee as upsert_node.

        Edge identity is (source, target, edge_type). If the edge already
        exists, merge properties using the same semantics as upsert_node.

        Args:
            source: node_id of the source node.
            target: node_id of the target node.
            edge_type: Relationship type (e.g., "HAS_RUN", "CHILD_OF").
            properties: Open-ended key-value pairs. May include seq for
                        ordered relationships.
        """
        ...

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node by its ID.

        MUST reflect buffered state. Check the in-memory buffer first,
        then fall back to the backing store. Handlers must always see the
        latest state, even if it hasn't been flushed yet.

        Returns:
            Node data dict including node_id, labels, and properties,
            or None if the node does not exist.
        """
        ...

    async def get_edge(
        self, source: str, target: str, edge_type: str
    ) -> dict[str, Any] | None:
        """Retrieve an edge by its composite key.

        MUST reflect buffered state. Same buffer-first guarantee as get_node.

        Returns:
            Edge data dict including source, target, edge_type, and
            properties, or None if the edge does not exist.
        """
        ...

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a backend-specific query against the persisted store.

        Runs against the backing store only (post-flush data). This is
        by design: analysis queries operate on flushed, consistent state.

        The query language is backend-specific:
        - DuckDB backend: SQL or SQL/PGQ
        - Future GraphForge backend: Cypher
        - Future PostgreSQL backend: SQL or AGE Cypher

        Args:
            query: Backend-specific query string.
            params: Optional query parameters.

        Returns:
            List of result rows as dicts.
        """
        ...

    async def flush(self) -> None:
        """Persist all buffered writes to the backing store.

        Called by lifecycle triggers, NEVER by handlers directly.
        Triggers include:
        - orchestrator:complete (natural run boundary)
        - session:end (final flush)
        - Buffer size threshold exceeded
        - Explicit lifecycle call

        If flush fails, log a warning and retain data in the buffer
        for the next attempt. NEVER propagate errors to callers.
        """
        ...

    async def close(self) -> None:
        """Shut down the store, releasing all resources.

        MUST call flush() before releasing resources. No silent data loss.
        After close(), all other methods are undefined behavior.
        """
        ...
```

## Non-Negotiable Guarantees

Any GraphStore implementation must honor ALL five of these guarantees. These are core protocol requirements, not optimizations, not workarounds. No exceptions.

### 1. Non-Blocking Writes

`upsert_node` and `upsert_edge` MUST return immediately. They buffer the operation and return. No disk I/O, no network, no blocking. Callers are in the event hot path -- every event in every session flows through these methods. Blocking here blocks the entire session.

### 2. Buffer-First Reads

`get_node` and `get_edge` MUST reflect buffered state. Check the in-memory buffer first, then fall back to the backing store. A handler that upserts a node and immediately reads it back must see the upserted state, even if flush hasn't happened yet.

### 3. Explicit Flush Lifecycle

`flush()` persists buffered writes to the backing store. It is called by lifecycle triggers:
- After each `orchestrator:complete` (natural boundary between runs)
- On `session:end` (final flush)
- When the buffer exceeds a size threshold
- On explicit lifecycle calls

Handlers never call `flush()` directly.

### 4. Close Guarantees

`close()` MUST call `flush()` before releasing resources. No silent data loss under normal shutdown. If the process crashes before close, the in-memory buffer is lost, but `events.jsonl` is the authoritative event source and the graph can be rebuilt.

### 5. Failure Isolation

Flush failure MUST NOT propagate to handlers. Log the error and keep the data in the buffer for the next flush attempt. The in-memory buffer is the safety net. Handlers must never see or handle storage errors.

## Write Path

See `write-path.dot` in this directory for the visual flow.

The write path has two stages, decoupled by the in-memory buffer:

**Hot path (microseconds):** Handler receives an event, calls `upsert_node`/`upsert_edge`, the call writes to a dict-based in-memory buffer and returns immediately. The handler returns `HookResult(continue)`.

**Background flush (milliseconds):** A background asyncio task drains the buffer on triggers (see Guarantee 3 above). It collects all pending upserts and writes them to the backing store in a single batch transaction.

This decoupling is what makes non-blocking writes possible regardless of the backing store's write latency.

## Read Path

See `read-path.dot` in this directory for the visual flow.

Two read paths serve different callers:

**Handler reads** (`get_node`, `get_edge`): Check the buffer first, then fall back to the backing store. Returns merged state. This ensures handlers always see the latest data.

**Analysis reads** (`execute_query`): Go directly to the backing store. Analysis queries see only flushed, consistent data. This is intentional -- analysis happens at query time, not in the event hot path, and operates on stable state.

## Lifecycle State Machine

See `graph-store-lifecycle.dot` in this directory for the visual state machine.

States:
1. **Unmounted** -- store not yet initialized
2. **Opening** -- connecting to backing store, ensuring schema exists
3. **Ready** -- buffer empty, awaiting events
4. **Receiving Events** -- buffer accumulating upserts from handler calls
5. **Flushing** -- background task draining buffer to backing store
6. **Closing** -- final flush in progress, then resource release
7. **Closed** -- all resources released, store unusable

Key transitions:
- `Receiving -> Flushing`: triggered by flush conditions
- `Flushing -> Ready`: buffer fully drained, no new events arrived during flush
- `Flushing -> Receiving`: new events arrived while flush was in progress
- `Flushing -> Receiving` (error path): flush failed, log warning, retry next trigger

## Labels

Labels are `set[str]`. Every node has at least one label (the base type). Most have two: the base type plus a sub-type discriminator.

Examples:
- Session node: `{"Session", "Root"}` or `{"Session", "Child"}`
- Step node: `{"Step", "PromptStep"}`, `{"Step", "AssistantStep"}`, `{"Step", "RecipeStep"}`
- Event node: `{"Event", "ContextCompaction"}`, `{"Event", "SkillLoaded"}`

Event sub-labels are derived using `derive_label()`: split on `:` and `_`, PascalCase join. This produces consistent, predictable labels for any event name without special-casing.

The protocol does not validate labels. That is the handler's responsibility. The store accepts whatever labels it receives.

## Properties

Properties are `dict[str, Any]` -- open-ended key-value pairs.

**Merge semantics on upsert:** When upserting a node or edge that already exists, new properties merge with existing ones. New keys are added, existing keys are overwritten by the new value, keys not mentioned in the new properties dict are preserved. This allows incremental enrichment of nodes across multiple events (e.g., `tool:pre` creates the node, `tool:post` adds `status` and `ended_at`).

**Lifted properties:** Implementations may lift frequently-queried properties to real columns in the backing store schema. For example, the DuckDB backend lifts `session_id` and `occurred_at` to real columns. The protocol interface does not expose this -- callers always pass properties as a dict, and the implementation decides what to lift.

## Implementing a New Backend

To implement a new GraphStore backend:

1. Implement all 7 methods from the Protocol interface above.
2. Honor all 5 non-negotiable guarantees -- especially non-blocking writes.
3. Design your buffer strategy. A simple Python dict works for single-process scenarios. Multi-process backends may need a concurrent buffer.
4. Map flush triggers to your backing store's batch write mechanism.
5. Ensure `get_node`/`get_edge` merge buffer state with persisted state.
6. Wrap synchronous backing stores with `asyncio.run_in_executor()`.
7. Handle `execute_query` with your backend's native query language.
8. Test against the protocol guarantees, not just the method signatures.

The DuckDB implementation (`DuckDBGraphStore`) is the reference implementation. See the design document in `docs/plans/` for DuckDB-specific decisions (schema, DuckPGQ overlay, Parquet export).
