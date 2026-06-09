# Context Intelligence — Graph Analysis

You have access to the **context-intelligence** graph layer for analyzing
Amplifier sessions through an event-driven property graph.

## Capabilities

**Graph-powered analysis (when server is running):** Query the property graph
with Cypher via the `graph_query` tool. Resolve large event payloads safely via
the `blob_read` tool — it returns a local file path, never raw content.

**Local JSONL fallback:** When no server is configured or reachable, the
`session-navigator` agent reads session files directly from disk using safe
bash/jq/grep patterns.

## Delegation

Always delegate session analysis to the specialist agents:

| Agent | Purpose |
|-------|---------|
| `context-intelligence:graph-analyst` | Graph queries, delegation tree tracing, blob resolution — **always use this first** |
| `context-intelligence:session-navigator` | Internal fallback only — invoked by graph-analyst when the server is unreachable |

The graph-analyst checks server availability automatically and falls back to
`session-navigator` when the server is unreachable or returns 0 sessions.

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` | Graph server URL for `graph_query` / `blob_read` | (disabled — falls back to JSONL navigation) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` | Bearer token for server API auth. Required when server is configured with auth. | (empty — auth disabled) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE` | Workspace scope for graph queries | (auto from project) |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` | Root directory where session files are stored | `~/.amplifier/projects` |

`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` is honored by both the graph-analyst
and the session-navigator (when navigating JSONL files). Override it when
sessions are stored outside the default Amplifier location.

## Upload Tool

The `context-intelligence-upload` CLI replays session events from disk to the
server. Use it for recovery scenarios: rebuilding the server graph after
connectivity failures, replaying interrupted sessions, or targeting a different
server.

`AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` is a secret — it must not appear as
plain text in any configuration file. Provide it through whatever secret
injection mechanism the deployment uses.

Invoke via the bash tool:

```bash
context-intelligence-upload \
  --path ${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:-~/.amplifier/projects}/my-project \
  --server-url "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL}" \
  --api-key "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY}"
```

Run `context-intelligence-upload --help` for full documentation including the
progress-file schema, ordering behaviour, and idempotency guarantee.
