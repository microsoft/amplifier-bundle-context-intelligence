# Orchestrator Run, Step, and Tool Execution Handlers Design

## Goal

Implement the three stub handlers (OrchestratorRunHandler, StepHandler, ToolExecutionHandler) that progressively assemble the OrchestratorRun subgraph from events, aligned with `11-navigation-graph-model.dot`.

## Background

The graph model defines a rich subgraph beneath each Session: OrchestratorRun → Step → ToolExecution, with edges encoding execution order (NEXT), containment (HAS_RUN, HAS_STEP), causality (TRIGGERED), concurrency (PARALLEL_WITH), and delegation (SPAWNED). The current codebase has stub handlers that accept events but don't build this structure. We need to implement the progressive assembly logic that turns 13 distinct events into the full OrchestratorRun subgraph.

**Grounded in:** `11-navigation-graph-model.dot` (5 node types, 8 edge types), `graph-data-model.md` (N2, N3, N4 specs), real session data from `44be6956` (verified event flow), `03-single-turn-event-flow.dot` (event ordering).

## Approach

Session-keyed cursors on `HookStateService` track mutable cross-event state per session. Three handlers consume 13 events to build the full OrchestratorRun subgraph: nodes (OrchestratorRun, Step, ToolExecution) and edges (HAS_RUN, HAS_STEP, NEXT, TRIGGERED, PARALLEL_WITH, SPAWNED). Each event either creates a node, creates an edge, enriches an existing node, or updates cursors.

## Architecture

```
Session (existing)
  │
  ├── HAS_RUN ──► OrchestratorRun     (created by execution:start)
  │                  │
  │                  ├── HAS_STEP {seq:0} ──► PromptStep      (created by prompt:submit)
  │                  │
  │                  ├── HAS_STEP {seq:1} ──► AssistantStep    (created by provider:request)
  │                  │                           │
  │                  │                           ├── TRIGGERED ──► ToolExecution  (created by tool:pre)
  │                  │                           │                    │
  │                  │                           │                    ├── PARALLEL_WITH ──► ToolExecution
  │                  │                           │                    └── SPAWNED ──► Session (child)
  │                  │                           │
  │                  │                           └── NEXT ──► AssistantStep {seq:2}
  │                  │
  │                  └── HAS_STEP {seq:2} ──► AssistantStep
  │
  └── HAS_RUN ──► OrchestratorRun (next turn)
```

Three handlers own distinct parts of this subgraph:

- **OrchestratorRunHandler** — OrchestratorRun node lifecycle + PromptStep creation + HAS_RUN edge
- **StepHandler** — AssistantStep nodes + HAS_STEP/NEXT edges + LLM enrichment
- **ToolExecutionHandler** — ToolExecution nodes + TRIGGERED/PARALLEL_WITH/SPAWNED edges

## Components

### SessionCursors — Per-Session State Tracking

Add to `services.py`:

```python
@dataclass
class SessionCursors:
    current_run_id: str | None = None
    current_step_id: str | None = None
    run_counter: int = 0
    step_counter: int = 0
    prompt_preview: str = ""
    parallel_groups: dict[str, list[str]] = field(default_factory=dict)
    tool_call_map: dict[str, str] = field(default_factory=dict)
```

Add to `HookStateService`:

```python
self._cursors: dict[str, SessionCursors] = {}

def get_cursors(self, session_id: str) -> SessionCursors:
    if session_id not in self._cursors:
        self._cursors[session_id] = SessionCursors()
    return self._cursors[session_id]

def remove_cursors(self, session_id: str) -> None:
    self._cursors.pop(session_id, None)
```

**Design decisions:**

- Cursors live on `HookStateService`, not on `GraphStore` — they're ephemeral graph-building bookkeeping, not persisted data.
- Keyed by `session_id` — parallel child sessions each get their own cursor state. A flat `current_run_id` would break under concurrent delegations.
- Lazy init via `get_cursors()` — forgiving of event ordering edge cases.
- `SessionHandler.session:end` calls `remove_cursors()` to clean up.
- The 5 vestigial cursor fields on `GraphState` (`current_session`, `current_run`, `current_step`, `step_counter`, `pending_delegate_tool_call_id`) are removed — they were never used and lived on the wrong class.

