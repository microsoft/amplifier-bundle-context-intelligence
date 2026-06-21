---
name: context-intelligence-graph-query
description: >
  Use when querying the context-intelligence property graph for session history,
  tool call traces, LLM iteration analysis, execution scale metrics, agent
  delegation trees, skill loading, and recipe orchestration. Covers all graph
  layers, cross-layer SOURCED_FROM joins, SST navigation, blob handling, and
  verified Cypher patterns.
license: MIT
metadata:
  version: "2.0.0"
---

# Context Intelligence Graph Query

This skill equips you to navigate and extract insights from the context-intelligence
property graph using the `graph_query` tool. The graph holds a complete record of
every Amplifier session — what happened, when, how things connect, and at what scale.

---

## Section 1 — What the Graph Gives You

The graph holds two complementary views of every session.

**Data layer 1** is the raw event stream. Every kernel event is preserved as a node,
queryable by type, field, and time. It answers: *what happened and when.* Complete
timeline, exact field values, every tool call and LLM exchange recorded as-is.

**Data layer 2** is the semantic layer. Events are assembled into meaningful runtime
entities — turns (OrchestratorRun), LLM iterations (Iteration), content blocks
(ContentBlock), tool calls (ToolCall), prompts (Prompt), and more. Connected by 15
typed relationships. It answers: *what ran, how, and at what scale.* Conversation
structure, execution scale, tool correlation, turn-level reasoning.

**The foundation layer** surfaces what happens above the kernel: delegation trees (Delegation, Agent), skill loading snapshots (SkillLoad), and recipe orchestration (RecipeRun, RecipeStep, Recipe). It answers: *who delegated to whom, which skills were active, and how recipe steps connect to the tool calls and delegations they triggered.*

All layers coexist in the same graph and are bridged by **SOURCED_FROM** edges — the
canonical cross-layer connection. Every data layer 2 entity carries one or more
SOURCED_FROM edges back to the raw data layer 1 events that produced it, giving every
semantic node a direct provenance link into the original event stream. Use data layer 1
when you need exact event fields or the raw timeline. Use data layer 2 when you need
structure, scale, or causation. Navigate between them with SOURCED_FROM.

