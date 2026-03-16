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

### 1. Install the bundle

```bash
amplifier bundle add git+https://github.com/colombod/amplifier-bundle-context-intelligence@main --app
amplifier bundle use context-intelligence
```

At this point the bundle is active. Every Amplifier session will write events to local JSONL files automatically — no server required.

### 2. (Optional) Enable forwarding to the Context Intelligence Server

To also push events to the server for graph storage and querying, start the server and configure the bundle to point at it.

**Start the server:**

```bash
git clone https://github.com/colombod/amplifier-context-intelligence
cd amplifier-context-intelligence
docker compose up -d
# Server ready at http://localhost:8000
```

**Configure the hook** via environment variables:

```bash
export AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL=http://localhost:8000
export AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE=my-project    # optional, auto-resolved from project slug
export AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL=INFO          # optional, default: INFO
```

Or via `settings.yaml` overrides (requires [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) with [overrides wiring PR #143](https://github.com/microsoft/amplifier-app-cli/pull/143)):

```yaml
# ~/.amplifier/settings.yaml  (or project .amplifier/settings.yaml)
overrides:
  hook-context-intelligence:
    config:
      context_intelligence_server_url: "http://localhost:8000"
      workspace: "my-project"   # optional
```

### 3. Verify it's working

After running an Amplifier session, check for the JSONL file:

```bash
ls ~/.amplifier/projects/<project_slug>/sessions/*/context-intelligence/
# You should see: events.jsonl  metadata.json
```

If `context_intelligence_server_url` is configured, check the server dashboard at `http://localhost:8000` — you should see the session under Active or Completed Sessions.

---

## Configuration reference

All config keys are read from the `overrides.hook-context-intelligence.config` block
in `settings.yaml`, or from environment variables via the behavior YAML.

| Key | Env var | Default | Description |
|-----|---------|---------|-------------|
| `context_intelligence_server_url` | `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` | *(empty)* | Base URL of the CI server. Events are only forwarded when this is set. |
| `workspace` | `AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE` | *(auto)* | Scopes graph data on the server. Resolved from `coordinator.config['workspace']`, then `project_slug`, then working directory slug. |
| `log_level` | `AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL` | `INFO` | Hook logging level. |
| `base_path` | — | `~/.amplifier/projects` | Root directory for local JSONL output. |
| `exclude_events` | — | `[]` | fnmatch patterns for events to suppress. |
| `dispatch_timeout` | `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT` | `30` | HTTP timeout in seconds for server dispatch. |
| `dispatch_failure_threshold` | `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_FAILURE_THRESHOLD` | `3` | Consecutive dispatch failures before the circuit breaker disables dispatch for the session. |

---

## Server dispatch

### Connection reuse

The hook maintains a persistent `httpx.AsyncClient` for HTTP dispatch to the CI server. The client uses lazy creation — it is instantiated on the first dispatch attempt and reused for all subsequent events in the same session. This gives TCP connection pooling without paying the connection-setup cost per event. On session end the client is closed automatically via `aclose()` in `_finalize_metadata`.

### Circuit breaker

1. Every failed dispatch (network error or non-2xx response) increments the consecutive failure counter.
2. Once the counter reaches `dispatch_failure_threshold`, dispatch is permanently disabled for the session.
3. One clear warning is emitted:
   > `Context intelligence server unreachable after N attempts — dispatch disabled for this session. Local JSONL capture continues.`
4. Subsequent events are silently skipped (no further log noise); local JSONL capture continues unaffected.

### Recovery

Restart the Amplifier session once the server is back. There is no mid-session auto-recovery — once the circuit opens it stays open for that session. The JSONL files contain a complete record of all events and can be replayed into the server after it recovers.

### Architecture diagram

See [`docs/dispatch-circuit-breaker.dot`](docs/dispatch-circuit-breaker.dot) for the full dispatch flow and circuit breaker state machine.

---

## What gets stored

### Local JSONL (always)

Every session writes to:
```
<base_path>/<project_slug>/sessions/<session_id>/context-intelligence/
├── events.jsonl    ← one JSON line per event
└── metadata.json   ← started_at, ended_at, status, parent_id
```

### Server-side graph (when server configured)

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
│   ├── config-resolution.dot           ← ConfigResolver fallback chain diagram
│   ├── session-disk-layout.dot         ← on-disk session directory structure
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

### Run module tests

```bash
cd modules/hook-context-intelligence
uv sync
uv run pytest tests/ -q
```

### Run bundle-level tests

```bash
cd modules/hook-context-intelligence
uv run pytest ../../tests/ -q
```

---

## Related

- [amplifier-context-intelligence](https://github.com/colombod/amplifier-context-intelligence) — the CI server (Neo4j + blob storage + dashboard)
- [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) — CLI that sends `project_slug` used for `workspace` resolution
- [amplifier](https://github.com/microsoft/amplifier) — the Amplifier framework
