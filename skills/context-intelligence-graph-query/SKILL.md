---
name: context-intelligence-graph-query
version: 1.0.0
description: Cypher query patterns for the context-intelligence graph store via graph_query tool
license: MIT
---

# Context Intelligence Graph Query (Cypher Dialect)

This skill teaches how to query the context-intelligence property graph using
the `graph_query` tool. All structural traversal — sessions, runs, steps, tool
executions, delegations — is done through Cypher queries executed via the
`graph_query` tool.

Query patterns for searching and traversing the context-intelligence graph.
Covers workspace scoping, structural traversal, delegation chains, step
sequencing, and graph algorithm patterns using native Cypher.

---

## When to Use Graph vs File Patterns

Choose the right approach based on what you need to find:

| Query Type | Tool | Example |
|-----------|------|---------|
| Structural navigation (sessions, runs, steps, delegations) | `graph_query` | "Find all runs in this session" |
| Relationship traversal (parent-child, SPAWNED, SUBSESSION_OF) | `graph_query` | "Find all child sessions" |
| Session statistics and aggregations | `graph_query` | "Count tool executions by tool name" |
| Prompt text keyword search | `bash`+`grep` or `graph_query` | "Find prompts containing 'authentication'" |
| Large payload inspection (messages, results) | `bash`+`jq` after `blob_read` | "Read tool result JSON" |
| Event log text search across sessions | `bash`+`grep` on events.jsonl | "Find all sessions with a specific error" |

**Fallback guidance:** If `graph_query` returns no results, fall back to
`bash`+`grep`/`jq` on the raw events.jsonl file — the graph may not have
been populated yet for in-progress sessions.

---

## Schema Reference — Data Layer 1