**Layer identification signal:** The `node_id` separator tells you which layer a node
came from. `__` (double underscore) = data layer 1 node. `::` (double colon) =
data layer 2 node. A few data layer 2 types use plain identifiers (ToolCall uses
the provider's tool_call_id directly; Orchestrator uses the orchestrator name string). Foundation layer entities use the same `::` separator; concept nodes (`Agent`, `Recipe`) use their name string directly as `node_id`, like `Orchestrator` in data layer 2.

---

## Section 2 — Schema Reference

### Temporal Property Types: ZONED DATETIME, Not Strings

**Read this before writing any query that touches a timestamp.** Every `*_at` property (`started_at`, `ended_at`, `occurred_at`, `completed_at`, `resumed_at`, `cancelled_at`, `last_loop_iteration_at`, `loop_completed_at`) and the non-`*_at` field `last_updated` — on nodes AND on the three edge types that carry `occurred_at` (`HAS_EVENT`, `HAS_SUBSESSION`, `FORKED`) — are stored as native Neo4j **`ZONED DATETIME`** values. They are NOT strings.

❌ Wrong: `WHERE s.started_at > '2026-05-01'` — silently returns no results (comparing ZONED DATETIME to string literal always evaluates false; Neo4j raises no error).

✅ Correct: `WHERE s.started_at > datetime('2026-05-01')` — wrap every literal in `datetime(...)`.

✅ `ORDER BY s.started_at` — correct as-is.

✅ `duration.between(s.started_at, s.ended_at)` — now works, returns a Neo4j DURATION value (e.g. PT1H30M).

See Gotcha #12 for the same warning at the point of use, and Section 6 for temporal query patterns.

### Data Layer 1 Nodes

| Node Label | Description | node_id Format |
|---|---|---|
| `:Session` | One Amplifier session. Sub-labels: `:RootSession`, `:SubSession`, `:ForkedSession`, `:IncompleteSession`. | Raw UUID |
| `:Event` | Every kernel event. Triple-labeled: `:Event` + `:{Category}Event` + `:{Specific}Event`. | `{session_id}__{event_name}__{epoch_ms}` |

Key properties on `:Event` nodes:
- `occurred_at` — **`ZONED DATETIME`** (native Neo4j temporal; compare with `datetime(...)`, not string literals — see "Temporal Property Types" above)
- `session_id` — owning session UUID
- `workspace` — workspace partition key
- `event_name` — raw event name (e.g. `tool:pre`)
- **`data`** — **JSON string** of the complete raw kernel event payload from the session JSONL. Not a Cypher map. Dot notation (`e.data.tool_name`) does not work in Cypher. Use lifted properties (`tool_name`, `model`, `tool_call_id`, etc.) which are extracted at ingest time as first-class node properties. When raw payload fields not lifted are needed, retrieve the `data` string and parse with `jq` outside Cypher (see Section 5). May contain `ci-blob://` URI references for large payloads.
- Plus event-specific lifted properties (e.g. `tool_name`, `tool_call_id` on `:ToolPreEvent`; `model`, `provider` on `:LlmResponseEvent`).

Common event labels: `:ToolPreEvent`, `:ToolPostEvent`, `:ToolErrorEvent`, `:LlmRequestEvent`, `:LlmResponseEvent`, `:PromptSubmitEvent`, `:ExecutionStartEvent`, `:ExecutionEndEvent`, `:DelegateAgentSpawnedEvent`, `:SessionStartEvent`, `:SessionEndEvent`.

### Data Layer 1 Edges

| Edge | From → To | Meaning |
|---|---|---|
| `HAS_FORK` | Session → Session | Parent session forked a child |
| `HAS_TOOL_CALL` | Session → ToolCall | Session owns a data layer 1 tool call lifecycle node |
| `HAS_EVENT` | Session → Event | Session owns an event node. Carries edge property `occurred_at` (ZONED DATETIME). |
| `HAS_EVENT` | ToolCall → Event | Tool call owns its lifecycle events |

### Data Layer 2 Entity Types

All data layer 2 nodes carry a `workspace` property and an SST type label.

| Entity | Labels | SST Type | node_id Format | Key Properties |
|---|---|---|---|---|
| Session | `:Session:SST_EVENT` (+ `:RootSession`/`:SubSession`/`:ForkedSession`/`:IncompleteSession`) | Temporal | Raw UUID | `started_at` (ZONED DATETIME), `ended_at` (ZONED DATETIME), `last_updated` (ZONED DATETIME), `status` |
| OrchestratorRun | `:OrchestratorRun:SST_EVENT` | Temporal | `{session_id}::orch_run::{started_at}` | `started_at` (ZONED DATETIME), `ended_at` (ZONED DATETIME), `completed_at` (ZONED DATETIME, when present), `orchestrator_name` |
| Iteration | `:Iteration:SST_EVENT` | Temporal | `{session_id}::iteration::{N}` | `iteration_number`, `started_at` (ZONED DATETIME) |
| ContentBlock | `:ContentBlock:SST_EVENT` | Temporal | `{session_id}::block::{iteration_N}::{index}` | `block_type`, `block_index`, `started_at` (ZONED DATETIME, when present) |
| ToolCall | `:ToolCall:SST_EVENT` | Temporal | `{tool_call_id}` (provider UUID directly) | `tool_name`, `tool_call_id`, `result_success`, `result_error`, `result_output`, `started_at` (ZONED DATETIME), `ended_at` (ZONED DATETIME), `parallel_group_id` |
| Prompt | `:Prompt:SST_EVENT` | Temporal | `{session_id}::prompt::{timestamp}` | `prompt_text`, `started_at` (ZONED DATETIME) |
| Cancellation | `:Cancellation:SST_EVENT` | Temporal | `{session_id}::cancellation::{timestamp}` | `occurred_at` (ZONED DATETIME) |
| ContextCompaction | `:ContextCompaction:SST_EVENT` | Temporal | `{session_id}::compaction::{timestamp}` | `occurred_at` (ZONED DATETIME) |
| MountPlan | `:MountPlan:SST_THING` | Resource | `{session_id}::mount_plan` | `mount_plan_data` |
| Orchestrator | `:Orchestrator:SST_CONCEPT` | Abstract | Orchestrator name string (e.g. `loop-streaming`) | `name` |

### Data Layer 2 Edge Types

All edges carry an `sst_semantic` property that expresses the relationship's meaning.

| Edge Type | `sst_semantic` | From → To | What It Means |
|---|---|---|---|
| `HAS_EXECUTION` | `CONTAINS` | Session → OrchestratorRun | Session contains this orchestrator run (one per user turn) |
| `FORKED` | `LEADS_TO` | Session → ForkedSession | Session forked a child session. Carries edge property `occurred_at` (ZONED DATETIME). |
| `HAS_ATTRIBUTE` | `EXPRESSES` | Session → Orchestrator | Session describes its orchestrator type |
| `HAS_PART` | `CONTAINS` | Session → MountPlan/Prompt/Cancellation | Session contains these parts |
| `HAS_PART` | `CONTAINS` | OrchestratorRun → Iteration | Run contains these LLM iterations |
| `HAS_PART` | `CONTAINS` | Iteration → ContentBlock | Iteration contains these content blocks |
| `HAS_TOOL_CALL` | `CONTAINS` | Iteration → ToolCall | Iteration contains these tool calls |
| `HAS_COMPACTION` | `CONTAINS` | Session → ContextCompaction | Session contains this compaction event |
| `HAS_SUBSESSION` | `LEADS_TO` | Session → SubSession | Session leads to a sub-session. Carries edge property `occurred_at` (ZONED DATETIME). |
| `CAUSED` | `LEADS_TO` | ContentBlock → ToolCall | This content block triggered this tool call |
| `PARALLEL_EXECUTION` | `NEAR` | ToolCall ↔ ToolCall | These tool calls ran concurrently in the same parallel group |
| `TRIGGERS` | `LEADS_TO` | Prompt → OrchestratorRun | This prompt started this orchestrator run |
| `ENABLES` | `LEADS_TO` | OrchestratorRun → Prompt | This run's completion enabled the next prompt |
| `SOURCED_FROM` | (none) | data_layer_2 entity → data_layer_1 Event | Cross-layer provenance bridge. Every data layer 2 entity has one SOURCED_FROM edge per contributing raw event. No `sst_semantic` — infrastructure, not SST model. |

### Foundation Layer Entity Types

All foundation layer nodes carry a `workspace` property and an SST type label.

| Entity | Labels | SST Type | node_id Format | Key Properties |
|---|---|---|---|---|
| Delegation | `:Delegation:SST_EVENT` | Temporal | `{parent_session_id}::delegation::{tool_call_id\|sub_session_id}` | `agent`, `sub_session_id`, `parent_session_id`, `started_at` (ZONED DATETIME), `ended_at` (ZONED DATETIME), `resumed_at` (ZONED DATETIME, when present), `cancelled_at` (ZONED DATETIME, when present), `context_depth`, `context_scope` |
| Agent | `:Agent:SST_CONCEPT` | Abstract | Agent name string (e.g. `foundation:explorer`) | `agent` |
| SkillLoad | `:SkillLoad:SST_EVENT` | Temporal | `{session_id}::skill::{skill_name}::{loaded_at_ts}` | `skill_name`, `content_length`, `loaded_at` |
| RecipeRun | `:RecipeRun:SST_EVENT` | Temporal | `{session_id}::recipe_run::{timestamp}` | `name`, `status`, `current_step`, `total_steps`, `last_loop_iteration_at` (ZONED DATETIME, when present), `loop_completed_at` (ZONED DATETIME, when present) |
| RecipeStep | `:RecipeStep:SST_EVENT` | Temporal | `{session_id}::recipe_run::{ts}::step::{N}` | `name`, `status`, `step_id` |
| Recipe | `:Recipe:SST_CONCEPT` | Abstract | Recipe name string | `name` |

### Foundation Layer Edge Types

| Edge Type | `sst_semantic` | From → To | What It Means |
|---|---|---|---|
| `HAS_AGENT` | `EXPRESSES` | Session(sub) → Agent | Sub-session describes its agent type |
| `ENCOMPASSES` | `CONTAINS` | Delegation → Session(sub) | Delegation encompasses the sub-session lifecycle |
| `TRIGGERED` | `LEADS_TO` | ToolCall → Delegation | Tool call triggered this delegation |
| `PARALLEL_AGENT` | `NEAR` | Delegation ↔ Delegation | These delegations ran concurrently |
| `HAS_SKILL_LOAD` | `CONTAINS` | Iteration → SkillLoad | Iteration contains this skill load |
| `HAS_RECIPE_RUN` | `CONTAINS` | Session → RecipeRun | Session contains this recipe run |
| `HAS_RECIPE` | `EXPRESSES` | RecipeRun → Recipe | RecipeRun describes its recipe type |
| `HAS_STEP` | `CONTAINS` | RecipeRun → RecipeStep | RecipeRun contains these steps |
| `TRIGGERED` | `LEADS_TO` | RecipeStep → RecipeRun(child) | Step spawned a nested recipe |
| `TRIGGERED` | `LEADS_TO` | RecipeStep → Delegation | Step triggered this delegation |
| `TRIGGERED` | `LEADS_TO` | RecipeStep → ToolCall | Step triggered this tool call |

---

## Section 3 — SST Navigation (Reasoning by Semantic Type)

The data layer 2 schema uses SST type labels to classify every node by its fundamental
character. These labels let you query across entity boundaries without knowing specific
node labels in advance.

### Querying by SST Type Label

Three SST type labels partition the semantic layer:

| SST Label | Meaning | Entities |
|---|---|---|
| `:SST_EVENT` | Temporal, bounded occurrence | Session, OrchestratorRun, Iteration, ContentBlock, ToolCall, Prompt, Cancellation, ContextCompaction, Delegation, SkillLoad, RecipeRun, RecipeStep |
| `:SST_THING` | Persistent resource or artifact | MountPlan |
| `:SST_CONCEPT` | Abstract, reusable identity | Orchestrator, Agent, Recipe |

**Example — find all temporal events in the last session:**

```cypher
MATCH (s:Session {workspace: $workspace})
WITH s ORDER BY s.started_at DESC LIMIT 1
MATCH (s)-[:HAS_EXECUTION|HAS_PART*1..3]->(e:SST_EVENT)
RETURN labels(e) AS types, e.node_id, e.started_at
ORDER BY e.started_at
LIMIT 50
```

**Example — find all abstract concepts referenced by a session:**

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_ATTRIBUTE]->(c:SST_CONCEPT)
RETURN c.name AS orchestrator_name
```

**Example — find all persistent resources (things) attached to sessions:**

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_PART]->(t:SST_THING)
RETURN s.node_id AS session, labels(t) AS resource_type, t.node_id
```

