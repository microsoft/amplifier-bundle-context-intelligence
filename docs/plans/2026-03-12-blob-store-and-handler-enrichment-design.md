# Blob-Backed Data Preservation and Handler Enrichment Design

## Goal

Preserve complete event data on every Neo4j graph node by introducing a blob store that offloads large fields to disk and a processor pipeline that enriches event clones with blob references — while fixing OrchestratorRun completion flush and documenting the StepHandler field name fix.

## Background

The context-intelligence hook captures orchestrator events and persists them to a Neo4j graph. Three issues exist in the current implementation:

1. **No `data` property on graph nodes.** Handlers cherry-pick specific fields but discard the full event payload. The graph should preserve the complete event data on every node so downstream agents and queries have full context without falling back to `events.jsonl`.

2. **OrchestratorRun never reaches "complete" status.** The `orchestrator:complete` handler sets `status` and `ended_at` but has no `flush()` call, so the update sits in the write buffer and may never reach Neo4j — particularly in `--mode single` where `session:end` may not fire.

3. **StepHandler field name mismatch.** The `llm:response` event uses normalized short keys from the orchestrator (`usage.input`, `usage.output`, etc.) that don't match the long-form keys the handler expected (`input_tokens`, `output_tokens`, etc.). This is already fixed in commit `11a90ce` and documented here for reference.

Event data can contain large payloads — full provider responses, tool results, conversation history — that would bloat Neo4j properties. A blob store offloads these to disk while the graph retains lightweight resolvable references.

## Approach

**Processor pipeline in the dispatcher (Approach A).**

The blob processor lives in the event dispatch path — after the dispatcher selects a handler but before calling it. Every handler receives a processed clone with blob refs already substituted. The blob store is a simple service on `HookStateService`.

Why this approach:

- Handlers never see raw large payloads (memory efficiency)
- Impossible to forget in new handlers — it's automatic
- Clean layering: dispatch handles data prep, handlers handle graph semantics, store handles persistence

## Architecture

```
Event arrives (immutable)
    |
    ├── LoggingHandler (priority 100): receives ORIGINAL data, writes to events.jsonl
    |
    └── Graph dispatch path (priority 90):
            |
            deep_clone(data)
            |
            blob_processor(clone, blob_store, session_id, node_id)
              ├── large fields -> blob_store.write() -> URI
              └── clone[field] = {"$blob_ref": uri}
            |
            processed clone -> handler
              ├── handler lifts specific fields for fast queries
              ├── handler stores properties["data"] = json.dumps(processed_clone)
              └── handler creates/enriches graph node as usual
```

## Components

### Blob Store

The `BlobStore` is a simple interface with a disk-backed v1 implementation.

**Protocol:**

```python
class BlobStore(Protocol):
    async def write(self, session_id: str, key: str, value: Any) -> str:
        """Serialize + persist value, return URI."""
        ...

    async def read(self, session_id: str, uri: str) -> dict | list:
        """Deserialize value from URI."""
        ...

    async def list(self, session_id: str) -> list[str]:
        """Enumerate blob URIs for a session."""
        ...

    async def dump(self, uri: str, dest_path: str | None = None) -> str:
        """Materialize blob as a JSON file on disk, return path."""
        ...
```

**Disk layout** (inside existing session directory):

```
context-intelligence/
    events.jsonl          # existing flat log (unchanged)
    metadata.json         # existing metadata (unchanged)
    blobs/                # NEW
        <node-id>__raw.json
        <node-id>__result.json
        <node-id>__messages.json
        ...
```

**URI scheme:** `ci-blob://<session-id>/<node-id>__<field>` — enough context to resolve back to an absolute file path from any scope. The session-id segment lets cross-session queries resolve blobs unambiguously.

The store lives on `HookStateService` as `self.blob_store`, alongside `self.graph`. The dispatcher configures it with the session's storage root during mount.

### Blob Processor

A pure function in the dispatch path. Receives immutable event data, returns a processed clone. **Never mutates the original.**

