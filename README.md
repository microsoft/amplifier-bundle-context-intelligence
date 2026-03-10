# amplifier-bundle-context-intelligence

An event-driven context intelligence system for Amplifier sessions. Captures session events as structured data — always as flat JSONL logs, optionally as a queryable property graph.

## Overview

The bundle ships a single hook module (`hook-context-intelligence`) with two independent capabilities:

**Session Logging** is always active. Every Amplifier event is appended to a flat JSONL file under `~/.amplifier/projects/{slug}/sessions/{session_id}/events.jsonl`. Each record is raw and untransformed:

```json
{"event": "session_start", "timestamp": "2024-01-15T10:23:45.123Z", "data": {...}}
```

A `metadata.json` file is written alongside it. This is the universal baseline — no configuration required beyond adding the hook.

**Graph Generation** is opt-in. When `enable_graph: true` is set and `graph_stores` are configured, a `GraphDataHook` activates and builds a property graph from the event stream. The graph represents session structure as five node types connected by eight edge types: `Session → OrchestratorRun → Step → ToolExecution → Event`. Writes fan out simultaneously to all configured backends via `CompositeGraphStore`.

## Quick Start

Add the hook to your Amplifier configuration:

```yaml
hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      base_path: "~/.amplifier/projects"  # optional, this is the default
      exclude_events: []
      log_level: "WARNING"
      enable_graph: false                  # set true to activate graph generation
      graph_stores: []                     # configure backends when enable_graph: true
```

With `enable_graph: false` (the default), only session logging is active. No graph backends need to be configured.

To activate the graph with a file-based backend:

```yaml
hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      enable_graph: true
      graph_stores:
        - type: "file"
          graph_forest_name: "default"
          config:
            graph_store_root: "~/.amplifier/graphs"
```

Multiple backends can be configured simultaneously — writes fan out to all of them with failure isolation between stores.

## Storage Backends

### FileGraphStore

Writes nodes and edges as JSON files on disk. No dependencies beyond Python. Queryable with standard shell tools (`jq`, `grep`, `find`).

```yaml
- type: "file"
  graph_forest_name: "default"
  config:
    graph_store_root: "~/.amplifier/graphs"
```

Use the `context-intelligence-file-search` skill for shell-based queries against this store.

### DuckDBGraphStore

Writes to a DuckDB database file. Supports SQL queries and DuckPGQ graph pattern matching, plus BM25 full-text search over node and edge properties.

```yaml
- type: "duckdb"
  graph_forest_name: "default"
  config:
    connection: "~/.amplifier/graphs/ci.db"
```

Use the `context-intelligence-graph-search` skill for SQL and DuckPGQ queries against this store.

### Neo4jGraphStore

Writes to a running Neo4j instance. Supports Cypher queries and native graph algorithms.

```yaml
- type: "neo4j"
  graph_forest_name: "default"
  config:
    uri: "bolt://localhost:7687"
    username: "neo4j"
    password: "password"
```

Use the `context-intelligence-neo4j-search` skill for Cypher queries against this store.

## Graph Data Model

Five node types:

| Node | Key Properties |
|------|---------------|
| `Session` | session_id, slug, started_at |
| `OrchestratorRun` | run_id, model, started_at |
| `Step` | step_id, step_type, status |
| `ToolExecution` | execution_id, tool_name, status |
| `Event` | event_id, event_type, timestamp |

Eight edge types:

| Edge | From → To | Meaning |
|------|-----------|---------|
| `HAS_RUN` | Session → OrchestratorRun | Session contains this run |
| `HAS_STEP` | OrchestratorRun → Step | Run contains this step |
| `NEXT` | Step → Step | Sequential ordering |
| `TRIGGERED` | Step → ToolExecution | Step triggered this tool call |
| `PARALLEL_WITH` | ToolExecution → ToolExecution | Concurrent tool calls |
| `SPAWNED` | OrchestratorRun → OrchestratorRun | Sub-agent delegation |
| `SUBSESSION_OF` | Session → Session | Nested session relationship |
| `HAS_EVENT` | * → Event | Any node to its raw events |

The full data model is specified in the sibling `amplifier-event-and-data-model-for-context-intelligence` repository.

## Architecture

The hook module uses a lightweight dispatcher at `mount()`. On mount, `LoggingHandler` always registers and subscribes to all events. `GraphDataHook` registers conditionally, controlled by a six-state deterministic state machine that manages backend lifecycle (init, active, error, degraded, shutdown).

Seven graph handlers cover the full event space:

- `SessionHandler` — session lifecycle events
- `OrchestratorRunHandler` — run start/end, model selection
- `StepHandler` — step transitions and status changes
- `ToolExecutionHandler` — tool call start, result, error
- `RecipeHandler` — recipe-specific events
- `SystemEventHandler` — system-level signals
- `DefaultHandler` — catch-all safety net; no event is ever silently dropped

`CompositeGraphStore` wraps all configured backends. A write failure in one store does not affect the others.

DOT architecture diagrams covering each subsystem are available in `context/` for reference and introspection.

## Skills

Four companion skills ship with the bundle. Load them via the Amplifier skill system.

**`context-intelligence-session-navigation`**
Navigate flat JSONL session files safely. Handles sessions with 100k+ token lines — provides patterns for chunked reading, event filtering, and extracting specific event types without loading the full file.

**`context-intelligence-file-search`**
Shell-based queries for FileGraphStore. Covers node lookup by ID, edge traversal, property filtering with `jq`, and bulk session analysis with standard Unix tools.

**`context-intelligence-graph-search`**
SQL and DuckPGQ queries for DuckDBGraphStore. Covers node and edge queries, graph pattern matching with `MATCH`, BM25 full-text search, and aggregation over session data.

**`context-intelligence-neo4j-search`**
Cypher queries for Neo4jGraphStore. Covers path queries, graph algorithm invocations, and pattern-based node/edge retrieval.

## Development

The hook module is a standard Python package managed with `uv`.

```bash
cd modules/hook-context-intelligence
uv run pytest tests/ -v
```

Dependencies: `duckdb==1.4.3`, `neo4j>=5.0,<7.0`. Requires Python 3.11+.

Type checking and linting:

```bash
uv run pyright amplifier_module_hook_context_intelligence/
uv run ruff check amplifier_module_hook_context_intelligence/
```

## License

MIT