### Querying by Edge Semantic

Every data layer 2 edge carries an `sst_semantic` property that expresses the relationship's
abstract meaning, independent of the concrete edge type. This lets you query causation,
containment, and concurrence uniformly.

| `sst_semantic` Value | Meaning | Concrete edges that carry it |
|---|---|---|
| `CONTAINS` | Part-of / containment relationship | `HAS_EXECUTION`, `HAS_PART`, `HAS_TOOL_CALL`, `HAS_COMPACTION` |
| `LEADS_TO` | Causal / sequential relationship | `FORKED`, `HAS_SUBSESSION`, `CAUSED`, `TRIGGERS`, `ENABLES` |
| `EXPRESSES` | Description / attribution relationship | `HAS_ATTRIBUTE` |
| `NEAR` | Concurrent / proximity relationship | `PARALLEL_EXECUTION` |

**Example — find all causal relationships emanating from a session:**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})-[r]->(target)
WHERE r.sst_semantic = 'LEADS_TO'
RETURN type(r) AS edge_type, r.sst_semantic, labels(target) AS target_type, target.node_id
LIMIT 50
```

**Example — find all concurrent tool calls in the session:**

```cypher
MATCH (tc1:ToolCall)-[r:PARALLEL_EXECUTION]-(tc2:ToolCall)
WHERE r.sst_semantic = 'NEAR'
  AND tc1.workspace = $workspace
RETURN tc1.tool_name, tc2.tool_name, tc1.parallel_group_id
```

### Hierarchical Traversal Pattern

Use variable-length paths with `HAS_EXECUTION|HAS_PART*` to traverse the full session
containment hierarchy in a single query. This pattern reaches any depth of the
Session → OrchestratorRun → Iteration → ContentBlock tree.

**Pattern — reach all descendants of a session:**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION|HAS_PART*]->(descendant)
RETURN labels(descendant) AS type, descendant.node_id
ORDER BY descendant.started_at
```

