# Event Schema Reference

Complete field reference for every canonical event recorded in `events.jsonl`. Each event is stored as `{"event": "...", "timestamp": "...", "data": {...}}` — the fields below describe the contents of the `data` object.

---

## Session Events

### `session:start`

Emitted when a new session begins.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Unique session identifier |
| `parent_id` | string | Parent session identifier (empty for root sessions) |
| `timestamp` | string | ISO 8601 timestamp |
| `working_dir` | string | Working directory path |
| `metadata` | object | Additional session metadata (provider info, config, etc.) |
| `agent_name` | string | Agent name if this is a delegated session (optional) |
| `parallel_group_id` | string | Parallel execution group (optional) |
| `recipe_name` | string | Recipe name if in recipe context (optional) |
| `recipe_step` | string | Recipe step if in recipe context (optional) |

### `session:fork`

Emitted when a session is forked (e.g., for agent delegation).

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | New forked session identifier |
| `parent` | string | Parent session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `working_dir` | string | Working directory path |
| `metadata` | object | Additional session metadata |
| `agent_name` | string | Agent name for the forked session (optional) |
| `parallel_group_id` | string | Parallel execution group (optional) |
| `recipe_name` | string | Recipe name if in recipe context (optional) |
| `recipe_step` | string | Recipe step if in recipe context (optional) |

### `session:end`

Emitted when a session ends (completed, failed, or cancelled).

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `status` | string | Final status: `"completed"`, `"failed"`, or `"cancelled"` |

---

## Orchestrator Events

### `orchestrator:start`

Emitted when an orchestrator run begins processing.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `run_id` | string | Unique identifier for this orchestrator run |

### `orchestrator:complete`

Emitted when an orchestrator run completes successfully.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `run_id` | string | Orchestrator run identifier |
| `status` | string | Completion status (e.g., `"success"`, `"cancelled"`) |

### `orchestrator:error`

Emitted when an orchestrator run encounters an error.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `run_id` | string | Orchestrator run identifier |
| `error` | string | Error message |
| `error_type` | string | Error class name |

---

## Prompt Events

### `prompt:submit`

Emitted when a user prompt is submitted to the orchestrator.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `content` | string | The user prompt text |
| `role` | string | Message role (typically `"user"`) |

---

## Tool Events

### `tool:pre`

Emitted before a tool is executed.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `tool_name` | string | Name of the tool being called |
| `tool_call_id` | string | Unique identifier for this tool call |
| `arguments` | object | Tool call arguments |

### `tool:post`

Emitted after a tool execution completes.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `tool_name` | string | Name of the tool that was called |
| `tool_call_id` | string | Tool call identifier (matches `tool:pre`) |
| `result` | any | Tool execution result |
| `success` | boolean | Whether the tool call succeeded |

---

## Provider Events

### `provider:request`

Emitted when a request is sent to an LLM provider.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `provider` | string | Provider name (e.g., `"anthropic"`, `"openai"`) |
| `model` | string | Model identifier |
| `messages` | array | Message array sent to the provider |

### `provider:response`

Emitted when a response is received from an LLM provider.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `provider` | string | Provider name |
| `model` | string | Model identifier |
| `usage` | object | Token usage statistics (input_tokens, output_tokens) |
| `stop_reason` | string | Reason the model stopped generating |

---

## LLM Events

### `llm:response`

Emitted when the LLM produces a complete response (may contain very large payloads).

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `content` | array | Response content blocks (text, tool_use, etc.) |
| `role` | string | Message role (typically `"assistant"`) |
| `model` | string | Model that generated the response |
| `stop_reason` | string | Reason the model stopped generating |
| `usage` | object | Token usage statistics |

---

## Recipe Events

### `recipe:start`

Emitted when a recipe begins execution.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `recipe_name` | string | Name of the recipe |
| `recipe_path` | string | File path to the recipe YAML |

### `recipe:step_start`

Emitted when a recipe step begins.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `recipe_name` | string | Recipe name |
| `step_name` | string | Name of the step |
| `step_index` | integer | Zero-based step index |

### `recipe:step_complete`

Emitted when a recipe step finishes.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `recipe_name` | string | Recipe name |
| `step_name` | string | Name of the step |
| `step_index` | integer | Zero-based step index |
| `status` | string | Step completion status |

### `recipe:complete`

Emitted when a recipe finishes execution.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `recipe_name` | string | Recipe name |
| `status` | string | Final recipe status |

---

## Delegate Events

### `delegate:start`

Emitted when an agent delegation begins (covers `delegate:agent_spawned`).

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Parent session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `agent_name` | string | Name of the delegated agent |
| `child_session_id` | string | Session identifier for the child agent |
| `instruction` | string | Delegation instruction text |

### `delegate:complete`

Emitted when an agent delegation completes (covers `delegate:agent_completed`).

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Parent session identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `agent_name` | string | Name of the delegated agent |
| `child_session_id` | string | Session identifier for the child agent |
| `status` | string | Completion status |
| `result` | string | Summary of the agent's result |
