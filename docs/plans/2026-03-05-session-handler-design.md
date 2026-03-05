# SessionHandler Design

## Goal

Design and implement the SessionHandler — the first handler for the context-intelligence hook module — which processes session lifecycle events and creates Session nodes and `SUBSESSION_OF` edges in the GraphStore.

## Background

The context-intelligence hook module observes Amplifier session lifecycle events and builds a graph representation of sessions and their relationships. The SessionHandler is the first concrete handler to be implemented, covering the four session lifecycle events. It serves as both the foundation for the graph and the proving ground for the GraphStore protocol and handler pattern.

This handler is part of a phased implementation sequence:

1. Define the GraphStore protocol as a Python Protocol class (just the interface)
2. Adapt the existing in-memory GraphState to conform to GraphStore
3. Implement SessionHandler using the in-memory GraphStore (testable immediately)
4. Build the DuckDB backend later (swap in behind the same protocol)

## Approach

SessionHandler is stateless — every session lifecycle event carries `session_id` and `parent_id` in infrastructure-injected fields, so no internal tracking or correlation is needed. This is in contrast to orchestrator-run handlers, which must maintain internal state to bracket events that lack run identifiers.

**Design principle:** Use event data directly when available. Only track internal state when the event stream doesn't provide correlation identifiers.

## Events Handled

| Event | Purpose |
|-------|---------|
| `session:start` | New session created (root or subsession) |
| `session:fork` | Session forked from parent with inherited context |
| `session:end` | Session completed |
| `session:resume` | Existing session resumed |

## Label Taxonomy

| Label | Condition |
|-------|-----------|
| `:Session` | Always present on all session nodes |
| `:Root` | `parent_id` is null or empty |
| `:Subsession` | `parent_id` is set (non-null, non-empty, non-whitespace) |
| `:ForkedSession` | Created via `session:fork` (inherits parent context from fork point) |
| `:Resumed` | `session:resume` event received for this session |

A forked session carries labels `{"Session", "Subsession", "ForkedSession"}` — it is a subsession AND it carries fork context. A delegated child session (via `session:start` with `parent_id`) carries only `{"Session", "Subsession"}` — no fork context.

## Edge Type

`SUBSESSION_OF` — from subsession → parent. Direction: child points to parent.

Renamed from `CHILD_OF` to match the `:Subsession` label. This correction was propagated to the research documents (`graph-data-model.md`, `13-navigation-graph-model.dot`, `14-session-instance-55c8841a.dot`).

## Node ID Scheme

- **Session nodes:** `node_id = session_id` (the session_id IS the node ID)
- **Event nodes from session:resume:** `{session_id}:event:session_resume:{timestamp}` (unique per resume occurrence)

## Event-to-Graph Mapping

### Shared Label Logic

```python
parent_id = (data.get("parent_id") or "").strip()
if parent_id:
    labels = {"Session", "Subsession"}
else:
    labels = {"Session", "Root"}
```

`parent_id` must be checked properly — not just present, but actually set (not null, not empty string, not whitespace).

### session:start

- Compute labels from `parent_id` check:
  - No parent → `{"Session", "Root"}`
  - Has parent → `{"Session", "Subsession"}`
- Upsert node: `node_id = data["session_id"]`
- Properties: `started_at` from event timestamp, `status = "running"`, `metadata` from `data.get("metadata", {})` (populated via CP-SM kernel metadata passthrough)
- If Subsession → upsert `SUBSESSION_OF` edge from `data["session_id"]` → `parent_id`

### session:fork

- Labels: `{"Session", "Subsession", "ForkedSession"}`
  - Always a Subsession (fork implies parent)
  - `:ForkedSession` distinguishes from `session:start` with parent — fork means the session inherits parent context from the fork point
- Upsert node: `node_id = data["session_id"]`
- Properties: `started_at` from event timestamp, `status = "running"`, `metadata` from `data.get("metadata", {})` (CP-SM)
- Upsert `SUBSESSION_OF` edge from `data["session_id"]` → `data["parent"]`

### session:end

- Upsert node: `node_id = data["session_id"]`
- Merge properties: `ended_at` from event timestamp, `status` from `data["status"]`
- No gap resilience needed — G1 (session:end never emitted) is fixed by CP-1, confirmed implemented in PR #37

### session:resume

