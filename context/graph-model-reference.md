# Graph Model Reference

> **Provenance:** Derived from the Context Intelligence server implementation —
> `utils.py` (`make_node_id`), `neo4j_store.py` (flush/MERGE/workspace logic), and
> the handler modules (`handlers/session.py`, `handlers/orchestrator_run.py`,
> `handlers/step.py`, `handlers/tool_execution.py`, `handlers/default.py`).

---

## Node Types

The following node types capture the full lifecycle of an Amplifier session.

### Session

**node_id:** raw session UUID (e.g. `f881e0a0-c055-4ee4-84ed-ff44703150ea`).

| Property | Type | Description |
|---|---|---|
| `node_id` | string | Raw UUID — MERGE key with `workspace`. |
| `workspace` | string | Workspace scope — auto-injected by `neo4j_store.flush()`. |
| `status` | string | `running` \| `completed` \| `error` |
| `started_at` | string | ISO 8601 (set on `session:start`). |
| `ended_at` | string \| null | ISO 8601 (set on `session:end`). |
| `metadata` | string | JSON string — raw metadata dict serialized by the store. |
| `data` | string | Full `session:start` payload as JSON string. |

**Sub-labels:** `:Root` (no parent), `:Subsession` (has `parent_id`),
`:ForkedSession` (from `session:fork`; also carries `:Subsession`).

### OrchestratorRun

One prompt→response cycle. **node_id:** `make_node_id(session_id, "execution:start", ts)`

| Property | Type | Description |
|---|---|---|
| `node_id` | string | `{session_id}__execution_start__{epoch_ms}` |
| `workspace` | string | Workspace scope. |
| `session_id` | string | Owning session UUID. |
| `started_at` | string | ISO 8601 from `execution:start`. |
| `ended_at` | string \| null | ISO 8601 from `orchestrator:complete`. |
| `execution_ended_at` | string \| null | ISO 8601 from `execution:end`. |
| `status` | string | `in_progress` \| `complete` \| `cancelled` \| `error` |
| `prompt_preview` | string | First 200 chars of the submitted prompt. |
| `response_preview` | string \| null | First 200 chars of the response. |
| `turn_count` | integer \| null | From `orchestrator:complete`. |

### Step

One LLM interaction. **node_id:** `make_node_id(session_id, "prompt:submit"|"provider:request", ts)`

Sub-labels: `:PromptStep` (`iteration`=0, created on `prompt:submit`);
`:AssistantStep` (`iteration`≥1, created on `provider:request`);
`:RecipeStep` (from `recipe:step`, `recipe:loop_iteration`, `recipe:approval`).

`:RecipeStep` nodes carry additional sub-labels based on the event type:
- `:RecipeStep:RecipeLoopIteration` — from `recipe:loop_iteration`; adds `step_id`, `max_iterations`, `iteration`.
- `:RecipeStep:RecipeApproval` — from `recipe:approval`; adds `stage_name`, `current_step`, `approval_prompt`.

| Property | Type | Description |
|---|---|---|
| `node_id` | string | Composite ID — see Node ID Format. |
| `workspace` | string | Workspace scope. |
| `session_id` | string | Owning session UUID. |
| `iteration` | integer \| null | 0 = PromptStep; 1+ = AssistantStep. |
| `provider` | string \| null | Provider identifier (from `provider:request`). |
| `model` | string \| null | Model identifier (from `llm:request`). |
| `request_at` | string \| null | ISO 8601 of the provider request. |
| `response_at` | string \| null | ISO 8601 of the LLM response. |
| `input_tokens` | integer \| null | Provider input tokens (NOT message count). |
| `output_tokens` | integer \| null | Provider output tokens. |
| `cached_tokens` | integer \| null | Cache-read tokens. |
| `cache_write_tokens` | integer \| null | Cache-write tokens. |
| `reasoning_tokens` | integer \| null | Reasoning tokens. |
| `message_count` | integer \| null | Orchestrator message count (from `usage.input`). |
| `finish_reason` | string \| null | LLM finish/stop reason. |
| `prompt_text` | string \| null | Full prompt text (PromptStep only). |
| `prompt_preview` | string \| null | First 200 chars of prompt (PromptStep only). |

