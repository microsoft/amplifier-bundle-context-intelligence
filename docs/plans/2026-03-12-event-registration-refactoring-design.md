# Event Registration Refactoring Design

## Goal

Refactor event discovery and registration in the context-intelligence hook to match the canonical `hooks-logging` pattern: use `ALL_EVENTS` from `amplifier_core.events` as the base event set, extend with both discovery channels, and consolidate discovery into a single function — eliminating the current dual-discovery where both `__init__.py` and `mount.py` independently query the coordinator.

## Background

The hook currently uses purely dynamic discovery via `collect_contributions("observability.events")` + `get_capability("observability.events")`. At runtime, this discovers only **4 events** (`execution:start`, `execution:end`, `llm:request`, `llm:response`) out of the 51 canonical events and 15+ actually emitted by the CLI. The reference `hooks-logging` module works correctly because it starts from `ALL_EVENTS` and supplements with discovery.

Our hook misses `tool:pre`, `tool:post`, `prompt:submit`, `session:start/end`, `orchestrator:complete`, and 6+ other events — resulting in a sparse graph with only Session and OrchestratorRun nodes.

The root cause is twofold:

1. **No static base**: We rely entirely on runtime discovery, which only returns events that modules explicitly contribute to the observability channel. Most modules don't contribute.
2. **Dual discovery**: Both `__init__.py` (for logging handler registration) and `MountFlow` (for graph handler registration) independently query the coordinator. This creates divergence risk — two code paths that must stay in sync but have no structural guarantee of doing so.

## Approach

**Approach A (chosen)**: Single discovery function in `__init__.py`, `ALL_EVENTS` base + both channels, pass resolved set to MountFlow.

Alternatives considered:

- **Approach B**: Keep MountFlow independent, share `ALL_EVENTS` base — rejected because code duplication persists and divergence risk remains.
- **Approach C**: Extract shared `EventResolver` class — rejected as over-abstraction for a problem Approach A solves simply.

## Architecture

The refactoring touches three layers:

```
mount() in __init__.py
  │
  ├── _discover_events()          ← Single source of truth
  │     ALL_EVENTS base
  │     + collect_contributions    (canonical channel)
  │     + get_capability           (legacy channel)
  │     = full union (no exclusions)
  │
  ├── LoggingHandler.register()   ← All events, no filter
  │
  └── MountFlow.run(coordinator, events)
        │                          ← Events arrive as parameter
        ├── HookConfig.is_excluded() filter
        └── GraphHandler.register()  ← Filtered set
```

## Components

### 1. Event Discovery — Single Source of Truth

`_discover_events()` in `__init__.py` is the single event resolution function. It builds the event set in three additive layers:

1. **Base**: `ALL_EVENTS` from `amplifier_core.events` — all 51 canonical core events. This ensures the hook sees every event the kernel and orchestrators emit.
2. **Extend via canonical channel**: `await coordinator.collect_contributions("observability.events")` — picks up custom module events not in the core catalog. This is the forward path.
3. **Extend via legacy channel**: `coordinator.get_capability("observability.events")` — backward compatibility for modules that haven't migrated to contributions. Kept until legacy is retired.

No exclusion filtering at the discovery level. The function returns the full union. Exclusion is a downstream concern applied by individual consumers.

### 2. MountFlow State Machine — 5 States

MountFlow's `run()` signature changes from `run(coordinator)` to `run(coordinator, events)` where `events` is the pre-resolved set from `_discover_events()`.

**State machine (reduced from 6 to 5 states):**

```
INIT → STATE_CREATED → HANDLERS_INSTANTIATED → SPECIFIC_REGISTERED → READY
```

The removed `EVENTS_DISCOVERED` state had its own `discover_events()` method that independently queried the coordinator. Now `remaining_events` is set from the passed-in `events` set after applying `HookConfig.is_excluded()`.

**Key behaviors:**

- **LoggingHandler** registers for ALL discovered events — no exclusions. Complete log, matching `hooks-logging` behavior.
- **MountFlow** applies `HookConfig.is_excluded()` from user's `exclude_events` config for graph handler registration only.
- This means the logging path sees everything; the graph path can be filtered by the user.

**Conscious design decision**: Events are discovered once in `mount()` and passed down. MountFlow does not query the coordinator independently. This eliminates dual-discovery divergence risk.

### 3. DOT File Updates

**`hook-event-discovery-and-dispatch.dot`** — Delete entirely. It documents the old CXDB architecture and is labelled as historical reference. The current architecture is fully documented by the other two DOT files.

**`hook-mount-dispatcher.dot`** — Update the top-level `mount()` flow:

- `discover_events` shows three additive layers: `ALL_EVENTS` base → `collect_contributions` (canonical) → `get_capability` (legacy) → returns full union
- No exclusion at discovery level
- Full set feeds LoggingHandler registration (all events, no filter)
- Same full set passed as parameter to `MountFlow.run(coordinator, events)`
- Add note: *"Exclusion is a graph-path concern, applied by HookConfig inside MountFlow"*

**`hook-mount-registration-flow.dot`** — MountFlow state machine 6→5 states:

- Remove `EVENTS_DISCOVERED` state
- `run(coordinator, events)` signature — events arrive as parameter
- `HANDLERS_INSTANTIATED` transitions directly to `SPECIFIC_REGISTERED`
- `remaining_events` computed as `events − excluded` at start of `register_specific_handlers`
- Add decision note: *"Conscious design decision: events are discovered once in mount() and passed down. MountFlow does not query the coordinator independently. Eliminates dual-discovery divergence risk."*

Any tests that reference the deleted CXDB DOT file will be updated or removed.

## Data Flow

```
Coordinator
  ├── ALL_EVENTS (static import)           ──┐
  ├── collect_contributions("obs.events")  ──┤  _discover_events()
  └── get_capability("obs.events")         ──┘        │
                                                       ▼
                                               full event set (union)
                                                  │           │
                                                  ▼           ▼
                                          LoggingHandler   MountFlow.run()
                                          (all events)        │
                                                              ▼
                                                    HookConfig.is_excluded()
                                                              │
                                                              ▼
                                                      GraphHandler
                                                    (filtered events)
```

## Error Handling

- If `collect_contributions` returns an empty set or fails, `ALL_EVENTS` base ensures the hook still registers for all core events. Discovery channels are additive — failure is graceful.
- If `get_capability` is unavailable (legacy retirement), the function continues with base + canonical channel. No code change needed — just remove the legacy block.
- MountFlow receives a guaranteed non-empty set (at minimum, `ALL_EVENTS`). No need for empty-set guards.

## Testing Strategy

### Local Unit Tests

Update `test_mount_dispatcher.py` and `test_mount_flow.py`:

- `_discover_events()` starts from `ALL_EVENTS` as base, not empty set
- Both channels extend the base (not replace it)
- Exclusions are NOT applied at discovery level
- MountFlow receives events as parameter via `run(coordinator, events)`
- Remove tests for deleted `MountFlow.discover_events()`
- Deduplicate: if both `ALL_EVENTS` and a channel contribute `"tool:pre"`, it appears once in the result set

### Integration Tests

Run against live Neo4j (`neo4j-test-env` container, port 7690):

- End-to-end: mount hook with real coordinator, fire real events, verify graph nodes and edges
- No synthetic event lists — use actual coordinator discovery
- Verify `tool:pre`/`tool:post` produce `ToolExecution` nodes
- Verify `session:start`/`session:end` produce `Session` nodes with correct status
- Verify `prompt:submit` produces `PromptStep` nodes
- Verify `schedule_flush()` and terminal `await flush()` actually persist nodes and edges
- Catch problems mocks have been hiding

## Support Issue

File an issue on `microsoft-amplifier/amplifier-support` with two recommendations:

### 1. Event Registration Deduplication

The hooks API (`coordinator.hooks.register`) accepts individual event strings. When a hook registers for `ALL_EVENTS` (51 events) plus discovery channels that may contribute overlapping events, duplicates are possible. Suggest the `HookRegistry` use a `set` internally for deduplication so that registering the same `(event, handler)` pair twice is a no-op rather than firing the handler twice per event. Alternatively, allow hooks to register with a `set[str]` instead of calling `register()` in a loop.

### 2. Module Observability Contributions

The streaming orchestrator and CLI emit events (`tool:pre`, `tool:post`, `prompt:submit`, `session:start/end`, etc.) but never contribute them to the `observability.events` channel. Any hook relying on the canonical discovery path (`collect_contributions("observability.events")`) only sees a fraction of actual events. The `hooks-logging` module works around this by importing `ALL_EVENTS` directly.

Suggest reviewing which modules should contribute their event vocabulary to the observability channel so that discovery-based hooks work correctly without needing the `ALL_EVENTS` fallback. The orchestrator module and the CLI's session runner are the primary gaps.

## Open Questions

1. **Legacy channel retirement**: When `get_capability` is retired, the discovery function simplifies to `ALL_EVENTS` + `collect_contributions` only. No code change needed — just remove the legacy block.
2. **Async cleanup cosmetics**: The cleanup path in `GraphDataHook` uses fire-and-forget async tasks that produce a benign "Task attached to different loop" error. This is cosmetic (terminal events already flush) but should be cleaned up in a follow-up.
