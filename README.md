# amplifier-bundle-context-intelligence

An [Amplifier](https://github.com/microsoft/amplifier) bundle that captures session events as structured data for analysis and querying.

The bundle writes every session event to a local JSONL log and — when configured with a server URL — forwards events to the [Context Intelligence Server](https://github.com/colombod/amplifier-context-intelligence) for graph storage and blob management.

---

## What it does

| Always active | When `context_intelligence_server_url` is set |
|---------------|-----------------------------------------------|
| Writes `events.jsonl` + `metadata.json` per session | POSTs every event to the CI server |
| | Registers `blob_list` / `blob_dump` as agent tools |

The `context-intelligence-analyst` agent is also included for querying session data — navigating local JSONL files and running Cypher queries against the CI server's Neo4j graph.

---

## Quick Start

### 0. Start the Context Intelligence Server

The bundle forwards events to the CI server for graph storage. Start it first:

```bash
git clone https://github.com/colombod/amplifier-context-intelligence
cd amplifier-context-intelligence
docker compose up -d
# Server ready at http://localhost:8000
```

> If you only want local JSONL logging (no graph), skip this step —
> the bundle works without a server and simply writes events to disk.

### 1. Install

```bash
amplifier bundle add git+https://github.com/colombod/amplifier-bundle-context-intelligence@main
amplifier bundle use context-intelligence
```

### 2. Configure (app-cli `settings.yaml`)

Using [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) ≥ the [overrides wiring PR](https://github.com/microsoft/amplifier-app-cli/pull/143), configure the hook via the `overrides` section:

```yaml
# ~/.amplifier/settings.yaml  (or project .amplifier/settings.yaml)
overrides:
  hook-context-intelligence:
    config:
      # Point at your running Context Intelligence Server
      context_intelligence_server_url: "http://localhost:8000"

      # Optional: explicit workspace name.
      # Auto-resolved from project_slug when not set.
      workspace: "my-project"
```

> **Note:** `amplifier-app-cli` PR #143 wired `overrides.<id>.config` to work for hooks.
> Without it, the `overrides` block for hooks was silently ignored.
> If you're on an older version, set config via environment variables instead (see below).

### 3. Environment variable fallback

```bash
export CI_SERVER_URL=http://localhost:8000
export CI_WORKSPACE=my-project
export CI_LOG_LEVEL=INFO
```

### Verify it's working

After running an Amplifier session, check for the JSONL file:

```bash
ls ~/.amplifier/projects/<project_slug>/context-intelligence/sessions/
# You should see: events.jsonl  metadata.json
```

If `context_intelligence_server_url` is configured, check the server dashboard at `http://localhost:8000` — you should see the session under Active or Completed Sessions.

---

## Configuration reference

All config keys are read from the `overrides.hook-context-intelligence.config` block
in `settings.yaml`, or from environment variables as shown.

| Key | Env var | Default | Description |
|-----|---------|---------|-------------|
| `context_intelligence_server_url` | `CI_SERVER_URL` | *(empty)* | Base URL of the CI server. Events are only forwarded when this is set. |
| `workspace` | `CI_WORKSPACE` | *(auto)* | Scopes graph data on the server. Resolved from `coordinator.config['workspace']`, then `project_slug`, then working directory slug. |
| `log_level` | `CI_LOG_LEVEL` | `INFO` | Hook logging level. |
| `base_path` | — | *(working dir)* | Root directory for local JSONL output. |
| `exclude_events` | — | `[]` | fnmatch patterns for events to suppress. |

---

## Context Intelligence Server

The server that receives events, stores the graph, and serves blobs is at:
👉 **[colombod/amplifier-context-intelligence](https://github.com/colombod/amplifier-context-intelligence)**

```bash
# Start the server
git clone https://github.com/colombod/amplifier-context-intelligence
cd amplifier-context-intelligence
docker compose up
```

The server runs at `http://localhost:8000` by default.

---

## What gets stored

### Local JSONL (always)

Every session writes to:
```
<base_path>/<project_slug>/sessions/<session_id>/context-intelligence/
├── events.jsonl    ← one JSON line per event
└── metadata.json   ← started_at, ended_at, status, parent_id
```

### Server-side graph (when CI server configured)

All graph building, Neo4j writes, and blob management happen in the CI server.
The graph model is documented in [`context/graph-model-reference.md`](context/graph-model-reference.md).

---

## Analyst agent

The `context-intelligence-analyst` agent can:
- Navigate and search local `events.jsonl` files safely (avoids 100k+ token lines)
- Query the server's Neo4j graph via `POST /cypher`
- List and retrieve blobs via `GET /blobs/*`

**Safe JSONL navigation** — the agent knows about large-file pitfalls and uses streaming extraction patterns. See [`context/safe-extraction-patterns.md`](context/safe-extraction-patterns.md).

---

## Repository structure

```
amplifier-bundle-context-intelligence/
├── bundle.md                           ← root bundle definition
├── behaviors/
│   └── context-intelligence.yaml      ← hook behavior (thin mount)
├── agents/
│   └── context-intelligence-analyst.md
├── context/
│   ├── event-schema.md                 ← all 51+ Amplifier events
│   ├── graph-model-reference.md        ← Neo4j graph model for Cypher queries
│   ├── safe-extraction-patterns.md     ← JSONL navigation patterns
│   └── agents/
│       └── session-storage-knowledge.md
├── modules/
│   └── hook-context-intelligence/      ← the Python hook module
├── docs/
│   └── logging-handler-flow.dot        ← thin forwarder architecture
└── skills/
    ├── context-intelligence-neo4j-search/
    └── context-intelligence-session-navigation/
```

---

## Development

### Branch status

`main` is the thin-forwarder architecture (this branch). Events are forwarded to the Context Intelligence Server for graph storage.

### Run tests

```bash
cd modules/hook-context-intelligence
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -q
```

---

## Related

- [amplifier-context-intelligence](https://github.com/colombod/amplifier-context-intelligence) — the CI server (Neo4j + blob storage + dashboard)
- [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) — CLI that sends `project_slug` used for `workspace` resolution
- [amplifier](https://github.com/microsoft/amplifier) — the Amplifier framework