### ToolExecution

One tool call and result. **node_id:** `make_node_id(session_id, "tool:pre", ts, disambiguator=tool_call_id)`

Sub-label: `:Delegation` added on `delegate:agent_spawned`; adds `child_session_id` and `child_agent`.

| Property | Type | Description |
|---|---|---|
| `node_id` | string | `{session_id}__tool_pre__{epoch_ms}__{tool_call_id}` |
| `workspace` | string | Workspace scope. |
| `session_id` | string | Owning session UUID. |
| `tool_call_id` | string | Provider-assigned tool call ID. |
| `tool_name` | string | Name of the tool (e.g. `delegate`, `bash`). |
| `parallel_group_id` | string | Parallel group ID (empty if not parallel). |
| `started_at` | string | ISO 8601 from `tool:pre`. |
| `ended_at` | string \| null | ISO 8601 from `tool:post` or `tool:error`. |
| `status` | string | `executing` \| `complete` \| `error` |
| `result_preview` | string \| null | First 500 chars of result. |
| `tool_input_preview` | string \| null | First 500 chars of input. |
| `child_session_id` | string \| null | Child UUID (`:Delegation` only). |
| `child_agent` | string \| null | Agent identifier (`:Delegation` only). |

### Event

Catch-all for events not handled by entity handlers. **node_id:** `make_node_id(session_id, event_name, ts)`

Sub-labels: derived via `DefaultHandler.derive_label()` — split on `:` and `_`, PascalCase joined.
Example: `session:resume` → `:Event:SessionResume`.

| Property | Type | Description |
|---|---|---|
| `node_id` | string | Composite ID. |
| `workspace` | string | Workspace scope. |
| `event_name` | string | Raw event name (e.g. `session:resume`). |
| `occurred_at` | string | ISO 8601 event timestamp. |
| `data` | string | Full event payload as JSON string. |

### RecipeRun

One recipe execution. Stubbed on the first `recipe:*` event; enriched on `recipe:complete`.

**node_id:** `{session_id}__recipe_run__{epoch_ms}`

| Property | Type | Description |
|---|---|---|
| `node_id` | string | `{session_id}__recipe_run__{epoch_ms}` |
| `workspace` | string | Workspace scope. |
| `session_id` | string | Owning session UUID. |
| `status` | string | `running` \| `complete` \| `failed` |
| `started_at` | string | ISO 8601 from first `recipe:*` event. |
| `ended_at` | string \| null | ISO 8601 from `recipe:complete` (null until complete). |
| `recipe_name` | string \| null | Recipe name (null until `recipe:complete`). |
| `total_steps` | integer \| null | Total step count (null until `recipe:complete`). |
| `success` | boolean \| null | Whether recipe succeeded (null until `recipe:complete`). |

---

## Edge Types

| Edge | From → To | Description |
|---|---|---|
| `SUBSESSION_OF` | `:Session` → `:Session` | Child/forked session → parent. On `session:start`/`session:fork` with `parent_id`. |
| `HAS_RUN` | `:Session` → `:OrchestratorRun` | Session owns each run. On `execution:start`. |
| `HAS_STEP` | `:OrchestratorRun` → `:Step` | Run owns each step (`:PromptStep, :AssistantStep, and :RecipeStep variants`). |
| `NEXT` | `:Step` → `:Step` | Links consecutive steps. The NEXT chain is the only ordering — no `seq` on edges. |
| `TRIGGERED` | `:Step` → `:ToolExecution` | AssistantStep triggered this tool call. On `tool:pre`. |
| `PARALLEL_WITH` | `:ToolExecution` → `:ToolExecution` | Same `parallel_group_id`. Directed but symmetric in intent. |
| `SPAWNED` | `:ToolExecution:Delegation` → `:Session` | Delegation spawned this child session. On `delegate:agent_spawned`. |
| `HAS_EVENT` | `:OrchestratorRun` \| `:Session` → `:Event` | Active run (or session if no run) → Event. Never attaches to `:Step`. |
| `HAS_RECIPE_RUN` | `:Session` → `:RecipeRun` | Session owns each recipe run. Written once on first `recipe:*` event. |
| `SPANS_RUN` | `:RecipeRun` → `:OrchestratorRun` | Non-owning reference linking a recipe run to the orchestrator runs it spans. Deduplicated. |

