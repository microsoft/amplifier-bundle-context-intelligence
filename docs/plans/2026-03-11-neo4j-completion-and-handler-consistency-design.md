# Neo4j Completion and Handler Consistency Design

## Goal

Complete the Neo4j data model implementation end-to-end so all events produce a full graph in Neo4j, eliminate all stubs, ensure timestamp type correctness, and improve handler file organization for consistency. Also update the workspace AGENTS.md to reflect the current state (Neo4j-only, all upstream gaps resolved).

## Background

The research repo (`amplifier-event-and-data-model-for-context-intelligence/`) defines a mature 5-node / 8-edge property graph data model derived from 8,616 real sessions. The `change-proposal.md` tracks 13 Change Packages (CPs). All CRITICAL and HIGH gaps are now resolved upstream:

| CP | Gap | Resolution |
|----|-----|------------|
| CP-1 (G1) | `session:end` not emitted | Fixed in amplifier-core v1.0.11 |
| CP-4 (G3) | Delegate `tool_call_id` enrichment | Re-landed in amplifier-foundation (commit `f70646b` by Brian) |
| CP-5 (G4) | Recipe events not visible | Fixed in amplifier-bundle-recipes PR #46 |
| CP-6 (G5) | `execution:start/end` missing in loop-basic | Fixed in amplifier-module-loop-basic |
| CP-7 (G2) | `execution:end` on CancelledError paths | Fixed in amplifier-module-loop-streaming (commit `7b953f2` by Brian) |

The recent merge removed DuckDB, file-based, and composite stores — Neo4j is now the sole graph backend. The in-memory `GraphState` remains as a fallback when `enable_graph=false`.

## Approach

Focused cleanup with full edge coverage — no scope creep, no touching upstream repos or amplifier-core. `SystemEventHandler` stays as a no-op (simplicity) — `DefaultHandler` catches those events generically. Six discrete sections, each independently testable.

## Architecture

No new components. All changes are within the existing module structure:

```
amplifier_module_hook_context_intelligence/
├── __init__.py                  ← import path update (Section 1)
├── neo4j_store.py               ← polish + timestamp conversion (Sections 2, 3)
├── handlers/
│   ├── __init__.py              ← docstring update (Section 1)
│   ├── logging_handler.py       ← moved here from module root (Section 1)
│   ├── tool_execution.py        ← add occurred_at to 2 edges (Section 4)
│   └── ...                      ← other handlers unchanged
└── ...

tests/
├── test_logging_handler.py      ← import update (Section 1)
├── conftest.py                  ← expanded reference graph (Section 5)
└── test_neo4j_edge_types.py     ← new integration tests (Section 5)
```

Plus the workspace-level `AGENTS.md` update (Section 6).

## Components

### Section 1: Move logging_handler.py into handlers/

`logging_handler.py` currently sits at the module root alongside infrastructure files (`neo4j_store.py`, `services.py`, `mount.py`, etc.), while all 7 other event handlers live inside `handlers/`. This is inconsistent.

**Changes:**

- Move `amplifier_module_hook_context_intelligence/logging_handler.py` → `amplifier_module_hook_context_intelligence/handlers/logging_handler.py`
- Update one import in `__init__.py`: `from .logging_handler import LoggingHandler` → `from .handlers.logging_handler import LoggingHandler`
- Update `handlers/__init__.py` docstring to mention `LoggingHandler` alongside the 7 graph handlers
- Update test imports in `tests/test_logging_handler.py` if they import the module path directly

**What doesn't change:** The wiring in `__init__.py` stays the same — `LoggingHandler` is still always-on, graph-free, registered at priority 100 by `mount()` directly. `MountFlow` never touches it. This is purely a file relocation for consistency.

### Section 2: Neo4j Store Polish

Three targeted fixes inside `neo4j_store.py`:

**a) Stale comments** — Lines 66 and 292 say `"# -- GraphStore methods (stubbed)"` and `"# -- QueryableStore methods (stubbed)"`. The methods beneath are fully implemented. Change to `"# -- GraphStore methods --"` and `"# -- QueryableStore methods --"`.