> **Scope:** This section describes **Data Layer 1** — the only schema that is actually
> implemented and queryable today. See the [Data Layer 2 Warning](#data-layer-2-warning)
> section before writing any Cypher queries.

### Node Types

Data Layer 1 contains exactly **three** node types.

| Node Label | Sub-labels | Description |
|---|---|---|
| `:Session` | `:RootSession` — no parent; `:ForkedSession` — spawned via `session:fork` | One Amplifier session. MERGE key: `{node_id, workspace}`. |
| `:ToolCall` | _(none)_ | One tool invocation lifecycle (pre → post/error). Created by `ToolCallHandler` on `tool:pre`. |
| `:Event` | `:{Category}Event`, `:{Specific}Event` — see Triple-Label Rule below | Every event that reaches `DefaultHandler`. Triple-labeled. |

---

### Edge Types

Data Layer 1 contains exactly **three** edge types.

| Edge | From → To | When Created |
|---|---|---|
| `HAS_FORK` | `:Session` → `:Session` | On `session:fork` — parent session → forked child. |
| `HAS_TOOL_CALL` | `:Session` → `:ToolCall` | On `tool:pre` — session owns the tool call lifecycle node. |
| `HAS_EVENT` | `:Session` → `:Event` | On every `DefaultHandler` event — session owns the event node. |
| `HAS_EVENT` | `:ToolCall` → `:Event` | On `tool:pre`, `tool:post`, `tool:error` — tool call owns each lifecycle event. |

---

### Event Triple-Label Rule

Every `Event` node carries exactly **three** labels derived from the raw event name
by `DefaultHandler.derive_labels()`:

1. **Base label** — always `:Event`
2. **Category label** — `:{Category}Event` (prefix before the last `:`, PascalCased)
3. **Specific label** — `:{Full}Event` (all parts split on `:` and `_`, PascalCased, `Event` suffix)

The full table of 24 known event types:

| Event Name | Category Label | Specific Label |
|---|---|---|
| `session:start` | `:SessionEvent` | `:SessionStartEvent` |
| `session:fork` | `:SessionEvent` | `:SessionForkEvent` |
| `session:end` | `:SessionEvent` | `:SessionEndEvent` |
| `session:resume` | `:SessionEvent` | `:SessionResumeEvent` |
| `execution:start` | `:ExecutionEvent` | `:ExecutionStartEvent` |
| `execution:end` | `:ExecutionEvent` | `:ExecutionEndEvent` |
| `orchestrator:complete` | `:OrchestratorEvent` | `:OrchestratorCompleteEvent` |
| `prompt:submit` | `:PromptEvent` | `:PromptSubmitEvent` |
| `prompt:complete` | `:PromptEvent` | `:PromptCompleteEvent` |
| `provider:request` | `:ProviderEvent` | `:ProviderRequestEvent` |
| `provider:response` | `:ProviderEvent` | `:ProviderResponseEvent` |
| `llm:request` | `:LlmEvent` | `:LlmRequestEvent` |
| `llm:response` | `:LlmEvent` | `:LlmResponseEvent` |
| `tool:pre` | `:ToolEvent` | `:ToolPreEvent` |
| `tool:post` | `:ToolEvent` | `:ToolPostEvent` |
| `tool:error` | `:ToolEvent` | `:ToolErrorEvent` |
| `delegate:start` | `:DelegateEvent` | `:DelegateStartEvent` |
| `delegate:agent_spawned` | `:DelegateEvent` | `:DelegateAgentSpawnedEvent` |
| `delegate:complete` | `:DelegateEvent` | `:DelegateCompleteEvent` |
| `recipe:start` | `:RecipeEvent` | `:RecipeStartEvent` |
| `recipe:step` | `:RecipeEvent` | `:RecipeStepEvent` |
| `recipe:complete` | `:RecipeEvent` | `:RecipeCompleteEvent` |
| `recipe:loop_iteration` | `:RecipeEvent` | `:RecipeLoopIterationEvent` |
| `skill:load` | `:SkillEvent` | `:SkillLoadEvent` |

Unknown events follow the same derivation automatically. Use `:Event` as the base
label when querying across all event types.

---

### FieldLifter Properties

`DefaultHandler` applies all matching `FieldLifter` instances to expose structured
fields as top-level node properties on every `:Event` node. All lifters fire (not
first-match-wins); specific lifters can override Universal.

| Lifter | Applies To (pattern) | Lifted Properties |
|---|---|---|
| `UniversalLifter` | `*` (all events) | `session_id`, `parent_id` |
| `ToolLifter` | `tool:*` | `tool_name`, `tool_input`, `tool_call_id`, `parallel_group_id` |
| `LlmLifter` | `llm:*` | `model`, `provider` |
| `DelegateLifter` | `delegate:*` | `agent`, `sub_session_id`, `parent_session_id`, `tool_call_id`, `parallel_group_id` |
| `PromptLifter` | `prompt:*` | `prompt`, `response_preview` |
| `RecipeLifter` | `recipe:*` | `recipe_name`, `current_step`, `description`, `status`, `step_id`, `total_steps` |
| `SessionLifter` | `session:*` | `parent`; from `metadata` dict: `agent_name`, `tool_call_id`, `parallel_group_id`, `recipe_name`, `recipe_step`, `recipe_step_index` |
| `SkillLifter` | `skill:*` | `skill_directory`, `skill_name` |
| `ArtifactLifter` | `artifact:*` | `bytes`, `path` |

`None` values and missing keys are silently skipped. `data` (full JSON payload) is
always written as a fallback, but prefer lifted properties for structured access.

---

### Data Layer 2 Warning

> ⚠️ **Do not write queries using any of the following labels or relationships.**
> They are either stub labels with no connected edges, or relationship types that
> do not exist in the graph. Queries referencing them will silently return no results.

**Labels That Exist But Have No Connected Edges:**

The following node labels may appear as orphan nodes in the database but are not
connected to the rest of the graph via any traversable relationship:

- `OrchestratorRun`
- `Step`
- `ToolExecution`
- `Delegation`
- `RecipeRun`

These are Data Layer 2 concepts that were planned but whose edge relationships
were never implemented. **Do not write queries that traverse to or from these labels.**

**Relationship Types That Do Not Exist:**

The following relationship types are referenced in older documentation or planning
documents but are **not present** in the graph:

- `HAS_RUN`
- `HAS_STEP`
- `TRIGGERED`
- `SPAWNED`
- `SUBSESSION_OF`
- `PARALLEL_WITH`
- `NEXT`

**Do not write queries using any of these relationship types.** They will match
nothing and silently produce empty result sets with no error.

---

### Node ID Formats

| Node Type | Format | Example |
|---|---|---|
| `:Session` (root) | Raw UUID | `f881e0a0-c055-4ee4-84ed-ff44703150ea` |
| `:Session` (forked) | `{hex}-{hex}_{agent-name}` | `a1b2c3d4-e5f6-7890-abcd-ef1234567890_foundation:explorer` |
| `:Event` | `{session_id}__{event_name_underscored}__{epoch_ms}` | `f881e0a0-...__tool_pre__1742018545123` |
| `:ToolCall` | `{session_id}__tool_call__{tool_call_id}` | `f881e0a0-...__tool_call__call_abc123` |

**Separator:** Double underscore `__` — never a single colon.
**`event_name_underscored`:** Raw event name with `:` replaced by `_` (e.g. `tool:pre` → `tool_pre`).
**`epoch_ms`:** Unix epoch milliseconds from the ISO 8601 timestamp.
**Disambiguator:** `tool_call_id` is appended to Event node IDs for tool lifecycle events to prevent collisions when parallel calls share the same millisecond timestamp.

---

### Two Paths to Tool Data

There are two complementary ways to query tool call information:

| Path | Pattern | Best For |
|---|---|---|
| **Flexible** — via Event | `(s:Session)-[:HAS_EVENT]->(e:ToolEvent)` | Filtering by tool name, reading lifted fields, querying all tool activity regardless of lifecycle state |
| **Structured** — via ToolCall | `(s:Session)-[:HAS_TOOL_CALL]->(tc:ToolCall)` | Getting the lifecycle node (start + end times), correlating pre/post/error events via `(tc)-[:HAS_EVENT]->(e)` |

The `:ToolCall` node provides:
- `tool_name` — the tool being called
- `tool_call_id` — provider-assigned correlation ID
- `session_id` — owning session
- `parallel_group_id` — set when the call is part of a parallel group
- `started_at` / `ended_at` — lifecycle timestamps (from `tool:pre` and `tool:post`/`tool:error`)

Both paths are valid. Use the flexible path for event-level queries; use the
structured path when you need the lifecycle view or duration calculations.

---

## Schema

### Node ID Format

Node IDs are generated by `make_node_id()` in `utils.py` and are
filesystem-safe on all platforms.

**Pattern:** `{session_id}__{event_name}__{timestamp_ms}`

**ToolExecution pattern:** `{session_id}__{event_name}__{timestamp_ms}__{tool_call_id}`

- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` → `prompt_submit`
- Session nodes use the raw `session_id` (a UUID) as their `node_id` — no
  transformation
- ToolExecution nodes include `tool_call_id` as a fourth segment to prevent
  collisions when parallel tool calls share the same millisecond timestamp
- Example: `6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000`
- ToolExecution example: `6afb3613-...ed18__tool_pre__1741270343000__toolu_01G9FD9g`

---

## Node Labels

Every node carries one or more labels. Base type labels plus optional
sub-type discriminator labels are applied at write time.

| Label | Meaning |
|-------|---------|
| `Session` | Fundamental execution boundary; one Amplifier session |
| `Root` | Top-level session with no parent (subtype of `Session`) |
| `Subsession` | Child session with a parent (subtype of `Session`) |
| `ForkedSession` | Session created via `session:fork`, inherits parent context (subtype of `Session`) |
| `OrchestratorRun` | One `execution:start` to `execution:end` bracket (one user turn) |
| `Step` | A unit of work within an `OrchestratorRun` |
| `PromptStep` | The causal trigger step (iteration 0); carries user prompt or delegation instruction (subtype of `Step`) |
| `AssistantStep` | An LLM iteration step within an interactive `OrchestratorRun` (subtype of `Step`) |
| `RecipeStep` | An LLM iteration step within a recipe-spawned session (subtype of `Step`) |
| `ToolExecution` | One `tool:pre` to `tool:post` pair; a single tool invocation |
| `Delegation` | A `ToolExecution` that spawned a child session via the delegate tool (subtype of `ToolExecution`) |
| `Event` | Any lifecycle or custom event not part of the core structural chain |
| `RecipeRun` | Recipe execution wrapper node; one per recipe invocation (linked via `HAS_RECIPE_RUN`) |
| `RecipeLoopIteration` | Subtype of `RecipeStep`; one per while-loop iteration (adds `step_id`, `max_iterations`, `iteration`) |
| `RecipeApproval` | Subtype of `RecipeStep`; approval gate within a staged recipe (adds `stage_name`, `current_step`, `approval_prompt`) |

Event sub-labels are derived using `derive_label()`: split on `:` and `_`,
PascalCase join. Examples: `ContextCompaction`, `SkillLoaded`, `OrchestrationStarted`.

---

## Relationship Types

| Relationship Type | From | To | Meaning |
|-------------------|------|-----|---------|
| `HAS_RUN` | `Session` | `OrchestratorRun` | Session contains ordered orchestrator runs |
| `HAS_STEP` | `OrchestratorRun` | `Step` | Run contains ordered steps (LLM iterations) |
| `NEXT` | `Step` | `Step` | Sequential causal ordering within a run |
| `TRIGGERED` | `Step` | `ToolExecution` | Step triggered these tool executions |
| `PARALLEL_WITH` | `ToolExecution` | `ToolExecution` | Concurrent execution in the same parallel group |
| `SPAWNED` | `ToolExecution` | `Session` | Delegation created a child session |
| `SUBSESSION_OF` | `Session` | `Session` | Child session to parent lineage |
| `HAS_EVENT` | `OrchestratorRun` (when active) / `Session` (fallback) | `Event` | Attaches lifecycle/custom events to their scope. DefaultHandler checks `cursors.current_run_id` — if an active run exists, the event attaches to the run; otherwise it falls back to the Session. |
| `HAS_RECIPE_RUN` | `Session` | `RecipeRun` | Written once on first `recipe:*` event |
| `SPANS_RUN` | `RecipeRun` | `OrchestratorRun` | Non-owning reference, deduplicated across approval-gate turns |

---

## Node Properties

Properties are stored directly on nodes (not in a JSON blob). The
following properties appear on all or most nodes:

| Property | Present On | Notes |
|----------|-----------|-------|
| `node_id` | All nodes | Unique across the database; see ID Format above |
| `workspace` | All nodes | Workspace partition key (default: `"default"`) |
| `session_id` | Most nodes | The session this node belongs to |
| `occurred_at` | Most nodes | ISO-8601 timestamp string of the originating event |
| `prompt_text` | `PromptStep` nodes | Full user prompt or delegation instruction text |
| `status` | `ToolExecution` nodes | E.g., `"completed"`, `"error"` |
| `tool_name` | `ToolExecution` nodes | Name of the tool invoked |

Additional properties are written by handlers as open-ended key-value pairs
and stored directly on the node.

### Event Data Preservation

Every node carries a `data` property containing the full event payload as a
JSON-encoded string. This ensures the complete raw event data is always
accessible without additional lookups.

Enriched nodes (those produced by handlers that write additional structured
properties) also carry `data_<event_name>` properties containing the JSON
payload of the enriching event. The property name is derived by replacing
colons and hyphens in the event name with underscores and prepending `data_`.

| Event Name | Property |
|------------|----------|
| `llm:request` | `data_llm_request` |
| `llm:response` | `data_llm_response` |
| `tool:post` | `data_tool_post` |
| `tool:error` | `data_tool_error` |
| `execution:end` | `data_execution_end` |
| `orchestrator:complete` | `data_orchestrator_complete` |
| `session:end` | `data_session_end` |
| `delegate:agent_spawned` | `data_delegate_agent_spawned` |
| `delegate:agent_completed` | `data_delegate_agent_completed` |

### Blob References

Large payloads — LLM messages, tool results, context snapshots — are stored
as external blobs rather than inline on nodes. A property whose value matches
the `$blob_ref` pattern is a pointer to an external blob, not the real value.

Example blob reference value stored on a node property:

```json
{
  "$blob_ref": "ci-blob://session_id/blob_key",
  "field": "raw",
  "node_id": "6afb3613-7041-4735-9c0f-c2171452ed18__tool_post__1741270343000",
  "size_bytes": 42000
}
```

Known blob fields:

| Field | Description |
|-------|-------------|
| `raw` | Raw serialized event payload |
| `result` | Tool execution result output |
| `messages` | LLM conversation messages array |
| `mount_plan` | File mount plan for delegate tool |
| `context_snapshot` | Context snapshot at execution boundary |
| `debug` | Debug diagnostic data |

The `blob_read` tool resolves a `ci-blob://` URI and writes the blob content
to a local file, returning the file path:

- **`blob_read(uri)`** — resolves a `ci-blob://session_id/blob_key` URI and
  returns the **file path** to the blob content on disk

### Agent workflow

When working with event data or blob references in the graph, follow this
5-step process:

1. **Call `graph_query`** with a Cypher query to find the node(s) of interest
   and retrieve their `data` or `data_<event_name>` property.
2. **Parse the `data` property** (a JSON string) to inspect the event payload
   and identify any `$blob_ref` pointers within the property values.
3. **Call `blob_read(uri)`** for each `ci-blob://` URI encountered to resolve
   the blob URI and obtain the local file path to the blob content.
4. **Check file size** before reading — blobs from `llm:request` /
   `llm:response` events can exceed 100 k tokens and will overflow agent
   context if read in full.
5. **Use `bash` + `jq`** to read and filter specific fields from the blob file
   at the path returned by `blob_read`. Never load blob content directly into
   the agent context — always use targeted `jq` selectors or `head`/`tail` to
   access specific fields or slices.

## Relationship Properties

| Property | Present On | Notes |
|----------|-----------|-------|
| `workspace` | All relationships | Workspace partition key; always written on flush |
| `occurred_at` | Most relationships | ISO-8601 timestamp string |

---

## Workspace Scoping

Every query is scoped to a **workspace** — an isolated partition identified
by the `workspace` property present on all nodes and relationships.

The `graph_query` tool handles automatic injection of the `$workspace`
parameter. When querying within the current workspace, the tool injects
the workspace value for you. Write Cypher queries that reference `$workspace`
explicitly in node patterns or WHERE clauses.

### 1. Default query (own workspace)

The `graph_query` tool auto-injects `$workspace` from the current session
context. Write queries that filter on `$workspace`:

```cypher
// $workspace auto-injected by graph_query tool
MATCH (s:Session {workspace: $workspace})
RETURN s.node_id, s.occurred_at
ORDER BY s.occurred_at DESC
```

### 2. Explicit workspace query

Pass `workspace="other-project"` to target a specific workspace:

```cypher
MATCH (s:Session {workspace: $workspace})
RETURN s.node_id, s.occurred_at
```

### 3. Cross-workspace (wildcard) query

Pass `workspace="*"` — the tool skips parameter injection entirely.
Write queries without `$workspace` filter, or add your own:

```cypher
// workspace="*" — no automatic injection
MATCH (s:Session)
RETURN s.workspace, s.node_id, s.occurred_at
ORDER BY s.workspace, s.occurred_at DESC
```

---

## Query Patterns

### Pattern 1: Find All Sessions in a Workspace

```cypher
MATCH (s:Session {workspace: $workspace})
RETURN s.node_id       AS session_id,
       s.occurred_at   AS started_at,
       labels(s)       AS session_labels
ORDER BY s.occurred_at DESC
```

To restrict to only top-level (root) sessions:

```cypher
MATCH (s:Session:RootSession {workspace: $workspace})
RETURN s.node_id AS session_id, s.started_at AS started_at
ORDER BY s.started_at DESC
```

### Pattern 2: Session Execution Brackets

Find all execution brackets (one per user turn):

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(e:ExecutionStartEvent)
RETURN e.node_id AS bracket_id, e.occurred_at AS turn_started
ORDER BY e.occurred_at
```

Brackets with duration (pair each start with its nearest end):

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(start:ExecutionStartEvent)
OPTIONAL MATCH (s)-[:HAS_EVENT]->(end:ExecutionEndEvent)
WHERE end.occurred_at > start.occurred_at
WITH start, min(end.occurred_at) AS turn_ended
RETURN start.node_id AS bracket_id,
       start.occurred_at AS turn_started,
       turn_ended,
       duration.between(datetime(start.occurred_at), datetime(turn_ended)) AS duration
ORDER BY start.occurred_at
```

### Pattern 3: Session Event Timeline

Complete chronological event timeline for a session:

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(e:Event)
RETURN e.event_name, labels(e), e.occurred_at
ORDER BY e.occurred_at
```

Filter to a specific event category (e.g., LLM events only):

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(e:LlmEvent)
RETURN e.event_name, e.model, e.occurred_at
ORDER BY e.occurred_at
```

### Pattern 4: Session Tool Activity

There are two complementary paths to tool data in Data Layer 1. Use the **flexible
path** (via `:ToolEvent`) for search and analysis — it lets you filter by tool name,
read lifted fields, and query all tool activity regardless of lifecycle state. Use the
**structured path** (via `:ToolCall`) when the lifecycle node itself is the natural
anchor — for example, when you need start + end timestamps or want to correlate
pre/post/error events via `(tc)-[:HAS_EVENT]->(e)`.

**Variant 1 — Flexible path (preferred for search and analysis):**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(e:ToolEvent)
RETURN e.event_name AS event_type,
       e.tool_name,
       e.tool_call_id,
       e.parallel_group_id,
       e.occurred_at
ORDER BY e.occurred_at
```

**Variant 2 — Filter to tool:pre only:**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(e:ToolPreEvent)
RETURN e.tool_name,
       e.tool_call_id,
       e.occurred_at
```

**Variant 3 — Structured path (when ToolCall is the anchor):**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN tc.tool_name,
       tc.tool_call_id,
       tc.parallel_group_id,
       tc.ended_at
ORDER BY tc.ended_at
```

### Pattern 5: Child Sessions and Delegation Metadata

**Variant 1 — Direct child sessions (structural, via HAS_FORK):**

```cypher
MATCH (parent:Session {workspace: $workspace, node_id: $session_id})-[:HAS_FORK]->(child:Session)
RETURN child.node_id    AS child_session_id,
       child.started_at AS started_at,
       labels(child)    AS session_labels
ORDER BY child.started_at
```

**Variant 2 — Delegation metadata (via DelegateAgentSpawnedEvent):**

```cypher
MATCH (parent:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(e:DelegateAgentSpawnedEvent)
RETURN e.agent            AS agent,
       e.sub_session_id   AS sub_session_id,
       e.tool_call_id     AS tool_call_id,
       e.parallel_group_id AS parallel_group_id,
       e.occurred_at      AS occurred_at
ORDER BY e.occurred_at
```

**Variant 3 — Combined (structural children with delegation metadata):**

```cypher
MATCH (parent:Session {workspace: $workspace, node_id: $session_id})-[:HAS_FORK]->(child:Session)
OPTIONAL MATCH (parent)-[:HAS_EVENT]->(e:DelegateAgentSpawnedEvent)
WHERE e.sub_session_id = child.node_id
RETURN child.node_id    AS child_session_id,
       child.started_at AS started_at,
       e.agent          AS agent,
       e.tool_call_id   AS tool_call_id
ORDER BY child.started_at
```

### Pattern 6: Session Overview

**Variant 1 — Flat summary (counts per session):**

```cypher
MATCH (s:Session {workspace: $workspace})
OPTIONAL MATCH (s)-[:HAS_EVENT]->(e:Event)
OPTIONAL MATCH (s)-[:HAS_TOOL_CALL]->(tc:ToolCall)
OPTIONAL MATCH (s)-[:HAS_FORK]->(child:Session)
RETURN s.node_id,
       s.started_at,
       s.status,
       count(DISTINCT e)     AS event_count,
       count(DISTINCT tc)    AS tool_call_count,
       count(DISTINCT child) AS child_session_count
ORDER BY s.started_at DESC
```

**Variant 2 — Breakdown by event category:**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_EVENT]->(e:Event)
WITH e, [lbl IN labels(e) WHERE lbl ENDS WITH 'Event' AND lbl <> 'Event'] AS sub_labels
WHERE size(sub_labels) > 0
RETURN sub_labels[0] AS event_category,
       count(e)       AS event_count
ORDER BY event_count DESC
```

### Pattern 7: Parallel Tool Call Groups

**Variant 1 — Via ToolCall (structured path):**

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE tc.parallel_group_id <> ''
RETURN tc.parallel_group_id  AS parallel_group_id,
       collect(tc.tool_name) AS tool_names,
       count(tc)             AS group_size
ORDER BY group_size DESC
```

**Variant 2 — Via ToolPreEvent (flexible path):**

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_EVENT]->(e:ToolPreEvent)
WHERE e.parallel_group_id <> ''
RETURN e.parallel_group_id  AS parallel_group_id,
       collect(e.tool_name) AS tool_names,
       count(e)             AS group_size
ORDER BY group_size DESC
```

**Variant 3 — Peak parallelism across workspace:**

```cypher
MATCH (s:Session:RootSession {workspace: $workspace})-[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE tc.parallel_group_id <> ''
WITH s.node_id AS session_id,
     tc.parallel_group_id AS grp,
     count(tc) AS grp_size
RETURN session_id,
       max(grp_size)       AS peak_parallelism,
       count(DISTINCT grp) AS parallel_group_count
ORDER BY peak_parallelism DESC
LIMIT 20
```

> **Note:** `parallel_group_id` is an empty string `""` (not null) when a tool runs
> alone. Use `tc.parallel_group_id <> ''` to filter parallel groups — not `IS NOT NULL`.

### Pattern 8: Search Prompt Text

`PromptSubmitEvent` nodes carry the `prompt` property (promoted by `PromptLifter`). Use
`PromptSubmitEvent` for submitted prompts and `PromptCompleteEvent` for completed ones.

**Basic search:**

```cypher
MATCH (e:PromptSubmitEvent {workspace: $workspace})
WHERE e.prompt CONTAINS $search_term
RETURN e.session_id, e.prompt, e.occurred_at
ORDER BY e.occurred_at DESC
```

**Case-insensitive search using `toLower()`:**

```cypher
MATCH (e:PromptSubmitEvent {workspace: $workspace})
WHERE toLower(e.prompt) CONTAINS toLower($search_term)
RETURN e.session_id, e.prompt, e.occurred_at
ORDER BY e.occurred_at DESC
```

### Pattern 9: Count Nodes by Label

```cypher
MATCH (n {workspace: $workspace})
RETURN labels(n) AS node_labels,
       count(n)   AS node_count
ORDER BY node_count DESC
```

Count a specific label type:

```cypher
MATCH (n:ToolCall {workspace: $workspace})
RETURN count(n) AS tool_call_count
```

### Pattern 10: Find Child Sessions of a Parent

**Variant 1 — Direct children only:**

```cypher
MATCH (parent:Session {workspace: $workspace, node_id: $session_id})-[:HAS_FORK]->(child:Session)
RETURN child.node_id    AS child_session_id,
       child.started_at AS started_at,
       labels(child)    AS session_labels
ORDER BY child.started_at
```

**Variant 2 — All descendants (any depth):**

```cypher
MATCH (parent:Session {workspace: $workspace, node_id: $session_id})-[:HAS_FORK*1..]->(descendant:Session)
RETURN descendant.node_id    AS descendant_session_id,
       descendant.started_at AS started_at,
       labels(descendant)    AS session_labels
ORDER BY descendant.started_at
```

### Pattern 11: Find Events Attached to a Session

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EVENT]->(e:Event)
RETURN e.node_id    AS event_id,
       labels(e)    AS event_labels,
       e.occurred_at AS occurred_at
ORDER BY e.occurred_at
```

> **Note:** In Data Layer 1, all HAS_EVENT edges attach directly to the Session (not to OrchestratorRun). ToolCall nodes also carry HAS_EVENT edges for their tool:pre and tool:post events.

Via ToolCall:

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[:HAS_TOOL_CALL]->(tc:ToolCall)-[:HAS_EVENT]->(e:Event)
RETURN tc.tool_name AS tool_name,
       tc.tool_call_id AS tool_call_id,
       e.event_name AS event_name,
       e.occurred_at AS occurred_at
ORDER BY e.occurred_at
```

### Pattern 12: Tool Activity Stats

`:ToolCall` nodes have no `status` property — derive success/failure from event types:
`tool:pre` = initiated, `tool:post` = completed, `tool:error` = failed.

**Per-tool event counts:**

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_EVENT]->(e:ToolEvent)
RETURN e.tool_name, e.event_name, count(e) AS n
ORDER BY e.tool_name, e.event_name
```

**Tool error rate:**

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_EVENT]->(e:ToolEvent)
WHERE e.event_name IN ['tool:post', 'tool:error']
RETURN e.tool_name,
       sum(CASE WHEN e.event_name = 'tool:error' THEN 1 ELSE 0 END) AS errors,
       sum(CASE WHEN e.event_name = 'tool:post' THEN 1 ELSE 0 END) AS successes
ORDER BY errors DESC
```