**Pattern — reach tool calls specifically (three-hop max):**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION|HAS_PART*1..3]->(iteration:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN tc.tool_name, tc.started_at, tc.result_success
ORDER BY tc.started_at
```

The `*1..3` bound prevents runaway traversal on large sessions. Use `*` (unbounded) only
when the session hierarchy depth is known to be shallow.

### Turn Chain Pattern

The `TRIGGERS` and `ENABLES` edges form a chain that represents the conversation flow:
each user prompt triggers an orchestrator run, and each completed run enables the next
prompt. Traversing this chain reconstructs the turn-by-turn progression of a session.

**Pattern — walk the turn chain forward from the first prompt:**

```cypher
MATCH path = (p:Prompt {workspace: $workspace})
             -[:TRIGGERS]->(run:OrchestratorRun)
             -[:ENABLES]->(next_prompt:Prompt)
WHERE p.session_id = $session_id
RETURN [node IN nodes(path) | {type: labels(node), id: node.node_id, at: node.started_at}]
  AS turn_chain
ORDER BY p.started_at
```

**Pattern — count turns in a session:**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_PART]->(p:Prompt)
      -[:TRIGGERS]->(run:OrchestratorRun)
RETURN count(run) AS turn_count
```

---

## Section 4 — Cross-Layer Queries

Data layer 1 (raw events) and data layer 2 (semantic entities) coexist in the same graph.
The canonical way to move between them is the `SOURCED_FROM` edge. Two additional fallback
strategies cover cases where SOURCED_FROM edges are absent (older sessions ingested before
the SOURCED_FROM handler was deployed).

### Join 1 — SOURCED_FROM (Canonical)

Every data layer 2 entity is linked back to the raw data layer 1 event(s) that produced
it via `SOURCED_FROM` edges. This is the preferred join strategy because it is exact,
direction-aware, and does not require shared scalar keys.

```cypher
// Navigate from a ToolCall entity back to its source raw event
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
MATCH (tc)-[:SOURCED_FROM]->(pre:ToolPreEvent)
RETURN tc.tool_name          AS tool_name,
       tc.result_success      AS succeeded,
       pre.occurred_at        AS event_fired_at,
       pre.data               AS raw_payload
ORDER BY pre.occurred_at
```

```cypher
// Navigate in the reverse direction — from a raw event to the semantic entity it produced
MATCH (pre:ToolPreEvent {workspace: $workspace, session_id: $session_id})
MATCH (tc:ToolCall)-[:SOURCED_FROM]->(pre)
RETURN pre.tool_name          AS event_name,
       pre.occurred_at        AS fired_at,
       tc.result_success      AS succeeded,
       tc.result_output       AS output
ORDER BY pre.occurred_at
```

Use this join when you want to retrieve the raw event payload for a semantic entity, or
when you want the structured result for a raw event.

### Join 2 — ToolCall Direct Match (Fallback)

The `:ToolCall` data layer 2 node uses the provider's `tool_call_id` directly as its
`node_id`. The `:ToolPreEvent` data layer 1 node lifts the same identifier as its
`tool_call_id` property. This shared key is a direct join between the layers.

```cypher
// Find the semantic ToolCall entity for a given raw ToolPreEvent
MATCH (e:ToolPreEvent {workspace: $workspace, tool_call_id: $tool_call_id})
MATCH (tc:ToolCall {node_id: e.tool_call_id})
RETURN e.tool_name          AS event_tool_name,
       e.occurred_at        AS event_time,
       tc.result_success    AS succeeded,
       tc.result_output     AS output,
       tc.ended_at          AS completed_at
```

Use this join when SOURCED_FROM edges are absent (older sessions) and you have a
ToolPreEvent. It works only for ToolCall entities — other data layer 2 types do not
share a direct key with data layer 1.

### Join 3 — Session Containment (Fallback)

When you need to correlate raw events with the semantic structure of a session, join
through the shared `:Session` node. Data layer 1 uses `HAS_EVENT` to attach raw event
nodes. Data layer 2 uses `HAS_EXECUTION` and `HAS_PART` to attach semantic entities.

```cypher
// Correlate raw LLM response events with semantic iterations
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
MATCH (s)-[:HAS_EVENT]->(lre:LlmResponseEvent)          // data layer 1
MATCH (s)-[:HAS_EXECUTION]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)                     // data layer 2
WHERE lre.iteration_number = iter.iteration_number
RETURN iter.iteration_number,
       lre.model            AS model,
       lre.occurred_at      AS responded_at,
       iter.started_at      AS iter_started
ORDER BY iter.iteration_number
```

The `s` (Session) node is the bridge: traverse `HAS_EVENT` to reach data layer 1 nodes,
traverse `HAS_EXECUTION`/`HAS_PART` to reach data layer 2 entities, then join on shared
scalar properties (`iteration_number`, `tool_call_id`, etc.).

### Workspace Scoping

Every query must be scoped to a workspace. The workspace is a partition key that prevents
results from bleeding across unrelated projects or users.

**Default workspace** — the `graph_query` tool automatically injects the configured
workspace as `$workspace`. Most queries use it without any explicit parameter:

```cypher
MATCH (s:Session {workspace: $workspace})
RETURN s.node_id, s.started_at
ORDER BY s.started_at DESC
LIMIT 10
```

**Explicit workspace parameter** — when the workspace differs from the default, pass it
explicitly in the `params` dict of the `graph_query` call:

```cypher
// Query with explicit workspace override
MATCH (s:Session {workspace: $workspace})
RETURN count(s) AS session_count
```

Invoke with `params: {"workspace": "my-other-project"}` to override the default.

**Cross-workspace queries** — pass `workspace: '*'` to query across all workspaces. Use
sparingly; cross-workspace queries skip the partition index and can be slow on large graphs:

```cypher
MATCH (s:Session)
WHERE s.workspace <> ''
RETURN s.workspace, count(s) AS sessions_per_workspace
ORDER BY sessions_per_workspace DESC
```

**Mandatory workspace placement** — always place `{workspace: $workspace}` on the anchor
node (the first `MATCH` pattern that establishes the starting point of the query). Do not
rely on downstream nodes or `WHERE` clauses alone to scope results. Placing the workspace
constraint on the anchor node allows the graph engine to use the workspace index and
avoids full graph scans:

```cypher
// CORRECT — workspace on anchor node Session
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun)
RETURN run.orchestrator_name, run.started_at

// INCORRECT — workspace on a downstream node (misses the index)
MATCH (s:Session {node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun {workspace: $workspace})
RETURN run.orchestrator_name, run.started_at
```

---

## Section 5 — Blob Handling (Critical)

### The `data` Field Is a JSON String, Not a Cypher Map

The `data` property on every `:Event` node holds the original kernel event payload as a
**JSON-encoded string**. It is not a Cypher map. You cannot use dot notation (`e.data.tool_name`)
to access sub-fields from Cypher. The entire payload is stored as an opaque string and must
be parsed in application code or with a post-processing tool like `jq`.

```cypher
// Returns the raw JSON string — you must parse it outside Cypher
MATCH (e:ToolPreEvent {workspace: $workspace, tool_call_id: $tool_call_id})
RETURN e.data AS raw_payload
```

### ci-blob:// URI Replacement for Large Payloads

When an event payload exceeds the graph storage threshold, the server replaces the full
`data` string with a `ci-blob://` URI reference. The URI points to a blob store entry
that holds the original payload. The `data` field in this case looks like:

```
ci-blob://SESSION_ID/EVENT_KEY
```

The presence of a `ci-blob://` value in `data` means the full payload is too large to
store inline and must be retrieved separately using `blob_read`.

### Agent Workflow for Blob-Aware Data Extraction

When writing queries that access the `data` field, always follow this four-step workflow:

**Step 1 — Run the Cypher query and retrieve the `data` field:**

```cypher
MATCH (e:LlmResponseEvent {workspace: $workspace})
WHERE e.session_id = $session_id
RETURN e.node_id, e.data
ORDER BY e.occurred_at DESC
LIMIT 5
```

**Step 2 — Inspect each `data` value. If it starts with `ci-blob://`, it is a blob
reference. Do NOT try to parse it as JSON.**

**Step 3 — For blob references, call `blob_read` with the URI. `blob_read` returns a
file path on the local filesystem — it does NOT return the content directly:**

```python
# blob_read returns {"file_path": "/tmp/ci-blobs/SESSION_ID/EVENT_KEY.json"}
# The content is at the file_path, not in the return value
result = blob_read(uri="ci-blob://SESSION_ID/EVENT_KEY")
file_path = result["file_path"]
```

**Step 4 — Extract the fields you need with `jq`. Never load the full blob into the
agent's context — large blobs can be tens of thousands of tokens:**

```bash
# Extract a specific field from the blob file using jq
jq '.messages[-1].content' /tmp/ci-blobs/SESSION_ID/EVENT_KEY.json

# Extract just the top-level keys to understand the structure
jq 'keys' /tmp/ci-blobs/SESSION_ID/EVENT_KEY.json

# Extract a nested field safely with a fallback
jq '.response.usage // "no usage data"' /tmp/ci-blobs/SESSION_ID/EVENT_KEY.json
```

### Rules for Blob Handling

- **Never load the full blob** — always use `jq` to extract only the fields you need.
- **`blob_read` returns a file path**, not content — dereference the path, then read.
- **Check for `ci-blob://` before parsing** — treat any `data` value that starts with
  `ci-blob://` as a URI, not as JSON.
- **Lifted properties bypass blobs** — commonly needed fields (e.g. `tool_name`,
  `tool_call_id`, `model`, `provider`) are lifted onto the node as top-level properties
  during ingestion. Query lifted properties directly from Cypher rather than fetching
  blobs when the lifted field is sufficient.

---

## Section 6 — Discovery Patterns (Verified Cypher)

The following patterns are verified to work against the data layer 2 schema. All use
`$workspace` as the workspace parameter automatically injected by `graph_query`.

### Pattern 1 — Full Conversation Turn Trace

Reconstructs the complete turn-by-turn flow of a session: each prompt, the orchestrator
run it triggered, the iterations within that run, and the tool calls in each iteration.

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_PART]->(p:Prompt)
      -[:TRIGGERS]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN p.started_at           AS turn_start,
       run.orchestrator_name  AS orchestrator,
       iter.iteration_number  AS iteration,
       tc.tool_name           AS tool,
       tc.result_success      AS succeeded,
       tc.started_at          AS tool_start
