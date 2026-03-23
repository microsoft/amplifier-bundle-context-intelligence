# Event Schema Reference

<!-- Loaded on-demand by agents via skills — not composed into bundles via @-reference or context.include.
     Place in context/ for agent discoverability; do NOT add to bundle.md or behavior YAML. -->

> **Provenance:** Content synthesized from the `amplifier-event-and-data-model-for-context-intelligence` research corpus (2025-07-17), which analyzed `amplifier-core` source code (`crates/amplifier-core/src/events.rs`) and 8,616 sessions of empirical data. Cross-validated against current `amplifier-core/events.rs` on 2026-03-11: all 41 canonical events confirmed present in both source and schema; no discrepancies found.

---

## Part 0: On-Disk Format

### Context-Intelligence JSONL Format

The `context-intelligence/events.jsonl` file is written by the LoggingHandler. Each line is a JSON object with exactly **three** top-level keys:

```json
{"event": "<event_name>", "timestamp": "<ISO-8601>", "data": {<event_payload>}}
```

| Field | Type | Source |
|-------|------|--------|
| `event` | string | Event name (e.g., `"session:start"`, `"tool:pre"`) |
| `timestamp` | string | ISO-8601 timestamp from the event data |
| `data` | object | Complete event payload, sanitized for JSON serialization |

**No field promotion.** Unlike foundation's `events.jsonl`, the context-intelligence format does NOT promote fields like `status`, `duration_ms`, `session_id` to top-level. Everything stays inside `data`.

**No envelope metadata.** No `ts`, `lvl`, or `schema` fields. The format is a clean event + timestamp + data triple.

### Foundation JSONL Format (for comparison)

Foundation's `events.jsonl` (written by `hooks-logging`) uses a richer envelope:

```json
{
  "ts": "2025-11-18T14:30:22.123+00:00",
  "lvl": "INFO",
  "schema": {"name": "amplifier.log", "ver": "1.0.0"},
  "event": "<event_name>",
  "session_id": "<session_id>",
  "parent_id": "<parent_id_or_null>",
  "timestamp": "<ISO-8601>",
  "status": "<if_present>",
  "duration_ms": "<if_present>",
  "module": "<if_present>",
  "component": "<if_present>",
  "error": "<if_present>",
  "request_id": "<if_present>",
  "span_id": "<if_present>",
  "parent_span_id": "<if_present>",
  "data": {"<remaining_keys>": "<values>"}
}
```

Fields `status`, `duration_ms`, `module`, `component`, `error`, `request_id`, `span_id`, `parent_span_id` are **promoted** from the `data` dict to top-level by hooks-logging.

### Infrastructure-Injected Fields

Every event automatically receives these fields from the HookRegistry, regardless of what the emitter provides:

| Field | Source | Notes |
|-------|--------|-------|
| `session_id` | `set_default_fields()` at session creation | Always present in `data`. Cannot be overridden by emitters. |
| `parent_id` | `set_default_fields()` at session creation | Always present in `data` (empty string for root sessions). |
| `timestamp` | `emit()` itself | UTC ISO-8601. Infrastructure-owned. Also promoted to top-level `timestamp` key. |

### The `raw` Field

When `session.raw: true` is configured, certain events include an optional `raw` field inside `data` containing the complete, untruncated API payload. This field can be **megabytes** in size.

**Events that may carry `raw`:**
- `llm:request` — full API request params
- `llm:response` — full API response body
- `session:start`, `session:fork`, `session:resume` — full config/mount plan

See Part 7 for payload size guidance.

---

## Part 1: Canonical Events (41)

All events defined in `amplifier-core/events.rs`. The Rust test at `events.rs:343` enforces exactly 41 entries. Events follow a strict `namespace:action` naming pattern.