---

## Graph Algorithm Examples

### Shortest Path Between Two Nodes

Find the shortest undirected path between any two nodes by `node_id`:

```cypher
MATCH (a {node_id: $source_id, workspace: $workspace}),
      (b {node_id: $target_id, workspace: $workspace}),
      path = shortestPath((a)-[*]-(b))
RETURN [n IN nodes(path)         | n.node_id]  AS node_chain,
       [r IN relationships(path) | type(r)]    AS rel_chain,
       length(path)                            AS hop_count
```

### All Paths from Session to a Specific Tool Execution

```cypher
MATCH (s:Session {node_id: $session_id, workspace: $workspace}),
      (te:ToolExecution {node_id: $tool_exec_id, workspace: $workspace}),
      path = (s)-[*]->(te)
RETURN [n IN nodes(path) | n.node_id]          AS path_nodes,
       [r IN relationships(path) | type(r)]    AS rel_types,
       length(path)                            AS depth
ORDER BY depth
LIMIT 10
```

### Variable-Length Traversal (Descendant Subgraph)

Walk up to 6 hops outward from a session to find all reachable nodes:

```cypher
MATCH (s:Session {node_id: $session_id, workspace: $workspace})
      -[:HAS_RUN | HAS_STEP | TRIGGERED | SPAWNED*1..6]->(descendant)
RETURN descendant.node_id AS node_id,
       labels(descendant)  AS node_labels,
       descendant.occurred_at AS occurred_at
ORDER BY descendant.occurred_at
```

