# PromptStep Handler Design

## Goal

Design the handling of `prompt:submit` events to create `:PromptStep` nodes in the graph, linked to their containing Session. Also introduce a universal node ID generator and structured logging infrastructure for all handlers.

## Background

The context-intelligence hook system processes Amplifier lifecycle events and builds a graph of sessions, steps, runs, and tool executions. The `prompt:submit` event fires when a user submits a prompt (or a delegation instruction arrives). This is the first event in a run's lifecycle — it precedes `execution:start`, which creates the OrchestratorRun. We need a clear handler flow that creates the PromptStep node, links it to the Session, and handles error states cleanly.

Two cross-cutting concerns emerged during design: node ID generation needs a universal pattern (currently ad-hoc), and handler logging needs structured event context (currently manual formatting).

## Scope

This design covers ONLY the `prompt:submit` event handling. OrchestratorRun creation belongs to `execution:start` in its own handler — not here.

## Approach

The `prompt:submit` handler creates a `:PromptStep` node linked to the Session via `HAS_STEP`. It does NOT create an OrchestratorRun — that's `execution:start`'s job. The handler lives on `OrchestratorRunHandler` because that handler owns the full prompt-to-completion lifecycle for a run.

Two shared utilities are introduced alongside: `make_node_id` for deterministic node IDs and `HandlerLogger` for structured logging with event context. Both live in `utils.py` and are used by all handlers.

## Architecture

```
prompt:submit event
    │
    ▼
OrchestratorRunHandler.__call__
    │
    ├── HandlerLogger.with_event()  ← structured logging context
    │
    ├── Validate: Session node exists
    │   └── ERROR if missing → log + return, no mutations
    │
    ├── make_node_id()  ← universal ID generator
    │
    ├── upsert_node()   ← PromptStep {Step, PromptStep}
    │
    └── upsert_edge()   ← Session ──HAS_STEP──▶ PromptStep
```

## Components

### Component 1: prompt:submit Handler Flow

When `prompt:submit` arrives, the handler:

1. Extracts `session_id`, `timestamp`, and `prompt` from event data
2. Retrieves the Session node (must already exist from `session:start` or `session:fork`)
3. If Session node not found → **ERROR state**. Log error, return without graph mutations. No partial state.
4. Generates `node_id` using universal pattern: `{session_id}:prompt:submit:{timestamp_ms}`
5. Creates `:PromptStep` node with labels `{"Step", "PromptStep"}` and properties:
   - `iteration = 0`
   - `prompt_text = data["prompt"]` (full text)
   - `prompt_preview = data["prompt"][:200]` (first 200 chars)
   - `occurred_at = timestamp`
   - `session_id = session_id`
6. Creates `HAS_STEP` edge from Session → PromptStep with `occurred_at = timestamp`
7. Returns `HookResult(action="continue")`

#### Event Payload

`prompt:submit` carries:

| Field | Source | Description |
|-------|--------|-------------|
| `session_id` | Infrastructure-injected | Present on every event |
| `timestamp` | Infrastructure-injected | Present on every event |
| `prompt` | Event-specific | The user's input or delegation instruction |
| `parent_id` | Infrastructure-injected | Present but not relevant to this handler |

#### Session Not Found = Error State

If the Session node does not exist when `prompt:submit` arrives, this is an **ERROR**. The handler:

- Logs an error with full event context (session_id, event name, timestamp)
- Returns `HookResult(action="continue")` WITHOUT creating any nodes or edges
- No partial state, no proceeding with bad data

#### What This Handler Does NOT Do