### OrchestratorRunHandler (4 events)

#### `prompt:submit`

Creates the PromptStep node and stores state on cursors. No edges created — those come from `execution:start`.

1. Extract `session_id`, `timestamp`, `prompt` from data
2. Validate session exists: `get_node(session_id)` — error exit if None
3. Get cursors: `services.get_cursors(session_id)`
4. `cursors.run_counter += 1`, `cursors.step_counter = 0`
5. Generate PromptStep ID: `make_node_id(session_id, "prompt:submit", timestamp)`
6. Create PromptStep node: `upsert_node(node_id, {"Step", "PromptStep"}, {iteration: 0, prompt_text, prompt_preview, occurred_at, session_id})`
7. Store on cursors: `current_step_id = node_id`, `prompt_preview = prompt[:200]`

#### `execution:start`

Creates the OrchestratorRun node and wires it to the Session and the PromptStep.

1. Get cursors: `services.get_cursors(session_id)`
2. Generate OrchestratorRun ID: `make_node_id(session_id, "execution:start", timestamp)`
3. Create OrchestratorRun node: `upsert_node(run_id, {"OrchestratorRun"}, {run_number: cursors.run_counter, started_at: timestamp, status: "in_progress", prompt_preview: cursors.prompt_preview, session_id})`
4. Set `cursors.current_run_id = run_id`
5. Create `HAS_RUN` edge: `upsert_edge(session_id, run_id, "HAS_RUN", {seq: cursors.run_counter, occurred_at})`
6. Create `HAS_STEP` edge: `upsert_edge(run_id, cursors.current_step_id, "HAS_STEP", {seq: 0, occurred_at})`

#### `execution:end`

Timing enrichment on OrchestratorRun.

1. Upsert `cursors.current_run_id` with `{execution_ended_at: timestamp}`
2. If payload has `response` and `status`, store those too (graceful if absent — CP-7 payload fix not fully landed in loop-streaming)

#### `orchestrator:complete`

Closes the OrchestratorRun. This is the reliable terminal event — fires on all paths including cancellation.

1. Upsert `cursors.current_run_id` with `{ended_at: timestamp, status (mapped: success→complete, cancelled→cancelled, error→error), turn_count}`
2. `cursors.current_run_id = None`

### StepHandler (4 event patterns)

#### `provider:request`

Creates AssistantStep nodes (iteration >= 1) and wires the NEXT chain.

1. Get cursors: `services.get_cursors(session_id)`
2. `cursors.step_counter += 1`
3. Clear `cursors.parallel_groups = {}` (new step, new batch context)
4. Generate AssistantStep ID: `make_node_id(session_id, "provider:request", timestamp)`
5. Determine label: `:AssistantStep` for interactive sessions, `:RecipeStep` for recipe-spawned sessions (check `Session.metadata.recipe_name` if available)
6. Create Step node: `upsert_node(step_id, {"Step", label}, {iteration: data.iteration, provider: data.provider, request_at: timestamp, occurred_at: timestamp, session_id})`
7. Create `HAS_STEP` edge: `upsert_edge(cursors.current_run_id, step_id, "HAS_STEP", {seq: cursors.step_counter, occurred_at})`
8. Create `NEXT` edge: `upsert_edge(cursors.current_step_id, step_id, "NEXT", {occurred_at})`
9. Update cursor: `cursors.current_step_id = step_id`

#### `llm:request`

Enriches the current Step with model information.

1. Upsert `cursors.current_step_id` with `{model: data.model}`
2. If `raw` field present (post-CP-V, when `session.raw: true`), store `raw_request_ref` reference

#### `llm:response`

Enriches the current Step with token counts, timing, and finish reason.

1. Upsert `cursors.current_step_id` with:
   - `input_tokens`, `output_tokens`, `cached_tokens`, `reasoning_tokens` (from `data.usage`)
   - `finish_reason` (from `data.finish_reason` or inferred from content blocks)
   - `response_at: timestamp`
   - `latency_ms` (computed from `request_at` if available)