**b) Missing schema indexes** — `_ensure_schema()` currently creates 3 indexes (`Node.node_id`, `Node.graph_forest_name`, `Session.node_id`). The data model's Cypher queries pattern-match on additional labels. Add indexes using `CREATE INDEX IF NOT EXISTS` (idempotent, same pattern as existing):

- `(OrchestratorRun, node_id)`
- `(Step, node_id)`
- `(ToolExecution, node_id)`
- `(Event, node_id)`

**c) get_edge forest filter** — The Neo4j fallback query for `get_edge` (when buffer misses) filters source and target nodes by `graph_forest_name` but doesn't filter the relationship's own `graph_forest_name` property. Add `AND r.graph_forest_name = $graph_forest_name` to the Cypher `WHERE` clause.

### Section 3: Timestamp Type Conversion in neo4j_store.py

All timestamps currently flow through as ISO-8601 strings from the kernel (`amplifier_core/hooks.py` stamps `data["timestamp"] = datetime.now(timezone.utc).isoformat()`). Handlers extract this string and pass it as-is into properties. `neo4j_store.py` `flush()` passes properties straight through to Cypher with zero conversion. This means every `occurred_at`, `started_at`, `ended_at`, `request_at`, `response_at` is stored as a Neo4j `String`, not native `DateTime`.

**Fix:** In `neo4j_store.py` `flush()`, before writing to Neo4j, scan properties for known timestamp fields (any property ending in `_at`) and parse them from ISO-8601 strings into Python `datetime` objects using `datetime.fromisoformat()`. The Neo4j Python driver natively serializes `datetime` objects as Neo4j `DateTime` properties.

**Why this approach:**

