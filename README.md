# amplifier-bundle-context-intelligence

An [Amplifier](https://github.com/microsoft/amplifier) bundle that captures session events as structured data for analysis and querying.

The bundle writes every session event to a local JSONL log and — when configured with a server URL — forwards events to the [Context Intelligence Server](https://github.com/colombod/amplifier-context-intelligence) for graph storage and blob management.

---

## What it does

| Always active | When `context_intelligence_server_url` is set |
|---------------|-----------------------------------------------|
| Writes `events.jsonl` + `metadata.json` per session | POSTs every event to the CI server |
| | Enables graph-powered Cypher queries via `graph_query` tool |
| | Enables `blob_read` tool for resolving `ci-blob://` URIs |

Two agents are included for querying session data:

- **`context-intelligence-graph-analyst`** — primary entry point. Queries the context-intelligence property graph using Cypher, resolves `ci-blob://` URIs, and automatically delegates to `context-intelligence-navigator` when the graph server is unreachable or returns 0 sessions.
- **`context-intelligence-navigator`** — local fallback agent. Navigates session data via flat JSONL files using safe `bash`/`jq`/`grep` extraction patterns when the server is unavailable. Invoked only by `graph-analyst` via the delegation chain — external callers should use `graph-analyst` as the entry point.

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

Or via `settings.yaml` overrides:

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
in `settings.yaml`, or from environment variables set in the shell or CI environment.

| Key | Env var | Default | Description |
|-----|---------|---------|-------------|
| `context_intelligence_server_url` | `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` | *(empty)* | Base URL of the CI server. Events are only forwarded when this is set. |
| `workspace` | `AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE` | *(auto)* | Scopes graph data on the server. Resolved from `coordinator.config['workspace']`, then `project_slug`, then working directory slug. |
| `log_level` | `AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL` | `INFO` | Hook logging level. |
| `base_path` | — | `~/.amplifier/projects` | Root directory for local JSONL output. |
| `exclude_events` | — | `[]` | fnmatch patterns for events to suppress. |
| `dispatch_timeout` | `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT` | `30` | HTTP write timeout in seconds for server dispatch uploads. |
| `dispatch_failure_threshold` | `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_FAILURE_THRESHOLD` | `3` | Consecutive dispatch failures before the circuit breaker disables dispatch for the session. |
| `dispatch_queue_capacity` | — | `256` | Maximum queued HTTP dispatches before dispatch is disabled for the session. |
| `close_drain_timeout` | — | `0.5` | Shutdown grace period in seconds for draining queued HTTP dispatches. |

---

## Server dispatch

### Dispatch isolation

The hook isolates server traffic behind a single bounded background worker per session. The event callback only appends local JSONL and enqueues best-effort HTTP work; it never waits for a server round trip. The worker lazily creates a persistent `httpx.AsyncClient`, reuses one keep-alive connection, and serializes POSTs to avoid unbounded task growth when the server is slow or unavailable.

HTTP timeouts are phase-specific rather than one blanket timeout: short `connect`/`pool` fail-fast bounds, a moderate `read` timeout, and `dispatch_timeout` applied to the `write` phase so larger payload uploads do not fail prematurely.

Each live POST also carries a deterministic top-level `idempotency_key` derived from the sanitized event envelope. The server may use that key to suppress duplicate live submissions while still allowing explicit replay mode from local `events.jsonl`.

If the dispatch queue fills, dispatch is disabled for the rest of the session and local JSONL capture continues. This prevents stalled network I/O from feeding back into hook latency or memory growth.

### Connection reuse

The worker uses lazy creation: it creates an `httpx.AsyncClient` on the first dispatch request and keeps it alive for the entire session lifetime. This lazy init avoids opening a connection before any events are dispatched. The shared client provides TCP connection pooling — a single keep-alive TCP connection is reused for all POSTs rather than opening a new connection per event. The client is closed via `aclose()` during session finalization.

### Circuit breaker

1. Every failed dispatch (network error or non-2xx response) increments the consecutive failure counter.
2. Once the counter reaches `dispatch_failure_threshold`, dispatch is permanently disabled for the session.
3. One clear warning is emitted:
   > `Context intelligence server unreachable after N attempts — dispatch disabled for this session. Local JSONL capture continues.`
4. Subsequent events are silently skipped (no further log noise); local JSONL capture continues unaffected.

### Recovery

Restart the Amplifier session once the server is back. There is no mid-session auto-recovery — once the circuit opens or the dispatch queue saturates, dispatch stays disabled for that session. The JSONL files contain a complete record of all events and can be replayed into the server after it recovers.

### Architecture diagram

See [`docs/dispatch-circuit-breaker.dot`](docs/dispatch-circuit-breaker.dot) for the full dispatch flow and circuit breaker state machine.

---

## What gets stored

### Local JSONL (always)

Every session writes to:
```
<base_path>/<project_slug>/sessions/<session_id>/context-intelligence/
├── events.jsonl    ← one JSON line per event
└── metadata.json   ← format, version, session lifecycle metadata
```

### Server-side graph (when server configured)

All graph building, Neo4j writes, and blob management happen in the CI server.
The graph model is documented in [`context/graph-model-reference.md`](context/graph-model-reference.md).

---

## Agents

| Agent | Tools | Role |
|-------|-------|------|
| `context-intelligence-graph-analyst` | `graph_query`, `blob_read`, `tool-filesystem`, `tool-bash`, `tool-skills` | Primary entry point — graph-powered analysis via Cypher, blob resolution |
| `context-intelligence-navigator` | `tool-filesystem`, `tool-search`, `tool-bash`, `tool-skills` | Local fallback — safe JSONL navigation via bash/jq/grep |

**Delegation chain:** External callers always invoke `context-intelligence-graph-analyst`. Before each analysis run, `graph-analyst` checks server availability. If the server is unreachable or the workspace contains 0 sessions, it delegates to `context-intelligence-navigator`, which navigates local JSONL files using safe extraction patterns. Navigator is never invoked directly by external callers.

**Safe JSONL navigation** — navigator knows about large-file pitfalls and uses streaming extraction patterns. See [`context/safe-extraction-patterns.md`](context/safe-extraction-patterns.md).

---

## Repository structure

```
amplifier-bundle-context-intelligence/
├── bundle.md                           ← root bundle definition
├── agents/
│   ├── context-intelligence-graph-analyst.md  ← primary entry point agent
│   └── context-intelligence-navigator.md      ← local fallback agent
├── context/
│   ├── event-schema.md                 ← all 51+ Amplifier events
│   ├── graph-model-reference.md        ← Neo4j graph model for Cypher queries
│   ├── safe-extraction-patterns.md     ← JSONL navigation patterns
│   ├── config-resolution.dot           ← ConfigResolver fallback chain diagram
│   ├── session-disk-layout.dot         ← on-disk session directory structure
│   ├── delegation-strategy.dot         ← graph-analyst → navigator delegation logic
│   └── agents/
│       └── session-storage-knowledge.md
├── modules/
│   ├── hook-context-intelligence/      ← the Python hook module
│   ├── tool-graph-query/               ← graph_query tool module
│   └── tool-blob-read/                 ← blob_read tool module
├── docs/
│   ├── dispatch-circuit-breaker.dot    ← dispatch flow and circuit breaker state machine
│   └── logging-handler-flow.dot        ← thin forwarder architecture
├── skills/
│   ├── context-intelligence-graph-query/
│   └── context-intelligence-session-navigation/
└── tests/
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