2. If `raw` field present, store `raw_response_ref` reference

#### `content_block:*`

Enriches the current Step with content block metadata.

- `content_block:start`: increment `content_block_count` on current step
- `content_block:end`: if block type indicates thinking, set `has_thinking = True`

### ToolExecutionHandler (5 events)

#### `tool:pre`

Creates ToolExecution node, wires TRIGGERED edge, and builds PARALLEL_WITH incrementally.

1. Get cursors: `services.get_cursors(session_id)`
2. Generate ToolExecution ID: `make_node_id(session_id, "tool:pre", timestamp)` — note: `tool_call_id` from payload is stored as a property but not used as the node ID (node IDs use the consistent `make_node_id` pattern)
3. Create ToolExecution node: `upsert_node(te_id, {"ToolExecution"}, {tool_call_id, tool_name, parallel_group_id, started_at: timestamp, status: "executing", session_id})`
4. Create `TRIGGERED` edge: `upsert_edge(cursors.current_step_id, te_id, "TRIGGERED", {seq: step's tool dispatch seq, occurred_at})`
5. `PARALLEL_WITH` wiring:
   - Look up `parallel_group_id` from data
   - If `parallel_groups[parallel_group_id]` already has entries, create `PARALLEL_WITH` edge from new TE to every existing TE in that group
   - Append `te_id` to `parallel_groups[parallel_group_id]`
6. Populate cursor lookup: `cursors.tool_call_map[tool_call_id] = te_id`

#### `tool:post`

Enriches ToolExecution with completion data.

1. Find ToolExecution node via `cursors.tool_call_map[tool_call_id]`
2. Upsert with `{ended_at: timestamp, status: "complete", result_preview: result[:500]}`

#### `tool:error`

Enriches ToolExecution with error status.

1. Find ToolExecution node via `cursors.tool_call_map[tool_call_id]`
2. Upsert with `{status: "error", error: data.error}`

#### `delegate:agent_spawned`

Adds `:Delegation` label and wires `SPAWNED` edge to child session.

1. Find ToolExecution node via `cursors.tool_call_map[tool_call_id]`
2. Add `:Delegation` label: upsert with labels `{"ToolExecution", "Delegation"}`
3. Set properties: `{child_session_id, child_agent: data.agent_name}`
4. Create `SPAWNED` edge: `upsert_edge(te_id, child_session_id, "SPAWNED", {occurred_at})`

#### `delegate:agent_completed`

Enriches the Delegation ToolExecution with completion.

1. Find ToolExecution node via `cursors.tool_call_map[tool_call_id]`
2. Upsert enrichment properties

## Data Flow

```
Event Stream                    Cursors                     Graph
─────────────                   ───────                     ─────
prompt:submit        ──►  run_counter++, step_counter=0  ──►  PromptStep node
                          current_step_id = ps_id
                          prompt_preview = prompt[:200]

execution:start      ──►  current_run_id = run_id       ──►  OrchestratorRun node
                                                          ──►  HAS_RUN edge
                                                          ──►  HAS_STEP {seq:0} edge

provider:request     ──►  step_counter++                 ──►  AssistantStep node
                          parallel_groups = {}            ──►  HAS_STEP edge
                          current_step_id = step_id       ──►  NEXT edge

llm:request          ──►  (reads current_step_id)        ──►  Step enrichment (model)
llm:response         ──►  (reads current_step_id)        ──►  Step enrichment (tokens, timing)
content_block:*      ──►  (reads current_step_id)        ──►  Step enrichment (block count)

tool:pre             ──►  tool_call_map[tcid] = te_id    ──►  ToolExecution node
                          parallel_groups updated         ──►  TRIGGERED edge
                                                          ──►  PARALLEL_WITH edge(s)

tool:post            ──►  (reads tool_call_map)           ──►  ToolExecution enrichment
tool:error           ──►  (reads tool_call_map)           ──►  ToolExecution enrichment

delegate:spawned     ──►  (reads tool_call_map)           ──►  Delegation label + SPAWNED edge
delegate:completed   ──►  (reads tool_call_map)           ──►  Delegation enrichment

execution:end        ──►  (reads current_run_id)          ──►  OrchestratorRun enrichment
orchestrator:complete──►  current_run_id = None           ──►  OrchestratorRun closed
```

