# Graph Topology and Wiring Fixes Design

## Goal

Fix 6 problems discovered in the Neo4j graph during smoke testing of the context-intelligence hook, grouped into 3 root causes: blob store not wired, ToolExecution node ID collisions, and events landing on Session instead of OrchestratorRun.

## Background

Smoke testing the context-intelligence hook against a real Amplifier session revealed that the graph topology has structural bugs affecting data quality, completeness, and correctness. The problems range from 946K-character inline properties (blob store never connected) to missing tool call nodes (ID collisions on parallel calls) to events orphaned on the Session node when they should be scoped to the active OrchestratorRun.

These are not edge cases — they fire on every session with parallel tool calls or large payloads.

## Problems Found

### Problem 1: Blob processor not running — raw data inline in Neo4j

- `data_llm_request` on AssistantStep: 437,103 chars (full provider request with conversation history)
- `data` on Session Root: 946,291 chars (full mount_plan with all agent descriptions)
- The `raw`, `messages`, and `mount_plan` fields should have been replaced with `{"$blob_ref": uri}` but weren't
- Root cause: `graph_data_hook.py` never creates a `DiskBlobStore` and never passes it to `MountFlow`. The `blob_store` slot is `None` all the way through, so the guard in `_wrap_with_session_guarantee` always short-circuits.

### Problem 2: read_file tool call missing from graph — node ID collision

- LLM response confirms 2 parallel tool calls: bash (`toolu_01G9FD9g...`) + read_file (`toolu_01G6KB6N...`)
- Only 1 ToolExecution node exists (bash). read_file is completely absent.
- Both `tool:pre` events have the same millisecond timestamp → `make_node_id(session_id, "tool:pre", timestamp)` produces the same ID → upsert merges them into one node, second tool overwrites first.

### Problem 3: PARALLEL_WITH self-loop

- The single ToolExecution node has a `PARALLEL_WITH` edge pointing to itself.
- Direct consequence of Problem 2: second `tool:pre` got the same node_id, parallel group code linked "new" node to "existing" one (which is itself).

### Problem 4: Orphan Session node created by orchestrator:complete

- Node `['Session']` with `node_id = <session_id>__execution_start__<ts>` — the OrchestratorRun's ID, not a session ID.
- Has `status=complete`, `turn_count=2`, `data_orchestrator_complete`.
- The session-guarantee wrapper called `ensure_session_node()` and bootstrapped a Session node using the run's synthetic node_id.
- Meanwhile the actual OrchestratorRun node is stuck at `status=in_progress`.

### Problem 5: artifact:read attached to Session instead of inside the run

- DefaultHandler creates `Event:ArtifactRead` → `HAS_EVENT` → Session.
- This file read happened during tool execution within a run — should be connected to the OrchestratorRun.

### Problem 6: prompt:complete attached to Session instead of inside the run

- Same pattern as Problem 5 — DefaultHandler creates `Event:PromptComplete` → `HAS_EVENT` → Session.
- This event signals the full prompt/response cycle completed — should be associated with the OrchestratorRun.

## Approach

Three independent fixes, one per root cause. Each is small and surgical — the infrastructure already exists, it just needs connecting or adjusting.

| Root Cause | Problems | Fix Summary |
|---|---|---|
| A: Blob store not wired | 1 | Instantiate `DiskBlobStore` in `graph_data_hook.py`, pass to `MountFlow` |
| B: Node ID collision | 2, 3 | Include `tool_call_id` in ToolExecution node IDs |
| C: Events on wrong parent | 4, 5, 6 | Make DefaultHandler run-aware via `cursors.current_run_id` |

## Architecture

No new components. All fixes modify existing modules in the graph data hook pipeline:

```
EventStream
  └─> graph_data_hook.py          ← Root A fix: wire blob_store
        └─> MountFlow
              ├─> blob_processor   (already correct, just never called)
              ├─> handlers/
              │     ├─> tool_execution.py  ← Root B fix: tool_call_id in node ID
              │     ├─> default.py         ← Root C fix: run-aware HAS_EVENT
              │     └─> ...
              └─> session_guarantee        ← Root C: investigate orphan node
```