```python
BLOB_FIELDS = {"raw", "result", "messages", "mount_plan", "context_snapshot", "debug"}

def process_event_data(data: dict, blob_store: BlobStore, session_id: str, node_id: str) -> dict:
    clone = deep_copy(data)
    for field_name in BLOB_FIELDS:
        if field_name in clone and clone[field_name] is not None:
            uri = blob_store.write(session_id, f"{node_id}__{field_name}", clone[field_name])
            clone[field_name] = {"$blob_ref": uri}
    return clone
```

**Contract guarantees:**

- Original data dict is NEVER modified
- No fields are removed from the clone
- No fields are filtered out
- The only mutation to the clone is: `large_value` → `{"$blob_ref": uri}`
- All other fields pass through identical

**Critical invariant:** Events flowing through the system are NEVER filtered or mutated. Enrichment (adding blob refs to a clone) is the ONLY operation permitted.

**Where it runs:** In the dispatcher, called once per event before the handler. The handler receives the processed clone. The original event data flows unchanged to LoggingHandler (it runs at a different priority and gets the real event, not the clone). Disk JSONL stays complete.

### Handler `data` Property

Once the blob processor enriches the clone, every handler stores the full processed clone as a `data` property on whatever node it creates or enriches:

```python
properties["data"] = json.dumps(processed_data)
```

The `data` property is the **complete processed clone** — small fields inline, large fields as blob refs. No filtering, no field selection. The handler doesn't need to think about what's large or small — that decision already happened upstream in the processor.

The handler still lifts specific fields for fast Cypher queries (`tool_name`, `input_tokens`, `status`, etc.) — that doesn't change.

**Enrichment events:** For events that enrich an existing node (like `llm:response` enriching an AssistantStep, or `tool:post` enriching a ToolExecution), the enrichment event's processed data is stored under a separate key — `data_<event_name>` (e.g. `data_llm_response`, `data_tool_post`) — so enrichment payloads don't overwrite the creation event's `data`.

**Resulting node properties:**

| Property | Content |
|---|---|
| Lifted primitives | `tool_name`, `input_tokens`, `status`, etc. — for fast Cypher queries |
| `data` | Full creation event payload (with blob refs for large fields) |
| `data_<enrichment_event>` | Full enrichment payloads (with blob refs) |

### OrchestratorRun Completion Fix

`orchestrator:complete` is the authoritative signal that a run finished. It already sets `status` and `ended_at` but has no flush call.

**Fix:** Add `await services.graph.flush()` to the `orchestrator:complete` handler, same as `execution:end` and `session:end`. This ensures the status reaches Neo4j even in `--mode single` where `session:end` may not fire.

### StepHandler Field Name Fix (already implemented)

The `llm:response` event uses normalized short keys from the orchestrator:

| Orchestrator emits | Handler previously expected |
|---|---|
| `usage.input` | `input_tokens` |
| `usage.output` | `output_tokens` |
| `usage.cache_read` | `cache_read_input_tokens` |
| `usage.cache_write` | *(not handled)* |
| `usage.reasoning` | `reasoning_tokens` |
| `finish_reason` (inside `data["raw"]`) | `finish_reason` (top level) |

The StepHandler now tries short keys first, falls back to long-form keys for backward compatibility. Also extracts `finish_reason` from `raw` if not found at top level.

**Status:** Already committed (commit `11a90ce`).

### Agent Tooling for Blob Access

The context-intelligence agent needs a tool to access blob data during investigation:

```
blob_tool operations:
    list(session_id)          -> list of {uri, field, node_id, size_bytes}
    dump(uri, dest_path?)     -> materializes blob as JSON file, returns path
```

**Agent workflow:**

1. Agent queries Neo4j: "show me ToolExecution nodes for session X"
2. Sees `data` property with `{"result": {"$blob_ref": "ci-blob://session-id/node__result"}}`
3. Agent calls: `blob_tool(dump, uri="ci-blob://...")`
4. Gets back: `/tmp/ci-blobs/node__result.json`
5. Agent uses `read_file` or `bash+jq` to inspect the materialized file

