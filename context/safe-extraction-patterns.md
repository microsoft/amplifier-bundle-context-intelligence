# Safe Extraction Patterns

Ready-to-use `jq` and `grep` recipes for navigating flat JSONL session files. All patterns follow the Safe Extraction Discipline — never `cat`, always preview, always filter.

**Convention:** All examples assume you are in a session directory:

```bash
cd ~/.amplifier/projects/{slug}/sessions/{session_id}/context-intelligence
```

---

## Orientation

Get your bearings before extracting anything.

### Count total events

```bash
wc -l < events.jsonl
```

### Event type distribution

```bash
jq -c '.event' events.jsonl | sort | uniq -c | sort -rn
```

### First and last timestamps

```bash
# First event timestamp
head -1 events.jsonl | jq -r '.timestamp'

# Last event timestamp
tail -1 events.jsonl | jq -r '.timestamp'
```

### Session duration from metadata

```bash
jq -r '[.started_at, .ended_at // "still running"] | join(" → ")' metadata.json
```

---

## Session Metadata

### Read metadata for a session

```bash
jq '.' metadata.json
```

### Find all running sessions in a project

```bash
for f in ~/.amplifier/projects/my-project/sessions/*/context-intelligence/metadata.json; do
  jq -r 'select(.status == "running") | .session_id' "$f" 2>/dev/null
done
```

### Find sessions by agent name

```bash
for f in ~/.amplifier/projects/my-project/sessions/*/context-intelligence/metadata.json; do
  jq -r 'select(.agent_name == "foundation:explorer") | .session_id' "$f" 2>/dev/null
done
```

### List all sessions with status

```bash
for f in ~/.amplifier/projects/my-project/sessions/*/context-intelligence/metadata.json; do
  jq -r '[.session_id, .status, .started_at] | join("\t")' "$f" 2>/dev/null
done | sort -t$'\t' -k3
```

---

## Event Extraction

### Extract events by type

```bash
jq -c 'select(.event == "tool:pre")' events.jsonl | head -20
```

### Extract events matching a pattern

```bash
grep -n '"event":"session:' events.jsonl | cut -d: -f1
```

### Extract a specific field from matching events

```bash
jq -c 'select(.event == "tool:pre") | .data.tool_name' events.jsonl | head -20
```

### Extract a specific line by number

```bash
sed -n '42p' events.jsonl | jq -c '.'
```

### Find all error events

```bash
grep -n '"event":"provider:error"\|"event":"tool:error"' events.jsonl | cut -d: -f1
```

### Extract error details

```bash
jq -c 'select(.event == "provider:error" or .event == "tool:error") | {event: .event, ts: .timestamp, error: .data.error}' events.jsonl | head -20
```

---

## Tracing a Turn

### Find the orchestrator run range

A single user turn typically spans from `prompt:submit` through `orchestrator:complete`. Find the line range:

```bash
# Find prompt:submit lines
grep -n '"event":"prompt:submit"' events.jsonl | cut -d: -f1

# Find orchestrator:complete lines
grep -n '"event":"orchestrator:complete"' events.jsonl | cut -d: -f1
```

Then extract events within that range:

```bash
sed -n '10,25p' events.jsonl | jq -c '{event: .event, ts: .timestamp}'
```

### List all tool calls in a turn

```bash
sed -n '10,25p' events.jsonl | jq -c 'select(.event == "tool:pre") | {tool: .data.tool_name, id: .data.tool_call_id}'
```

### Match tool:pre and tool:post pairs

```bash
# Find a specific tool call's pre and post
CALL_ID="call_abc123"
jq -c "select(.data.tool_call_id == \"$CALL_ID\") | {event: .event, tool: .data.tool_name, ts: .timestamp}" events.jsonl | head -20
```

---

## Session Hierarchy

### Find child sessions

```bash
PARENT_ID="55c8841a-1234-5678-9abc-def012345678"
for f in ~/.amplifier/projects/my-project/sessions/*/context-intelligence/metadata.json; do
  jq -r "select(.parent_id == \"$PARENT_ID\") | [.session_id, .agent_name // \"(root)\", .status] | join(\"\t\")" "$f" 2>/dev/null
done
```

### Build parent-child tree

```bash
for f in ~/.amplifier/projects/my-project/sessions/*/context-intelligence/metadata.json; do
  jq -r '[.parent_id // "(none)", .session_id, .agent_name // "(root)"] | join("\t")' "$f" 2>/dev/null
done | sort
```

### Find the root session for a child

```bash
jq -r '.parent_id' metadata.json
# Then navigate to the parent session directory
```

---

## Performance and Safety

### Preview before extracting

Always check size before running heavy queries:

```bash
# Check file size
wc -l < events.jsonl
ls -lh events.jsonl

# Preview first few events
head -3 events.jsonl | jq -c '{event: .event, ts: .timestamp}'
```

### Stream processing for large files

For very large session files, use streaming `jq` with early exit:

```bash
# First 5 tool calls only
jq -c 'select(.event == "tool:pre") | .data.tool_name' events.jsonl | head -5
```

### Safe grep — always use line numbers and cut

Never pipe raw `grep` output of event data into context. Always extract line numbers first:

```bash
# SAFE: get line numbers first, then extract specific lines
grep -n '"event":"llm:response"' events.jsonl | cut -d: -f1

# Then extract just the fields you need from a specific line
sed -n '15p' events.jsonl | jq -c '{event: .event, model: .data.model, tokens: .data.usage}'
```

### Count without extracting

```bash
# Count tool calls without loading content
grep -c '"event":"tool:pre"' events.jsonl

# Count unique tool names
jq -c 'select(.event == "tool:pre") | .data.tool_name' events.jsonl | sort -u | wc -l
```

---

## Payload Size Awareness

Some events carry payloads that are **megabytes** in size. Before extracting any event, check the payload size table in `@context-intelligence:context/event-schema.md` Part 7.

**Events with potentially huge payloads:**
- `llm:request` / `llm:response` — full conversation history, 100k+ tokens per line
- `provider:request` — full message array sent to the LLM provider
- Any event with a `raw` field — untruncated API request/response data

**Always extract only the fields you need**, never the full event:
```bash
# SAFE: extract only small metadata fields from an llm:response
sed -n '42p' events.jsonl | jq -c '{event, ts: .timestamp, model: .data.model, usage: .data.usage}'

# DANGEROUS: extracting the full event (could be megabytes)
sed -n '42p' events.jsonl | jq '.'
```
