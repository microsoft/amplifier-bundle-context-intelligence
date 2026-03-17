# Session Storage Deep Knowledge — Context-Intelligence

## CRITICAL: Handling Large Session Files

Session event logs (`events.jsonl`) can be **extremely large** (67MB+) with individual lines containing **100k+ tokens**. This is because each event can include the entire conversation history up to that point.

**Standard tools WILL FAIL. You must use surgical extraction patterns.**

> **Context-intelligence note:** This bundle writes files to a `context-intelligence/` subdirectory
> inside each session folder — NOT directly in the session root. Always navigate to the correct
> subdirectory before running extraction commands.

## Context-Intelligence Storage Path

The `LoggingHandler` writes to a `context-intelligence/` subdirectory:

```
~/.amplifier/projects/
└── {project-slug}/
    └── sessions/
        ├── {session-id-a}/               ← session root (foundation's files here)
        │   ├── metadata.json             ← foundation metadata (different schema)
        │   ├── transcript.jsonl          ← foundation transcript
        │   └── context-intelligence/     ← CI subdirectory (THIS bundle)
        │       ├── metadata.json         ← LoggingHandler metadata (see schema below)
        │       └── events.jsonl          ← flat JSONL event log (can be 67MB+)
        └── {session-id-b}/
            └── context-intelligence/
                ├── metadata.json
                └── events.jsonl
```

**Always `cd` to the `context-intelligence/` subdirectory before extraction:**

```bash
cd ~/.amplifier/projects/{slug}/sessions/{session_id}/context-intelligence
```

## File Size Danger Zones

| File | Typical Size | Line Size | Safe to Read? |
|------|-------------|-----------|---------------|
| `context-intelligence/metadata.json` | <1KB | Small | **Yes — always safe** |
| `context-intelligence/events.jsonl` | 10-100MB+ | **HUGE** | Never read full lines |

### Why Lines Are Huge

Each `llm:request` and `llm:response` event in `events.jsonl` contains:
- Full conversation history (all previous messages)
- All loaded context files
- System instructions
- Tool definitions

A single line can easily be 100k-200k tokens.

## Safe Extraction Discipline

### The 4 Golden Rules

1. **Metadata first** — always read `metadata.json` before touching `events.jsonl`
2. **Line numbers only** — use `grep -n ... | cut -d: -f1`, never grep output directly
3. **Surgical jq** — extract only small named fields, never `.data` on llm events
4. **Character limits** — when content is unavoidable, `cut -c1-500` or `jq '.field[:200]'`

### NEVER DO THIS on events.jsonl

```bash
# WILL FAIL — outputs entire lines (100k+ tokens each)
grep "pattern" events.jsonl
cat events.jsonl
read_file events.jsonl

# WILL FAIL — even with head, grep outputs full matching lines first
grep "error" events.jsonl | head -5

# WILL FAIL — raw jq on llm events outputs the entire conversation history
jq '.data' events.jsonl
```

### ALWAYS DO THIS Instead

**Step 1: Get metadata first (always safe)**

```bash
cat metadata.json                     # Full file — always small
wc -l events.jsonl                    # How many events?
ls -lh events.jsonl                   # File size warning check
head -c 500 events.jsonl              # First 500 chars only
```

**Step 2: Extract line numbers only (no content)**

```bash
# Get line numbers where pattern matches, NOT the lines themselves
grep -n "pattern" events.jsonl | cut -d: -f1 | head -10

# Count event types safely
grep -c '"event":"llm:response"' events.jsonl
```

**Step 3: Surgical jq extraction**

```bash
# Extract ONLY small fields, never full data
jq -c '{event: .event, ts: .timestamp}' events.jsonl | head -20

# For LLM events, extract metadata only — never .data directly
jq -c 'select(.event | startswith("llm:")) | {event: .event, ts: .timestamp}' events.jsonl

# Extract from specific line (get line first, then parse small fields)
sed -n '123p' events.jsonl | jq '{event: .event, ts: .timestamp}'
```

**Step 4: Character-limited extraction**

```bash
# Get first N characters of a specific line
sed -n '123p' events.jsonl | cut -c1-500

# Get specific field with length limit
sed -n '123p' events.jsonl | jq -r '.event'
```

## Event Data Size by Type

