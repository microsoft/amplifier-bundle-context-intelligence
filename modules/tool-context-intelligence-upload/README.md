# tool-context-intelligence-upload

An Amplifier tool module that replays context-intelligence session events to the [Context Intelligence Server](https://github.com/microsoft/amplifier-context-intelligence). It discovers all `events.jsonl` files under a given path, sorts sessions in BFS topological order (parents before children), and POSTs each event to the server's `/events` endpoint. Because every POST carries a SHA-256–derived `idempotency_key`, re-running the upload against the same data is safe — the server suppresses duplicates. The tool is designed for recovery scenarios: use it whenever the live hook could not reach the server during a session and you need to replay events after the server is back.

---

## Installation

### As an Amplifier module (recommended)

This module is included in the `amplifier-bundle-context-intelligence` bundle. Adding the bundle brings the module into your Amplifier installation.

> **This module is CLI-only.** It ships the standalone `context-intelligence-upload` console script and **no in-session Amplifier tools** — there is no `mount()` and no `amplifier.modules` entry point, so nothing from this module appears inside an Amplifier session. Run it from your shell.

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main
```

### As a standalone CLI

Install as a standalone command-line tool using `uv`:

```bash
# Install as a uv tool (adds context-intelligence-upload to PATH)
uv tool install "amplifier-module-tool-context-intelligence-upload @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-context-intelligence-upload"
```

Or install into the current environment:

```bash
uv pip install "amplifier-module-tool-context-intelligence-upload @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-context-intelligence-upload"
```

After installation, the `context-intelligence-upload` command is available in your shell.

---

## CLI Usage

```
context-intelligence-upload --path PATH --server-url URL --api-key KEY
                             [--job-id ID] [--progress FILE]
```

### Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--path PATH` | **required** | File or folder to replay. If `PATH` is a `metadata.json` file, only that single session is processed. Otherwise, the tool recurses into `PATH` searching for all `metadata.json` files. |
| `--server-url URL` | **required** | Base URL of the Context Intelligence ingestion server (e.g. `https://context-intelligence.example.com`). The `/events` endpoint is appended automatically. |
| `--api-key KEY` | **required** | Bearer token sent in the `Authorization` header for every request. |
| `--job-id ID` | optional | Stable identifier for this upload job. Useful for correlating progress files and log output across retries. A random UUID4 is auto-generated and printed to stderr when omitted. |
| `--progress FILE` | optional | Path to write the progress JSON file. Default: `/tmp/context-intelligence-upload-{job_id}.json` |
| `-h` | — | Show compact help (usage line + flag list) and exit. |
| `--help` | — | Show full documentation and exit. |

### Legacy hooks-logging import (--format logging-hook)

`--format` selects which input schema the tool discovers and ingests. The default, `context-intelligence`, is today's behavior described throughout the rest of this document. `logging-hook` is a separate, additive import path for the legacy `hooks-logging` format.

**What it does.** Discovers legacy `hooks-logging` sessions (schema `{name: "amplifier.log", ver: "1.x"}`) under `--path`, transforms each event **in memory**, and POSTs the transformed events to the **same** `/events` endpoint used by the default path. No new server surface and no new storage format is introduced.

**Non-destructive.** No files are written to, or deleted from, disk during discovery or transformation — the legacy archive on disk is never touched. (This is a deliberate contrast with the older `amplifier-ci-migrate` tool's materialize-to-disk approach; that tool is unaffected, unchanged, and out of scope here.)

**Dedup always on.** The legacy import always uses server-side dedup (`replay=False`), so it is idempotent — an aborted or interrupted run is always safe to rerun. `--no-replay` does not apply to this path: passing `--no-replay` together with `--format logging-hook` fails fast with **exit code 2** before any discovery or upload happens.

**Discrimination.** Sessions are selected by their `metadata.json` format. `--format logging-hook` ingests only legacy sessions; the default `--format context-intelligence` ingests only native sessions. The two paths never cross, even when a legacy `events.jsonl` and a native `context-intelligence/` tree both exist under the same session directory.

**Slug parity with native.** The workspace for a migrated legacy event is derived from the legacy session's `working_dir` using the **same** slugifier the live context-intelligence hook uses (`config_resolver._slugify_path`). Migrated legacy events therefore land in the **exact same workspace** as native captures from the same working directory — the two coexist and dedupe together rather than forking into separate workspaces.

**Exit codes.** `0` — clean (no skipped/unmapped/live-skipped sessions or events). `3` — completed with issues (one or more events were skipped or unmapped, or one or more sessions were live-skipped; see the reconciliation summary printed to stderr). `2` — usage error (e.g. `--no-replay` combined with `--format logging-hook`). This is additive to the default path's exit codes; `--format context-intelligence` never returns `3`.

**Regression coverage.** This path is exercised end-to-end by the standing DTU profile [`context-intelligence-upload-format-validation.yaml`](../../.amplifier/digital-twin-universe/profiles/context-intelligence-upload-format-validation.yaml), which proves isolation, discrimination, slug parity/no-fork, and coexistence/dedup against a real Context Intelligence server.

### Examples

**Replay a single session directory:**

```bash
context-intelligence-upload \
    --path ~/.amplifier/projects/my-project/sessions/abc123/context-intelligence \
    --server-url https://context-intelligence.example.com \
    --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
```

**Replay an entire project tree:**

```bash
context-intelligence-upload \
    --path ~/.amplifier/projects/my-project \
    --server-url https://context-intelligence.example.com \
    --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
```

**Target a recovery server with a custom job ID:**

```bash
context-intelligence-upload \
    --path /data/sessions \
    --server-url https://recovery.example.com \
    --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY \
    --job-id my-retry-job-001
```

### Authentication

By default the tool authenticates with a **static API key** (`--api-key`) — unchanged. It can instead use a **Microsoft Entra bearer token** from your developer `az login` session by selecting `--auth-mode entra`.

| Flag | Required | Description |
|------|----------|-------------|
| `--auth-mode {static,entra}` | optional (default `static`) | `static` sends `--api-key` as the bearer. `entra` obtains a Microsoft Entra bearer token from your `az login` session. |
| `--auth-resource api://<id>` | **required when `--auth-mode entra`** | The Entra audience the token is requested for — `api://<server-app-client-id>`. |

Both can also be supplied via environment variables (both are `${VAR}`-substitutable):

| Env var | Equivalent flag |
|---------|-----------------|
| `AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_MODE` | `--auth-mode` |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_RESOURCE` | `--auth-resource` |

**Replay with an Entra bearer token:**

```bash
context-intelligence-upload --server-url https://ci.example.com \
  --auth-mode entra --auth-resource api://<server-app-client-id>
```

> **Entra mode works both interactively and non-interactively.** It acquires the bearer token via `DefaultAzureCredential`, which walks a credential chain (env-var service principal → managed identity → workload identity → shared cache → `az login`). The same `--auth-mode entra` therefore works for a developer (`az login`) and for a hosted/service caller (managed identity / workload identity / service principal) with no flag change.
>
> **Non-interactive (app-to-app) prerequisite.** The hosted identity must be granted an **application** app-role (e.g. `Contributor`) on the server's Entra App Registration, and the environment must expose a credential `DefaultAzureCredential` can find. A host with *no* Azure identity at all can still use a static `--api-key`.
>
> **Server-side prerequisite.** Entra mode requires the **server** to be configured to validate Entra tokens. Against a server that only accepts static keys, use `--auth-mode static`.

---

---

## Recovery Scenarios

### Server unreachable during a live session

When the live hook cannot reach the server (circuit breaker tripped, network outage), it disables server dispatch for the rest of the session but continues writing local `events.jsonl` files. Once the server is back, replay the events manually:

```bash
context-intelligence-upload \
    --path ~/.amplifier/projects/my-project/sessions/affected-session-id/context-intelligence \
    --server-url https://context-intelligence.example.com \
    --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
```

### Targeting a different server

To replay events to a secondary or recovery server instance — for example, after a server migration or when testing against a staging environment — override `--server-url`:

```bash
context-intelligence-upload \
    --path ~/.amplifier/projects/my-project \
    --server-url https://new-ci-server.example.com \
    --api-key $NEW_SERVER_API_KEY \
    --job-id migration-2026-01-15
```

### Replaying after data loss

If the server loses stored events (database restore, accidental wipe), replay the full local archive to rebuild its state. The idempotency guarantee ensures that events already present on the server are not duplicated:

```bash
context-intelligence-upload \
    --path ~/.amplifier/projects \
    --server-url https://context-intelligence.example.com \
    --api-key $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY \
    --job-id full-replay-after-data-loss
```

Monitor progress separately:

```bash
watch -n2 cat /tmp/context-intelligence-upload-full-replay-after-data-loss.json
```

---

## Idempotency Guarantee

Every event POST carries an `idempotency_key` field built from the event payload using a SHA-256 hash:

```
idempotency_key = "aci-event-v1:" + SHA-256(canonical_json({event, workspace, data}))
```

The key is **deterministic** — the same event data always produces the same key regardless of when or how many times the upload runs. This means:

- **Safe to re-run** — running the upload again against the same `PATH` will not create duplicate records on the server, provided the server uses the `idempotency_key` to deduplicate (which the Context Intelligence Server does).
- **No duplicates** — re-uploading after a partial failure resumes cleanly; events already ingested are ignored by the server.
- **Replay is not deduplication** — the upload tool itself does not track which events have been sent. It always re-uploads every event it finds. Deduplication is enforced server-side using the `idempotency_key`.

---

## Progress File

The upload tool writes a JSON progress file to disk and updates it atomically after every event (write to `.tmp` suffix then `os.replace`). External readers always see a consistent snapshot.

**Running state:**

```json
{
  "job_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "running",
  "started_at": "2026-01-15T10:23:44.123456+00:00",
  "sessions_total": 12,
  "sessions_completed": 7,
  "current_session_id": "sess-abc123",
  "current_session_events_total": 84,
  "current_session_events_sent": 42,
  "failed_at": null
}
```

**Failed state:**

```json
{
  "job_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "failed",
  "started_at": "2026-01-15T10:23:44.123456+00:00",
  "sessions_total": 12,
  "sessions_completed": 7,
  "current_session_id": "sess-abc123",
  "current_session_events_total": 84,
  "current_session_events_sent": 42,
  "failed_at": {
    "session_id": "sess-abc123",
    "event_index": 42,
    "http_status": 503,
    "error": "HTTP 503 from https://context-intelligence.example.com/events"
  }
}
```

**Field reference:**

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string | Identifier for this upload job |
| `status` | string | `"running"` \| `"completed"` \| `"failed"` |
| `started_at` | string | ISO 8601 timestamp when the job started |
| `sessions_total` | int | Total number of sessions to upload |
| `sessions_completed` | int | Number of sessions fully uploaded |
| `current_session_id` | string \| null | Session ID currently being uploaded |
| `current_session_events_total` | int | Total events in the current session |
| `current_session_events_sent` | int | Events sent for the current session so far |
| `failed_at` | object \| null | Failure details, or `null` if not failed |

---

## Workspace Behaviour

The `workspace` field in every uploaded event is read directly from the `events.jsonl` line. The upload tool passes this value unchanged to the server — it is **never overridden** or transformed by the CLI.

This means the workspace written to the server reflects exactly what was captured during the original session. If you replay sessions from two different projects (each with their own workspace), they remain separated on the server without any extra configuration.

```jsonl
{"event":"tool:call","workspace":"my-project","timestamp":"2026-01-15T10:23:44.123Z","data":{...}}
{"event":"tool:result","workspace":"my-project","timestamp":"2026-01-15T10:23:44.456Z","data":{...}}
```

The `workspace` key is part of the event record in `events.jsonl` — the upload tool does not accept a `--workspace` flag and does not read workspace from `metadata.json` or any other source.
