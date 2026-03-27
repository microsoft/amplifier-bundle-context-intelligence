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

## Upload Tools

Two tools are available for replaying captured session events to the graph server. Use them
to push locally-recorded `events.jsonl` files to the graph at any time, independently of the
live capture hook.

| Tool | Parameters | Purpose |
|------|------------|---------|
| `context_intelligence_upload_start` | `path` (required), `server_url` (optional), `api_key` (optional) | Begin an async upload job for a session's `events.jsonl` file. Returns a `job_id` for status polling. |
| `context_intelligence_upload_status` | `job_id` (required) | Poll the progress of a running upload job. Returns status, events processed, and any error details. |

**Configuration resolution inside Amplifier:** `server_url` and `api_key` are optional
parameters. When omitted, they are resolved automatically from the environment variables
`AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` and `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY`
respectively, using the same configuration used by the capture hook.

**Workspace is never a parameter.** The workspace is always read directly from the session's
`events.jsonl` file (stored in the metadata written by the hook). You cannot override it —
this ensures uploads are always attributed to the correct workspace scope.

**Idempotency guarantee.** Upload jobs are idempotent. Re-uploading the same `events.jsonl`
file to the same server will not create duplicate nodes or edges in the graph. The server
uses event IDs to detect and skip already-ingested events, making it safe to re-run uploads
as many times as needed.

**Use cases:**

- **Rebuilding the server graph after connectivity failures** — if the capture hook was unable
  to reach the server during a session (circuit breaker tripped), replay the locally-stored
  `events.jsonl` once connectivity is restored.
- **Targeting a different server** — supply an explicit `server_url` to upload session data
  to a non-default graph server (e.g. a staging environment or a teammate's instance).
- **Replaying after data loss** — if the server lost data (database reset, migration, crash),
  replay all local session files to rebuild the graph from the on-disk record.
