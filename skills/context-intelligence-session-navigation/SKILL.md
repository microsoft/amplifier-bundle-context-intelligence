---
name: context-intelligence-session-navigation
description: 'Use when extracting session data directly from JSONL files — the baseline path when the graph server is unavailable or when operating outside graph-analyst'
version: 0.2.0
license: MIT
---

# Context Intelligence Session Navigation

This skill covers navigation of flat JSONL session files written by the `LoggingHandler`. These files are the universal baseline — always present regardless of whether graph stores are configured. Use shell tools (`jq`, `grep`, `wc`, `head`) to extract data safely.

For the complete event-by-event field reference, see `context/event-schema.md`.
For ready-to-use jq/grep recipes, see `context/safe-extraction-patterns.md`.

---

## Disk Layout

```
~/.amplifier/projects/{project-slug}/sessions/{session_id}/context-intelligence/
├── events.jsonl      # one JSON object per line, append-only
└── metadata.json     # session metadata, written on start, updated on end
```

- `~/.amplifier/projects/` — default base path (configurable via `config.base_path`)
- `{project-slug}` — derived from the full working directory path (see Project Slug Algorithm below)
- `{session_id}` — unique session identifier (UUID or UUID with agent suffix for child sessions)
- `context-intelligence/` — subdirectory containing the session data files
- `events.jsonl` — append-only log of every event the kernel emits during the session
- `metadata.json` — compact session metadata for quick lookup without parsing the full event log

Example paths:

```
~/.amplifier/projects/-home-user-myapp/sessions/55c8841a-1234-5678-9abc-def012345678/context-intelligence/events.jsonl
~/.amplifier/projects/-home-user-myapp/sessions/55c8841a-1234-5678-9abc-def012345678/context-intelligence/metadata.json
```

---

## Record Format

Each line in `events.jsonl` is a single JSON object with exactly **four** fields, always in this order:

```json
{"event":"tool:pre","workspace":"my-project","timestamp":"2026-03-10T11:13:09.792+00:00","data":{...}}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | string | Event name in `{namespace}:{action}` format |
| `workspace` | string | Workspace scope — always present, empty string `""` when not configured |
| `timestamp` | string | ISO 8601 timestamp of when the event was recorded |
| `data` | object | Raw event payload, exactly as the kernel emitted it |

**Key principle:** No field promotion, no level classification, no payload mutation. What the kernel emits is exactly what gets stored in `data`. The `workspace` field is the only addition the hook makes — it is injected at write time from `ConfigResolver.workspace`.

---

## metadata.json

Written on `session:start` or `session:fork`, updated on `session:end`.

### Required Fields (always present)

| Field | Type | Description |
|-------|------|-------------|
| `format` | string | Schema family identifier, always `"context-intelligence"` |
| `version` | string | Schema version, always `"1.0.0"` |
| `session_id` | string | Unique session identifier |
| `workspace` | string | Workspace scope — same value as in `events.jsonl`, empty string if not configured |
| `parent_id` | string | Parent session identifier (empty string for root sessions) |
| `started_at` | string | ISO 8601 timestamp |
| `status` | string | `"running"` / `"completed"` / `"failed"` / `"cancelled"` |
| `working_dir` | string | Working directory path |

### Optional Fields (omitted when absent — no nulls)

| Field | Type | Description |
|-------|------|-------------|
| `agent_name` | string | Agent name (e.g., `"foundation:explorer"`) |
| `parallel_group_id` | string | Parallel execution group identifier |
| `recipe_name` | string | Recipe name if in recipe context |
| `recipe_step` | string | Recipe step if in recipe context |
| `ended_at` | string | ISO 8601 timestamp (added on `session:end`) |

Optional fields are omitted entirely when absent — no null values, compact JSON.

Example (minimal root session):

```json
{"format":"context-intelligence","version":"1.0.0","session_id":"55c8841a-...","workspace":"my-project","parent_id":"","started_at":"2026-03-10T11:13:09.000+00:00","status":"running","working_dir":"/home/user/myapp"}
```

Example (child session with optional fields):

```json
{"format":"context-intelligence","version":"1.0.0","session_id":"55c8841a-...-foundation:explorer","workspace":"my-project","parent_id":"1cb9e5f5-...","started_at":"2026-03-10T11:13:09.000+00:00","status":"completed","ended_at":"2026-03-10T11:15:42.000+00:00","working_dir":"/home/user/myapp","agent_name":"foundation:explorer","recipe_name":"code-review","recipe_step":"analyze"}
```

---

## Workspace and Project Slug

**Workspace** scopes all event data. It is written into every `events.jsonl` line and `metadata.json`. Two concepts work together:

| Concept | Purpose | Where used |
|---------|---------|------------|
| `project_slug` | Directory name under `~/.amplifier/projects/` | On-disk path |
| `workspace` | Field in every record | Querying and filtering |

By **default** workspace equals `project_slug` (both derived from the working directory). They can differ when workspace is set explicitly via `settings.yaml` or env var — for example, workspace `"my-api"` while project_slug is `"-home-user-myapp"`.

**Consequence for navigation:**
- **Directory-first lookup** — when workspace matches the project_slug, all sessions for that workspace live under `~/.amplifier/projects/{workspace}/sessions/`. This is fast and the common case.
- **Field-based filtering** — when workspace was set explicitly (overriding the slug default), scan across all project directories and filter records by `jq 'select(.workspace == "TARGET")'`.

Always check both: attempt directory lookup first, then fall back to cross-project field scan.

---

## Project Slug Algorithm

The project slug is derived from the **full absolute path** of the working directory:

1. Take the resolved absolute path of `working_dir`
2. Replace every `/` with `-` and every `\` with `-`
3. Remove `:` (Windows drive letters)
4. If the result does not start with `-`, prepend `-`
5. Use `"default"` if the result is empty

Examples:

| Working Directory | Slug |
|-------------------|------|
| `/workspace` | `-workspace` |
| `/home/user/my-api` | `-home-user-my-api` |
| `/home/user/repos/amplifier-core` | `-home-user-repos-amplifier-core` |

> Note: the slug encodes the **full path**, not just the basename. `/home/alice/myapp` and `/home/bob/myapp` get different slugs: `-home-alice-myapp` and `-home-bob-myapp`.

---

## Safe Extraction Discipline

Session event files can contain lines with 100k+ tokens (e.g., `llm:response` with full model output). Four golden rules protect against context overflow:

1. **Never `cat` an events.jsonl file.** A single line can be larger than your entire context window. Always use targeted extraction.

2. **Use `jq -c` for structured queries.** The `-c` flag keeps output compact (one line per result). Always filter to specific fields rather than dumping entire records.

3. **Use `grep -n | cut` for line-targeted extraction.** First find matching line numbers with `grep -n`, then extract specific lines with `sed` or `head`/`tail`. Never pipe raw grep output of event data into your context.

4. **Preview with `wc -l` and `head` before extracting.** Always check file size and peek at the first few lines before running any extraction. This prevents accidentally pulling in massive files.

---

## Common Navigation Patterns

### Resolve workspace to a project directory

```bash
# If workspace == project_slug (common default), sessions are here:
ls ~/.amplifier/projects/{workspace}/sessions/