ORDER BY p.started_at, iter.iteration_number, tc.started_at
LIMIT 100
```

> **Size note:** Run a count query first (`count(tc)`) if the session has more than a few
> turns. Raise the limit only after confirming the total is manageable. Use SKIP to paginate.

### Pattern 2 — Tool Usage Per LLM Iteration

Counts and lists every tool call grouped by which LLM iteration fired it. Useful for
understanding how many tools each iteration invoked and what they were.

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN iter.iteration_number  AS iteration,
       collect(tc.tool_name)  AS tools_called,
       count(tc)              AS tool_count
ORDER BY iter.iteration_number
LIMIT 50
```

### Pattern 3 — Parallel Tool Groups

Finds all tool calls that executed concurrently within the same parallel group. The
`parallel_group_id` property identifies the group; `PARALLEL_EXECUTION` edges connect
the members directly.

```cypher
MATCH (tc1:ToolCall {workspace: $workspace})
      -[:PARALLEL_EXECUTION]-(tc2:ToolCall)
WHERE tc1.node_id < tc2.node_id  // deduplicate undirected pairs
  AND tc1.session_id = $session_id
RETURN tc1.parallel_group_id   AS group_id,
       tc1.tool_name            AS tool_a,
       tc2.tool_name            AS tool_b,
       tc1.started_at           AS started_at
ORDER BY tc1.started_at
LIMIT 50
```

### Pattern 4 — ContentBlock → ToolCall Causation

Traces which content block in the LLM response caused each tool call to be issued.
The `CAUSED` edge from `ContentBlock` to `ToolCall` expresses this direct causation.

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_PART]->(block:ContentBlock)
      -[:CAUSED]->(tc:ToolCall)
RETURN iter.iteration_number   AS iteration,
       block.block_index        AS block_index,
       block.block_type         AS block_type,
       tc.tool_name             AS tool_triggered,
       tc.result_success        AS succeeded
