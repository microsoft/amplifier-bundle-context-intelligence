# Hook Mount and Registration Flow Design

## Goal

Design the hook mount and event registration flow for the context-intelligence bundle — a deterministic state machine that discovers events, matches them to handlers via a protocol contract, and guarantees every non-excluded event gets at least one handler.

## Background

The context-intelligence bundle observes orchestrator events and builds a property graph representing sessions, runs, steps, tool executions, and system events. Before any handler can process events, the bundle must:

1. Stand up shared services (graph state, configuration)
2. Instantiate handlers that know which events they own
3. Discover what events actually exist in the running system
4. Register each discovered event to at least one handler

This mount flow is the foundation for all subsequent handler implementation. It must be deterministic — same inputs (modules loaded, config) always produce the same registrations — and testable at every state transition.

## Approach

**Protocol-first registration.** The mount flow follows a strict sequence:

1. Instantiate shared services
2. Instantiate handlers (each conforming to a handler protocol with a declared `handled_events` set)
3. Discover events via both coordinator channels
4. Match discovered events to handlers via the protocol's `handled_events`
5. Register remaining unclaimed events to the default handler

No inheritance hierarchy. Runtime-checkable protocol. Every handler — entity-specific or default — is testable against the same contract.

## Architecture

### Handler Protocol (EventHandler)

Every handler conforms to a single protocol:

```python
@runtime_checkable
class EventHandler(Protocol):
    handled_events: set[str]
    # The collection of event names this handler owns.
    # Immutable after construction.
    # Used during mount to match discovered events to handlers.
    # e.g. {"session:start", "session:fork", "session:end", "session:resume"}

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult: ...
    # Receives a dispatched event. Uses GraphState via services
    # for node/edge retrieval and upsert. Always returns
    # HookResult(action="continue").
    # Must never raise — errors caught by dispatch wrapper.

    services: HookStateService
    # Injected at construction. Access to all shared services.
```

The protocol is deliberately minimal: what you handle, how you're called, your access to shared services. This gives us:

- **Deterministic registration** — `handled_events` is inspectable at mount time
- **Uniform dispatch** — every handler has the same call signature
- **Testable in isolation** — mock services, call handler, assert node/edge upserts

### Service Container (HookStateService)

The `HookStateService` is the top-level component shared across all handlers. It provides access to the services handlers need:

- **`graph: GraphState`** — node/edge retrieval and upsert for the property graph
- **`config: HookConfig`** — exclusion filters, verbosity settings, connection parameters

Extensible — additional services can be added later without changing the handler protocol. Handlers access services through it: `self.services.graph.upsert_node(...)`, `self.services.config.exclusion_filter(...)`.

### GraphState

The `GraphState` provides all graph operations handlers need:

| Operation | Description |
|-----------|-------------|
| `get_node(id, labels)` | Retrieve an existing node for patching |
| `get_edge(source, target, type)` | Retrieve an existing edge for patching |
| `upsert_node(id, labels, properties)` | Create or update a node |
| `upsert_edge(source, target, type, properties)` | Create or update an edge |

Per-session tracking state:

- Current `:Session` node reference
- Current `:OrchestratorRun` node reference (the run in flight)
- Current `:Step` node reference (the step in flight)
- Step counter (so new steps get the correct `seq`)
- Pending delegate `tool_call_id` (to correlate `delegate:agent_spawned` back to the `:ToolExecution` that triggered it)

GraphState is **storage-agnostic** — it defines the operations, not the backend. The actual persistence (local files, database, remote API) is behind the upsert/get interface.

## Components

### Handler Set (7 Handlers)

All conform to the `EventHandler` protocol. All share the same `HookStateService` instance.

| Handler | Node Labels | Events Owned | Creates/Updates |
|---------|------------|-------------|-----------------|
| **SessionHandler** | `:Session` | `session:start`, `session:fork`, `session:end`, `session:resume` | Session nodes, `:FORKED_FROM` edges |
| **OrchestratorRunHandler** | `:OrchestratorRun`, `:Step:PromptStep` | `prompt:submit`, `execution:start`, `execution:end`, `orchestrator:complete` | OrchestratorRun nodes, `:CONTAINS_RUN` edges, PromptStep node (head of `:NEXT` chain) |
| **StepHandler** | `:Step:AssistantStep` | `provider:request`, `llm:response`, `llm:request:*`, `llm:response:*`, `content_block:*` | AssistantStep nodes, `:CONTAINS_STEP` edges, `:NEXT` edges, blob refs |
| **RecipeStepHandler** | `:Step:RecipeStep` | `recipe:step_started`, `recipe:step_completed`, `recipe:approval:*` | RecipeStep nodes, `:CONTAINS_STEP` edges, `:NEXT` edges |
| **ToolExecutionHandler** | `:ToolExecution` | `tool:pre`, `tool:post`, `tool:error`, `delegate:agent_spawned`, `delegate:agent_completed`, `delegate:context_inherited`, `delegate:session_resumed` | ToolExecution nodes, `:TRIGGERED` edges, `:PARALLEL_WITH` edges, `:DELEGATED_TO` edges |
| **EventHandler** | `:Event:ContextCompaction`, `:Event:CancelRequested`, `:Event:CancelCompleted` | `context:compaction`, `cancel:requested`, `cancel:completed` | Event nodes with full-scope labels, `:HAS_EVENT` edges |
| **DefaultHandler** | `:Event:{DerivedFullScope}` | *(everything unclaimed and not excluded)* | Derives full-scope labels from event name dynamically, attaches via `:HAS_EVENT` edges |

