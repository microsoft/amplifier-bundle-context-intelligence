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
    source: git+https://github.com/colombod/amplifier-bundle-context-intelligence@main#subdirectory=modules/hook-context-intelligence
    config:
      # base_path: "~/.amplifier/projects"  # optional; resolved lazily if omitted
      # project_slug: "my-project"          # optional; resolved lazily if omitted
      # project: "my-project"              # optional; used as graph_forest_name fallback
      exclude_events: []
      log_level: "WARNING"
      enable_graph: false                   # set true to activate graph generation
      graph_store:                          # configure when enable_graph: true
        type: "neo4j"
        # graph_forest_name: "default"        # fallback chain: graph_forest_name -> config.project -> coordinator.config.project_slug -> "default"
        config:
          uri: "${NEO4J_URI:bolt://localhost:7687}"
          username: "${NEO4J_USERNAME:neo4j}"
          password: "${NEO4J_PASSWORD}"
          database: "${NEO4J_DATABASE:neo4j}"
```

With `enable_graph: false` (the default), only session logging is active. No graph backend needs to be configured.

## Neo4j Setup

The graph backend requires a running Neo4j instance. Install and start Neo4j:

```bash
# Docker (recommended for development)
docker run -d --name neo4j-dev \
  --restart unless-stopped \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=none \
  -v $HOME/neo4j/data:/data \
  -v $HOME/neo4j/logs:/logs \
  -v $HOME/neo4j/plugins:/plugins \
  -v $HOME/neo4j/import:/import \
   neo4j:5-community
```

The store creates necessary indexes on first flush (idempotent).

## Direct Application Integration

Use this when embedding the hook into a custom application built on `amplifier-core` or `amplifier-foundation`, rather than through the Amplifier CLI hook system.

### Loading the hook

```python
from amplifier_module_hook_context_intelligence import mount

# mount() is async — takes a coordinator and a config dict
cleanup = await mount(coordinator, config)
```

`mount()` registers handlers on the coordinator's hook system and returns a cleanup callable (or `None` if the graph path is disabled). Call the returned callable to deregister handlers when the session ends.

### Config dict — complete reference

```python
config = {
    # --- Identity / Storage paths ---
    "project_slug": "my-project",          # str; default: derived from coordinator or "default"
    "base_path": "~/.amplifier/projects",  # str; default: "~/.amplifier/projects"

    # --- Logging path (always active, zero config needed) ---
    "log_level": "WARNING",                # str; default: "WARNING"

    # --- Graph path (opt-in) ---
    "enable_graph": True,                  # bool; default: False
    "exclude_events": [],                  # list[str]; fnmatch patterns; graph path only

    # --- Neo4j connection ---
    "graph_store": {
        "type": "neo4j",
        "graph_forest_name": "my-project",  # explicit override; default: project_slug chain
        "config": {
            "uri": "bolt://localhost:7687",
            "username": "neo4j",            # omit both username+password for unauthenticated
            "password": "password",
            "database": "neo4j",            # default: "neo4j"
        },
    },
}
```

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `project_slug` | `str` | derived | Scopes storage paths and graph namespace |
| `base_path` | `str` | `~/.amplifier/projects` | Root directory for session logs |
| `log_level` | `str` | `"WARNING"` | Log verbosity for the hook module |
| `enable_graph` | `bool` | `False` | Activates the graph path (opt-in) |
| `exclude_events` | `list[str]` | `[]` | fnmatch patterns for events to skip in the graph |
| `graph_store.type` | `str` | — | Backend type; only `"neo4j"` is supported |
| `graph_store.graph_forest_name` | `str` | project_slug chain | Explicit namespace override for graph data |
| `graph_store.config.uri` | `str` | `bolt://localhost:7687` | Neo4j connection URI |
| `graph_store.config.username` | `str` | — | Omit (with `password`) for unauthenticated access |
| `graph_store.config.password` | `str` | — | Omit (with `username`) for unauthenticated access |
| `graph_store.config.database` | `str` | `"neo4j"` | Neo4j database name |

### Coordinator config — runtime reads

The hook reads these keys from `coordinator.config` lazily — resolved on the **first event**, not at mount time:

```python
coordinator.config["project_slug"]   # Derives graph_forest_name and storage paths
coordinator.config["base_path"]      # Fallback for storage root (default: ~/.amplifier/projects)
```

The app-cli sets these automatically. In a custom application you **must** set `project_slug` before the first event fires:

```python
coordinator.config["project_slug"] = "-my-project-slug"
```

The slug format uses `-` as separator (e.g. `/home/user/my-project` → `-home-user-my-project`).

### Coordinator capabilities consumed

```python
coordinator.get_capability("session.working_dir")
# Fallback for project_slug derivation. Returns the working directory path.
# The foundation bundle registers this on child sessions automatically.
# For root sessions in custom apps, register it manually:
coordinator.register_capability("session.working_dir", str(Path.cwd()))
```

```python
coordinator.collect_contributions("observability.events")
# Event discovery channel. Modules contribute custom event names here.
# The hook discovers events from: ALL_EVENTS (core) + this channel + legacy capability.
```

### graph_forest_name resolution chain

This is the namespace that scopes all graph data. First non-empty value wins:

| Priority | Source |
|----------|--------|
| 1 | `config["graph_store"]["graph_forest_name"]` — explicit override in hook config |
| 2 | `config["project"]` — secondary alias |
| 3 | `config["project_slug"]` — from hook config |
| 4 | `coordinator.config["project_slug"]` — from app-cli (set at session creation time) |
| 5 | `coordinator.get_capability("session.working_dir")` — slugified working directory |
| 6 | `"default"` — final fallback |

### Storage paths

Paths are derived from `base_path` and `project_slug` — they are not configurable individually:

```
Session logs:  <base_path>/<project_slug>/sessions/<session_id>/context-intelligence/events.jsonl
Metadata:      <base_path>/<project_slug>/sessions/<session_id>/context-intelligence/metadata.json
Blob store:    <base_path>/<project_slug>/sessions/<session_id>/context-intelligence/blobs/<key>.json
```

### CI_ENABLE_GRAPH environment variable

```bash
CI_ENABLE_GRAPH=true  # Values: "1", "true", "yes" (case-insensitive)
```

This overrides `enable_graph: false` regardless of YAML config. It exists because the Amplifier CLI merges behavior YAML on top of `settings.yaml` — a behavior-level `enable_graph: false` silently wins over a user's `settings.yaml` `enable_graph: true`. The env var bypasses this precedence.

### Minimal example

```python
from amplifier_core import AmplifierSession
from amplifier_module_hook_context_intelligence import mount
from pathlib import Path

# 1. Set up coordinator config BEFORE session creation
config = {"project_slug": "-my-app"}
session = AmplifierSession(config=config)

# 2. Mount the hook with graph enabled
hook_config = {
    "enable_graph": True,
    "project_slug": "-my-app",
    "base_path": str(Path.home() / ".my-app" / "projects"),
    "graph_store": {
        "type": "neo4j",
        "config": {
            "uri": "bolt://localhost:7687",
            "database": "neo4j",
        },
    },
}

cleanup = await mount(session.coordinator, hook_config)

# 3. Run your session — events are captured automatically
result = await session.execute("Hello world")

# 4. Cleanup when done
if cleanup:
    await cleanup()
```

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
