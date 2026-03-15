# Graph Model Reference

> **Provenance:** Derived from the context-intelligence property graph schema used to store Amplifier session data in Neo4j. This document is the authoritative reference for agents constructing Cypher queries or reasoning about graph topology.

---

## Node Types

Five node types capture the full lifecycle of an Amplifier session.

### Session

Represents an Amplifier conversation session — either a root session initiated by the user, a child session spawned by a `delegate` tool call, or a logical subsession grouping.

| Property | Type | Description |
|---|---|---|
| `session_id` | string | Primary identifier — unique across the forest. |
| `parent_id` | string \| null | Parent session ID for child/subsession nodes; null for root. |
| `graph_forest_name` | string | Workspace scope key — all nodes in a workspace share this value. |
| `created_at` | string | ISO 8601 creation timestamp. |
| `status` | string | `active` \| `complete` \| `error` |
| `bundle` | string | Bundle name active for this session (if known). |
| `model` | string | Model identifier used (if known). |

**Sub-labels:** Nodes carry one or more additional labels:
- `:Root` — top-level session (no parent).
- `:Child` — session spawned by a `delegate` tool call from a parent.
- `:Subsession` — logical grouping subsession (e.g. recipe stage).

---

### OrchestratorRun

Represents a single invocation of the Amplifier orchestrator loop within a session. One session may have multiple orchestrator runs (e.g. after a tool result is processed).

| Property | Type | Description |
|---|---|---|
| `run_id` | string | Unique run identifier (node ID format). |
| `session_id` | string | Owning session. |
| `graph_forest_name` | string | Workspace scope key. |
| `started_at` | string | ISO 8601 start timestamp. |
| `finished_at` | string \| null | ISO 8601 finish timestamp; null if still active. |
| `status` | string | `running` \| `complete` \| `error` |
| `step_count` | integer | Number of steps executed in this run. |

---

### Step

Represents one logical step within an orchestrator run: either a prompt submission, an assistant response, or a recipe execution.

| Property | Type | Description |
|---|---|---|
| `step_id` | string | Unique step identifier (node ID format). |
| `session_id` | string | Owning session. |
| `run_id` | string | Owning orchestrator run. |
| `graph_forest_name` | string | Workspace scope key. |
| `sequence` | integer | Monotonically increasing position within the run. |
| `timestamp` | string | ISO 8601 timestamp. |
| `content_blob_ref` | string \| null | Blob storage reference for large text payloads (see Constraints). |

**Sub-labels:**
- `:PromptStep` — a user or system prompt submitted to the model.
- `:AssistantStep` — an assistant response (may include tool calls).
- `:RecipeStep` — a recipe execution step with `recipe_id` and `stage` properties.

---

### ToolExecution

Represents a single tool call and its result within an assistant step.

| Property | Type | Description |
|---|---|---|
| `execution_id` | string | Unique execution identifier (node ID format). |
| `session_id` | string | Owning session. |
| `step_id` | string | Owning assistant step. |
| `graph_forest_name` | string | Workspace scope key. |
| `tool_name` | string | Name of the tool invoked (e.g. `delegate`, `bash`, `read_file`). |
| `tool_call_id` | string | Provider-assigned call ID from the transcript. |
| `started_at` | string | ISO 8601 start timestamp. |
| `finished_at` | string \| null | ISO 8601 finish timestamp. |
| `duration_ms` | integer \| null | Execution duration in milliseconds. |
| `status` | string | `success` \| `error` \| `pending` |
| `input_blob_ref` | string \| null | Blob reference for tool input payload. |
| `output_blob_ref` | string \| null | Blob reference for tool output payload. |

**Sub-label:**
- `:Delegation` — specifically a `delegate` tool call that spawned a child session. Carries additional properties:
  - `child_session_id` — ID of the spawned child session.
  - `agent` — agent identifier passed to the delegate call.

---

### Event

Represents a raw Amplifier event recorded in `context-intelligence/events.jsonl`. Events are the ground truth from which all other nodes are derived.

| Property | Type | Description |
|---|---|---|
| `event_id` | string | Unique event identifier (node ID format). |
| `session_id` | string | Owning session. |
| `graph_forest_name` | string | Workspace scope key. |
| `event_type` | string | Event name (e.g. `session:start`, `tool:pre`, `tool:post`). |
| `timestamp` | string | ISO 8601 timestamp from the event. |
| `data_blob_ref` | string \| null | Blob reference for the full `data` payload when it exceeds inline size limits. |

---

## Edge Types

Eight directed edge types describe relationships between nodes.

| Edge | From → To | Description |
|---|---|---|
| `HAS_RUN` | `Session` → `OrchestratorRun` | Links a session to each orchestrator run it contains. |
| `HAS_STEP` | `OrchestratorRun` → `Step` | Links an orchestrator run to each step it contains. |
| `NEXT` | `Step` → `Step` | Sequential ordering within a run; `sequence` property on both nodes provides ordering. |
| `TRIGGERED` | `Step` → `ToolExecution` | Links an assistant step to each tool execution it triggered. |
| `PARALLEL_WITH` | `ToolExecution` → `ToolExecution` | Links concurrent tool executions within the same assistant step. Symmetric in intent but stored as directed. |
| `SPAWNED` | `ToolExecution:Delegation` → `Session:Child` | Links a delegation tool execution to the child session it created. |
| `SUBSESSION_OF` | `Session:Subsession` → `Session` | Links a subsession node to its logical parent session. |
| `HAS_EVENT` | `Session` → `Event` | Links a session to every raw event recorded for it. |