Walk the delegation lineage (any depth):

```cypher
MATCH path = (root:Session {workspace: $workspace})
             -[:HAS_RUN*0..1]->()-[:HAS_STEP*0..1]->()
             -[:TRIGGERED*0..1]->(d:Delegation)
             -[:SPAWNED*0..1]->(child:Session)
             -[:SUBSESSION_OF*0..]->(root)
RETURN path
LIMIT 50
```

---

## Usage via graph_query Tool

All patterns above are executed through the `graph_query` tool. Pass a Cypher
query string as the first argument; the tool handles workspace scoping and
returns results as a list of row dicts.

Basic usage — find sessions in the current workspace:

```
graph_query(
  "MATCH (s:Session {workspace: $workspace}) "
  "RETURN s.node_id, s.occurred_at ORDER BY s.occurred_at DESC"
)
# Returns: list of dicts, one per row
```

With additional parameters — find runs for a specific session:

```
graph_query(
  "MATCH (s:Session {workspace: $workspace, node_id: $session_id})"
  "-[:HAS_RUN]->(r:OrchestratorRun) "
  "RETURN r.node_id AS run_id, r.occurred_at AS started_at",
  params={"session_id": "6afb3613-7041-4735-9c0f-c2171452ed18"}
)
```

Query another workspace explicitly:

```
graph_query(
  "MATCH (s:Session {workspace: $workspace}) RETURN s.node_id",
  workspace="project-alpha"
)
```

Cross-workspace query (wildcard — no `$workspace` injected):

```
graph_query(
  "MATCH (s:Session) "
  "RETURN s.workspace AS ws, count(s) AS session_count "
  "ORDER BY session_count DESC",
  workspace="*"
)
```

> **Note:** `graph_query` operates on the **persisted (flushed) store only**.
> In-memory buffered writes are not visible to Cypher queries until the store
> has been flushed. Use `get_node()` / `get_edge()` for buffer-aware reads.

---

## ID Format Reference

### Session nodes

Session `node_id` is the raw UUID from the Amplifier session. No
transformation is applied — the UUID is used directly:

```
55c8841a-1234-4abc-8def-000000000001
```

### All other nodes

Non-session nodes follow the pattern `{session_id}__{event_name}__{epoch_ms}`,
using `__` (double underscore) as the separator:

```
55c8841a-1234-4abc-8def-000000000001__prompt_submit__1737972001000
55c8841a-1234-4abc-8def-000000000001__tool_pre__1737972005000
55c8841a-1234-4abc-8def-000000000001__execution_start__1737972000000
```

Parsing the ID:

```python
# Split on double underscore separator
parts = node_id.split("__")
# parts[0] = session_id UUID
# parts[1] = event_name (colons replaced with underscores)
# parts[2] = epoch_ms as string
```