- Handlers stay untouched — they keep passing strings (that's what the kernel gives them)
- The store layer is responsible for the type mapping before writing
- Cypher queries get native temporal comparison (`<`, `>`, `ORDER BY`, `duration.between()`) without `datetime()` casts
- Only `neo4j_store.py` changes — single responsibility

**Timestamp fields to convert:** Any property whose key ends with `_at` — `occurred_at`, `started_at`, `ended_at`, `request_at`, `response_at`, `execution_ended_at`, `delegate_completed_at`, etc.

### Section 4: Add occurred_at to PARALLEL_WITH and SPAWNED Edges

Currently 9 of 11 `upsert_edge` calls include `occurred_at`. Two edges — `PARALLEL_WITH` and `SPAWNED` (both in `tool_execution.py`) — pass empty property dicts `{}`. For consistency, all 8 edge types should carry `occurred_at`.

**Changes in `handlers/tool_execution.py`:**

- `PARALLEL_WITH` edge: change `{}` to `{"occurred_at": timestamp}`
- `SPAWNED` edge: change `{}` to `{"occurred_at": timestamp}`

Both already have access to the `timestamp` variable from `data.get("timestamp", "")`.

### Section 5: Integration Tests for All 8 Edge Types

The existing `conftest.py` reference graph covers 3 edge types (`HAS_RUN`, `HAS_STEP`, `TRIGGERED`) with 4 nodes. Tests run against the live `neo4j-test-env` container at `neo4j://localhost:7690` (Bolt) / `localhost:7480` (HTTP).

**Expand the reference graph** and add integration tests for the remaining 5 edge types:

| Edge | Setup Needed |
|------|-------------|
| `NEXT` | Add a second `Step:AssistantStep` node, wire `NEXT` from `PromptStep` → `AssistantStep` |
| `PARALLEL_WITH` | Add a second `ToolExecution` in the same `parallel_group_id`, wire bidirectional edges |
| `SPAWNED` | Add a `:Delegation` labeled `ToolExecution` + child `Session`, wire `SPAWNED` |
| `SUBSESSION_OF` | Add a child `:Subsession` node, wire `SUBSESSION_OF` to the root |
| `HAS_EVENT` | Add an `:Event` node (e.g. `:ContextCompaction`), wire `HAS_EVENT` from the `OrchestratorRun` |

**Each edge type test verifies:**

1. Edge exists between the correct source and target nodes (correct labels, correct direction)
2. Edge has `occurred_at` property (and it's stored as native Neo4j `DateTime`, not string)
3. `seq` property where applicable (`HAS_RUN`, `HAS_STEP`, `TRIGGERED`)
4. Edge survives flush → raw Cypher confirms it landed in Neo4j

### Section 6: AGENTS.md Update

The workspace `AGENTS.md` has several stale sections that need to reflect the Neo4j-only reality.

**Remove/rewrite:**

- **"Standing Rule: Storage Implementation Parity"** (lines 153–162) — references DuckDB, file-based stores, multiple `GraphStore` implementations, reconciliation between stores. All gone. Replace with a note that Neo4j is the sole graph backend (plus in-memory `GraphState` fallback).
- **"Standing Rule: Schema-Skill Synchronization"** (lines 146–151) — references `"sql"` dialect and DuckDB backend. Update to reference `"cypher"` dialect and Neo4j backend, pointing to `skills/context-intelligence-neo4j-search/SKILL.md`.
- **Workspace Layout** (lines 16–31) — add `agents/` directory to the tree.

**Add:**

- Note that all CRITICAL/HIGH upstream gaps (CP-1, CP-4, CP-5, CP-6, CP-7) are resolved — the event stream is now complete for full graph population.
- Document the `context-intelligence-analyst` agent in `agents/`.

**Keep as-is:** Critical Rules, Workspace Lifecycle, Testing, Issue Filing — all still accurate.

## Data Flow

No changes to data flow. Events still arrive from amplifier-core hooks → handlers extract properties → `upsert_node`/`upsert_edge` into the graph store buffer → `flush()` writes to Neo4j.

The only data flow refinement is in `flush()`: timestamp string properties are now converted to Python `datetime` objects before the Neo4j driver serializes them, so Neo4j receives native `DateTime` values instead of strings.

```
kernel event (ISO-8601 string timestamps)
  → handler (extracts timestamp, passes as string)
    → graph store buffer (holds string)
      → flush() [NEW: converts *_at strings → datetime objects]
        → Neo4j driver (serializes datetime → native DateTime)
          → Neo4j (stores native DateTime property)
```

## Error Handling

- **Timestamp parsing:** If a `*_at` property value fails `datetime.fromisoformat()`, log a warning and pass the original string through. Do not fail the flush for a malformed timestamp.
- **Index creation:** `CREATE INDEX IF NOT EXISTS` is idempotent — safe to run repeatedly, no error on existing indexes.
- **File move (Section 1):** If any import is missed, tests will fail with `ImportError` immediately — caught by CI.

## Testing Strategy

| Section | Test Approach |
|---------|--------------|
| 1 — Handler move | Existing `test_logging_handler.py` (25 tests) must pass with updated imports |
| 2 — Neo4j polish | Existing neo4j integration tests verify index creation via `SHOW INDEXES`; new test for `get_edge` forest filter |
| 3 — Timestamp conversion | Integration tests verify `typeof(n.occurred_at)` returns `DateTime` in Neo4j, not `String` |
| 4 — occurred_at on edges | Covered by the new integration tests in Section 5 |
| 5 — 8 edge types | New integration test class with all 8 edge types verified end-to-end against live `neo4j-test-env` at `localhost:7690` |
| 6 — AGENTS.md | No automated test — manual review of content accuracy |

## Open Questions

None — all clarifying questions were resolved during brainstorming:

- `SystemEventHandler` stays as no-op (simplicity)
- `_ensure_schema()` kept (index-only, idempotent)
- Timestamp conversion happens in store layer, not handlers
- All 8 edge types get `occurred_at` for consistency
- CP-4 and CP-7 confirmed resolved upstream by Brian