**Label convention:** Labels preserve full event scope. No abbreviation or scope-dropping. `:Event:ContextCompaction` not `:Event:Compaction`. `:Event:CancelRequested` not `:Event:Cancellation`. The `Step` base label is always present as a structural type: `:Step:AssistantStep`, `:Step:RecipeStep`, `:Step:PromptStep`.

## Data Flow

### Mount Flow — Six-State Deterministic State Machine

The mount operation is a deterministic state machine with six states. See the companion DOT diagram at `docs/hook-mount-registration-flow.dot` for the visual representation.

#### State 1: INIT

Hook instantiated, no state exists yet.

**Transition:** Create `HookStateService` containing:
- `GraphState` — node/edge retrieval, upsert, session tracking
- `HookConfig` — exclusion filters, verbosity, connection params

#### State 2: STATE_CREATED

`HookStateService` is ready with all services initialized.

**Transition:** Instantiate all 7 handlers, each receiving the `HookStateService` reference. Aggregate all `handled_events` from the 6 entity handlers into a `claimed_events` set. The `DefaultHandler` starts with an empty `handled_events`.

#### State 3: HANDLERS_INSTANTIATED

All handlers constructed and conforming to `EventHandler` protocol. `claimed_events` set computed.

**Transition:** Query both discovery channels:
1. `coordinator.collect_contributions("observability.events")` — canonical channel
2. `coordinator.get_capability("observability.events")` — legacy channel

Union the results into `discovered_events`. Apply exclusion filter from `HookConfig`:

```
remaining_events = discovered_events - excluded_events
```

Exclusion filter applies **once** before any registration, not per-handler.

#### State 4: EVENTS_DISCOVERED

Full set of remaining (non-excluded) events known.

**Transition:** For each of the 6 entity handlers:
```
for event in handler.handled_events:
    if event in remaining_events:
        coordinator.hooks.register(event, handler)
```

#### State 5: SPECIFIC_REGISTERED

All entity handlers registered for their claimed events.

**Transition:** Compute unclaimed events and register the default handler:
```
unclaimed_events = remaining_events - claimed_events
for event in unclaimed_events:
    coordinator.hooks.register(event, default_handler)
```

The default handler derives `:Event:{FullScope}` labels from the event name string.

#### State 6: READY

Hook is fully mounted. Every discovered non-excluded event has at least one handler registered.

### Key Invariant

**Every discovered non-excluded event gets at least one handler** — either a specific entity handler (or multiple, if their claims overlap) or the default handler. No event is unhandled.

### Summary Flow

```
INIT
  │  Create HookStateService (GraphState + HookConfig)
  ▼
STATE_CREATED
  │  Instantiate 7 handlers, aggregate claimed_events
  ▼
HANDLERS_INSTANTIATED
  │  Discover events (2 channels), apply exclusion filter
  ▼
EVENTS_DISCOVERED
  │  Register entity handlers for claimed events
  ▼
SPECIFIC_REGISTERED
  │  Register default handler for unclaimed events
  ▼
READY
```

## Error Handling

- **Handler `__call__` must never raise** — errors are caught and logged by the dispatch wrapper surrounding every handler invocation. The wrapper always returns `HookResult(action="continue")` even on failure.
- **Mount failures are fatal** — if any state transition fails (service creation, handler instantiation, discovery), the mount aborts and the hook reports an error to the coordinator. Partial registration is not permitted.
- **Discovery returns empty** — valid state. If no events are discovered, no handlers are registered and the hook reaches READY with zero registrations.
- **Exclusion removes all events** — valid state. Same as empty discovery.

## Testing Strategy

Each state transition is independently testable:

| State Transition | Test Strategy |
|-----------------|---------------|
| INIT → STATE_CREATED | Assert `HookStateService` is constructed with `GraphState` and `HookConfig`. Assert `GraphState` operations are callable. |
| STATE_CREATED → HANDLERS_INSTANTIATED | Assert all 7 handlers are instantiated. Assert each conforms to `EventHandler` protocol (runtime check). Assert `claimed_events` is the union of all entity handler `handled_events`. |
| HANDLERS_INSTANTIATED → EVENTS_DISCOVERED | Mock coordinator to return known event sets from both channels. Assert `discovered_events` is the union. Assert exclusion filter removes correct events. Assert `remaining_events` is correct. |
| EVENTS_DISCOVERED → SPECIFIC_REGISTERED | Mock `coordinator.hooks.register`. Assert each entity handler's events are registered to that handler. Assert no events outside `remaining_events` are registered. |
| SPECIFIC_REGISTERED → READY | Assert `unclaimed_events = remaining_events - claimed_events`. Assert default handler is registered for each unclaimed event. Assert total registrations = `len(remaining_events)`. |

**Protocol conformance tests:** Every handler (including `DefaultHandler`) is tested against the `EventHandler` protocol independently — mock `HookStateService`, call handler with synthetic event data, assert correct node/edge upserts on `GraphState`.

**Determinism test:** Run the full mount flow twice with identical inputs, assert identical registration sets.

## Open Questions

- **Wildcard event matching:** Handlers like `StepHandler` declare `llm:request:*` patterns. How does the mount flow match discovered events like `llm:request:anthropic` against the `llm:request:*` pattern? This needs a defined matching strategy (prefix match, glob, regex).
- **Handler ordering within specific registration:** Multiple entity handlers can claim the same event. When this happens, all matching handlers are registered via `coordinator.hooks.register()`, which dispatches to each by priority. The default handler is only registered for events that no entity handler claims.
- **GraphState flush semantics:** When does GraphState flush pending state to the backend? On `session:end`? On a timer? On every upsert? This is a backend concern but affects handler design.
