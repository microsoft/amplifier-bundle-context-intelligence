# amplifier-bundle-context-intelligence

An [Amplifier](https://github.com/microsoft/amplifier) bundle that captures session events as structured data for analysis and querying.

The bundle writes every session event to a local JSONL log and — when configured with a server URL — forwards events to the [Context Intelligence Server](https://github.com/microsoft/amplifier-context-intelligence) for graph storage and blob management.

---

## What it does

| Always active | When `context_intelligence_server_url` is set |
|---------------|-----------------------------------------------|
| Writes `events.jsonl` + `metadata.json` per session, both tagged with `workspace` | POSTs every event to the CI server |
| | Enables graph-powered Cypher queries via `graph_query` tool |
| | Enables `blob_read` tool for resolving `ci-blob://` URIs |

Two agents are included for querying session data:

- **`graph-analyst`** — primary entry point. Queries the context-intelligence property graph using Cypher, resolves `ci-blob://` URIs, and automatically delegates to `session-navigator` when the graph server is unreachable or returns 0 sessions.
- **`session-navigator`** — local fallback agent. Navigates session data via flat JSONL files using safe `bash`/`jq`/`grep` extraction patterns when the server is unavailable. Invoked only by `graph-analyst` via the delegation chain — external callers should use `graph-analyst` as the entry point.

---

## Understanding workspace

**Workspace** is the primary isolation boundary for event data. It is written into every local file and every server POST — sessions in different workspaces are completely independent whether queried locally or via the graph. Typical uses: separate projects (`my-api`, `frontend`), separate environments (`dev`, `staging`, `prod`), or separate teams on a shared server.

### How workspace appears in data

`workspace` is a top-level field in all three places data is written:

**`events.jsonl`** — every line:
```json
{"event":"session:start","workspace":"my-project","timestamp":"2026-01-15T10:23:44.123Z","data":{"session_id":"abc-123","working_dir":"/home/user/myapp",...}}
```

**`metadata.json`** — session-level record:
```json
{"format":"context-intelligence","version":"1.0.0","session_id":"abc-123","workspace":"my-project","parent_id":"","started_at":"2026-01-15T10:23:44.123Z","status":"completed","ended_at":"2026-01-15T10:24:01.456Z","working_dir":"/home/user/myapp"}
```

**Server POST** (`POST /events`) — forwarded to the CI server:
```json
{"event":"session:start","workspace":"my-project","idempotency_key":"aci-event-v1:<sha256>","data":{...}}
```

### Resolution priority

The hook resolves `workspace` using the same `config → coordinator → default` pattern as all other properties:

| Priority | Source | Notes |
|----------|--------|-------|
| **1 (highest)** | `config["workspace"]` | From `settings.yaml` or env var — see [Configuration reference](#configuration-reference) |
| **2** | `coordinator.config["workspace"]` | Set programmatically before the session starts — see [Embedding](#embedding-in-an-amplifier-application) |
| **3 (lowest)** | `project_slug` derived from working directory | Automatic — `/home/user/my-api` becomes `-home-user-my-api` |

---

## Quick Start

### 1. Install

**Add to an existing app** (recommended) — layers the behavior on top of your active bundle without pulling in foundation as a dependency:

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=behaviors/context-intelligence.yaml --app
```

**Standalone** — creates a dedicated session configuration using the full root bundle (includes foundation):

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main
amplifier bundle use context-intelligence
```

Every Amplifier session will now write events to local JSONL files automatically — no server required.

### 2. (Optional) Enable server forwarding

To push events to the [Context Intelligence Server](https://github.com/microsoft/amplifier-context-intelligence) for graph storage and querying, you need a running server instance and its API key. See the [server repository](https://github.com/microsoft/amplifier-context-intelligence) for setup instructions.

Once the server is running, point the hook at it with the server URL and API key:

**Configure** via `settings.yaml`:

```yaml
# ~/.amplifier/settings.yaml  (or project .amplifier/settings.yaml)
overrides:
  hook-context-intelligence:
    config:
      context_intelligence_server_url: "http://localhost:8000"
      context_intelligence_api_key: "<your-api-key>"
      workspace: "my-project"    # optional — auto-resolved if omitted
```

Or via environment variables:

```bash
export AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL=http://localhost:8000
export AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY=<your-api-key>
export AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE=my-project   # optional
```

### 3. Verify

After running a session:

```bash
ls ~/.amplifier/projects/<project_slug>/sessions/*/context-intelligence/
# events.jsonl  metadata.json

head -1 ~/.amplifier/projects/<project_slug>/sessions/*/context-intelligence/events.jsonl | jq .workspace
cat ~/.amplifier/projects/<project_slug>/sessions/*/context-intelligence/metadata.json | jq .workspace
```

If the server is configured, open `http://localhost:8000/dashboard` — your session will appear once authenticated.

---

## Embedding in an Amplifier application

When integrating this hook from Python rather than through the bundle CLI, call `mount()` directly.

```python
from amplifier_module_hook_context_intelligence import mount
```

Signature:

```python
async def mount(coordinator, config: dict) -> cleanup_fn
```

`mount()` returns an async cleanup coroutine that **must** be awaited when the session ends — it drains in-flight HTTP dispatches and closes the persistent HTTP client.

### Minimal — JSONL only

```python
cleanup = await mount(coordinator, config={})
# ... session runs ...
await cleanup()
```

With an empty config dict the hook resolves everything from `coordinator.config` and the working-directory slug.

### Full config dict

All keys are optional. Omitted keys fall through to `coordinator.config` then to built-in defaults:

```python
config = {
    # Server forwarding — omit entirely to disable
    "context_intelligence_server_url": "http://localhost:8000",
    "context_intelligence_api_key": "your-api-key",

    # Workspace — written into every events.jsonl record and metadata.json
    # Omit to fall back to coordinator.config["workspace"], then project_slug
    "workspace": "my-project",

    # Storage (default: ~/.amplifier/projects)
    "base_path": "/var/data/amplifier/projects",

    # Tuning — all optional
    "log_level": "WARNING",
    "exclude_events": ["context:compaction"],
    "dispatch_timeout": 30,
    "dispatch_failure_threshold": 3,
}

cleanup = await mount(coordinator, config=config)
await cleanup()
```

### Workspace via coordinator.config

When workspace varies at runtime (e.g., multi-tenant apps), omit `workspace` from the config dict and set it on the coordinator instead. It is consulted as the middle fallback:

```python
coordinator.config["workspace"] = tenant_id   # priority 2 — used when config["workspace"] is absent

cleanup = await mount(coordinator, config={
    "context_intelligence_server_url": "http://localhost:8000",
    "context_intelligence_api_key": "your-api-key",
})
```

### Accessing resolved values

`mount()` registers a `ConfigResolver` as the `context_intelligence.config_resolver` capability:

```python
resolver = coordinator.get_capability("context_intelligence.config_resolver")
resolver.workspace                  # resolved workspace string
resolver.base_path                  # resolved Path object
resolver.session_dir("abc-123")     # Path to a session's CI directory
```

---

## Configuration reference

The `config` dict passed to `mount()` uses the same keys as the `overrides.hook-context-intelligence.config` block in `settings.yaml`. The Amplifier framework maps `AMPLIFIER_CONTEXT_INTELLIGENCE_<KEY>` environment variables into the config dict before `mount()` is called, so env vars and `settings.yaml` entries share the same priority level.

| Key | Env var | Default | Description |
|-----|---------|---------|-------------|
| `context_intelligence_server_url` | `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` | *(empty)* | Base URL of the CI server. Events are forwarded only when this is set. |
| `context_intelligence_api_key` | `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` | *(empty)* | Bearer token matching the server's `api_key`. Added as `Authorization: Bearer <value>` on every HTTP dispatch. |
| `workspace` | `AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE` | *(auto)* | Written into every `events.jsonl` record and `metadata.json`. Resolution: `config["workspace"]` → `coordinator.config["workspace"]` → `project_slug`. |
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

The hook isolates server traffic behind a single bounded background worker per session. The event callback only appends local JSONL and enqueues best-effort HTTP work — it never waits for a server round trip. The worker lazily creates a persistent `httpx.AsyncClient`, reuses one keep-alive connection, and serializes POSTs to avoid unbounded task growth when the server is slow or unavailable.

HTTP timeouts are phase-specific: short `connect`/`pool` fail-fast bounds, a moderate `read` timeout, and `dispatch_timeout` applied to the `write` phase so larger payload uploads do not fail prematurely.

Each live POST carries a deterministic `idempotency_key` derived from the sanitized event envelope. The server may use it to suppress duplicate live submissions while still allowing explicit replay from local `events.jsonl`.

If the dispatch queue fills, dispatch is disabled for the rest of the session and local JSONL capture continues.

### Connection reuse

The worker uses lazy creation: it creates an `httpx.AsyncClient` on the first dispatch request and keeps it alive for the entire session lifetime. This avoids opening a connection before any events arrive. TCP connection pooling means a single keep-alive connection is reused for all POSTs rather than opening a new one per event. The client is closed via `aclose()` during session finalization.

### Circuit breaker

1. Every failed dispatch increments the consecutive failure counter.
2. Once the counter reaches `dispatch_failure_threshold`, dispatch is permanently disabled for the session.
3. One debug message is emitted (visible only at DEBUG log level):
   > `Context intelligence server unreachable after N attempts — dispatch disabled for this session. Local JSONL capture continues.`
4. Subsequent events are silently skipped; local JSONL capture continues unaffected.

### Recovery

Restart the session once the server is back. There is no mid-session auto-recovery. The JSONL files contain a complete record and can be replayed into the server after it recovers.

See [`docs/dispatch-circuit-breaker.dot`](docs/dispatch-circuit-breaker.dot) for the full dispatch flow and circuit breaker state machine.

---

## What gets stored

### Local files (always)

```
<base_path>/<project_slug>/sessions/<session_id>/context-intelligence/
├── events.jsonl    ← one JSON line per event, each tagged with workspace
└── metadata.json   ← session lifecycle record, also tagged with workspace
```

`events.jsonl` record schema — fields in order:

```
event      string   — event name, e.g. "session:start", "tool:call"
workspace  string   — isolation scope (empty string if not configured)
timestamp  string   — ISO 8601 timestamp from event data
data       object   — full sanitized event payload
```

`metadata.json` schema:

```
format      string   — always "context-intelligence"
version     string   — always "1.0.0"
session_id  string
workspace   string   — same value as in events.jsonl
parent_id   string   — empty if root session
started_at  string
status      string   — "running" → "completed" / "failed"
ended_at    string   — set on finalisation
working_dir string
```

Optional fields (present only when set): `agent_name`, `parallel_group_id`, `recipe_name`, `recipe_step`.

### Server-side graph (when server configured)

All graph building, Neo4j writes, and blob management happen in the CI server.
See [`context/graph-model-reference.md`](context/graph-model-reference.md) for the Neo4j graph model.

---

## Agents

| Agent | Tools | Role |
|-------|-------|------|
| `graph-analyst` | `graph_query`, `blob_read`, `tool-filesystem`, `tool-bash`, `tool-skills` | Primary entry point — graph-powered analysis via Cypher, blob resolution |
| `session-navigator` | `tool-filesystem`, `tool-search`, `tool-bash`, `tool-skills` | Local fallback — safe JSONL navigation via bash/jq/grep |

**Delegation chain:** External callers always invoke `graph-analyst`. If the server is unreachable or the workspace contains 0 sessions, it delegates to `session-navigator`, which navigates local JSONL files using safe extraction patterns. `session-navigator` is never invoked directly.

See [`context/safe-extraction-patterns.md`](context/safe-extraction-patterns.md) for JSONL navigation patterns.

---

## Repository structure

```
amplifier-bundle-context-intelligence/
├── bundle.md                           ← root bundle definition
├── agents/
│   ├── graph-analyst.md  ← primary entry point agent
│   └── session-navigator.md      ← local fallback agent
├── context/
│   ├── event-schema.md                 ← all 51+ Amplifier events
│   ├── graph-model-reference.md        ← Neo4j graph model for Cypher queries
│   ├── safe-extraction-patterns.md     ← JSONL navigation patterns
│   ├── config-resolution.dot           ← ConfigResolver fallback chain diagram
│   ├── session-disk-layout.dot         ← on-disk session directory structure
│   ├── delegation-strategy.dot         ← graph-analyst → session-navigator delegation logic
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

```bash
# Module tests
cd modules/hook-context-intelligence
uv sync
uv run pytest tests/ -q

# Bundle-level tests
uv run pytest ../../tests/ -q
```

---

## Related

- [amplifier-context-intelligence](https://github.com/microsoft/amplifier-context-intelligence) — the CI server (Neo4j + blob storage + dashboard)
- [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) — CLI that sends `project_slug` used for workspace resolution
- [amplifier](https://github.com/microsoft/amplifier) — the Amplifier framework
