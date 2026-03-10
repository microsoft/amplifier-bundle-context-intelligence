# RecipeHandler Design

## Goal

Replace the broken `RecipeStepHandler` stub (which claims non-existent event names and does nothing) with a working `RecipeHandler` that claims all 6 recipe events with correct names, creates rich `:Event` nodes with derived labels and structured properties, and attaches them to the Session via `HAS_EVENT`.

## Background

The current `RecipeStepHandler` in `handlers/recipe_step.py` is non-functional. It claims event names that no executor emits (`recipe:step_started`, `recipe:step_completed`, `recipe:approval:*`), so its `__call__` is never reached. All recipe events fall through to the `DefaultHandler`, which creates generic `:Event` nodes without extracting the rich structured metadata these events carry.

Meanwhile, CP-5 (RESOLVED in amplifier-bundle-recipes PR #46) confirmed that recipe events now flow correctly through the hook system. Real session data from `44be6956` shows 38 `recipe:loop_iteration`, 22 `recipe:loop_complete`, and 1 `recipe:approval` events — all falling through to the default handler.

The graph data model (`graph-data-model.md` lines 11, 140, 225, 248, 647) establishes that recipe is a second-order concept: no structural `RecipeRun` node. Recipe events become `:Event` nodes with derived labels. This handler implements that design.

## Approach

A dedicated `RecipeHandler` claims all 6 recipe events, creates `:Event:{DerivedLabel}` nodes with structured properties extracted from each event's payload, and attaches them to the Session via `HAS_EVENT`. The handler dispatches internally between two payload shapes (lifecycle events vs loop events) since the recipe executor builds them differently.

## Architecture

```
Session (existing)
  │
  ├── HAS_EVENT ──► Event:RecipeStart         (recipe:start)
  ├── HAS_EVENT ──► Event:RecipeStep          (recipe:step)
  ├── HAS_EVENT ──► Event:RecipeApproval      (recipe:approval)
  ├── HAS_EVENT ──► Event:RecipeLoopIteration  (recipe:loop_iteration)  ×N
  ├── HAS_EVENT ──► Event:RecipeLoopComplete   (recipe:loop_complete)   ×N
  └── HAS_EVENT ──► Event:RecipeComplete       (recipe:complete)
```

All recipe events fire in the **parent session** between orchestrator runs (verified from real data: 6+ minute gaps between `tool:post` and first recipe event). Session-level scoping via `HAS_EVENT` is correct — these are not scoped to an OrchestratorRun or Step.

The handler flow is documented in `context/recipe-handler.dot`.

## Components

### Rename: RecipeStepHandler → RecipeHandler

The file `handlers/recipe_step.py` is renamed to `handlers/recipe.py`. The class is renamed from `RecipeStepHandler` to `RecipeHandler`. The old name was misleading — this handler handles all recipe orchestration events, not just step events.

Update `mount.py` to import from the new location. Update `tests/test_handlers.py` if it references the old class name.

### Correct Event Claims

The current handler claims wrong event names:

```python
# WRONG (current)
handled_events = frozenset({"recipe:step_started", "recipe:step_completed", "recipe:approval:*"})
```

Fix to match what the recipe executor actually emits (verified from CP-5 and real session data):

```python
# CORRECT
handled_events: frozenset[str] = frozenset({
    "recipe:start",
    "recipe:step",
    "recipe:complete",
    "recipe:approval",
    "recipe:loop_iteration",
    "recipe:loop_complete",
})
```

### Event → Node Mapping

Each event creates an `:Event:{DerivedLabel}` node. Labels derived via `derive_label()`:

| Event | Derived Label | Properties Stored |
|-------|--------------|-------------------|
| `recipe:start` | `:Event:RecipeStart` | `recipe_name`, `description`, `total_steps`, `status` |
| `recipe:step` | `:Event:RecipeStep` | `recipe_name`, `step_id` (from `steps[current_step].id`), `step_index` (`current_step`), `total_steps`, `status` |
| `recipe:complete` | `:Event:RecipeComplete` | `recipe_name`, `success`, `total_steps`, `status` |
| `recipe:approval` | `:Event:RecipeApproval` | `recipe_name`, `stage_name`, `approval_prompt` (first 500 chars of `prompt`), `current_step`, `total_steps`, `status` |
| `recipe:loop_iteration` | `:Event:RecipeLoopIteration` | `step_id`, `iteration`, `max_iterations` |
| `recipe:loop_complete` | `:Event:RecipeLoopComplete` | `step_id`, `iterations_completed`, `max_iterations`, `results_count` |

**Exclusions:**
- `context_snapshot` on `recipe:loop_iteration` is NOT stored — it's too large for node properties (contains full task implementation reports). Store only the scalar fields.
- `steps[]` array on `recipe:start`/`recipe:step`/`recipe:approval` is NOT stored as a node property — it's a snapshot of all step statuses and can be very large. The `total_steps` count is sufficient.

### Two Payload Shapes

The handler must handle two distinct payload structures:

**Shape 1 — Lifecycle events** (`recipe:start`, `recipe:step`, `recipe:complete`, `recipe:approval`):
Built by `_build_recipe_event_data()` in the executor. Contains:

```python
{
    "name": "subagent-driven-development",  # recipe name
    "description": "...",
    "current_step": 5,       # 0-based index
    "total_steps": 7,
    "steps": [...],          # status snapshot array — NOT stored
    "status": "running" | "waiting_approval" | "completed" | "failed",
    # + event-specific extras (prompt, stage_name, success)
    # + infra: parent_id, timestamp
}
```

**Shape 2 — Loop events** (`recipe:loop_iteration`, `recipe:loop_complete`):
NOT built by `_build_recipe_event_data()`. Contains:

```python
{
    "step_id": "spec-review-loop",
    "iteration": 1,           # starts at 1
    "max_iterations": 3,
    "context_snapshot": {...}, # LARGE — NOT stored
    # + infra: parent_id, timestamp
}
```

The handler dispatches to `_handle_lifecycle_event()` or `_handle_loop_event()` based on event name.

### Edge Wiring

All recipe events create a `HAS_EVENT` edge from Session to the Event node:

```python
upsert_edge(session_id, event_node_id, "HAS_EVENT", {"occurred_at": timestamp})
```

This follows the `DefaultHandler` pattern and aligns with `11-navigation-graph-model.dot` line 84: `Session -> Event [HAS_EVENT, 1:0..N, (between runs)]`.

### session_id Extraction

Recipe events carry `session_id` at the **top-level event envelope**, not in the `data` dict. The handler's `__call__` receives `data` which may or may not contain `session_id` depending on how the hook system passes it.

Check `data.get("session_id")` first. If not present, the handler cannot create graph nodes — return `HookResult(continue)` without mutations (same error-exit pattern as other handlers).

### Implementation Pattern

Follow the established handler pattern from `OrchestratorRunHandler`:

- `__init__(self, services: HookStateService)` with `HandlerLogger`
- `__call__(self, event, data) -> HookResult` dispatches by event name
- Private `_handle_lifecycle_event()` and `_handle_loop_event()` methods
- Use `make_node_id(session_id, event_name, timestamp)` for node IDs
- Use `derive_label(event_name)` for the derived label (import from `DefaultHandler` or re-implement)

## Data Flow

```
Event Stream                    Handler                         Graph
─────────────                   ───────                         ─────
recipe:start           ──►  _handle_lifecycle_event()     ──►  Event:RecipeStart node
                              extract: name, description,       + HAS_EVENT from Session
                              total_steps, status

recipe:step            ──►  _handle_lifecycle_event()     ──►  Event:RecipeStep node
                              extract: name, step_id,           + HAS_EVENT from Session
                              step_index, total_steps, status

recipe:loop_iteration  ──►  _handle_loop_event()          ──►  Event:RecipeLoopIteration node
                              extract: step_id, iteration,      + HAS_EVENT from Session
                              max_iterations

recipe:loop_complete   ──►  _handle_loop_event()          ──►  Event:RecipeLoopComplete node
                              extract: step_id,                 + HAS_EVENT from Session
                              iterations_completed,
                              max_iterations, results_count

recipe:approval        ──►  _handle_lifecycle_event()     ──►  Event:RecipeApproval node
                              extract: name, stage_name,        + HAS_EVENT from Session
                              prompt[:500], current_step,
                              total_steps, status

recipe:complete        ──►  _handle_lifecycle_event()     ──►  Event:RecipeComplete node
                              extract: name, success,           + HAS_EVENT from Session
                              total_steps, status
```

## Error Handling

- **Missing session_id:** Handler returns `HookResult(continue)` without graph mutations. Logs a warning.
- **Missing payload fields:** All property extraction uses `.get()` with defaults. Lifecycle events may lack optional fields (`description`, `prompt`); loop events always have the core scalars but guard with `.get()` regardless.
- **Unexpected event name:** The `handled_events` frozenset guarantees only the 6 known events reach `__call__`. If an unknown event somehow arrives, log a warning and return `HookResult(continue)`.
- **`derive_label()` edge case:** Event names like `recipe:loop_iteration` produce `RecipeLoopIteration` via the standard derivation (split on `:`, title-case each segment, join). Verify this in tests.

## Testing Strategy

Tests in `tests/test_recipe_handler.py`:

1. **Event claims** — verify `handled_events` contains all 6 correct event names and does NOT contain the old wrong names.

2. **Lifecycle events** — for each of `recipe:start`, `recipe:step`, `recipe:complete`, `recipe:approval`:
   - Creates `:Event:{DerivedLabel}` node
   - Properties include `recipe_name`, `total_steps`, `status`
   - Event-specific properties present (e.g., `stage_name` on approval, `success` on complete)
   - `HAS_EVENT` edge from Session to Event node

3. **Loop events** — for each of `recipe:loop_iteration`, `recipe:loop_complete`:
   - Creates `:Event:{DerivedLabel}` node
   - Properties include `step_id`, `iteration`/`iterations_completed`, `max_iterations`
   - `HAS_EVENT` edge from Session
   - `context_snapshot` is NOT stored as a property

4. **Missing session_id** — returns `HookResult(continue)` without graph mutations.

5. **Real data scenario** — replay exact payloads from session `44be6956`:
   - `recipe:loop_iteration`: `step_id="spec-review-loop"`, `iteration=1`, `max_iterations=3`
   - `recipe:loop_complete`: `step_id="spec-review-loop"`, `iterations_completed=0`, `max_iterations=3`, `results_count=1`
   - `recipe:approval`: `name="subagent-driven-development"`, `stage_name="final-review"`, `current_step=5`, `total_steps=7`, `status="waiting_approval"`

## Data Model Doc Update

`graph-data-model.md` Event→Node Mapping table needs new rows for the 6 recipe events. Currently the table has no recipe event rows. Add:

| Event | Creates / Updates | Labels | Fields Set |
|-------|------------------|--------|------------|
| `recipe:start` | Creates **Event** + `HAS_EVENT` edge | `:Event:RecipeStart` | `recipe_name`, `description`, `total_steps`, `status` |
| `recipe:step` | Creates **Event** + `HAS_EVENT` edge | `:Event:RecipeStep` | `recipe_name`, `step_id`, `step_index`, `total_steps`, `status` |
| `recipe:complete` | Creates **Event** + `HAS_EVENT` edge | `:Event:RecipeComplete` | `recipe_name`, `success`, `total_steps`, `status` |
| `recipe:approval` | Creates **Event** + `HAS_EVENT` edge | `:Event:RecipeApproval` | `recipe_name`, `stage_name`, `approval_prompt`, `current_step`, `total_steps`, `status` |
| `recipe:loop_iteration` | Creates **Event** + `HAS_EVENT` edge | `:Event:RecipeLoopIteration` | `step_id`, `iteration`, `max_iterations` |
| `recipe:loop_complete` | Creates **Event** + `HAS_EVENT` edge | `:Event:RecipeLoopComplete` | `step_id`, `iterations_completed`, `max_iterations`, `results_count` |

## Out of Scope

- **`:RecipeStep` multi-label on Step nodes** — this is the `StepHandler`'s responsibility (checking if the Session has `metadata.recipe_name`). Not handled by `RecipeHandler`.
- **Recipe-spawned child session metadata** (`metadata.recipe_name`, `metadata.recipe_step`, etc.) — already handled by `SessionHandler` via CP-SM passthrough.
- **`recipe:error`** — declared in source but no emit call site found. Not claimed by this handler. Will fall through to `DefaultHandler` if it ever fires.
- **Structural `RecipeRun` node** — the data model explicitly says no structural node (line 647). Recipe runs are reconstructable by querying sessions that share `metadata.recipe_name`.

## Open Questions

None — design is fully validated against the data model and real session data.