- Does NOT create an OrchestratorRun node (that's `execution:start`'s responsibility)
- Does NOT track `run_number` or `step_counter`
- Does NOT link to an OrchestratorRun (the run doesn't exist yet at `prompt:submit` time)
- Does NOT handle `execution:start`, `execution:end`, or `orchestrator:complete`

#### Schema Implications for DuckDB

No schema changes needed. The `session_id` and `occurred_at` are already lifted columns on the `nodes` table. `prompt_text`, `prompt_preview`, `iteration` go into the `properties` JSON column. The `HAS_STEP` edge uses the existing `edges` table.

### Component 2: Universal Node ID Generator

A shared utility function used by ALL handlers to generate deterministic node IDs from event data.

#### Pattern

```
{session_id}:{event_name}:{timestamp_ms}
```

- **Deterministic** — same event data always produces same ID
- **Unique** — no two events with the same name fire at the same millisecond within a session
- **Meaningful** — you can read the `node_id` and know what it represents
- **No GUIDs needed**

#### Function

```python
def make_node_id(session_id: str, event_name: str, timestamp: str) -> str:
    """Generate a deterministic node ID from event data.
    
    Pattern: {session_id}:{event_name}:{timestamp_ms}
    
    Session nodes are the EXCEPTION -- they use session_id directly
    because session_id is the foreign key the entire event system references.
    """
    # Convert ISO timestamp to epoch milliseconds
    ...
```

#### Where It Lives

In a shared `utils.py` file alongside `services.py`, `graph_store.py`, `protocol.py` — importable by all handlers.

#### Exception: Session Nodes

Session nodes keep `session_id` as their `node_id`. This is because every event in the system references sessions by `session_id` — it's the universal foreign key. All other event-created nodes use the `{session_id}:{event_name}:{timestamp_ms}` pattern.

#### Impact on Existing SessionHandler

The `session:resume` Event node currently uses `f"{session_id}:event:session_resume:{timestamp}"`. This needs to be updated to use `make_node_id(session_id, "session:resume", timestamp)`. The Session node itself stays as `session_id`.

#### Examples

| Event | node_id |
|-------|---------|
| `session:start` | `abc123` (Session node — exception) |
| `session:resume` (Event node) | `abc123:session:resume:1709683200123` |
| `prompt:submit` (PromptStep) | `abc123:prompt:submit:1709683200456` |
| `execution:start` (OrchestratorRun) | `abc123:execution:start:1709683200789` |
| `tool:pre` (ToolExecution) | `abc123:tool:pre:1709683201000` |
| `context:compaction` (Event) | `abc123:context:compaction:1709683201500` |

### Component 3: Structured Logging (HandlerLogger + EventLogContext)

A thin logging infrastructure that automatically includes event context in every log message.

#### Problem

When a handler logs an error (like "Session not found"), you need full diagnostic context: which session, which event, when. Without structured logging, each handler manually formats context into log messages — repetitive and inconsistent.

#### Solution

```python
class HandlerLogger:
    def __init__(self, handler_name: str, logger: logging.Logger) -> None:
        self._handler = handler_name
        self._logger = logger
    
    def with_event(self, event: str, data: dict[str, Any]) -> EventLogContext:
        """Create a log context bound to a specific event."""
        return EventLogContext(
            self._handler, self._logger,
            session_id=data.get("session_id", ""),
            event=event,
            timestamp=data.get("timestamp", ""),
        )

class EventLogContext:
    """Log context with handler name, session_id, and event name pre-bound."""
    
    def __init__(self, handler, logger, session_id, event, timestamp):
        self._prefix = f"[{handler}] [{session_id}] [{event}]"
        self._logger = logger
    
    def info(self, msg, *args): 
        self._logger.info(f"{self._prefix} {msg}", *args)
    
    def warning(self, msg, *args):
        self._logger.warning(f"{self._prefix} {msg}", *args)
    
    def error(self, msg, *args):
        self._logger.error(f"{self._prefix} {msg}", *args)
```

#### Usage in Handlers

```python
class PromptSubmitHandler:
    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("PromptSubmitHandler", logger)
    
    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)
        
        session_id = data.get("session_id")
        if not session_id:
            log.error("No session_id in event data")
            return HookResult(action="continue")
        
        session_node = await self.services.graph.get_node(session_id)
        if session_node is None:
            log.error("Session node not found")
            return HookResult(action="continue")
        
        log.info("Creating PromptStep node")
        ...
```

#### Output Format

```
ERROR [PromptSubmitHandler] [abc123] [prompt:submit] Session node not found
INFO  [PromptSubmitHandler] [abc123] [prompt:submit] Creating PromptStep node
INFO  [SessionHandler] [abc123] [session:start] Created Root session node
```

#### Where It Lives

In `utils.py` alongside `make_node_id` — both are shared infrastructure for all handlers.

#### Retrofit to Existing SessionHandler

The SessionHandler already uses `logging.getLogger(__name__)`. It should be updated to use `HandlerLogger` for consistency across all handlers.

## Data Flow

```
1. prompt:submit event arrives at OrchestratorRunHandler
2. Handler extracts session_id, timestamp, prompt from event data
3. Handler queries graph store: get_node(session_id) → Session node or None
4. ERROR PATH: Session is None → log.error() → return HookResult(continue)
5. HAPPY PATH: make_node_id(session_id, "prompt:submit", timestamp) → node_id
6. upsert_node(node_id, labels={Step, PromptStep}, properties={...}) → PromptStep in graph
7. upsert_edge(session_id → node_id, HAS_STEP, {occurred_at}) → edge in graph
8. return HookResult(continue)
```

## Error Handling

| Error Condition | Handler Response |
|----------------|-----------------|
| Session node not found | Log error with full event context. Return `HookResult(continue)`. No graph mutations. |
| No `session_id` in event data | Log error. Return `HookResult(continue)`. |
| `upsert_node` / `upsert_edge` failure | Propagate exception (graph store is responsible for its own error handling) |

The key principle: **no partial state**. If the Session doesn't exist, we don't create a dangling PromptStep. The handler either completes fully or makes zero mutations.

## Handler Placement

The `prompt:submit` event lives on `OrchestratorRunHandler`. This handler owns the full prompt-to-completion lifecycle for a run:

- `prompt:submit` → creates PromptStep
- `execution:start` → creates OrchestratorRun (future design)
- `execution:end` / `orchestrator:complete` → finalizes run (future design)

This keeps the full run lifecycle in one handler.

## State Machine

See `context/prompt-submit-handler.dot` for the full DOT state machine diagram of the `prompt:submit` handler flow. Session-not-found is modeled as an error state (octagon shape) with a distinct error exit path.

## Testing Strategy

- **Unit tests for `make_node_id`**: Verify deterministic output, correct format, millisecond conversion from ISO timestamps
- **Unit tests for `HandlerLogger`**: Verify log output format includes handler name, session_id, event name
- **Handler tests**: Mock graph store, verify PromptStep node creation with correct labels and properties
- **Error path tests**: Verify no graph mutations when Session node is missing
- **Edge creation tests**: Verify `HAS_STEP` edge from Session to PromptStep with correct `occurred_at`

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| PromptStep linked to Session, not OrchestratorRun | The OrchestratorRun doesn't exist yet at `prompt:submit` time. `execution:start` creates the run later. |
| Session not found is ERROR state | No partial graph mutations. Log and return clean. |
| Node ID pattern: `{session_id}:{event_name}:{timestamp_ms}` | Deterministic, unique, meaningful. No GUIDs. Universal across all handlers. |
| Session nodes exempt from pattern | `session_id` is the universal foreign key in the event system |
| Structured logging via HandlerLogger | Consistent context (handler, session_id, event) in every log message |
| `make_node_id` and `HandlerLogger` in shared `utils.py` | Reusable by all 7 handlers |
| `prompt:submit` stays on OrchestratorRunHandler | Full run lifecycle in one handler |

## Open Questions

1. **Timestamp format conversion** — `make_node_id` needs to convert ISO timestamp to epoch milliseconds. The exact format of the `timestamp` field (from infrastructure injection) needs to be verified. Is it ISO 8601 with fractional seconds? What precision?

2. **SessionHandler retrofit** — The existing SessionHandler needs updating to use `HandlerLogger` and to update the `session:resume` Event node_id to use `make_node_id`. This is a small retrofit, not a rewrite.

3. **HAS_STEP edge from Session** — The data model originally specified `HAS_STEP` from OrchestratorRun → Step. We're now creating `HAS_STEP` from Session → PromptStep because the run doesn't exist yet. When `execution:start` creates the OrchestratorRun, should the edge be re-wired? Or is Session → PromptStep the permanent relationship? This needs to be resolved when we design the `execution:start` handler.
