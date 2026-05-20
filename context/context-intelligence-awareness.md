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
| `allow_workspaces` (config key, not env var) | fnmatch workspace patterns permitted to dispatch to the server. When any entry is present, only matching workspaces forward events. When empty — the default — **nothing dispatches** (deny-all posture). Set under `overrides.hook-context-intelligence.config` in `settings.yaml`. | `[]` — deny-all |
| `deny_workspaces` (config key, not env var) | fnmatch workspace patterns blocked from dispatch. Trims from what `allow_workspaces` opened. No effect when allow list is empty. Set under `overrides.hook-context-intelligence.config`. | `[]` |
| `forwarding_enabled` (config key, not env var) | Explicit boolean override. `false` suppresses dispatch regardless of workspace patterns. Normally injected by the CLI's `context_intelligence.path_rules` mechanism rather than set directly. | (absent — deferred to workspace pattern logic) |
| `context_intelligence.path_rules` (top-level settings key) | Host-side path rules evaluated by the CLI at session start. First matching rule wins. When `forwarding_enabled: false` fires, the CLI injects an override that blocks dispatch for that working directory regardless of workspace patterns. Configured at the **top level** of `settings.yaml` (not under `config:`). | `[]` |

## Forwarding semantics (dispatch opt-in model)

Server dispatch is **off by default**. The resolution order is:

1. `forwarding_enabled: false` in config (injected by CLI path rules) → blocked, no further evaluation
2. `allow_workspaces` is empty → blocked (deny-all default; must opt in explicitly)
3. Workspace matches none of `allow_workspaces` → blocked (not opted in)
4. Workspace matches any `deny_workspaces` pattern → blocked (trimmed from allow)
5. Otherwise → **dispatches to server**

Local `events.jsonl` is **always written** regardless of dispatch status.

## Upload Tool

The `context-intelligence-upload` CLI replays session events from disk to the server.
Use it for recovery scenarios: rebuilding server graph after connectivity failures,
replaying interrupted sessions, or targeting a different server.

**Finding connection parameters:**

Check the environment variables:

```bash
echo $AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL
echo $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
```

`AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` is a secret — it must not appear as plain text in any configuration file. Provide it through whatever secret injection mechanism the deployment uses (environment variables set by the runtime, a secrets manager, or equivalent).

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
