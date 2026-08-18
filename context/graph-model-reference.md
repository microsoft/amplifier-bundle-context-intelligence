# Graph Model Reference

> **Scope:** This document covers the raw event stream (Layer 1) — node types, edge types,
> event labels, field lifters, and node ID formats. For semantic layer navigation (Layer 2
> runtime entities and foundation layer delegation, skills, and recipe orchestration), load
> the `context-intelligence-graph-query` skill.

---

## Node Types

Data Layer 1 contains exactly **three** node types.

| Node Label | Sub-labels | Description |
|---|---|---|
| `:Session` | `:RootSession` — no parent; `:ForkedSession` — spawned via `session:fork` | One Amplifier session. MERGE key: `{node_id, workspace}`. |
| `:ToolCall` | _(none)_ | One tool invocation lifecycle (pre → post/error). Created by `ToolCallHandler` on `tool:pre`. |
| `:Event` | `:{Category}Event`, `:{Specific}Event` — see Triple-Label Rule below | Every event that reaches `DefaultHandler`. Triple-labeled. |

---

## Edge Types

Data Layer 1 contains exactly **three** edge types.

| Edge | From → To | When Created |
|---|---|---|
| `HAS_FORK` | `:Session` → `:Session` | On `session:fork` — parent session → forked child. |
| `HAS_TOOL_CALL` | `:Session` → `:ToolCall` | On `tool:pre` — session owns the tool call lifecycle node. |
| `HAS_EVENT` | `:Session` → `:Event` | On every `DefaultHandler` event — session owns the event node. |
| `HAS_EVENT` | `:ToolCall` → `:Event` | On `tool:pre`, `tool:post`, `tool:error` — tool call owns each lifecycle event. |

---

## Semantic Layers

The raw event stream described in this document is the foundation. Two additional
layers assemble these events into queryable semantic entities:

- **Kernel layer** — Session, OrchestratorRun, Iteration, ContentBlock, ToolCall,
  Prompt, and more. Connected by typed relationships (`HAS_EXECUTION`, `HAS_PART`,
  `HAS_TOOL_CALL`, `TRIGGERED`, `SOURCED_FROM`, etc.).
- **Foundation layer** — Delegation, Agent, SkillLoad, RecipeRun, RecipeStep, Recipe.
  Connected by `HAS_AGENT`, `ENCOMPASSES`, `TRIGGERED`, `HAS_SKILL_LOAD`,
  `HAS_RECIPE_RUN`, `HAS_STEP`, `HAS_RECIPE`.

For the full schema, node_id formats, edge semantics, and verified Cypher patterns
covering all layers, load the `context-intelligence-graph-query` skill.

---

## Event Triple-Label Rule

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

## Node ID Formats

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

## FieldLifter Properties

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

## Two Paths to Tool Data

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

## Workspace Scoping

All nodes carry a `workspace` property injected by the server at write time.
The MERGE key for every node is `{node_id, workspace}`.

The `graph_query` tool auto-injects `$workspace` as a query parameter. Reference
it in every MATCH clause to scope results to the current workspace:

```cypher
MATCH (s:Session {workspace: $workspace})
RETURN s.node_id, s.started_at
LIMIT 10
```

Omitting `{workspace: $workspace}` from a MATCH pattern silently returns nodes
from all workspaces. Always include it unless a cross-workspace query is intentional.

---

## Blob References

Large payloads (tool results, prompts, LLM responses) are stored as blobs, not
inline in the graph. Event nodes carry preview properties; full content lives at
a `ci-blob://` URI stored in the `data` field or a dedicated blob property.

**URI scheme:** `ci-blob://{session_id}/{key}`

Use the `blob_read` tool to retrieve content — it writes to a local file and
returns a dict with the path. Never pass the URI to `read_file` directly.

```
blob_read(uri="ci-blob://f881e0a0-c055-4ee4-84ed-ff44703150ea/events.jsonl")
# → returns: {"path": "<blob store root>/f881e0a0-c055-4ee4-84ed-ff44703150ea/events.jsonl.json", "source": {"name": ..., "url": ..., "origin": ...}}
```

The path takes the form `<session_id>/<key>.json` under the blob store root. The
root is a `ci-blobs` directory inside the OS temp directory, which differs by
platform (`/tmp/ci-blobs` on macOS and Linux, `%TEMP%\ci-blobs` on Windows) —
always use the returned `path` verbatim rather than constructing one.

> ⚠️ **Warning:** Blob files can contain lines with 100k+ tokens. Opening them with
> `read_file` will overflow your context window. Use shell extraction tools only,
> with `$BLOB_PATH` being the `path` value returned by `blob_read`:
>
> ```bash
> jq '.data.prompt' "$BLOB_PATH" | head -20
> head -5 "$BLOB_PATH"
> ```