The agent never loads blob content into its context window directly. It dumps to disk and uses existing file tools — same safe extraction pattern as the existing JSONL navigation guidance.

## Data Flow

### Event Dispatch (write path)

```
Event arrives (immutable dict)
    │
    ├─► LoggingHandler (priority 100)
    │     receives ORIGINAL data
    │     appends to events.jsonl (complete, no blob refs)
    │
    └─► Graph dispatch path (priority 90)
          │
          deep_clone(data)
          │
          blob_processor(clone, blob_store, session_id, node_id)
          │   for each field in BLOB_FIELDS ∩ clone.keys():
          │       blob_store.write(session_id, key, value) → uri
          │       clone[field] = {"$blob_ref": uri}
          │
          processed_clone → handler.handle(processed_clone)
          │   handler lifts primitives for fast queries
          │   handler stores properties["data"] = json.dumps(processed_clone)
          │   handler creates/enriches Neo4j node
          │
          flush() writes buffered properties to Neo4j
```

### Blob Resolution (read path)

```
Agent queries Neo4j node
    │
    reads data property → JSON with {"$blob_ref": "ci-blob://..."}
    │
    calls blob_tool(dump, uri)
    │
    blob_store resolves URI → session dir / blobs / <file>.json
    │
    copies to /tmp/ci-blobs/<file>.json
    │
    agent reads file with standard file tools
```

## Error Handling

| Failure | Behavior |
|---|---|
| **Blob store write failure** | Log warning. Store `{"$blob_error": "write failed: <reason>"}` in the clone in place of the blob ref. The event still flows to the handler — graph persistence is never blocked by blob store failures. |
| **Blob store read/dump failure** | Return clear error to agent tool caller. Agent can fall back to `events.jsonl`. |
| **Missing blob on disk** (cleanup, corruption) | `dump` returns error with the URI that was requested. Agent can try `events.jsonl` as fallback. |

## Testing Strategy

### Unit Tests

- **BlobStore:** write/read/list/dump round-trip, disk layout verification, URI resolution
- **Blob processor:** clone immutability (assert original unchanged), blob ref substitution, non-blob fields pass through, empty/missing fields handled, `None` values skipped
- **Handler `data` property:** each handler stores processed data, enrichment events use `data_<event>` keys, JSON serialization of the full clone

### Integration Tests

- Full event flow → blob processor → handler → Neo4j flush → verify `data` property contains blob refs, verify blobs on disk
- OrchestratorRun completion: emit `orchestrator:complete`, verify status reaches Neo4j

### Smoke Tests

- Shadow container with real Amplifier session, verify graph has `data` properties with real blob refs, verify blob files on disk, verify agent tool can dump and inspect

## Files to Create or Modify

| Action | File | Purpose |
|---|---|---|
| **NEW** | `blob_store.py` | BlobStore protocol + DiskBlobStore implementation |
| **NEW** | `blob_processor.py` | `process_event_data()` function + `BLOB_FIELDS` constant |
| **MODIFY** | `services.py` | Add `blob_store` to HookStateService |
| **MODIFY** | `mount.py` or dispatch wrapper | Wire blob processor into dispatch path |
| **MODIFY** | All 7 graph handlers | Each handler stores `data` (or `data_<event>`) property from the processed clone |
| **MODIFY** | `orchestrator_run.py` | Add `await flush()` to `orchestrator:complete` |
| **NEW** | Blob access tool | `list` and `dump` operations for agent consumption |
| **MODIFY** | Neo4j skill | Document `$blob_ref` pattern and blob tool usage for agents |

## Open Questions

1. Should the blob store have a TTL/cleanup mechanism, or is it tied to session lifecycle (cleaned when sessions are purged)?
2. Should the `$blob_ref` object carry metadata beyond the URI (e.g., `size_bytes`, `content_type`, `field_name`)?
3. Should the `BLOB_FIELDS` list be configurable via HookConfig, or is a hardcoded module constant sufficient for v1?
