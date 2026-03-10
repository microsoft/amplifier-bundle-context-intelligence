---
name: context-intelligence-session-navigation
description: 'Navigate flat JSONL session files — the universal baseline for session data extraction'
version: 0.1.0
license: MIT
---

# Context Intelligence Session Navigation

This skill covers navigation of flat JSONL session files written by the `LoggingHandler`. These files are the universal baseline — always present regardless of whether graph stores are configured. Use shell tools (`jq`, `grep`, `wc`, `head`) to extract data safely.

For the complete event-by-event field reference, see `context/event-schema.md`.
For ready-to-use jq/grep recipes, see `context/safe-extraction-patterns.md`.

---

## Disk Layout

```
~/.amplifier/projects/{project-slug}/sessions/{session_id}/
├── events.jsonl      # one JSON object per line, append-only
└── metadata.json     # session metadata, written on start, updated on end
```

- `~/.amplifier/projects/` — default base path (configurable via `config.base_path`)
- `{project-slug}` — derived from the working directory (see Project Slug Algorithm below)
- `{session_id}` — unique session identifier (UUID or UUID with agent suffix for child sessions)
- `events.jsonl` — append-only log of every event the kernel emits during the session
- `metadata.json` — compact session metadata for quick lookup without parsing the full event log

Example paths:

```
~/.amplifier/projects/my-project/sessions/55c8841a-1234-5678-9abc-def012345678/events.jsonl
~/.amplifier/projects/my-project/sessions/55c8841a-1234-5678-9abc-def012345678/metadata.json
```

## Record Format

Each line in `events.jsonl` is a single JSON object with exactly three fields:

```json
{"event": "tool:pre", "timestamp": "2026-03-10T11:13:09.792+00:00", "data": {...}}
```

| Field | Type | Description |
|-------|------|-------------|
| `event` | string | Event name in `{namespace}:{action}` format |
| `timestamp` | string | ISO 8601 timestamp of when the event was recorded |
| `data` | object | Raw event payload, exactly as the kernel emitted it |

**Key principle:** No field promotion, no level classification, no payload mutation. What the kernel emits is exactly what gets stored. The raw JSONL is a universal source that can be transformed on read or bulk-imported into graph stores later.

## metadata.json

Written on `session:start` or `session:fork`, updated on `session:end`.

### Required Fields (always present)

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Unique session identifier |
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

Optional fields are omitted entirely when absent — no null values, compact JSON. This keeps `metadata.json` small and fast to parse.

Example (minimal root session):

```json
{"session_id":"55c8841a-...","parent_id":"","started_at":"2026-03-10T11:13:09.000+00:00","status":"running","working_dir":"/home/user/project"}
```

Example (child session with optional fields):

```json
{"session_id":"55c8841a-...-3c3c7d7ed17b4281_foundation:explorer","parent_id":"1cb9e5f5-...","started_at":"2026-03-10T11:13:09.000+00:00","status":"completed","ended_at":"2026-03-10T11:15:42.000+00:00","working_dir":"/home/user/project","agent_name":"foundation:explorer","recipe_name":"code-review","recipe_step":"analyze"}
```

## Safe Extraction Discipline

Session event files can contain lines with 100k+ tokens (e.g., `llm:response` with full model output). Four golden rules protect against context overflow:

1. **Never `cat` an events.jsonl file.** A single line can be larger than your entire context window. Always use targeted extraction.

2. **Use `jq -c` for structured queries.** The `-c` flag keeps output compact (one line per result). Always filter to specific fields rather than dumping entire records.

3. **Use `grep -n | cut` for line-targeted extraction.** First find matching line numbers with `grep -n`, then extract specific lines with `sed` or `head`/`tail`. Never pipe raw grep output of event data into your context.

4. **Preview with `wc -l` and `head` before extracting.** Always check file size and peek at the first few lines before running any extraction. This prevents accidentally pulling in massive files.

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

## Common Navigation Patterns

### List all sessions for a project

```bash
ls ~/.amplifier/projects/my-project/sessions/
```

### Event summary for a session

```bash
# Count total events
wc -l < events.jsonl

# Count events by type
jq -c '.event' events.jsonl | sort | uniq -c | sort -rn
```

### Find sessions by status

```bash
# Find all running sessions
for f in ~/.amplifier/projects/my-project/sessions/*/metadata.json; do
  jq -r 'select(.status == "running") | .session_id' "$f" 2>/dev/null
done
```

### Count events by type in a session

```bash
grep -c '"event":"tool:pre"' events.jsonl
```

### Find errors in a session

```bash
# Find error events
grep -n '"event":"orchestrator:error"\|"event":"tool:error"' events.jsonl | cut -d: -f1
```

### Extract a specific event by line number

```bash
sed -n '42p' events.jsonl | jq -c '.event, .timestamp'
```

## Project Slug Algorithm

The project slug is derived from the working directory in 5 steps:

1. Take the `working_dir` from the session's start event
2. Resolve to an absolute path (expand `~`, resolve symlinks)
3. Extract the final path component (basename)
4. Convert to lowercase
5. Replace non-alphanumeric characters (except hyphens) with hyphens, collapse consecutive hyphens, strip leading/trailing hyphens

Examples:

| Working Directory | Slug |
|-------------------|------|
| `/home/user/My Project` | `my-project` |
| `/home/user/amplifier-core` | `amplifier-core` |
| `/home/user/UPPER_CASE_DIR` | `upper-case-dir` |