| Event Type | Data Size | Safe Fields |
|------------|-----------|-------------|
| `session:start` | Small | All fields safe |
| `session:fork` | Small | All fields safe |
| `session:end` | Small | All fields safe |
| `prompt:submit` | Medium | `event`, `ts` only |
| `llm:request` | **HUGE** | `event`, `timestamp` only — never `.data` |
| `llm:response` | **HUGE** | `event`, `timestamp` only — never `.data` |
| `tool:pre` | Variable | `event`, `timestamp`, `data.tool_name` |
| `tool:post` | Variable | `event`, `timestamp`, `data.tool_name`, `data.duration_ms` |
| `task:agent_spawned` | Medium | Most fields safe |
| `task:completed` | Medium | Most fields safe |
| `orchestrator:complete` | Small | All fields safe |

> **Note:** The `events.jsonl` record format is `{"event": ..., "timestamp": ..., "data": {...}}`.
> The `timestamp` field is at the top level. The `data` object is raw kernel payload — it CAN BE
> HUGE for `llm:*` events and MUST NOT be read directly.

## Provider-Specific Field Names

When using surgical jq to extract LLM event fields from `data`:

| Provider | Messages Field | System Field |
|----------|---------------|--------------|
| Anthropic | `data.params.messages` | `data.params.system` |
| OpenAI | `data.params.input` | `data.params.instructions` |
| Azure OpenAI | Same as OpenAI | Same as OpenAI |

> These fields contain full conversation history — use character limits if accessing them.

## Context-Intelligence metadata.json Structure

Written by `LoggingHandler` on `session:start` / `session:fork`, updated on `session:end`.

**Complete example (child session with all optional fields):**

```json
{
  "session_id": "55c8841a-1234-5678-9abc-def012345678",
  "parent_id": "1cb9e5f5-0000-1111-2222-333344445555",
  "started_at": "2026-03-10T11:13:09.000+00:00",
  "status": "completed",
  "ended_at": "2026-03-10T11:15:42.000+00:00",
  "working_dir": "/home/user/project",
  "agent_name": "foundation:explorer",
  "parallel_group_id": "a3f2b1c4-abcd-ef01-2345-678901234567",
  "recipe_name": "code-review",
  "recipe_step": "analyze"
}
```

**Field reference:**

| Field | Always Present | Description |
|-------|---------------|-------------|
| `session_id` | Yes | Unique session identifier |
| `parent_id` | Yes | Parent session identifier (empty string for root sessions) |
| `started_at` | Yes | ISO 8601 timestamp when session started |
| `status` | Yes | `"running"` / `"completed"` / `"failed"` / `"cancelled"` |
| `working_dir` | Yes | Working directory path at session start |
| `agent_name` | No | Agent name (e.g., `"foundation:explorer"`) — omitted when absent |
| `parallel_group_id` | No | Parallel execution group ID — omitted when absent |
| `recipe_name` | No | Recipe name if running in recipe context — omitted when absent |
| `recipe_step` | No | Recipe step if running in recipe context — omitted when absent |
| `ended_at` | No | ISO 8601 timestamp — added on `session:end` only |

**Key behaviors:**
- Optional fields are **omitted entirely** when not present — no null values, compact JSON
- `parent_id` is an empty string `""` for root sessions (not null)
- `status` starts as `"running"` and is updated to the final status on `session:end`
- File is written with `json.dumps(metadata, separators=(",", ":"))` — no whitespace

**Minimal root session example:**

```json
{"session_id":"55c8841a-1234-5678-9abc-def012345678","parent_id":"","started_at":"2026-03-10T11:13:09.000+00:00","status":"running","working_dir":"/home/user/project"}
```

## Skill Routing

Context-intelligence skills cover two complementary approaches for working with session data.

**Available skills:**

- `context-intelligence-graph-query` — Cypher query patterns for the property graph via the `graph_query` tool; requires the context-intelligence server to be configured
- `context-intelligence-session-navigation` — flat JSONL session file navigation via bash/jq/grep; always available without any server dependency

**Routing table:**

| Task | Skill | Availability |
|------|-------|--------------|
| Graph traversal / Cypher queries | `context-intelligence-graph-query` | Requires server |
| Local JSONL extraction / search | `context-intelligence-session-navigation` | Always works |

## Key Principle

**Always extract metadata first, content never (or surgically).**

The pattern is:

1. Read `metadata.json` → always safe, always small
2. Get line numbers → `grep -n ... | cut -d: -f1`
3. Count events → `grep -c "pattern"`
4. If you MUST see content → `cut -c1-500` or `jq '.field[:200]'`

Never read `events.jsonl` lines directly. Never pipe `grep` output of event data into context.
The `context-intelligence/` subdirectory is this bundle's territory — the parent session directory
contains different files managed by foundation.