# If workspace was set explicitly and differs from project_slug,
# find all directories containing sessions tagged with this workspace:
grep -rl '"workspace":"{workspace}"' ~/.amplifier/projects/*/sessions/*/context-intelligence/metadata.json \
  | sed 's|/context-intelligence/metadata.json||'
```

### List all sessions for a workspace

```bash
# Fast path: workspace matches directory name (default case)
for f in ~/.amplifier/projects/my-project/sessions/*/context-intelligence/metadata.json; do
  jq -r '[.session_id, .status, .started_at, .agent_name // "(root)"] | join("\t")' "$f" 2>/dev/null
done | sort -t$'\t' -k3

# Scoped path: workspace set explicitly — scan all projects, filter by field
for f in ~/.amplifier/projects/*/sessions/*/context-intelligence/metadata.json; do
  jq -r 'select(.workspace == "my-project") | [.session_id, .status, .started_at, .agent_name // "(root)"] | join("\t")' "$f" 2>/dev/null
done | sort -t$'\t' -k3
```

### Check what workspace a session belongs to

```bash
jq -r '.workspace' metadata.json
# or from events.jsonl (first line only — safe):
head -1 events.jsonl | jq -r '.workspace'
```

### Filter events by workspace across a project

```bash
# Count events per workspace across all sessions in a project directory:
jq -r '.workspace' ~/.amplifier/projects/-home-user-myapp/sessions/*/context-intelligence/events.jsonl \
  | sort | uniq -c | sort -rn
```

### Find sessions by status within a workspace

```bash
# Within a single project directory:
for f in ~/.amplifier/projects/my-project/sessions/*/context-intelligence/metadata.json; do
  jq -r 'select(.status == "running") | .session_id' "$f" 2>/dev/null
done

# Cross-project, scoped to workspace:
for f in ~/.amplifier/projects/*/sessions/*/context-intelligence/metadata.json; do
  jq -r 'select(.workspace == "my-project" and .status == "running") | .session_id' "$f" 2>/dev/null
done
```

### Event summary for a session

```bash
# Count total events
wc -l < events.jsonl

# Count events by type
jq -c '.event' events.jsonl | sort | uniq -c | sort -rn
```

### Find errors in a session

```bash
# Find error event line numbers only (never output the full lines)
grep -n '"event":"orchestrator:error"\|"event":"tool:error"' events.jsonl | cut -d: -f1
```

### Extract a specific event by line number

```bash
sed -n '42p' events.jsonl | jq -c '{event, workspace, ts: .timestamp}'
```

### Count events by type in a session

```bash
grep -c '"event":"tool:pre"' events.jsonl
```

---

## Event Name Taxonomy

All events follow the `{namespace}:{action}` naming convention:

| Namespace | Events | Description |
|-----------|--------|-------------|
| `session` | `start`, `fork`, `end` | Session lifecycle |
| `prompt` | `submit` | User prompt submission |
| `provider` | `request`, `response` | Provider API calls |
| `llm` | `request`, `response` | LLM inference |
| `tool` | `pre`, `post`, `error` | Tool execution lifecycle |
| `orchestrator` | `start`, `complete`, `error` | Orchestrator run lifecycle |
| `context` | `compaction` | Context window management |
| `cancel` | `requested`, `completed` | Cancellation lifecycle |
| `recipe` | `start`, `step`, `complete`, `approval`, `loop_iteration`, `loop_complete` | Recipe orchestration |
| `delegate` | `agent_spawned`, `agent_completed`, `context_inherited`, `session_resumed` | Agent delegation |

For the complete field reference for each event, see `context/event-schema.md`.
