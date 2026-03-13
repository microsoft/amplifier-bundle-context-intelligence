# Flush Enrichment and Blob Lifting Fixes Design

## Goal

Fix 3 remaining issues found in Neo4j graph smoke testing: orphan Session nodes from empty-label upserts after flush, incorrect token counts from Python `or` truthiness, and missing `finish_reason` because blob offloading runs before handler extraction.

## Background

After the blob store and graph topology work landed, shadow container smoke tests revealed 3 data-quality problems in the Neo4j graph:

1. An orphan `Session` node appears alongside the real `OrchestratorRun` node — same `node_id`, different labels, conflicting properties.
2. `input_tokens` on `AssistantStep` nodes shows `3` (the message count) instead of `~107K` (the real token count from the provider).
3. `finish_reason` is `None` on every step node despite the provider returning `stop_reason: "tool_use"` / `"end_turn"`.

All three trace back to ordering and truthiness bugs in the event processing pipeline.

## Approach

Three targeted fixes in the existing pipeline, each addressing one root cause:

- **neo4j_store.py flush:** Distinguish node-creation upserts (have labels → MERGE) from enrichment upserts (empty labels → MATCH). This eliminates the orphan node.
- **blob_processor.py:** Lift useful subfields from `raw` into the clone *before* replacing `raw` with a blob ref. This makes token counts and finish reason available to downstream handlers.
- **handlers/step.py:** Use explicit `None` checks instead of `or` truthiness, prefer provider token keys over orchestrator message-count keys, and read `finish_reason`/`stop_reason` from the top level (where the blob processor now places them).

## Architecture

The fix spans three layers of the event processing pipeline:

```
Event arrives
    │
    ▼
┌─────────────────────┐
│   blob_processor.py  │  ← Fix 2: lift raw.usage + raw.stop_reason into clone
│                      │     THEN replace raw with blob ref
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  handlers/step.py    │  ← Fix 3: read lifted fields, explicit None checks
│                      │     stores real token counts + finish_reason
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   neo4j_store.py     │  ← Fix 1: empty labels → MATCH (enrichment),
│   flush()            │     non-empty labels → MERGE (creation)
└─────────────────────┘
```

## Components

### Component 1: MATCH for Empty-Label Upserts (neo4j_store.py)

**Location:** `neo4j_store.py`, the `flush()` method, the node grouping/writing section.

**Problem sequence:**

1. `execution:start` → `upsert_node(run_id, {"OrchestratorRun"}, ...)` — buffered with correct label
2. `execution:end` → `upsert_node(run_id, set(), ...)` — merges into buffer (buffer retains OrchestratorRun label)
3. `execution:end` → `flush()` — writes to Neo4j as `MERGE (n:OrchestratorRun {node_id: ...})` ✓
4. `orchestrator:complete` → `upsert_node(run_id, set(), ...)` — buffer is empty post-flush, new entry with empty labels
5. `orchestrator:complete` → `flush()` — empty labels → default `"Session"` → `MERGE (n:Session {node_id: ...})` → **creates orphan**

**Fix:** When a buffered node has empty labels, the flush uses `MATCH` instead of `MERGE`:

```cypher
-- Non-empty labels (creating or ensuring a node exists):
MERGE (n:`OrchestratorRun` {node_id: "..."})
SET n += props

-- Empty labels (enriching an existing node):
MATCH (n {node_id: "..."})
SET n += props
```

Nodes with empty labels go into a separate "enrichment" group that uses `MATCH`. With `MATCH`, Neo4j finds the node by `node_id` regardless of its labels. If the node doesn't exist yet, `MATCH` finds nothing and the enrichment is silently skipped — which is the safe behavior.

**The default `"Session"` fallback for empty labels is removed entirely.** Empty labels = enrichment = MATCH not MERGE.