### ToolExecution nodes

ToolExecution nodes include the `tool_call_id` as a disambiguator to prevent
collisions when parallel tool calls share the same millisecond timestamp.
The `__` (double underscore) separator is used between all four segments:

```
55c8841a-1234-4abc-8def-000000000001__tool_pre__1737972005000__toolu_01G9FD9g
```

Parsing the ID:

```python
# Split on double underscore separator
parts = node_id.split("__")
# parts[0] = session_id UUID
# parts[1] = event_name (colons replaced with underscores)
# parts[2] = epoch_ms as string
# parts[3] = tool_call_id (only present on ToolExecution nodes)
```

### Relationship identity

Relationships have no stored ID property. Identity is composite:
`(source.node_id, target.node_id, type(r))`. To locate a specific
relationship, match by endpoint `node_id` values and relationship type.

---

## Critical Gotchas

### 1. `metadata` is a JSON string, not a map

Node `metadata` properties are stored as JSON-encoded strings. You cannot
filter on nested fields directly in Cypher. Parse them in application code
after retrieving:

```cypher
// Correct — retrieve and parse in code
MATCH (s:Session {workspace: $workspace})
RETURN s.node_id, s.metadata
```

Do **not** attempt `s.metadata.some_key` — Cypher will return `null`.

### 2. Silently dropped events

