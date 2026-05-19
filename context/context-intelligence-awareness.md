# Context Intelligence

You have access to the **context-intelligence** bundle for analyzing Amplifier sessions
through an event-driven property graph.

## Capabilities

**Event capture (always active):** The `hook-context-intelligence` hook captures all
session events as structured JSONL and optionally forwards them to a graph server.
Events are stored at:

```
~/.amplifier/projects/{slug}/sessions/{id}/context-intelligence/events.jsonl
~/.amplifier/projects/{slug}/sessions/{id}/context-intelligence/metadata.json
```

**Graph-powered analysis (when server is running):** Query the property graph with
Cypher via the `graph_query` tool. Resolve large event payloads safely via the
`blob_read` tool — it returns a local file path, never raw content.

## Delegation

Always delegate session analysis to the specialist agents:

| Agent | Purpose |
|-------|---------|
| `context-intelligence:graph-analyst` | Graph queries, delegation tree tracing, blob resolution — **always use this first** |
| `context-intelligence:session-navigator` | Internal fallback only — invoked by graph-analyst when server is unreachable |

The graph-analyst checks server availability automatically and falls back to
`session-navigator` when the server is unreachable or returns 0 sessions.

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------| 
| `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` | Graph server ingest URL | (disabled) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` | Bearer token for server API auth. Must match the server's `api_key`. Required when the server is configured with auth (Docker deployments and `context-intelligence-server-init` setups). | (empty — auth disabled) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE` | Workspace scope for graph queries | (auto from project) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL` | Hook logging verbosity | `INFO` |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT` | Server dispatch timeout (seconds) | `30` |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_FAILURE_THRESHOLD` | Circuit breaker trips after N failures | `3` |

## Upload Tool

The `context-intelligence-upload` CLI replays session events from disk to the server.
Use it for recovery scenarios: rebuilding server graph after connectivity failures,
replaying interrupted sessions, or targeting a different server.

**Finding connection parameters:**

Check environment variables first:
```bash
echo $AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL
echo $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
```
If not set, read from your Amplifier bundle config YAML under `hook-context-intelligence.config`:
- `context_intelligence_server_url`
- `context_intelligence_api_key`

**Invoke via bash tool:**
```bash
context-intelligence-upload \
  --path ~/.amplifier/projects/my-project \
  --server-url "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL}" \
  --api-key "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY}"
```

**Monitor a long-running upload** (run in background, check progress file):
```bash
context-intelligence-upload \
  --path ~/.amplifier/projects/my-project \
  --server-url "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL}" \
  --api-key "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY}" \
  --job-id my-recovery-job &

cat /tmp/context-intelligence-upload-my-recovery-job.json
```

Run `context-intelligence-upload --help` for full documentation including progress file schema,
ordering behaviour, and idempotency guarantee.

## Modes

Two opt-in modes extend the always-active capabilities:

| Mode | Activation | Purpose |
|------|-----------|---------|
| `context-intelligence` | `/context-intelligence` | Design workspace for building new CI-aware Amplifier components |
| `bundle-usage` | `/bundle-usage` | Analyse what bundles and their components a session or workspace actually used versus what was declared; surfaces tree-shake, mode-refactor, and config-gap opportunities |

**`/bundle-usage` — delegation:**

When the `bundle-usage` mode is active, delegate to `context-intelligence:bundle-usage-analyst` for all usage and gap analysis requests. The analyst calls the `bundle_usage` tool (three-layer: CI graph signals → local cache inventory → set-arithmetic gap) and writes a structured report.

```
# Example
delegate(agent="context-intelligence:bundle-usage-analyst",
         instruction="Analyse session <id> and produce a usage report.")
```

Requires a configured CI server for the signals layer (see Configuration above). The inventory and gap layers run without a server.
