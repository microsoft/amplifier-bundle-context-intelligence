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
| `server.include` (nested config key) | fnmatch workspace patterns permitted to dispatch to the server. When any entry is present, only matching workspaces forward events. When empty — the default — **nothing dispatches** (deny-all posture). Nested under `overrides.hook-context-intelligence.config.server` in `settings.yaml`. Union semantics: `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_INCLUDE` env var values are merged with config values. | `[]` — deny-all |
| `server.exclude` (nested config key) | fnmatch workspace patterns blocked from dispatch. Trims from what `include` opened. No effect when include list is empty. Nested under `overrides.hook-context-intelligence.config.server`. Union semantics: `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_EXCLUDE` env var values are merged with config values. | `[]` |
| `context_intelligence.path_rules` (top-level settings key) | Host-side path rules evaluated by the CLI at session start. Tilde (`~`) is expanded; **all** matching rules contribute. Each matching rule's path pattern is transformed into a workspace name pattern (e.g. `~/work/**` → `-home-<user>-work-*`) and appended to `server.include`. Injected patterns are additive with any patterns already set under `overrides.hook-context-intelligence.config`. Configured at the **top level** of `settings.yaml` (not under `config:`). | `[]` |

## Forwarding semantics (dispatch opt-in model)

Server dispatch is **off by default**. The bundle's `ConfigResolver` evaluates dispatch via the following chain (first match wins):

1. `include` is empty → blocked (deny-all default; must opt in explicitly)
2. Workspace matches none of `include` → blocked (not opted in)
3. Workspace matches any `exclude` pattern → blocked (`exclude` trims from what `include` opened; no effect when include list is empty)
4. Otherwise → **dispatches to server**

Local `events.jsonl` is **always written** regardless of dispatch status.

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
- `server.url` (nested under `server:`)
- `server.api_key` (nested under `server:`)

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