**Key invariant preserved:** Nodes are always created with labels first (`execution:start` creates with `{"OrchestratorRun"}`, `session:start` creates with `{"Session", "Root"}`, etc.). Enrichment calls with empty labels find the existing node by `node_id`.

### Component 2: Blob Processor Lifts Fields from `raw` (blob_processor.py)

**Location:** `blob_processor.py`, the `process_event_data()` function.

**Change:** Before replacing `raw` with a blob ref, extract useful subfields and merge them into the clone:

1. If `raw` exists and is a dict:
   - Extract `raw.stop_reason` → set `clone["stop_reason"]` (if not already set at top level)
   - Extract `raw.finish_reason` → set `clone["finish_reason"]` (if not already set at top level)
   - Extract `raw.usage` → merge into `clone["usage"]` dict (provider keys like `input_tokens`, `output_tokens`, `cache_read_input_tokens` complement the orchestrator's normalized short keys like `input`, `output`, `cache_read`)
2. THEN replace `raw` with blob ref as before.

The clone the handler receives:

```python
{
    "usage": {
        "input": 3,                         # from orchestrator (message count)
        "output": 146,                      # from orchestrator
        "cache_read": 105205,               # from orchestrator
        "input_tokens": 107421,             # lifted from raw.usage
        "output_tokens": 146,               # lifted from raw.usage
        "cache_read_input_tokens": 105205,  # lifted from raw.usage
    },
    "stop_reason": "tool_use",              # lifted from raw
    "raw": {"$blob_ref": "ci-blob://..."},  # offloaded
    ...
}
```

**Invariants preserved:**

- Original event data is NEVER mutated (deep clone only)
- No fields are removed from the clone
- The blob ref still replaces `raw`
- Lifting only ADDS fields to the clone, never removes

### Component 3: StepHandler Uses Correct Field Names (handlers/step.py)

**Location:** `handlers/step.py`, the `_handle_llm_response` method.

**Change:** With the blob processor now lifting `raw.usage` fields, the StepHandler uses explicit `None` checks and stores both message counts and token counts:

```python
usage = data.get("usage")
if usage and isinstance(usage, dict):
    # Token counts from provider (lifted from raw.usage by blob processor)
    input_tokens = usage.get("input_tokens")
    if input_tokens is not None:
        properties["input_tokens"] = input_tokens
    output_tokens = usage.get("output_tokens")
    if output_tokens is not None:
        properties["output_tokens"] = output_tokens

    # Cached tokens: prefer provider's key, fall back to orchestrator's
    cached = usage.get("cache_read_input_tokens")
    if cached is None:
        cached = usage.get("cache_read")
    if cached is None:
        cached = usage.get("cached_tokens")
    if cached is not None:
        properties["cached_tokens"] = cached

    cache_write = usage.get("cache_creation_input_tokens")
    if cache_write is None:
        cache_write = usage.get("cache_write")
    if cache_write is not None:
        properties["cache_write_tokens"] = cache_write

    reasoning = usage.get("reasoning_tokens")
    if reasoning is None:
        reasoning = usage.get("reasoning")
    if reasoning is not None:
        properties["reasoning_tokens"] = reasoning

    # Message count from orchestrator's normalized key
    message_count = usage.get("input")
    if message_count is not None:
        properties["message_count"] = message_count

# Finish reason: now at top level (lifted from raw by blob processor)
finish_reason = data.get("finish_reason") or data.get("stop_reason")
if finish_reason is not None:
    properties["finish_reason"] = finish_reason
```

Key changes from before:

- Use explicit `is not None` checks, not Python `or` truthiness
- Prefer provider's long-form keys (`input_tokens`) over orchestrator's short keys (`input`)
- Store `usage.input` as `message_count` (separate property), not as `input_tokens`
- `finish_reason` / `stop_reason` found at top level because blob processor lifted them from `raw`

## Data Flow

### Event Sequence (confirmed)

```
prompt:submit           ← opens the run cycle
execution:start         ← orchestrator begins
  [LLM turns + tool calls]
execution:end           ← GUARANTEED on ALL paths (finally block), has response
orchestrator:complete   ← fires on success + error, NOT on CancelledError
prompt:complete         ← app-cli layer, after orchestrator returns
session:end             ← cleanup, FINAL event
```

### Processing Pipeline Per Event

```
1. Raw event arrives from orchestrator
2. blob_processor.process_event_data():
   a. Deep clone the event data
   b. If raw is a dict:
      - Lift raw.stop_reason → clone.stop_reason
      - Lift raw.finish_reason → clone.finish_reason
      - Merge raw.usage → clone.usage
   c. Replace raw with blob ref
   d. Return modified clone
3. Handler receives clone:
   - StepHandler reads clone.usage.input_tokens (real tokens)
   - StepHandler reads clone.stop_reason (finish reason)
   - Calls upsert_node with correct properties
4. neo4j_store buffers the upsert
5. flush() writes to Neo4j:
   - Non-empty labels → MERGE (create/update)
   - Empty labels → MATCH (enrich existing)
```

## Error Handling

- **MATCH with empty labels finds nothing:** If the target node doesn't exist, MATCH is a no-op. The enrichment is silently skipped and a warning is logged. This is safe — you can't enrich something that doesn't exist.
- **Blob processor raw lifting:** If `raw` is not a dict or has no `usage`/`stop_reason`, lifting is skipped silently. No failure path.
- **StepHandler missing fields:** If no token fields are found after lifting, properties simply aren't set — same as today's behavior. No errors thrown.

## Testing Strategy

### Unit Tests

- **neo4j_store.py flush tests:** Upsert with empty labels after flush → MATCH finds existing node, no orphan created. Also: empty-label upsert when node doesn't exist → silent no-op.
- **blob_processor tests:** `process_event_data` with `raw` containing `usage` and `stop_reason` → clone has lifted fields in `usage` dict and top-level `stop_reason`. Original `raw` replaced with blob ref. Original event data unchanged.
- **StepHandler tests:** Update existing tests to use the new field names. Add test for `message_count` stored separately from `input_tokens`. Add test confirming `finish_reason` comes from top-level `stop_reason` (lifted by blob processor).

### Integration Tests

- Full event flow → blob processor lifts from raw → handler gets correct tokens and finish_reason → Neo4j node has real token counts.

### Smoke Test Verification

- Shadow container session → `OrchestratorRun` has `status=complete`, `AssistantStep` has `input_tokens=107K` (not 3), `finish_reason=tool_use`/`end_turn`, no orphan `Session` nodes.

## Files to Modify

| Action | File | Purpose |
|---|---|---|
| MODIFY | `neo4j_store.py` | Empty-labels flush uses MATCH instead of MERGE; remove Session default |
| MODIFY | `blob_processor.py` | Lift `raw.usage`, `raw.stop_reason`, `raw.finish_reason` before offloading |
| MODIFY | `handlers/step.py` | Use explicit None checks, prefer provider keys, store message_count |
| MODIFY | `tests/test_neo4j_store.py` | Test MATCH-for-enrichment behavior |
| MODIFY | `tests/test_blob_processor.py` | Test raw field lifting |
| MODIFY | `tests/test_step_handler.py` | Update token extraction tests |

## Confirmed Non-Issues

- **`graph_forest_name = "-workspace"`** is correct for container smoke tests. The app-cli at `session_runner.py:178` sets `project_slug = get_project_slug()` which slugifies `Path.cwd()`. In a container where CWD is `/workspace`, the slug is `-workspace`. The hook's resolution chain reads this from `coordinator.config.get("project_slug")`.
- **`prompt:complete` attached to Session (not Run)** is correct. It fires after `orchestrator:complete` clears `current_run_id`, so the DefaultHandler correctly falls back to Session as the parent.

## Open Questions

None — all design decisions validated in conversation.