Events written during the same millisecond with identical `node_id` values
are silently deduplicated on `MERGE`. If two events share `session_id`,
`event_name`, and `timestamp_ms`, only the first is stored. Use
`tool_call_id` (present on `ToolExecution` nodes) to disambiguate parallel
tool calls.

### 3. No `seq` ordering on edges

`HAS_RUN`, `HAS_STEP`, and `NEXT` relationships do **not** carry a `seq`
property. To order steps within a run, sort by `occurred_at` on the node:

```cypher
MATCH (run:OrchestratorRun {workspace: $workspace})-[:HAS_STEP]->(step:Step)
RETURN step.node_id, step.occurred_at
ORDER BY step.occurred_at ASC
```

### 4. Workspace scoping is manual

`graph_query` injects `$workspace` automatically, but only if you reference
`$workspace` in your query. Omitting the filter from a MATCH clause silently
returns data from **all** workspaces. Always include `{workspace: $workspace}`
on the anchor node of every query.

### 5. `HAS_EVENT` target rules

`HAS_EVENT` attaches to the **current active `OrchestratorRun`** when one is
open (`cursors.current_run_id` is set); otherwise it falls back to the
`Session` node. This means lifecycle events emitted outside an orchestrator
run (e.g., session-level hooks) are attached directly to the Session, not to
a Run.

### 6. Node `MERGE` key is `{node_id, workspace}`

All nodes are upserted using `MERGE (n {node_id: $node_id, workspace: $workspace})`.
Querying by `node_id` alone (without `workspace`) may match nodes from
other workspaces in a shared database. Always include `workspace` in
identity lookups.

---

## Notes

### Properties vs labels

Labels are separate from properties. You can filter on both:

```cypher
// Filter by label AND property
MATCH (step:PromptStep {workspace: $workspace})
RETURN step.node_id

// Filter by property only (scans more nodes)
MATCH (n {workspace: $workspace})
WHERE 'PromptStep' IN labels(n)
RETURN n.node_id
```

Prefer label-based filters — they use index-backed label scans and are faster
than property-only filters.

### Multi-label nodes

Nodes carry both a base label and a sub-type label. Both can be used in MATCH:

```cypher
// Matches any Session regardless of subtype
MATCH (s:Session {workspace: $workspace}) ...

// Matches only root sessions (both labels present)
MATCH (s:Session:Root {workspace: $workspace}) ...

// Equivalent WHERE form
MATCH (s:Session {workspace: $workspace})
WHERE s:Root ...
```

### Workspace property on relationships

Relationships also carry `workspace`. For cross-workspace queries where
you traverse relationships, add a relationship filter if needed:

```cypher
// workspace="*"
MATCH (s:Session)-[r:HAS_RUN]->(run:OrchestratorRun)
WHERE r.workspace = $target_workspace
RETURN s.node_id, run.node_id
```

### Buffer visibility

`graph_query` runs against the **persisted state only**. Nodes and
relationships buffered via `upsert_node`/`upsert_edge` but not yet flushed
will **not** appear in Cypher query results. Always flush before running
analysis queries when you need up-to-date results.

---

## Foundational Traversal Primitive

The multi-relationship wildcard reaches any descendant node type in one
Cypher pattern, naturally crossing sub-session boundaries via SPAWNED.
Add a depth cap to prevent runaway traversal on deep delegation chains.

```cypher
-- All ToolExecutions under a session (including sub-sessions, any depth)
MATCH (root:Session {node_id: $session_id, workspace: $workspace})
      -[:HAS_RUN|HAS_STEP|TRIGGERED|SPAWNED*1..20]->(te:ToolExecution)
RETURN te.tool_name, count(te) AS calls
ORDER BY calls DESC
```

Replace `ToolExecution` with any terminal node type: `AssistantStep`,
`RecipeStep`, `Event`, etc.

**Note:** `parallel_group_id` is an empty string `""` (not null) when a tool
runs alone. Use `te.parallel_group_id <> ""` to isolate parallel groups — not
`IS NOT NULL`.

---

## Time-Activity Queries

**Why `started_at <= T`:** For a run to be active at instant T, it must have
started at or before T. `started_at >= T` would find runs that hadn't started
yet — the opposite of what you want.

**Point-in-time** — root sessions with an active run at instant T:

```cypher
MATCH (r:OrchestratorRun {workspace: $workspace})
WHERE r.started_at <= $point_in_time
  AND (r.ended_at IS NULL OR r.ended_at >= $point_in_time)
MATCH (s:Session)-[:HAS_RUN]->(r)
OPTIONAL MATCH (s)-[:SUBSESSION_OF*1..]->(root:Session:Root {workspace: $workspace})
RETURN DISTINCT
  coalesce(root.node_id, s.node_id)       AS root_session_id,
  coalesce(root.started_at, s.started_at) AS root_started
ORDER BY root_started DESC
```

**Time-range** — root sessions with any run that started within [t1, t2]:

```cypher
MATCH (r:OrchestratorRun {workspace: $workspace})
WHERE r.started_at >= $t1 AND r.started_at <= $t2
MATCH (s:Session)-[:HAS_RUN]->(r)
OPTIONAL MATCH (s)-[:SUBSESSION_OF*1..]->(root:Session:Root {workspace: $workspace})
RETURN
  coalesce(root.node_id, s.node_id)       AS root_session_id,
  coalesce(root.started_at, s.started_at) AS root_started,
  count(DISTINCT r)                        AS runs_in_window
ORDER BY root_started DESC
```