ORDER BY iter.iteration_number, block.block_index
LIMIT 100
```

### Pattern 5 — Session Comparison

Compares two sessions side by side: total turns, total iterations, total tool calls,
and success rate. Useful for comparing agent behavior across sessions.

```cypher
MATCH (s:Session {workspace: $workspace})
WHERE s.node_id IN [$session_id_a, $session_id_b]
OPTIONAL MATCH (s)-[:HAS_PART]->(p:Prompt)-[:TRIGGERS]->(run:OrchestratorRun)
OPTIONAL MATCH (run)-[:HAS_PART]->(iter:Iteration)
OPTIONAL MATCH (iter)-[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN s.node_id                                          AS session,
       count(DISTINCT p)                                  AS turns,
       count(DISTINCT iter)                               AS iterations,
       count(DISTINCT tc)                                 AS tool_calls,
       sum(CASE WHEN tc.result_success THEN 1 ELSE 0 END) AS successful_tools
ORDER BY s.node_id
```

### Pattern 6 — Failed Tool Calls

Lists every tool call that failed (result_success is false), including the error message
and the session it belongs to. Useful for diagnosing error-prone sessions.

```cypher
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EXECUTION]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE tc.result_success = false
RETURN s.node_id              AS session,
       iter.iteration_number  AS iteration,
       tc.tool_name           AS tool,
       tc.result_error        AS error,
       tc.started_at          AS failed_at
ORDER BY tc.started_at
LIMIT 50
```

> **Size note:** This query spans ALL sessions in the workspace. Scope to a single session
> with `AND s.node_id = $session_id` to limit exposure, or add `ORDER BY tc.started_at DESC`
> to retrieve the most recent failures first.

### Pattern 7 — Data Layer 1 / Data Layer 2 Cross-Layer Join

Joins raw `:ToolPreEvent` nodes (data layer 1) with semantic `:ToolCall` entities
(data layer 2) using the shared `tool_call_id` key. Returns both the raw event timestamp
and the structured result from the semantic layer.

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EVENT]->(pre:ToolPreEvent)
MATCH (tc:ToolCall {node_id: pre.tool_call_id})
RETURN pre.tool_name          AS tool_name,
       pre.occurred_at        AS event_fired_at,
       tc.result_success      AS succeeded,
       tc.result_error        AS error,
       tc.result_output       AS output_preview,
       tc.ended_at            AS completed_at
ORDER BY pre.occurred_at
LIMIT 50
```

### Pattern 8 — SOURCED_FROM Cross-Layer Navigation

Navigates from a semantic `:ToolCall` entity (data layer 2) through its `SOURCED_FROM`
edge to the originating `:ToolPreEvent` (data layer 1). Returns both the structured
result stored on the semantic entity and the raw event timestamp from the event stream.
Use this pattern as the canonical cross-layer join when SOURCED_FROM edges are present.

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
MATCH (tc)-[:SOURCED_FROM]->(pre:ToolPreEvent)
RETURN iter.iteration_number  AS iteration,
       tc.tool_name            AS tool,
       tc.result_success       AS succeeded,
       pre.occurred_at         AS event_fired_at,
       pre.data                AS raw_payload
ORDER BY pre.occurred_at
LIMIT 25
```

> **Size note:** `pre.data` may be a `ci-blob://` URI or a large JSON string. Limit to 25 rows
> and follow the blob handling workflow (Section 5) before loading any `data` field.

### Delegation Tree

Lists every agent delegation in a session: which tool call triggered it, which agent was spawned, and the resulting sub-session.

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
      -[:TRIGGERED]->(d:Delegation)
RETURN d.agent, d.sub_session_id, d.context_depth,
       d.started_at, d.ended_at, tc.tool_name AS via_tool
ORDER BY d.started_at
LIMIT 50
```

### Skills Active Per Iteration

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_SKILL_LOAD]->(sl:SkillLoad)
RETURN iter.iteration_number, sl.skill_name, sl.content_length, sl.loaded_at
ORDER BY iter.iteration_number, sl.loaded_at
LIMIT 100
```

### Recipe Run Trace

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_RECIPE_RUN]->(rr:RecipeRun)
      -[:HAS_STEP]->(step:RecipeStep)
OPTIONAL MATCH (step)-[:TRIGGERED]->(target)
RETURN rr.name, step.name, step.status,
       labels(target) AS triggered_type, target.node_id AS triggered_id
ORDER BY step.step_id
LIMIT 50
```

---

## Section 7 — Result Size Management and Pagination

The graph can hold hundreds or thousands of sessions, each containing many events, tool
calls, and semantic nodes. Returning results without limits is the most common way to
destroy your context window. Every query must be designed with size in mind.

---

### The Cardinal Rule: Always LIMIT

**Every query that traverses unbounded data MUST include a `LIMIT` clause.** There are
no exceptions. A session with 50 turns and 300 tool calls will return 300+ rows from an
unguarded Pattern 1 query. Multiplied across even 10 sessions, that is 3,000+ rows —
enough to saturate the context window before you have read a single result.

```cypher
// WRONG — no LIMIT, will return everything
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN tc.tool_name, tc.started_at

// CORRECT — bounded
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN tc.tool_name, tc.started_at
ORDER BY tc.started_at
LIMIT 50
```

---

### Safe Default LIMIT Values by Query Type

Use these defaults when you do not know the expected result size in advance. Reduce
further if the query is part of a larger multi-step analysis.

| Query type | Safe default LIMIT | Notes |
|---|---|---|
| Session listing | 10 | Very wide rows (many properties) |
| Tool call listing | 50 | One row per call; can be large per session |
| Event listing | 25 | `data` field makes rows wide |
| Iteration listing | 25 | One row per LLM round-trip |
| Delegation listing | 25 | Usually sparse, but can be large in recipe sessions |
| Cross-layer joins | 25 | Double the data per row |
| Aggregation / GROUP BY | 50 | Aggregated rows are lean |
| Path / hierarchy traversal | 25 | Variable row width |
| Full conversation trace | 50 | One row per tool call across all turns |

If you need more rows than the safe default, always run a COUNT query first (see below)
to understand the actual result size before raising the limit.

---

### Count-First Pattern (Always Run Before Wide Queries)

Before executing any query that returns multi-field rows over an unknown population,
run a count-first query to understand the scale. This costs almost nothing and prevents
context overflow.

```cypher
// Step 1 — count first (cheap)
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE s.node_id = $session_id
RETURN count(tc) AS total_tool_calls
```

```cypher
// Step 2 — retrieve data only after you know the count
// If total_tool_calls > 50, use pagination (see SKIP/LIMIT below)
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN iter.iteration_number, tc.tool_name, tc.result_success, tc.started_at
ORDER BY tc.started_at
LIMIT 50
```

Apply this pattern whenever you are querying a session you have not seen before, or when
querying across multiple sessions at once.

---

### SKIP + LIMIT Pagination Pattern

When you need more results than the safe default, paginate using `SKIP` and `LIMIT`.
Never raise the limit beyond 200 rows per page — the context cost of wide rows
compounds quickly.

```cypher
// Page 1 — first 50 results
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN iter.iteration_number, tc.tool_name, tc.result_success, tc.started_at
ORDER BY tc.started_at
SKIP 0 LIMIT 50

