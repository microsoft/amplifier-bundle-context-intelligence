# amplifier-bundle-context-intelligence

An event-driven context intelligence system for Amplifier sessions. Captures session events as structured data — always as flat JSONL logs, optionally as a queryable property graph.

## Overview

The bundle ships a single hook module (`hook-context-intelligence`) with two independent capabilities:

**Session Logging** is always active. Every Amplifier event is appended to a flat JSONL file under `~/.amplifier/projects/{slug}/sessions/{session_id}/events.jsonl`. Each record is raw and untransformed:

```json
{"event": "session_start", "timestamp": "2024-01-15T10:23:45.123Z", "data": {...}}
```

A `metadata.json` file is written alongside it. This is the universal baseline — no configuration required beyond adding the hook.

**Graph Generation** is opt-in. When `enable_graph: true` is set and a `graph_store` is configured, a `GraphDataHook` activates and builds a property graph from the event stream. The graph represents session structure as five node types connected by eight edge types: `Session → OrchestratorRun → Step → ToolExecution → Event`. Writes go directly to a Neo4j instance.

## Installation

```bash
amplifier bundle add git+https://github.com/colombod/amplifier-bundle-context-intelligence@main --app
```

## Quick Start

Add the hook to your Amplifier configuration:

```yaml
hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      # base_path: "~/.amplifier/projects"  # optional; resolved lazily if omitted
      # project_slug: "my-project"          # optional; resolved lazily if omitted
      # project: "my-project"              # optional; used as graph_forest_name fallback
      exclude_events: []
      log_level: "WARNING"
      enable_graph: false                   # set true to activate graph generation
      graph_store:                          # configure when enable_graph: true
        type: "neo4j"
        graph_forest_name: "default"        # fallback chain: graph_forest_name -> config.project -> coordinator.config.project_slug -> "default"
        config:
          uri: "${NEO4J_URI:-bolt://localhost:7687}"
          username: "${NEO4J_USERNAME:-neo4j}"
          password: "${NEO4J_PASSWORD}"
          database: "${NEO4J_DATABASE:-neo4j}"
```

With `enable_graph: false` (the default), only session logging is active. No graph backend needs to be configured.

## Neo4j Setup

The graph backend requires a running Neo4j instance. Install and start Neo4j:

```bash
# Docker (recommended for development)
docker run -d --name neo4j-dev \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5
```

The store creates necessary indexes on first flush (idempotent).

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

The hook module uses a lightweight dispatcher at `mount()`. On mount, `LoggingHandler` always registers and subscribes to all events. `GraphDataHook` registers conditionally, controlled by a six-state deterministic state machine that manages backend lifecycle.

Seven graph handlers cover the full event space:

- `SessionHandler` — session lifecycle events
- `OrchestratorRunHandler` — run start/end, model selection
- `StepHandler` — step transitions and status changes
- `ToolExecutionHandler` — tool call start, result, error
- `RecipeHandler` — recipe-specific events
- `SystemEventHandler` — system-level signals
- `DefaultHandler` — catch-all safety net; no event is ever silently dropped

DOT architecture diagrams covering each subsystem are available in `context/` for reference and introspection.

## Skills

Two companion skills ship with the bundle. Load them via the Amplifier skill system.

**`context-intelligence-session-navigation`**
Navigate flat JSONL session files safely. Handles sessions with 100k+ token lines — provides patterns for chunked reading, event filtering, and extracting specific event types without loading the full file.

**`context-intelligence-neo4j-search`**
Cypher queries for Neo4jGraphStore. Covers path queries, graph algorithm invocations, and pattern-based node/edge retrieval.

## Development

The hook module is a standard Python package managed with `uv`.

```bash
cd modules/hook-context-intelligence
uv run pytest tests/ -v
```

Dependencies: `neo4j>=5.0,<7.0`. Requires Python 3.11+.

Type checking and linting:

```bash
uv run pyright amplifier_module_hook_context_intelligence/
uv run ruff check amplifier_module_hook_context_intelligence/
```

## License

MIT