### Session Lifecycle (4 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `session:start` | Kernel | Root session begins or delegation child starts executing |
| `session:end` | Kernel | Session cleanup. **See Gap G1** — rarely emitted in practice |
| `session:fork` | Kernel | Child session created (first event in child's stream) |
| `session:resume` | Kernel | Session resumed from saved state |

### Prompt Lifecycle (2 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `prompt:submit` | Orchestrator | User prompt submitted to orchestrator |
| `prompt:complete` | Orchestrator | Orchestrator finished processing prompt |

### Planning (2 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `plan:start` | Orchestrator | Optional planning phase begins |
| `plan:end` | Orchestrator | Planning phase ends |

### Provider (7 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `provider:request` | Orchestrator | Request sent to LLM provider. Carries `provider`, `model`. **⚠️ HUGE payload** (full message array). |
| `provider:response` | Orchestrator | **Dead constant — see Gap G7.** Defined in events.rs but never emitted by loop-streaming. |
| `provider:retry` | Orchestrator | Provider request being retried |
| `provider:error` | Orchestrator | Provider returned an error |
| `provider:throttle` | Orchestrator | Rate limiting active (added v1.1.x) |
| `provider:tool_sequence_repaired` | Orchestrator | Tool sequence auto-repaired (added v1.1.x) |
| `provider:resolve` | Orchestrator | Provider resolved (added v1.1.x) |

### LLM Request/Response (2 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `llm:request` | Orchestrator | LLM call initiated. May carry `raw` field. **⚠️ HUGE payload** — contains full conversation history. |
| `llm:response` | Orchestrator | LLM response received. May carry `raw` field. **⚠️ HUGE payload** when `raw` present. |

### Streaming Content (3 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `content_block:start` | Orchestrator | Content block begins (thinking, text, tool_use) |
| `content_block:delta` | Orchestrator | **Dead constant — see Gap G8.** Never emitted. |
| `content_block:end` | Orchestrator | Content block ends |

### Thinking (2 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `thinking:delta` | Orchestrator | Thinking content streamed |
| `thinking:final` | Orchestrator | Final thinking content |

### Tool Invocation (3 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `tool:pre` | Orchestrator | Before tool execution. Carries `tool_name`, `tool_call_id`, `parallel_group_id`. |
| `tool:post` | Orchestrator | After tool execution. Carries result. |
| `tool:error` | Orchestrator | Tool execution error |

### Context Management (4 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `context:pre_compact` | Context module | Before compaction |
| `context:post_compact` | Context module | After compaction |
| `context:compaction` | Context module | Compaction summary. Carries `before_tokens`, `after_tokens`, `strategy_level` (1–8). |
| `context:include` | Context module | Context file included |

### Orchestrator / Execution (3 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `orchestrator:complete` | Orchestrator | **Universal terminal event.** Carries `status`, `turn_count`. Always emitted — even on cancellation. |
| `execution:start` | Orchestrator | Orchestrator loop begins. **See Gap G5** — not emitted by loop-basic. |
| `execution:end` | Orchestrator | Orchestrator loop ends. **See Gap G2** — not emitted on cancellation. |

### Policy / Approvals (4 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `policy:violation` | Hook modules | Policy violation detected |
| `approval:required` | Hook modules | User approval needed |
| `approval:granted` | Hook modules | Approval granted |
| `approval:denied` | Hook modules | Approval denied |

### Cancellation (2 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `cancel:requested` | Kernel | Cancellation initiated (Ctrl+C). Carries `is_immediate`. |
| `cancel:completed` | Kernel | Cancellation finished |

### Artifacts (2 events)

| Event | Emitter | Notes |
|-------|---------|-------|
| `artifact:write` | Tool modules | File/artifact written |
| `artifact:read` | Tool modules | File/artifact read |

### User Notifications (1 event)

| Event | Emitter | Notes |
|-------|---------|-------|
| `user:notification` | Various | Notification to user |

### ⚠️ Phantom Events (NOT in the canonical 41)

These event names appear in older documentation but do **not** exist in `amplifier-core/events.rs`. Do not search for them — they will return zero results.

| Phantom Event | Correct Name / Status |
|--------------|----------------------|
| `orchestrator:start` | Does not exist. No matching start event for `orchestrator:complete`. See Gap G6. |
| `orchestrator:error` | Does not exist. Errors surface via `provider:error` or `tool:error`. |
| `delegate:start` | Does not exist. Correct name: `delegate:agent_spawned` (module event, Part 2). |
| `delegate:complete` | Does not exist. Correct name: `delegate:agent_completed` (module event, Part 2). |
| `recipe:step_start` | Does not exist. Correct name: `recipe:step` (module event, Part 2). |
| `recipe:step_complete` | Does not exist. Not a real event at all. |

---

## Part 2: Module-Emitted Events (~18)

These events are emitted by modules, not defined in the kernel's canonical 41. Events marked ✓ are registered via `observability.events`; events marked ✗ are direct-emit only (invisible to auto-discovery).

### Delegate Tool Events (4 events, ✓ registered)

Source: `amplifier-foundation/modules/tool-delegate`

| Event | Notes |
|-------|-------|
| `delegate:agent_spawned` | Child agent session created. Carries `agent`, `sub_session_id`, `parent_session_id`. **Lacks `tool_call_id` — see Gap G3.** |
| `delegate:agent_resumed` | Delegate session resumed |
| `delegate:agent_completed` | Delegate session finished. Carries `success`. |
| `delegate:error` | Delegation failed |

### Recipe Tool Events (7 events, ✗ NOT registered — see Gap G4)

Source: `amplifier-bundle-recipes/modules/tool-recipes`

| Event | Notes |
|-------|-------|
| `recipe:start` | Recipe execution begins |
| `recipe:step` | Recipe step begins (NOT `recipe:step_start`) |
| `recipe:complete` | Recipe finishes (NOT `recipe:step_complete`) |
| `recipe:approval` | Approval gate reached |
| `recipe:loop_iteration` | Loop iteration begins |
| `recipe:loop_complete` | Loop finishes |
| `recipe:error` | Declared but **not verified** — no `emit()` call site found in source audit |

**⚠️ Recipe events are currently invisible.** They use `asyncio.create_task()` (fire-and-forget) and are NOT registered via `observability.events`. Zero out of 8,616 analyzed sessions contain recipe events.

### Skills Tool Events (3 events, ✓ registered)

Source: `amplifier-module-tool-skills`

| Event | Notes |
|-------|-------|
| `skills:discovered` | Skills discovered during mount |
| `skill:loaded` | Individual skill loaded |
| `skill:unloaded` | Skill unloaded |

### Other Module Events (4 events, ✗ direct emit)

| Event | Source Module | Notes |
|-------|-------------|-------|
| `session-naming:debug` | `hooks-session-naming` | Debug output |
| `deprecation:warning` | `hooks-deprecation` | Deprecation notice |
| `notify:turn-complete` | `hooks-notify` | Turn completion notification |
| `orchestrator:rate_limit_delay` | `loop-streaming` | Rate limit delay |

---

## Part 3: Canonical Event Cycle

The fundamental repeating pattern in every session:

```
provider:request (iteration N)
  → llm:request → llm:response
    → content_block:start (thinking) → content_block:end
    → content_block:start (text)     → content_block:end
    → content_block:start (tool_use) → content_block:end
      → tool:pre (tool #1) → tool:post (tool #1)
      → tool:pre (tool #2) → tool:post (tool #2)
      → ...
provider:request (iteration N+1)
```

### Empirical Frequencies

From a root session with 2,216 events across 14 turns:

| Event Type | Count | Per Turn |
|-----------|-------|----------|
| `content_block` (start/end pairs) | 636 (318 pairs) | ~22.7 |
| `llm:request` / `llm:response` pairs | 294 (147 pairs) | ~10.5 |
| `provider:request` | 144 | ~10.3 |
| `tool:pre` / `tool:post` pairs | 282 (141 pairs) | ~10.1 |
| `context:compaction` | 114 | ~8.1 |
| `delegate:agent_spawned` / `agent_completed` pairs | 22 (11 pairs) | ~0.8 |

*\* Per Turn calculated on pair-count where "(N pairs)" is shown, individual count otherwise.*

**Key ratios:**
- `provider:request` to `tool:pre` ≈ 1:1 — most LLM calls result in at least one tool call
- `context:compaction` fires roughly every 19 events
- Parallel tool calls share a `parallel_group_id` UUID but events are emitted sequentially

### Where context:compaction Fires

Compaction occurs AFTER `provider:request` (between `provider:request` and `llm:request`), not between `prompt:submit` and `provider:request`. The prompt-to-provider gap is 3–50ms with no events between them.

---

## Part 4: Session Lifecycle and Terminal Patterns

### Three Empirical Terminal Patterns

**Pattern 1: Normal completion**
```
orchestrator:complete {status: "success", turn_count: N}
prompt:complete
session:end  ← rarely emitted in practice (0/300 sampled sessions; see Gap G1 severity: CRITICAL)
```

**Pattern 2: Cancellation**
```
cancel:requested {is_immediate: false}
... (in-flight tool completes gracefully)
orchestrator:complete {status: "cancelled"}
cancel:completed {was_immediate: false}
prompt:complete
```

**Pattern 3: Error**
```
provider:error {error: {...}}
orchestrator:complete {status: "error"}
prompt:complete
```

### orchestrator:complete as Universal Terminal

`orchestrator:complete` is the one event **guaranteed** to fire at the end of every orchestrator run, regardless of outcome. It carries `status` (`"success"`, `"cancelled"`, `"error"`) and `turn_count`.

**Use `orchestrator:complete` as your primary run boundary marker**, not `session:end` (unreliable — Gap G1) or `execution:end` (missing on cancellation — Gap G2).

### session:end Reliability

`session:end` is defined in the kernel but rarely emitted in practice. Of 300 sampled sessions, 0 contained this event (Gap G1). The `metadata.json` file is a more reliable source for session end state — the LoggingHandler updates it on `session:end` events when they do fire.

### Child Session Scale

| Session Type | Events | Turns | Events/Turn | LLM Cycles |
|-------------|--------|-------|-------------|------------|
| Root | 2,216 | 14 | 158 | 147 |
| Child (single delegation) | 42 | 1 | 42 | 2 |
| Recipe step session | 83 | 1 | 83 | 4 |

---

## Part 5: Observability Gaps

Known gaps that affect what data may be missing from sessions. Based on empirical analysis of 8,616 sessions and source code inspection.

| Gap | Severity | Description | Workaround |
|-----|----------|-------------|------------|
| G1 | CRITICAL | `session:end` never emitted — hooks unloaded before emission | Use `metadata.json` or last `orchestrator:complete` for session end state |
| G2 | CRITICAL | `execution:end` not emitted on cancellation (5 paths skip it in loop-streaming) | Use `orchestrator:complete` timestamp (always emitted) |
| G3 | CRITICAL | `delegate:agent_spawned/completed` lack `tool_call_id` | Match via timestamp proximity to `tool:pre` where `tool_name="delegate"` |
| G4 | HIGH | Recipe events invisible — fire-and-forget + no `observability.events` registration | Detect recipe sessions by `tool_name="recipes"` in parent's `tool:pre` |
| G5 | MODERATE | `execution:start/end` absent in loop-basic (default orchestrator) | Use `prompt:submit` as start, `orchestrator:complete` as end |
| G6 | LOW | No `orchestrator:start` — unpaired terminal marker | Use `prompt:submit` or `execution:start` as effective start |
| G7 | LOW | `provider:response` is a dead constant — defined but never emitted | Use `llm:response` for response data |
| G8 | LOW | `content_block:delta` is a dead constant — defined but never emitted | Use `content_block:start/end` pairs |

---

## Part 6: Session ID Formats

| Type | Format | Example |
|------|--------|---------|
| Root | UUID v4 | `1cb9e5f5-48b2-4dd6-9728-bcc3b0303f2b` |
| Child (delegation) | `{parent_span}-{child_span}_{agent_name}` | `1cb9e5f5-...-3c3c7d7ed17b4281_foundation:zen-architect` |
| Recipe | `{span_hex}-{YYYYMMDD-HHMMSS}_recipe` | `7cc787dd22d54f6c-20251118-114317_recipe` |

### session:fork First-Event Fingerprint

A child session's **first event** is always `session:fork`. This is a reliable fingerprint for detecting child sessions. The `session:fork` payload includes `parent` (the parent session ID) and `session_id` (the child's ID).

