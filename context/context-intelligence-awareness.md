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

Secrets live in `~/.amplifier/keys.env`, never as literal values in `settings.yaml`. Two patterns are supported:

*Default env var names (no `settings.yaml` override needed):*
```bash
# ~/.amplifier/keys.env
AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL=http://localhost:8000
AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY=<your-api-key>
```

*Custom key name with `settings.yaml` override (using `${...}` interpolation — safe to commit):*
```bash
# ~/.amplifier/keys.env
CONTEXT_INTELLIGENCE_TEAM_SERVER_API_KEY=<your-api-key>
```
```yaml
# ~/.amplifier/settings.yaml
overrides:
  hook-context-intelligence:
    config:
      context_intelligence_api_key: "${CONTEXT_INTELLIGENCE_TEAM_SERVER_API_KEY}"
```

Verify the env vars are exported:
```bash
echo $AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL
echo $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
```

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
