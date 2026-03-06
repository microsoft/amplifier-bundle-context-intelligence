# QueryableStore Protocol Split Design

> **Goal:** Split `GraphStore` into a base protocol (write/read/lifecycle) and an extended
> `QueryableStore` protocol (adds query capabilities with dialect discovery). Align skill
> architecture to be per-dialect rather than monolithic.

**Date:** 2026-03-06

---

## Problem

The current `GraphStore` protocol includes `execute_query` as a required method. Two of three
implementations (`FileGraphStore`, `GraphState`) implement it only to raise `NotImplementedError`.
This is an Interface Segregation Principle violation — stores that cannot query are forced to
pretend they can.

Additionally, the single SKILL.md conflates SQL/PGQ query guidance with general graph store
documentation, even though query capability is backend-specific.

## Approach: Separate `QueryableStore` Protocol

Split into two `@runtime_checkable` protocols with an inheritance relationship.

### Protocol Definitions

Both protocols live in `graph_store.py`:

```python
@runtime_checkable
class GraphStore(Protocol):
    """Base async protocol for graph storage backends."""

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None: ...
    async def upsert_edge(self, source: str, target: str, edge_type: str, properties: dict[str, Any]) -> None: ...
    async def get_node(self, node_id: str) -> dict[str, Any] | None: ...
    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None: ...
    async def flush(self) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class QueryableStore(GraphStore, Protocol):
    """Extended protocol for stores that support structured queries."""

    @property
    def supported_dialects(self) -> frozenset[str]: ...

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None, dialect: str | None = None
    ) -> list[dict[str, Any]]: ...
```

### Key Decisions

- `supported_dialects` returns `frozenset[str]` (immutable, hashable).
- `execute_query` gains optional `dialect` parameter. `None` means "use the store's default."
  If a dialect is provided and not in `supported_dialects`, raise `ValueError`.
- Both protocols stay in `graph_store.py` — they are the same conceptual contract, layered.

## Implementation Changes

### `DuckDBGraphStore`

- Satisfies `QueryableStore` (extends `GraphStore`).
- Adds `supported_dialects` property returning `frozenset({"sql"})`.
- `execute_query` gains optional `dialect: str | None = None` parameter.
- If `dialect` is provided and not in `supported_dialects`, raises `ValueError`.
- Search index remains a private implementation detail (`_search_buffer`,
  `_auto_populate_search_index`) — not part of any protocol.

### `FileGraphStore`

- Satisfies `GraphStore` only.
- Remove `execute_query` entirely — no more `NotImplementedError` stub.

### `GraphState` (in-memory test helper in `services.py`)

- Satisfies `GraphStore` only.
- Remove `execute_query` entirely.

### Factory (`store_factory.py`)

- Return type stays `GraphStore`.
- Callers needing queries: `isinstance(store, QueryableStore)`.

## Test Changes

### `test_graph_store.py`

- Protocol conformance tests split: `GraphStore` tests drop `execute_query`.
- New `QueryableStore` conformance tests verify the extended contract
  (must have `execute_query` AND `supported_dialects`).

### `test_duckdb_store.py`

- `TestExecuteQuery` stays (DuckDB supports it).
- Add `test_supported_dialects_returns_frozenset` and `test_invalid_dialect_raises`.

### `test_file_store.py`

- Remove `TestExecuteQuery` class entirely.

### `test_services.py`

- Remove `test_execute_query_raises_not_implemented` from `TestGraphState`.

## Skill Architecture

Current: one monolithic `context-intelligence-graph-search/SKILL.md` covering everything.

New: skills scoped by query dialect.

```
skills/
  context-intelligence-graph-search/     # Rename content to scope it as SQL dialect
    SKILL.md                              # SQL + PGQ dialect skill
                                          # Header: "Applies to QueryableStore backends
                                          #          reporting 'sql' in supported_dialects"

  context-intelligence-file-search/      # FUTURE (out of scope for this refactor)
    SKILL.md                              # jq/grep/find patterns for JSON files
```

### Standing Rule Update

The AGENTS.md synchronization rule currently says "any schema change must update SKILL.md."
Update to: "any schema change must update the *relevant dialect skill(s)*" — acknowledging
multiple skills will exist.

## Documentation Updates

- `context/graph-store-protocol.md`: Reflect the two-tier protocol.
- `SKILL.md`: Add dialect scope header, note `execute_query` is `QueryableStore`-only.
- `AGENTS.md`: Update standing rules for multi-skill, multi-store world.

## Out of Scope

- File-based query capability and its skill (follow-up work).
- PGQ dialect support (DuckDB reports `{"sql"}` until PGQ extension loading is implemented).
- Neo4j or other future backends.