## Error Handling

- **Missing session node:** `prompt:submit` validates session exists via `get_node(session_id)`. If None, logs error and returns early — no PromptStep created, no cursors mutated.
- **Missing cursor state:** All handlers use `get_cursors()` which lazily initializes. If `current_run_id` or `current_step_id` is None when expected, the handler logs a warning and returns early rather than crashing.
- **Missing tool_call_id mapping:** `tool:post`, `tool:error`, and `delegate:*` look up `tool_call_map`. If the key is missing (e.g., `tool:pre` was missed), log a warning and skip enrichment.
- **Out-of-order events:** The cursor pattern is tolerant — `upsert_node` is idempotent, so duplicate or reordered events that create the same node will merge properties rather than fail.
- **Incomplete payloads:** `execution:end` gracefully handles missing `response`/`status` fields (CP-7 payload fix not fully landed). All property extraction uses `.get()` with defaults.
- **Cleanup on session end:** `SessionHandler.session:end` calls `remove_cursors()` to prevent memory leaks from abandoned sessions.

## Testing Strategy

- **Unit tests per handler:** Each handler gets tests for its events using the reference session data from `44be6956`. Verify node creation, edge creation, property enrichment, and cursor state after each event.
- **Integration tests:** Full event sequence replay from real session data. Assert the complete subgraph shape matches expected: correct node counts, edge types, property values, and NEXT chain ordering.
- **Parallel tool execution tests:** Verify PARALLEL_WITH edges are correctly wired when multiple `tool:pre` events share the same `parallel_group_id`.
- **Delegation tests:** Verify `delegate:agent_spawned` adds the `:Delegation` label and creates the `SPAWNED` edge to the child session.
- **Error path tests:** Missing session, None cursor IDs, missing tool_call_id mappings — verify graceful degradation with warning logs instead of crashes.
- **Multi-turn tests:** Verify `run_counter` increments across turns and each OrchestratorRun gets its own step chain.

## Tool Call ID Lookup

`tool:post`, `tool:error`, `delegate:agent_spawned`, and `delegate:agent_completed` need to find the ToolExecution node created by `tool:pre`. These events carry `tool_call_id` in their payload.

**Chosen approach:** `tool_call_map: dict[str, str]` on `SessionCursors` mapping `tool_call_id → node_id`. Populated by `tool:pre`, consumed by `tool:post`/`tool:error`/`delegate:*`. Cleared on `orchestrator:complete`. This is consistent with the cursor pattern and avoids a graph read per event.

**Rejected alternative:** Querying the graph for a ToolExecution node with matching `tool_call_id` property. Works but requires a read per event and couples handler logic to query capabilities.

## DOT Diagrams

Three DOT files in `context/`:

1. **`prompt-submit-handler.dot`** (updated) — corrected flow: creates PromptStep only, no edges, stores on cursors
2. **`orchestrator-run-assembly.dot`** (new) — progressive assembly across all 13 events and 3 handlers, color-coded by action type
3. **`orchestrator-run-states.dot`** (new) — OrchestratorRun state machine (in_progress → complete/cancelled/error)

## Data Model Updates Needed

`graph-data-model.md` Event→Node Mapping table (line 409) currently says `prompt:submit` creates OrchestratorRun. Update to reflect:

- `prompt:submit` → creates PromptStep only
- `execution:start` → creates OrchestratorRun, wires `HAS_RUN` and `HAS_STEP {seq:0}`

Also update `11-navigation-graph-model.dot` N2 node annotation: `Created by: prompt:submit` → `Created by: execution:start`

## Out of Scope

- **SystemEventHandler** (`context:compaction`, `cancel:requested`, `cancel:completed`) — separate design
- **RecipeStepHandler** — event names need fixing first (CP-5 mismatch), separate design
- **DefaultHandler HAS_EVENT scoping** — currently flat Session attachment, should scope to innermost active node. Can be improved once cursors provide `current_run_id`/`current_step_id`