```json
{
  "event": "session:fork",
  "timestamp": "...",
  "data": {
    "parent": "1cb9e5f5-48b2-4dd6-9728-bcc3b0303f2b",
    "session_id": "1cb9e5f5-...-3c3c7d7ed17b4281_foundation:zen-architect"
  }
}
```

### Ordering Detail

In child sessions, the event ordering is:
1. `session:fork` (emitted during `initialize()`)
2. `session:start` (emitted at the start of `execute()`)
3. `prompt:submit`, `execution:start`, ... (normal orchestrator events)

---

## Part 7: Event Payload Sizes

**⚠️ This section is critical for safe extraction. Some events carry payloads that are megabytes in size. NEVER extract full lines from events.jsonl without checking this table first. See `safe-extraction-patterns.md` for how to work with large events safely.**

| Event | Payload Size | Safe to Extract Fully? | Safe Fields |
|-------|-------------|----------------------|-------------|
| `session:start` | Small | Yes | All |
| `session:fork` | Small | Yes | All |
| `session:resume` | Small | Yes | All |
| `session:end` | Small | Yes | All |
| `execution:start` | Small | Yes | All |
| `execution:end` | Small | Yes | All |
| `prompt:submit` | Medium | Careful — prompt text may be long | `event`, `timestamp` |
| `prompt:complete` | Medium–Large | Careful — carries full response in root sessions | `event`, `timestamp` |
| `llm:request` | **HUGE** | **NEVER** — full conversation history | `event`, `timestamp`, `data.model`, `data.message_count` |
| `llm:response` | **HUGE** | **NEVER** when `raw` present | `event`, `timestamp`, `data.model`, `data.usage`, `data.duration_ms` |
| `provider:request` | **HUGE** — full message array | **NEVER** | `event`, `timestamp`, `data.provider`, `data.model` |
| `tool:pre` | Variable | Check `tool_name` first | `event`, `timestamp`, `data.tool_name`, `data.tool_call_id` |
| `tool:post` | Variable | Check `tool_name` first | `event`, `timestamp`, `data.tool_name`, `data.duration_ms` |
| `tool:error` | Small–Medium | Usually safe | All |
| `content_block:start` | Small | Yes | All |
| `content_block:end` | Small–Medium | Usually safe | `event`, `timestamp`, `data.type` |
| `context:compaction` | Small | Yes | All |
| `orchestrator:complete` | Small | Yes | All |
| `cancel:*` | Small | Yes | All |
| `delegate:*` | Small | Yes | All |
| All other events | Variable | Preview first | `event`, `timestamp` |

