# tool-context-intelligence-upload

An Amplifier tool module that replays context-intelligence session events to the [Context Intelligence Server](https://github.com/microsoft/amplifier-context-intelligence). It discovers all `events.jsonl` files under a given path, sorts sessions in BFS topological order (parents before children), and POSTs each event to the server's `/events` endpoint. Because every POST carries a SHA-256–derived `idempotency_key`, re-running the upload against the same data is safe — the server suppresses duplicates. The tool is designed for recovery scenarios: use it whenever the live hook could not reach the server during a session and you need to replay events after the server is back.

---

## Installation

### As a standalone CLI (recommended)

Install as a standalone command-line tool using `uv`. **This is the install path that actually puts `context-intelligence-upload` on your `PATH`:**

```bash
# Install as a uv tool (adds context-intelligence-upload to PATH)
uv tool install "amplifier-module-tool-context-intelligence-upload @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-context-intelligence-upload"
```

Or install into the current environment:

```bash
uv pip install "amplifier-module-tool-context-intelligence-upload @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-context-intelligence-upload"
```

After installation, the `context-intelligence-upload` command is available in your shell.

### As an Amplifier module

This module is included in the `amplifier-bundle-context-intelligence` bundle, which brings the in-session context-intelligence hook and query tools into your Amplifier installation:

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main
```

> **This does not install the CLI.** This module is CLI-only — it ships the standalone `context-intelligence-upload` console script and **no in-session Amplifier tools**: there is no `mount()` and no `amplifier.modules` entry point, so `amplifier bundle add` does not place the `context-intelligence-upload` command on your `PATH`. Use the `uv tool install` command above to get the CLI.

---

## CLI Usage

```
context-intelligence-upload [--path PATH] [--server-url URL] [--api-key KEY]
                            [--destination NAME] [--auto-approve|-y]
                            [--format FORMAT] [--job-id ID] [--progress FILE]
```

Every flag is optional. With no flags at all the tool resolves everything from the config you already have — see [Zero-Argument Usage](#zero-argument-usage) below.

### Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--path PATH` | optional | File or folder to replay. If `PATH` is a `metadata.json` file, only that single session is processed; otherwise the tool recurses into `PATH`. **When `--path` is omitted** the tool auto-discovers sessions under `~/.amplifier/projects`. |
| `--server-url URL` | optional | Base URL of the Context Intelligence ingestion server (e.g. `https://context-intelligence.example.com`). The `/events` endpoint is appended automatically. When omitted, resolved from your configured destinations. |
| `--api-key KEY` | optional | Bearer token sent in the `Authorization` header for every request. When omitted, resolved from your configured destinations (including `${VAR}` values backed by `~/.amplifier/keys.env`). |
| `--destination NAME` | optional | Select a configured destination **by name**, with no prompt. Required in non-interactive contexts when two or more destinations are configured. An unknown name is an error listing the valid names. |
| `--auto-approve`, `-y` | optional | Skip the `Proceed? [y/N]` confirmation. Use this in CI/automation. |
| `--format FORMAT` | optional | `context-intelligence` (default) or `logging-hook`. Selects which input schema is discovered and ingested. |
| `--job-id ID` | optional | Stable identifier for this upload job. Useful for correlating progress files and log output across retries. A random UUID4 is auto-generated and printed to stderr when omitted. |
| `--progress FILE` | optional | Path to write the progress JSON file. Default: `/tmp/context-intelligence-upload-{job_id}.json` |
| `-h` | — | Show compact help (usage line + flag list) and exit. |
| `--help` | — | Show full documentation and exit. |

---

## Zero-Argument Usage

The ideal gesture is no arguments at all:

```bash
$ context-intelligence-upload
```

With no flags, the tool resolves the server URL and API key from the destination(s) already configured for the live hook, auto-discovers sessions, applies that destination's include/exclude filters, shows a preview of what will be sent, and asks once before sending anything.

A full interactive run looks like this:

```
$ context-intelligence-upload
Destination: team  (https://context-intelligence.example.com)
Sessions to upload: 12   (~1,840 events)
Filtered out by this destination's include/exclude: 4

Proceed? [y/N] y
[3/12] my-project/abc123  ▕████▌ 45% (96/214)
...
Uploaded 12 sessions / 1,840 events (0 skipped, 4 filtered out) to team in 41s.
```

---

## Where Connection Config Comes From

Connection settings are resolved in this order, first match wins:

1. **Explicit flags** — `--server-url`, `--api-key`, `--auth-mode`, `--auth-resource`.
2. **Environment variables** — `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL`, `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY`, and the `--auth-mode`/`--auth-resource` env pair.
3. **Your configured destinations** — the named `destinations` map the live hook reads, at `overrides.hook-context-intelligence.config.destinations` in `~/.amplifier/settings.yaml`.

**Amplifier home only.** Only `~/.amplifier/settings.yaml` and `~/.amplifier/keys.env` are read. A project-local `./.amplifier/settings.yaml` or `./.amplifier/keys.env` is **not consulted**.

```yaml
# ~/.amplifier/settings.yaml
overrides:
  hook-context-intelligence:
    config:
      destinations:
        team:
          url: https://context-intelligence.example.com
          api_key: "${TEAM_CI_KEY}"
          include:
            - my-project
          exclude:
            - scratch
```

```bash
# ~/.amplifier/keys.env
TEAM_CI_KEY=sk-team-abc123
```

`${VAR}` values in `settings.yaml` are expanded from `~/.amplifier/keys.env` using exactly the same rules the Amplifier CLI uses for the hook: `KEY=VALUE` lines, `#` comments and blank lines ignored, one layer of surrounding quotes stripped, and **a real process environment variable always wins over the `keys.env` value**. A missing `keys.env` is not an error — it simply yields no expansions.

If **no destinations are configured** and no connection flags/env are supplied, the tool exits with **exit code 2** and tells you to configure a destination or pass `--server-url`/`--api-key`. It never silently guesses.

---

## Choosing a Destination

| Situation | Behavior |
|-----------|----------|
| Exactly one destination configured | Used automatically, **no prompt** — there is nothing to disambiguate. |
| Two or more, interactive terminal | A numbered prompt lists each destination's name and URL; you pick one. |
| Two or more, `--destination NAME` given | The named destination is used, no prompt. |
| Two or more, **non-interactive** (not a TTY), no `--destination` | Error listing the valid names, **exit code 2**. The tool never blocks waiting for input in a script. |
| `--destination NAME` names something unconfigured | Error listing the valid names, **exit code 2**. |

A malformed destination entry that you did not select never blocks the run — entries are validated individually.

---

## Session Auto-Discovery

**When `--path` is omitted**, the tool discovers sessions under `~/.amplifier/projects` using the discovery logic of the selected `--format`:

| `--format` | Discovery layout |
|------------|-------------------|
| `context-intelligence` (default) | `~/.amplifier/projects/<project>/sessions/<id>/context-intelligence/` |
| `logging-hook` | `~/.amplifier/projects/<project>/sessions/<id>/` (legacy `events.jsonl` directly in the session folder) |

Passing `--path` still works exactly as before and simply narrows *which files are looked at*.

---

## Destination Filtering

When a destination has `include`/`exclude` patterns configured, they are applied using the same matcher the live hook used at capture time, so replay routing matches capture routing exactly.

**The discriminator is the session's own recorded `working_dir`**, resolved in this order:

1. `metadata["working_dir"]` recorded with the session (both formats record it).
2. For a legacy session with only a workspace slug, an **approximate** working directory is reconstructed from that slug — this is best-effort and documented as approximate, since slugging is not losslessly invertible.
3. Only if no recorded or derivable working directory exists at all, the `--path` value is used as a last-resort approximation.

**`--path` never decides filtering.** It may well point at a backup or a copied archive whose location says nothing about where the session actually ran; matching a destination's patterns against that path would route the wrong way. `--path` scopes discovery; the recorded `working_dir` decides matching.

Filtered sessions are never dropped silently — they are counted and reported as the `filtered-out` number in both the preview and the final summary. If every discovered session is filtered out, that is not an error: the tool exits `0` with `0 uploaded / N filtered-out`.

When you supply raw `--server-url`/`--api-key` (no destination object at all), **no filtering is applied** — you said explicitly where to send, so the tool sends there.

---

## Preview and Confirmation

Before uploading, the tool prints a preview: destination name and URL, session count, approximate total events, and the filtered-out count. Then it asks:

```
Proceed? [y/N]
```

The default is **No** — answering no (or just pressing Enter) aborts with nothing uploaded and exit code `0`.

| Situation | Behavior |
|-----------|----------|
| Interactive terminal | Preview is printed, then `Proceed? [y/N]`. |
| `--auto-approve` / `-y` | Preview is printed, confirmation is skipped, upload proceeds immediately (use in CI). |
| Non-interactive (not a TTY) **without** `--auto-approve` | Error telling you to pass `--auto-approve`, **exit code 2**. The tool never hangs on input and never silently uploads. |

---

## Progress Output

In an interactive terminal, the tool shows **two-level** live progress — an outer counter tracking sessions and an inner bar tracking events within the current session:

```
[3/12] my-project/abc123  ▕████▌ 45% (96/214)
```

Events already present on the server (deduplicated) count as skipped rather than re-sent.

When output is piped or not a TTY, there are no ANSI redraws — you get **one plain line per session** instead.

Either way, the run ends with a **final summary**: destination name and URL, sessions uploaded, events sent, events skipped, filtered-out count, and elapsed duration.

The machine-readable progress JSON file (`--progress`) is written exactly as before, unchanged.

---

## Non-Goals (v1)

- **No project-local config.** Only Amplifier home (`~/.amplifier/settings.yaml`, `~/.amplifier/keys.env`) is read.
- **One destination per run — no fan-out.** Each run targets exactly one selected destination. There is no `--all` flag and no fan-out to several destinations in a single run.

### Legacy hooks-logging import (--format logging-hook)

`--format` selects which input schema the tool discovers and ingests. The default, `context-intelligence`, is today's behavior described throughout the rest of this document. `logging-hook` is a separate, additive import path for the legacy `hooks-logging` format.

**What it does.** Discovers legacy `hooks-logging` sessions (schema `{name: "amplifier.log", ver: "1.x"}`) under `--path`, transforms each event **in memory**, and POSTs the transformed events to the **same** `/events` endpoint used by the default path. No new server surface and no new storage format is introduced.

**Non-destructive.** No files are written to, or deleted from, disk during discovery or transformation — the legacy archive on disk is never touched. (This is a deliberate contrast with the older `amplifier-ci-migrate` tool's materialize-to-disk approach; that tool is unaffected, unchanged, and out of scope here.)

**Dedup always on.** The legacy import always uses server-side dedup (`replay=False`), so it is idempotent — an aborted or interrupted run is always safe to rerun. `--no-replay` does not apply to this path: passing `--no-replay` together with `--format logging-hook` fails fast with **exit code 2** before any discovery or upload happens.

**Discrimination.** Sessions are selected by their `metadata.json` format. `--format logging-hook` ingests only legacy sessions; the default `--format context-intelligence` ingests only native sessions. The two paths never cross, even when a legacy `events.jsonl` and a native `context-intelligence/` tree both exist under the same session directory.

**Slug parity with native.** The workspace for a migrated legacy event is derived from the legacy session's `working_dir` using the **same** slugifier the live context-intelligence hook uses (`config_resolver._slugify_path`). Migrated legacy events therefore land in the **exact same workspace** as native captures from the same working directory — the two coexist and dedupe together rather than forking into separate workspaces.

**Exit codes.** `0` — clean (no skipped/unmapped/live-skipped sessions or events; also the code returned when you decline the confirmation, and when every session is filtered out). `3` — completed with issues (one or more events were skipped or unmapped, or one or more sessions were live-skipped; see the reconciliation summary printed to stderr). `2` — usage error: `--no-replay` combined with `--format logging-hook`, no destinations configured and no connection flags, a non-interactive run with two or more destinations and no `--destination`, an unknown `--destination` name, or a non-interactive run without `--auto-approve`. This is additive to the default path's exit codes; `--format context-intelligence` never returns `3`.

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