## Components

### Root A: Blob Store Wiring (Problem 1)

#### `config_resolver.py` — Add `blob_store_root` property

Add a property that resolves to the project-level directory (the parent directory that contains session subdirectories). This is where blob files live alongside `events.jsonl`.

#### `graph_data_hook.py` — Wire `DiskBlobStore` to `MountFlow`

Import `DiskBlobStore`, instantiate it from `resolver.blob_store_root`, pass it to the `MountFlow` constructor via the `blob_store=` parameter. Three lines of code.

Once wired, the existing blob processor in `mount.py` fires, handlers receive processed clones with `$blob_ref` URIs, and the 437K/946K inline properties become small reference strings.

### Root B: ToolExecution Node ID Collision (Problems 2, 3)

#### `utils.py` — Add optional `disambiguator` param to `make_node_id`

`make_node_id` gains an optional `disambiguator` parameter. When provided, it is appended to the generated ID. When omitted, behavior is unchanged — full backward compatibility.

The resulting ID format for ToolExecution: `session_id__tool_pre__epoch_ms__tool_call_id`

#### `handlers/tool_execution.py` — Pass `tool_call_id` to `make_node_id`

`ToolExecutionHandler._handle_tool_pre` extracts `tool_call_id` from the event data and passes it into `make_node_id` as the disambiguator.

The `tool_call_id` is unique per tool call (assigned by the LLM), preserves deterministic replay (same inputs → same ID), and only affects ToolExecution — no change needed for other node types.

This also fixes the PARALLEL_WITH self-loop: with distinct node IDs, the parallel group tracking correctly creates edges between different nodes.

### Root C: DefaultHandler Run-Awareness (Problems 4, 5, 6)

#### `handlers/default.py` — Become run-aware

Check `cursors.current_run_id` before creating the `HAS_EVENT` edge. If an active run exists, attach the event to the OrchestratorRun. Fall back to Session if no active run.

```
Before:  Session --[HAS_EVENT]--> Event:ArtifactRead
After:   OrchestratorRun --[HAS_EVENT]--> Event:ArtifactRead
         (falls back to Session --[HAS_EVENT]--> if no active run)
```

This covers `artifact:read`, `prompt:complete`, `content_block:start/end`, and any future unclaimed event that fires during a run. Events outside a run (like `session:resume`) still attach to Session.

#### Orphan Session node (Problem 4)

Needs investigation during implementation. The session-guarantee wrapper may be creating a Session node using the run's node_id when `orchestrator:complete` fires. The wrapper's `session_id` extraction logic needs to be verified against how the orchestrator populates the event data.

### Documentation and Dot File Updates

The graph schema changes affect downstream documentation:

- **Dot files** in `context/` — the graph schema diagram needs updating: DefaultHandler's `HAS_EVENT` now targets OrchestratorRun (when active) instead of always Session, and ToolExecution node IDs include `tool_call_id` as disambiguator.
- **SKILL.md** at `skills/context-intelligence-neo4j-search/SKILL.md` — the node ID format for ToolExecution changes, the `HAS_EVENT` edge semantics change (run-scoped vs session-scoped).
- **Any existing design docs** that reference the graph topology need a note about the DefaultHandler run-awareness change.

## Data Flow

### Blob Store (after fix)

```
Event arrives at graph_data_hook
  → MountFlow receives event + DiskBlobStore instance
    → blob_processor checks fields against size thresholds
      → large fields written to disk as blob files
      → event clone gets {"$blob_ref": "blob://session/field/hash"} in place of raw data
        → handlers receive slim event clone
          → Neo4j properties stay small
```

### ToolExecution (after fix)