### Why Some Events Are Huge

- **`llm:request`**: Contains the full conversation history (all previous messages), loaded context files, system instructions, and tool definitions. A single event can be 100k–200k tokens.
- **`llm:response`**: Contains the complete LLM response. When `raw` field is present, can be megabytes.
- **`provider:request`**: Carries the full message array sent to the provider, including all context.
- **`prompt:complete`**: In root sessions, carries both the prompt and the full response text.

### Provider-Specific Field Names

When you need to extract specific fields from provider events:

| Provider | Messages Field | System Field |
|----------|---------------|-------------|
| Anthropic | `data.params.messages` | `data.params.system` |
| OpenAI | `data.params.input` | `data.params.instructions` |
| Azure OpenAI | Same as OpenAI | Same as OpenAI |

---

## Part 8: Per-Event Field Reference

Per-event `data` payload fields for the 18 events most commonly queried by context-intelligence tooling. All events automatically receive the [infrastructure-injected fields](#infrastructure-injected-fields) (`session_id`, `parent_id`, `timestamp`) — those are not repeated below.

> **Phantom-event note:** Several entries here document event names that appear in older documentation but do **not** exist in `amplifier-core/events.rs`. They are included for completeness and cross-reference; each section notes the correct canonical name.

---

### `session:start`

Emitted by the Kernel when a root session begins or a delegation child starts executing.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | The new session's UUID |
| `parent_id` | string | Parent session ID (empty string for root sessions) |
| `config` | object | Session configuration (provider, model, bundle, etc.) |
| `raw` | object \| absent | Full config/mount plan when `session.raw: true` — can be large |

---

### `session:fork`

Emitted as the **first event** in a child session's stream. Reliable fingerprint for detecting child sessions.

| Field | Type | Description |
|-------|------|-------------|
| `parent` | string | Parent session ID |
| `session_id` | string | The child session's ID |
| `raw` | object \| absent | Full mount plan when `session.raw: true` |

---

### `session:end`

Emitted by the Kernel on session cleanup. **Rarely fires in practice** — see Gap G1.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | The session that ended |
| `status` | string | Final session status |
| `ended_at` | string | ISO-8601 timestamp of session end |

---

### `orchestrator:start`

> **⚠️ Phantom event.** `orchestrator:start` does **not** exist in `amplifier-core/events.rs` — see Gap G6. Use `prompt:submit` or `execution:start` as an effective start boundary. This section is included for documentation cross-reference only.

| Field | Type | Description |
|-------|------|-------------|
| *(event does not exist)* | — | No fields — this event is never emitted |

---

### `orchestrator:complete`

The **universal terminal event** — guaranteed to fire at the end of every orchestrator run regardless of outcome. Primary run boundary marker.

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"`, `"cancelled"`, or `"error"` |
| `turn_count` | integer | Number of turns completed in this run |

---

### `orchestrator:error`

> **⚠️ Phantom event.** `orchestrator:error` does **not** exist in `amplifier-core/events.rs`. Errors surface via `provider:error` (provider failures) or `tool:error` (tool failures). This section is included for documentation cross-reference only.

| Field | Type | Description |
|-------|------|-------------|
| *(event does not exist)* | — | No fields — this event is never emitted |

---

### `prompt:submit`

Emitted by the Orchestrator when a user prompt is submitted for processing.

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | string | The user prompt text (may be long) |
| `turn` | integer | Turn number within the session |

---

### `tool:pre`

Emitted by the Orchestrator before tool execution. Carries identifying keys needed to correlate with `tool:post`.

| Field | Type | Description |
|-------|------|-------------|
| `tool_name` | string | Name of the tool being invoked |
| `tool_call_id` | string | Unique ID for this tool call (correlates with `tool:post`) |
| `parallel_group_id` | string \| absent | UUID shared by all tools in a parallel group |
| `input` | object | Tool input parameters (size varies by tool) |

---

### `tool:post`

Emitted by the Orchestrator after tool execution completes (success or failure).

| Field | Type | Description |
|-------|------|-------------|
| `tool_name` | string | Name of the tool that was invoked |
| `tool_call_id` | string | Matches the corresponding `tool:pre` |
| `duration_ms` | number | Execution time in milliseconds |
| `result` | any | Tool output (size varies — check `tool_name` before extracting) |

---

### `provider:request`

Emitted by the Orchestrator when a request is sent to the LLM provider. **⚠️ HUGE payload** — contains the full message array.

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Provider name (e.g., `"anthropic"`, `"openai"`) |
| `model` | string | Model identifier |
| `params` | object | **Full request params including message array — NEVER extract whole field** |

**Safe fields only:** `data.provider`, `data.model`. Never extract `data.params` in full.

---

### `provider:response`

> **⚠️ Dead constant.** `provider:response` is defined in `amplifier-core/events.rs` but **never emitted** by loop-streaming (Gap G7). Use `llm:response` for response data instead.

| Field | Type | Description |
|-------|------|-------------|
| *(never emitted)* | — | Defined in source but no emit call site exists |

---

### `llm:response`

Emitted by the Orchestrator when the LLM response is received. **⚠️ HUGE payload** when `raw` is present.

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | Model that produced the response |
| `usage` | object | Token usage stats (`input_tokens`, `output_tokens`) |
| `duration_ms` | number | Time from request to response in milliseconds |
| `stop_reason` | string | Why generation stopped (`"end_turn"`, `"tool_use"`, etc.) |
| `raw` | object \| absent | Full API response body when `session.raw: true` — can be megabytes |

---

### `recipe:start`

Emitted by the recipes tool module when a recipe begins execution. **⚠️ Currently invisible** — uses fire-and-forget async (Gap G4).

| Field | Type | Description |
|-------|------|-------------|
| `recipe_name` | string | Name of the recipe being executed |
| `recipe_path` | string | File path to the recipe YAML |
| `session_id` | string | The recipe's session ID |

---

### `recipe:step_start`

> **⚠️ Phantom event alias.** The canonical event name is `recipe:step` (not `recipe:step_start`). This section documents the fields emitted by `recipe:step`. **⚠️ Currently invisible** — uses fire-and-forget async (Gap G4).

| Field | Type | Description |
|-------|------|-------------|
| `step_name` | string | Name of the recipe step |
| `step_index` | integer | Zero-based step index |
| `recipe_name` | string | Parent recipe name |

---

### `recipe:step_complete`

> **⚠️ Phantom event.** `recipe:step_complete` does **not** exist. There is no step-completion event emitted by the recipes module. Use `recipe:complete` for overall completion or check step outcomes via agent results. **⚠️ Currently invisible** even if it existed — uses fire-and-forget async (Gap G4).

| Field | Type | Description |
|-------|------|-------------|
| *(event does not exist)* | — | No fields — this event is never emitted |

---

### `recipe:complete`

Emitted by the recipes tool module when a recipe finishes. **⚠️ Currently invisible** — uses fire-and-forget async (Gap G4).

| Field | Type | Description |
|-------|------|-------------|
| `recipe_name` | string | Name of the recipe that completed |
| `status` | string | Completion status (`"success"`, `"error"`) |
| `steps_completed` | integer | Number of steps that ran |

---

### `delegate:start`

> **⚠️ Phantom event alias.** The canonical event name is `delegate:agent_spawned` (not `delegate:start`). This section documents the fields emitted by `delegate:agent_spawned`. Registered via `observability.events`.

| Field | Type | Description |
|-------|------|-------------|
| `agent` | string | Agent bundle identifier (e.g., `"foundation:zen-architect"`) |
| `sub_session_id` | string | The child agent's session ID |
| `parent_session_id` | string | The parent session that spawned the agent |
| `tool_call_id` | absent | **Gap G3** — `tool_call_id` is not included; correlate via timestamp proximity to `tool:pre` where `tool_name="delegate"` |

---

### `delegate:complete`

> **⚠️ Phantom event alias.** The canonical event name is `delegate:agent_completed` (not `delegate:complete`). This section documents the fields emitted by `delegate:agent_completed`. Registered via `observability.events`.

| Field | Type | Description |
|-------|------|-------------|
| `agent` | string | Agent bundle identifier |
| `sub_session_id` | string | The completed child agent's session ID |
| `success` | boolean | Whether the agent completed successfully |
