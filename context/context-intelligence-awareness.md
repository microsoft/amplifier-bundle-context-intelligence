# Context Intelligence

You have access to the **context-intelligence** bundle for analyzing Amplifier sessions
through an event-driven property graph.

## Three Composition Modes

Pick the smallest behavior that covers your need. The hook (producer) and the
analysis agents/tools (consumer) are independently composable.

**Logging-only** (`context-intelligence-logging` behavior):
- The `hook-context-intelligence` module **only** — no agents, tools, skills, or design mode.
- Use for pure session instrumentation/telemetry: capture all events as structured
  JSONL (and optionally dispatch them to the graph server) without adding any
  read/query surface to the session.
- This is the **producer** side only. It does NOT include `graph_query`, `blob_read`,
  or the navigation agents — you cannot read events back with this behavior alone.
- Events are stored at:
  ```
  <BASE_PATH>/{slug}/sessions/{id}/context-intelligence/events.jsonl
  <BASE_PATH>/{slug}/sessions/{id}/context-intelligence/metadata.json
  ```
  where `BASE_PATH` defaults to `~/.amplifier/projects`.

**Read/query-only** (`context-intelligence-design` behavior):
- Agents, tools, skills, and design mode — **no event capture hook**.
- Use when composing into apps that need session navigation/query/exploration
  without instrumenting the session itself.
- `graph_query` and `blob_read` read from `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL`
  (env var or `~/.amplifier/settings.yaml`) — no hook required.
- Step down to `context-intelligence-analysis` (no design mode) or
  `context-intelligence-navigation` (local JSONL only) for a narrower surface.

**Full** (`context-intelligence` behavior):
- Composes **both** the design and logging behaviors — read/query capabilities
  AND the `hook-context-intelligence` event-capture module in one drop-in.
- Equivalent to composing `context-intelligence-design` + `context-intelligence-logging`.

## Capabilities

**Graph-powered analysis (when server is running):** Query the property graph with
Cypher via the `graph_query` tool. Resolve large event payloads safely via the
`blob_read` tool — it returns a local file path, never raw content.

**Local JSONL fallback:** When no server is configured or reachable, the
`session-navigator` agent reads session files directly from disk using safe
bash/jq/grep patterns.

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
| `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` | Graph server URL for `graph_query` / `blob_read` | (disabled — falls back to JSONL navigation) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` | Bearer token for server API auth. Required when server is configured with auth. | (empty — auth disabled) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE` | Workspace scope for graph queries | (auto from project) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` | Root directory where session files are stored | `~/.amplifier/projects` |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL` | Hook logging verbosity (full behavior only) | `INFO` |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT` | Server dispatch timeout in seconds (full behavior only) | `30` |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_FAILURE_THRESHOLD` | Circuit breaker threshold (full behavior only) | `3` |

`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` is honored by both the hook (when writing
events) and the agents (when navigating JSONL files). Override it when sessions are
stored outside the default Amplifier location.

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
  --path ${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:-~/.amplifier/projects}/my-project \
  --server-url "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL}" \
  --api-key "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY}"
```

**Monitor a long-running upload** (run in background, check progress file):
```bash
context-intelligence-upload \
  --path ${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:-~/.amplifier/projects}/my-project \
  --server-url "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL}" \
  --api-key "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY}" \
  --job-id my-recovery-job &

cat /tmp/context-intelligence-upload-my-recovery-job.json
```

Run `context-intelligence-upload --help` for full documentation including progress file schema,
ordering behaviour, and idempotency guarantee.