Use the time-range variant to find sessions resumed after a long gap: each
resume creates a new OrchestratorRun with a fresh `started_at`.

`OPTIONAL MATCH + coalesce`: when the owning session is already a root
(no SUBSESSION_OF exists), `root` is null and coalesce falls back to `s`.

---

## Recipe Analytics

**Find all sessions that ran a recipe:**

```cypher
MATCH (s:Session:Root {workspace: $workspace})-[:HAS_RECIPE_RUN]->(rr:RecipeRun)
RETURN s.node_id AS session_id, s.started_at,
       rr.node_id AS recipe_run_id, rr.recipe_name,
       rr.status, rr.started_at AS recipe_started, rr.ended_at AS recipe_ended
ORDER BY rr.started_at DESC
```

**Recipe execution duration** (uses RecipeStep timestamps as fallback when
`ended_at` is null):

```cypher
MATCH (s:Session {node_id: $session_id, workspace: $workspace})
      -[:HAS_RECIPE_RUN]->(rr:RecipeRun)
MATCH (s2:Session)-[:SUBSESSION_OF*0..]->(s)
MATCH (s2)-[:HAS_RUN]->(:OrchestratorRun)-[:HAS_STEP]->(step:RecipeStep)
RETURN rr.node_id AS recipe_run_id,
       rr.recipe_name,
       coalesce(rr.started_at, min(step.occurred_at)) AS effective_start,
       coalesce(rr.ended_at,   max(step.occurred_at)) AS effective_end,
       min(step.occurred_at) AS first_step,
       max(step.occurred_at) AS last_step
```

> **Note:** Cypher implicitly groups by the non-aggregated columns `rr.node_id` and
> `rr.recipe_name` — no explicit `GROUP BY` clause is needed.

**Loop count and depth per recipe:**

```cypher
MATCH (s:Session {node_id: $session_id, workspace: $workspace})
      -[:HAS_RECIPE_RUN]->(rr:RecipeRun)
MATCH (s2:Session)-[:SUBSESSION_OF*0..]->(s)
MATCH (s2)-[:HAS_RUN]->()-[:HAS_STEP]->(li:RecipeLoopIteration)
RETURN rr.recipe_name,
       li.step_id                  AS loop_name,
       count(li)                   AS iterations,
       max(li.iteration)           AS max_iteration_reached
ORDER BY iterations DESC
```

**Note:** `RecipeRun.ended_at` and `recipe_name` are only set when
`recipe:complete` fires. Use coalesce to fall back to step timestamps
when the run node is still a stub.

---

## Parallelism Degree

**Parallel groups per session (any depth via wildcard):**

```cypher
MATCH (root:Session {node_id: $session_id, workspace: $workspace})
      -[:HAS_RUN|HAS_STEP|TRIGGERED|SPAWNED*1..20]->(te:ToolExecution)
WHERE te.parallel_group_id <> ""
RETURN te.parallel_group_id,
       collect(te.tool_name) AS tools,
       count(te)             AS parallel_degree
ORDER BY parallel_degree DESC
```

**Sessions with the highest peak parallelism across the workspace:**

```cypher
MATCH (s:Session:Root {workspace: $workspace})
      -[:HAS_RUN|HAS_STEP|TRIGGERED|SPAWNED*1..20]->(te:ToolExecution)
WHERE te.parallel_group_id <> ""
WITH s.node_id AS session_id, te.parallel_group_id AS grp, count(te) AS grp_size
RETURN session_id,
       max(grp_size)          AS peak_parallelism,
       count(DISTINCT grp)    AS parallel_groups
ORDER BY peak_parallelism DESC LIMIT 20
```

**Note:** `parallel_group_id` is `""` (empty string, not null) for
non-parallel tools. Always use `<> ""` to filter, never `IS NOT NULL`.

---

## Token Efficiency

**Property distinction — never confuse these:**
- `input_tokens` — provider's actual token count (from `usage.input_tokens`). Use for cost analysis.
- `message_count` — orchestrator's message count (from `usage.input`). Use for context window analysis.
Do not sum `input_tokens` and `message_count` together — they measure different things.

`cached_tokens` and `cache_write_tokens` can be null on older sessions.
Always use `coalesce(property, 0)` in aggregations.

**Token usage aggregated per orchestrator run:**

```cypher
MATCH (s:Session {node_id: $session_id, workspace: $workspace})
      -[:HAS_RUN]->(r:OrchestratorRun)
      -[:HAS_STEP]->(a:AssistantStep)
RETURN r.node_id                                AS run_id,
       r.started_at,
       sum(a.input_tokens)                      AS total_input,
       sum(a.output_tokens)                     AS total_output,
       sum(coalesce(a.cached_tokens, 0))        AS total_cached,
       sum(coalesce(a.cache_write_tokens, 0))   AS total_cache_written,
       sum(coalesce(a.reasoning_tokens, 0))     AS total_reasoning,
       count(a)                                 AS llm_turns
ORDER BY r.started_at
```

**Cache efficiency — sessions with the best prompt cache utilisation:**

```cypher
MATCH (s:Session:Root {workspace: $workspace})
      -- AssistantStep reached via HAS_STEP only; TRIGGERED goes to ToolExecution
      -[:HAS_RUN|HAS_STEP|SPAWNED*1..20]->(a:AssistantStep)
WHERE a.input_tokens IS NOT NULL
WITH s.node_id AS session_id,
     sum(a.input_tokens)                    AS raw_input,
     sum(coalesce(a.cached_tokens, 0))      AS cached
WHERE raw_input + cached > 0
RETURN session_id,
       raw_input + cached                                     AS total_input_tokens,
       cached                                                 AS cache_hits,
       round(100.0 * cached / (raw_input + cached), 1)       AS cache_hit_pct
ORDER BY cache_hit_pct DESC LIMIT 20
```