---

## Node ID Format

All node identifiers use a canonical encoded format to guarantee uniqueness across the forest:

```
<session_id>:<event_type>:<timestamp_ms>:<tool_call_id>
```

| Segment | Description |
|---|---|
| `session_id` | Full Amplifier session ID (e.g. `f881e0a0-c055-4ee4-84ed-ff44703150ea`). |
| `event_type` | Event name with `:` replaced by `_` (e.g. `tool_pre`). |
| `timestamp_ms` | Unix epoch milliseconds derived from the ISO 8601 event timestamp. |
| `tool_call_id` | Provider tool call ID when applicable; `_` (underscore) for non-tool events. |

**Examples:**

```
# Session node
f881e0a0-c055-4ee4-84ed-ff44703150ea:session_start:1742018532000:_

# ToolExecution node
f881e0a0-c055-4ee4-84ed-ff44703150ea:tool_pre:1742018545123:call_abc123
```

---

## Workspace Scoping

All nodes carry a `graph_forest_name` property. This property is the **primary workspace boundary** — every query MUST filter by `graph_forest_name` unless explicitly doing cross-workspace analysis.

The `graph_forest_name` is derived from the workspace root directory path (normalised and slugified). Two sessions sharing the same working directory belong to the same forest.

**Always scope queries:**

```cypher
MATCH (s:Session {graph_forest_name: $forest})
...
```

---

## Common Cypher Query Patterns

### 1. Session Overview

Returns all sessions in the forest with their status and step counts.

```cypher
MATCH (s:Session {graph_forest_name: $forest})
OPTIONAL MATCH (s)-[:HAS_RUN]->(r:OrchestratorRun)-[:HAS_STEP]->(step:Step)
RETURN
  s.session_id   AS session_id,
  labels(s)      AS labels,
  s.status       AS status,
  s.created_at   AS created_at,
  s.bundle       AS bundle,
  count(step)    AS total_steps
ORDER BY s.created_at DESC
LIMIT 200
```

---

### 2. Delegation Tree

Returns the full parent→child delegation hierarchy rooted at a given session, up to 5 levels deep.

```cypher
MATCH path = (root:Session {session_id: $root_session_id, graph_forest_name: $forest})
             -[:HAS_RUN]->(:OrchestratorRun)
             -[:HAS_STEP]->(:Step)
             -[:TRIGGERED]->(d:ToolExecution:Delegation)
             -[:SPAWNED]->(child:Session)
WITH root, child, d, length(path) AS depth
WHERE depth <= 5
RETURN
  root.session_id   AS parent_session,
  child.session_id  AS child_session,
  d.agent           AS agent,
  d.started_at      AS spawned_at,
  child.status      AS child_status
ORDER BY d.started_at
```

---

### 3. Tool Usage Distribution

Returns a count of each tool used across all sessions in the forest.

```cypher
MATCH (s:Session {graph_forest_name: $forest})
      -[:HAS_RUN]->(:OrchestratorRun)
      -[:HAS_STEP]->(:Step)
      -[:TRIGGERED]->(t:ToolExecution)
RETURN
  t.tool_name   AS tool,
  count(t)      AS invocations,
  avg(t.duration_ms) AS avg_duration_ms
ORDER BY invocations DESC
LIMIT 50
```

---

### 4. Session Timeline

Returns an ordered sequence of steps and tool executions for a single session, suitable for rendering as a timeseries or swim-lane chart.

```cypher
MATCH (s:Session {session_id: $session_id, graph_forest_name: $forest})
      -[:HAS_RUN]->(r:OrchestratorRun)
      -[:HAS_STEP]->(step:Step)
OPTIONAL MATCH (step)-[:TRIGGERED]->(t:ToolExecution)
RETURN
  r.run_id        AS run_id,
  step.step_id    AS step_id,
  step.sequence   AS sequence,
  labels(step)    AS step_labels,
  step.timestamp  AS step_timestamp,
  t.tool_name     AS tool_name,
  t.started_at    AS tool_started_at,
  t.finished_at   AS tool_finished_at,
  t.duration_ms   AS duration_ms,
  t.status        AS tool_status
ORDER BY r.started_at, step.sequence, t.started_at
```

---

## Constraints

### Node Limit

Queries MUST include `LIMIT 200` when returning unbounded node sets. The context-intelligence agent will refuse to render graphs with more than **200 nodes** in a single NetworkGraph component. Use aggregation or filtering to stay within this limit.

### Forest Scoping

Every query MUST include `{graph_forest_name: $forest}` on the anchor node unless cross-forest analysis is explicitly requested. Unscoped queries will return data from all workspaces and are not permitted in agent-facing tools.

### Blob References

Large text payloads (step content, tool inputs/outputs, event data) are not stored inline in the graph. Nodes carry a `*_blob_ref` property pointing into the blob storage layer. To retrieve the full payload, use the `blob_reader` tool with the reference string. **Do not** attempt to reconstruct blob content from graph properties alone.

### Timestamp Format

All timestamps stored in the graph are **ISO 8601 strings** (e.g. `2026-03-15T06:22:12.000+00:00`). The node ID format uses Unix epoch milliseconds for compactness, but graph properties always use the human-readable ISO 8601 form. Use `datetime()` in Cypher for range comparisons:

```cypher
WHERE datetime(s.created_at) >= datetime($since)
```