- Upsert node: `node_id = data["session_id"]`, add `"Resumed"` to labels (merge semantics handles label addition without losing existing labels)
- Create Event node: `node_id = f"{data['session_id']}:event:session_resume:{timestamp}"`, labels `{"Event", "SessionResume"}` (via `derive_label()`)
- Properties from event data
- Upsert `HAS_EVENT` edge from `data["session_id"]` → event node id, with `occurred_at`

## Data Flow

```
Amplifier event bus
  │
  ▼
hook module (dispatch)
  │
  ▼
SessionHandler.handle(event_name, data)
  │
  ├─ Compute labels from parent_id
  ├─ Upsert Session node via GraphStore.upsert_node()
  ├─ Upsert SUBSESSION_OF edge via GraphStore.upsert_edge() (if subsession)
  └─ For resume: create Event node + HAS_EVENT edge
```

## Error Handling

- **Upsert semantics throughout:** All node and edge operations use upsert (create-or-merge), making operations idempotent. Duplicate events produce the same graph state.
- **Missing parent_id on fork:** A `session:fork` without a parent is structurally invalid. Log a warning and treat as a root session (degrade gracefully rather than crash).
- **Missing session_id:** Fatal — the event is malformed. Log error and skip.

## Corrections Applied to Research Documents

These corrections were applied during this design session (2026-03-05) and propagated to the research documents:

1. `:Child` → `:Subsession` on Session nodes
2. `CHILD_OF` → `SUBSESSION_OF` edge type
3. Added `:ForkedSession` label for sessions created via `session:fork`
4. `:Prompt` → `:PromptStep` on Step nodes (consistency with `:AssistantStep`, `:RecipeStep`)
5. Event sub-labels use `derive_label()` for all events — no `:Custom` catch-all

Files updated: `graph-data-model.md`, `13-navigation-graph-model.dot`, `14-session-instance-55c8841a.dot`, `README.md` (changelog)

## Dependencies

- **GraphStore protocol** — must be defined first (Python Protocol class with `upsert_node`, `upsert_edge`)
- **GraphState conformance** — existing in-memory implementation adapted to the GraphStore protocol
- **CP-1 (session:end)** — already implemented in PR #37
- **CP-SM (session metadata passthrough)** — already implemented in PR #37

## Testing Strategy

- Unit tests with in-memory GraphState (conforms to GraphStore protocol)
- For each event type: inject synthetic event data, call handler, assert specific `upsert_node`/`upsert_edge` calls
- **Label computation:** verify Root vs Subsession vs ForkedSession assignment
- **parent_id validation:** null, empty string, whitespace-only all produce `:Root`; non-empty trimmed value produces `:Subsession`
- **session:resume:** verify `:Resumed` label added without losing existing labels
- **HAS_EVENT edge:** verify creation for resume events with correct Event node labels
- **derive_label():** verify produces `"SessionResume"` for `session:resume` events

## Open Questions

### CRITICAL: Semantics of session:resume

**Status:** Needs answer from Brian before finalizing resume handler logic.

What are the exact semantics of `session:resume`?

- Does the resumed session get a **new** `session_id` or reuse the original?
- Does it also emit `session:start`, or is `session:resume` the only lifecycle start event?
- What's in the event payload beyond `original_session_id`?
- Does it replay/load the previous transcript?

This affects whether `:Resumed` is a label on the **same** Session node or a **new** Session node that links back to the original. Current design assumes it's a label added to the existing node, but this needs confirmation.

**Message for Brian:**

> We're designing the SessionHandler for context-intelligence. The handler needs to process `session:resume` events, but we're unclear on the semantics. When a session is resumed:
> 1. Does it get a new session_id or reuse the original?
> 2. Does it also emit session:start, or is session:resume the only start event?
> 3. What's in the payload besides original_session_id?
> 4. Does it replay the previous transcript into the new session?
>
> This determines whether we add `:Resumed` to the existing Session node or create a new Session node linked to the original. Currently assuming label on existing node.

### Handler internal state for run correlation (future — not this handler)

Events within an orchestrator execution (`provider:request`, `tool:pre`, etc.) carry NO run/execution identifier — only `session_id`, `parent_id`, and `timestamp`. The orchestrator-run handler will need to maintain a `current_run` pointer internally by tracking `prompt:submit` opens and `orchestrator:complete` closes. This is handler-internal state, not a gap in the event system — events within a single session are sequential, so positional tracking is deterministic. No changes to core needed. Documented here for awareness since it contrasts with SessionHandler's stateless design.