// Page 2 — next 50
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN iter.iteration_number, tc.tool_name, tc.result_success, tc.started_at
ORDER BY tc.started_at
SKIP 50 LIMIT 50
```

**Pagination rules:**
- Always include `ORDER BY` before `SKIP`/`LIMIT` — without it, page boundaries are
  non-deterministic and you may see duplicate or missing rows across pages.
- Use a stable, unique sort key (`started_at` + `node_id` as tiebreaker) to guarantee
  consistent ordering across pages.
- Stop paginating when the returned row count is less than the page size — that signals
  the last page.

---

### Progressive Exploration Strategy

For unfamiliar sessions or multi-session queries, always follow a three-phase funnel.
Going straight to full detail is almost always a mistake.

**Phase 1 — Orient (counts and summaries only)**

```cypher
// How many sessions, how large?
MATCH (s:Session {workspace: $workspace})
OPTIONAL MATCH (s)-[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(iter:Iteration)
RETURN s.node_id, s.started_at, s.status, count(iter) AS iteration_count
ORDER BY s.started_at DESC
LIMIT 10
```

**Phase 2 — Scope (aggregated view of the target session)**

```cypher
// What happened in this session, at a glance?
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
OPTIONAL MATCH (s)-[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(iter:Iteration)
OPTIONAL MATCH (iter)-[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN count(DISTINCT iter) AS iterations,
       count(DISTINCT tc)   AS tool_calls,
       sum(CASE WHEN tc.result_success = false THEN 1 ELSE 0 END) AS failures
```

**Phase 3 — Drill (filtered, bounded detail)**

```cypher
// Now retrieve the specific rows you need, filtered and limited
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE tc.result_success = false   // focus on failures only
RETURN iter.iteration_number, tc.tool_name, tc.result_error, tc.started_at
ORDER BY tc.started_at
LIMIT 25
```

This funnel ensures you only load detailed rows for the subset you actually need.

---

### Bounding Variable-Length Path Traversal

Variable-length path patterns (`*`, `*1..N`) can fanout explosively on large or deeply
nested graphs. Always bound them.

```cypher
// DANGEROUS — unbounded path, will traverse everything reachable
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION|HAS_PART*]->(descendant)
RETURN labels(descendant), descendant.node_id

// SAFE — bounded depth (3 hops covers the full Session→Run→Iter→Block hierarchy)
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION|HAS_PART*1..3]->(descendant)
RETURN labels(descendant), descendant.node_id
ORDER BY descendant.started_at
LIMIT 100
```

**Recommended depth bounds:**
- `*1..2` — Session → Run → Iteration (stops before ContentBlock/ToolCall)
- `*1..3` — Session → Run → Iteration → ContentBlock (full semantic hierarchy)
- `*1..4` — includes ToolCall via ContentBlock (only if you need CAUSED edges)
- Avoid `*` or `*1..10` entirely — use explicit typed-edge chains instead.

---

### Filtering Before Returning (Reduce in Graph, Not in Client)

Apply `WHERE` filters inside the Cypher query rather than retrieving all rows and
filtering in the calling code. Every unneeded row is context tokens wasted.

```cypher
// INEFFICIENT — retrieve all tool calls, filter in code
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN tc.tool_name, tc.result_success, tc.started_at
LIMIT 200

// EFFICIENT — filter in Cypher, return only what you need
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE tc.tool_name = 'delegate'
  AND tc.started_at > $cutoff_time
RETURN tc.tool_name, tc.result_success, tc.started_at
ORDER BY tc.started_at
LIMIT 50
```

**Common filter strategies:**
- Filter by `session_id` first to scope to one session before retrieving detailed data.
- Use `tool_name` filters to narrow tool call queries to the tool you care about.
- Use `started_at` range filters to limit time-based queries.
- Use `result_success = false` to focus on error analysis.
- Use `LIMIT 1` with `ORDER BY ... DESC` to get the single most recent item.

---

### Multi-Session Queries: Extra Caution

Queries that span multiple sessions multiply the row count by the number of sessions
matched. Always add an explicit session count guard or use `WHERE s.node_id IN [...]`
to constrain to a known set.

```cypher
// DANGEROUS — matches all sessions in workspace, multiplies rows
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN s.node_id, tc.tool_name, tc.result_success
LIMIT 50  // 50 rows across ALL sessions — almost certainly not what you want

// SAFE — one session at a time
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      ...

// SAFE — explicit set of sessions
MATCH (s:Session {workspace: $workspace})
WHERE s.node_id IN [$session_a, $session_b, $session_c]
      ...
LIMIT 50  // 50 rows across 3 known sessions — controlled
```

When you do need cross-session analysis, use aggregation (COUNT, collect, GROUP BY)
to collapse results before returning them, then drill into specific sessions.

---

## Gotchas

**1. Data layer 2 nodes only exist if handlers ran.**
Semantic entities (OrchestratorRun, Iteration, ContentBlock, ToolCall, etc.) are
created by data layer 2 handlers during event ingestion. If a session was ingested
before data layer 2 was deployed, or if the handler for a specific event type is
disabled, those nodes will not exist. Always use `OPTIONAL MATCH` when joining
data layer 2 entities against unknown sessions.

**2. `result_success: false` signals the error path.**
A `:ToolCall` node with `result_success = false` means the tool returned an error.
The `result_error` property holds the error message. A missing `result_success`
property (null) means the `ToolPostEvent` or `ToolErrorEvent` has not been processed
yet — the tool call is still in-flight or the handler did not run.

**3. `data` is a JSON string, not a Cypher map.**
The `data` property on `:Event` nodes is a serialized JSON string. You cannot access
`e.data.tool_name` in Cypher. Lifted properties (`tool_name`, `tool_call_id`, `model`,
etc.) are your first resort. When you need raw payload fields not lifted, retrieve the
`data` string and parse it with `jq` outside Cypher (see Section 5).

**4. `ENABLES` edges are sparse.**
The `ENABLES` edge from `OrchestratorRun` to the next `Prompt` is only written when
the session has a multi-turn chain. Single-turn sessions and sessions where the run
ended without a follow-up prompt will have no `ENABLES` edge. For a session with N
prompts, there are exactly N−1 `ENABLES` edges (each run connects to the next prompt,
but the last run has no successor). Do not rely on `ENABLES` existing to determine if
a session ended cleanly.

**5. Workspace scoping is mandatory.**
Every query must include `{workspace: $workspace}` on the anchor node. Omitting the
workspace filter causes a full graph scan and may return results from unrelated projects
or users. The `graph_query` tool automatically injects `$workspace` — always include
it in the first `MATCH` pattern.

**6. The node MERGE key is `{node_id, workspace}`.**
Data layer 2 nodes are merged using the composite key `{node_id, workspace}`. This
means the same logical entity (e.g. an Orchestrator named `loop-streaming`) can exist
as separate nodes in different workspaces. Cross-workspace queries (passing `workspace:
'*'`) will return one node per workspace, not one node per unique `node_id`. Account
for this when aggregating across workspaces.

**7. `SOURCED_FROM` edges may be absent on older sessions.**
Sessions ingested before the SOURCED_FROM handler was deployed will not have any
cross-layer provenance edges. To check which data layer 2 nodes are missing their
source link, run:

```cypher
MATCH (n:SST_EVENT) WHERE NOT (n)-[:SOURCED_FROM]->() AND NOT n:Session RETURN labels(n), count(*)
```

If this returns results, fall back to Join 2 (ToolCall Direct Match) or Join 3
(Session Containment) for those sessions.

**8. Foundation layer nodes only exist when those features were used.**
A session with no delegation, no skills, and no recipes will have no `Delegation`, `SkillLoad`, `RecipeRun`, or `RecipeStep` nodes. Always use `OPTIONAL MATCH` when joining foundation layer entities against arbitrary sessions.

**9. `Agent` and `Recipe` are concept nodes shared across sessions.**
Unlike `SST_EVENT` entities, `Agent` and `Recipe` nodes are merged by name across the entire workspace. Querying `(a:Agent)` without a session anchor will span all sessions. Scope through the session: reach `Agent` via `HAS_AGENT` from the sub-session, or `Recipe` via `HAS_RECIPE` from a `RecipeRun`.

**10. `SkillLoad` may attach to `Session` directly, not `Iteration`.**
Skills loaded before the first `provider:request` have no active `Iteration`. The `HAS_SKILL_LOAD` edge then comes from `Session` rather than `Iteration`. Pattern "Skills Active Per Iteration" only returns skills tied to an iteration — add `OPTIONAL MATCH (s)-[:HAS_SKILL_LOAD]->(sl:SkillLoad)` to catch session-level loads.

**11. Unbounded queries will destroy your context window.**
A graph with many sessions is NOT like a small in-memory dataset. Each session can have
hundreds of tool calls, thousands of events, and dozens of iterations. A query with no
`LIMIT` clause against the whole workspace can return tens of thousands of rows, saturating
the context window before any result can be processed. Three mandatory habits:

1. **Always LIMIT.** Every query that traverses tool calls, events, or iterations must have
   `LIMIT N`. Start at the safe defaults from Section 7. Raise only after counting.

2. **Count before widening.** If you need to understand the full extent of a dataset, run a
   `count()` aggregation first. The count result is a single number — it costs almost nothing.
   Then decide whether the actual rows are safe to retrieve.

3. **Anchor on a session before traversing.** The pattern `MATCH (s:Session {workspace: $workspace})`
   without a `node_id` filter spans every session. Add `node_id: $session_id` or
   `WHERE s.node_id IN [...]` to constrain the starting set before any traversal.

See Section 7 for the complete size management and pagination reference.

**12. Temporal comparisons require the `datetime()` wrapper.** Comparing a ZONED DATETIME
property to a string literal (`WHERE s.started_at > '2026-05-01'`) always evaluates false —
no error is raised, no results are returned, the query silently produces nothing. Use
`datetime('2026-05-01')` instead. ORDER BY on temporal columns requires no change. See
"Temporal Property Types" at the top of Section 2 for the full list of ZONED DATETIME
properties.

- `duration.between(s.started_at, s.ended_at)` computes elapsed time (session length,
  tool-call duration) and returns a Neo4j DURATION value (e.g. PT1H30M).
- `WHERE s.started_at > datetime() - duration('P30D')` enables rolling time-window queries
  (sessions started in the last 30 days). Both were impossible with string storage.

**13. `IncompleteSession` is a health marker, not a terminal label.**
A session node carrying `:IncompleteSession` reached `session:end` with no prior `session:start`
or `session:fork` event captured. It carries **none** of the terminal labels (`:RootSession`,
`:SubSession`, `:ForkedSession`) and has `has_terminal: false`. It is not a stub for a lost root
session — it is a health signal. A spike in the count indicates upstream event loss. Count them
with:

```cypher
MATCH (s:Session:IncompleteSession) RETURN count(s)
```

Do not treat `:IncompleteSession` nodes as `:RootSession`. Filter them out of normal terminal-
session queries with `WHERE NOT s:IncompleteSession`, or check `has_terminal: false` on the
session node. A WARNING is logged at ingest time: "reached end with no start/fork event; marked
IncompleteSession (recovered)".