```
tool:pre event (tool_call_id=toolu_01G9FD9g)
  → make_node_id(session, "tool:pre", ts, "toolu_01G9FD9g")
    → node_id = "session__tool_pre__1710000000000__toolu_01G9FD9g"

tool:pre event (tool_call_id=toolu_01G6KB6N)  [same millisecond]
  → make_node_id(session, "tool:pre", ts, "toolu_01G6KB6N")
    → node_id = "session__tool_pre__1710000000000__toolu_01G6KB6N"

Two distinct nodes → PARALLEL_WITH edge between them (not self-loop)
```

### DefaultHandler Event Routing (after fix)

```
Event fires during active run:
  → DefaultHandler checks cursors.current_run_id
    → run_id exists → OrchestratorRun --[HAS_EVENT]--> Event node

Event fires outside any run:
  → DefaultHandler checks cursors.current_run_id
    → run_id is None → Session --[HAS_EVENT]--> Event node
```

## Error Handling

- **Blob store write failure**: Already handled in `blob_processor.py` — stores `{"$blob_error": "write failed: <reason>"}` in the clone. No change needed.
- **Missing `tool_call_id` in tool:pre**: Fall back to current behavior (timestamp-only node ID). Log a warning.
- **Missing `current_run_id` when DefaultHandler checks**: Fall back to Session (existing behavior). This is the normal case for events outside a run.

## Testing Strategy

- **Unit tests for `make_node_id`**: Verify new disambiguator parameter works, verify backward compatibility when omitted.
- **Unit tests for `ToolExecutionHandler`**: Two parallel `tool:pre` events with the same timestamp but different `tool_call_id`s produce distinct nodes and correct `PARALLEL_WITH` edges.
- **Unit tests for `DefaultHandler`**: Event during active run → `HAS_EVENT` targets run. Event with no active run → `HAS_EVENT` targets Session.
- **Integration test for blob wiring**: After a real session, verify at least one node has `$blob_ref` in its data property.
- **Smoke test**: Shadow container with real Amplifier session, verify:
  - (a) No 400K+ inline properties
  - (b) Both parallel tool calls have distinct ToolExecution nodes with correct `PARALLEL_WITH` edge
  - (c) Unclaimed events during a run attach to OrchestratorRun
  - (d) OrchestratorRun reaches `status=complete`

## Files to Create or Modify

| Action | File | Purpose |
|---|---|---|
| MODIFY | `config_resolver.py` | Add `blob_store_root` property |
| MODIFY | `graph_data_hook.py` | Instantiate `DiskBlobStore`, pass to `MountFlow` |
| MODIFY | `utils.py` | Add optional `disambiguator` param to `make_node_id` |
| MODIFY | `handlers/tool_execution.py` | Pass `tool_call_id` to `make_node_id` |
| MODIFY | `handlers/default.py` | Become run-aware — attach `HAS_EVENT` to run when active |
| MODIFY | `tests/test_utils.py` | Test `make_node_id` with disambiguator |
| MODIFY | `tests/test_tool_execution_handler.py` | Test parallel tools produce distinct nodes |
| MODIFY | `tests/test_default_handler.py` | Test run-scoped vs session-scoped `HAS_EVENT` |
| MODIFY | `tests/test_blob_store.py` or integration tests | Test blob wiring end-to-end |
| MODIFY | `context/*.dot` or `context/*.md` | Update graph schema diagrams |
| MODIFY | `skills/context-intelligence-neo4j-search/SKILL.md` | Update schema docs |

## Open Questions

1. **Orphan Session node origin**: Is the session-guarantee wrapper extracting `session_id` correctly from `orchestrator:complete`, or is it using a field that contains the run's node_id? Needs investigation during implementation.
2. **`prompt:complete` enrichment**: Should `prompt:complete` also enrich the OrchestratorRun node (set final status/turn_count) in addition to being an Event node? Currently `orchestrator:complete` does this, but `prompt:complete` fires too and carries similar data.
3. **`content_block:start/end` scope**: Should these events (currently no-op in StepHandler) also become run-scoped Event nodes via DefaultHandler, or stay as no-ops?