---

## Node ID Format

| Node Type | Format |
|---|---|
| `Session` | Raw UUID — `f881e0a0-c055-4ee4-84ed-ff44703150ea` |
| All others | `{session_id}__{safe_event}__{epoch_ms}` |
| `ToolExecution` | `{session_id}__{safe_event}__{epoch_ms}__{tool_call_id}` |

`safe_event` = event name with `:` → `_` (e.g. `execution:start` → `execution_start`).
`epoch_ms` = Unix epoch ms from the ISO 8601 timestamp. Separator is `__` (double underscore) — never `:`.

```
# OrchestratorRun
f881e0a0-c055-4ee4-84ed-ff44703150ea__execution_start__1742018532000

# ToolExecution (with tool_call_id disambiguator)
f881e0a0-c055-4ee4-84ed-ff44703150ea__tool_pre__1742018545123__call_abc123
```

---

## Workspace Scoping

All nodes carry `workspace` injected by `neo4j_store.flush()`. MERGE key is `{node_id, workspace}`.
The `graph_query` tool auto-injects `$workspace`. Reference it in every MATCH:

```cypher
MATCH (s:Session {workspace: $workspace})
RETURN s.node_id, s.status, s.started_at
ORDER BY s.started_at DESC
LIMIT 50
```

---

## Critical Gotchas

1. **`metadata` is a JSON string.** The `metadata` dict on Session nodes is serialized
   to a JSON string by `neo4j_store._sanitize_properties`. Parse it in application code
   or via `apoc.convert.fromJsonMap()` — it is not a native Neo4j map property.

2. **Three events are silently dropped.** `context:compaction`, `cancel:requested`, and
   `cancel:completed` are claimed by `SystemEventHandler` and produce no graph nodes.
   These events never appear in Event queries.

3. **No `seq` ordering on edges.** NEXT edges carry no sequence number. Step ordering
   is encoded only in the NEXT chain topology — traverse `[:NEXT*]` to reconstruct
   order; do not rely on edge properties for sequence.

4. **Workspace scoping is manual.** `$workspace` is injected by the `graph_query` tool
   but only filters if you write `{workspace: $workspace}` in your MATCH patterns.
   Omitting it silently returns data from all workspaces.

5. **`HAS_EVENT` never attaches to `:Step`.** The `DefaultHandler` attaches Event nodes
   to the active `OrchestratorRun` (if one exists) or directly to `Session`. Step nodes
   are never the source of a `HAS_EVENT` edge.

6. **MERGE key is `{node_id, workspace}`.** Session nodes use the raw UUID as `node_id`;
   others use the `__`-separated composite. Querying by `node_id` alone without
   `workspace` may match nodes from other workspaces.

7. **RecipeRun stub properties are null until `recipe:complete`.** `ended_at`,
   `recipe_name`, `total_steps`, and `success` are only set when `recipe:complete` fires.
   If a session ends before the recipe completes, the RecipeRun node has `status="running"`
   with all four properties as null. Use `RecipeStep.occurred_at` min/max as a duration
   fallback when `ended_at` is null.

---

## Blob References

Large payloads (tool results, prompts, LLM responses) are stored in blob storage, not
inline in the graph. Nodes carry preview properties; full content lives at a `ci-blob://` URI.

**URI scheme:** `ci-blob://{session_id}/{key}`

Use the `blob_read` tool to retrieve content — it writes to a local file and returns the path:

```
blob_read(uri="ci-blob://f881e0a0-c055-4ee4-84ed-ff44703150ea/events.jsonl")
```

**⚠️ Warning:** Blob files can contain lines with 100k+ tokens. Never open them with
`read_file`. Extract via shell tools only:

```bash
jq '.data.prompt' /path/to/blob.jsonl | head -20
head -5 /path/to/blob.jsonl
```
