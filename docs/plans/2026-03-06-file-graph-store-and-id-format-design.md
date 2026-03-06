# File-Based GraphStore and Universal ID Format Design

## Goal

Implement a file-based GraphStore protocol implementation that uses the filesystem for graph persistence, with universally filesystem-safe node and edge IDs affecting all protocol implementations.

## Background

The current graph store implementation uses DuckDB exclusively, and node IDs contain colons (`prompt:submit`) which are illegal or problematic as filenames on Windows and macOS. To support a lightweight file-based graph store where filenames ARE the navigation, IDs must be filesystem-safe on all platforms. Additionally, DuckDB is a heavy dependency -- having a zero-dependency file-based store as the default makes the system more portable and debuggable.

## This Is Atomic

The ID format change, file store implementation, edge ID scheme, config layout change, and factory update are all one atomic piece of work. They cannot be split.

## Approach

1. Change `make_node_id` to produce filesystem-safe IDs using `__` separators and underscore-replaced event names
2. Introduce `make_edge_id` with a `==[edge_type]==` separator scheme
3. Implement `FileGraphStore` as a new `GraphStore` protocol implementation using flat JSON files
4. Restructure config to nest type-specific config under a `config` key per store type
5. Update the factory to default to `"file"` and pass `**impl_config` to constructors
6. Update test fixtures to use explicit DuckDB in-memory config

## Architecture

```
GraphStore Protocol (unchanged interface)
    │
    ├── DuckDBGraphStore  (existing, constructor unchanged)
    │       └── config: { connection: "..." }
    │
    └── FileGraphStore    (NEW)
            └── config: { location: "..." }

utils.py
    ├── make_node_id()    (format change: __ separators, no colons)
    └── make_edge_id()    (NEW)

store_factory.py
    └── create_graph_store()  (updated: nested config, default "file")
```

## Components

### Universal Node ID Format (Breaking Change)

The `make_node_id` function in `utils.py` changes its output format. This affects ALL protocol implementations -- DuckDB, file store, future Neo4j.

**Current format (DEPRECATED):**
```
{session_id}:{event_name}:{timestamp_ms}
→ 6afb3613-7041-4735-9c0f-c2171452ed18:prompt:submit:1741270343000
```

**New format:**
```
{session_id}__{event_name}__{timestamp_ms}
(colons in event_name replaced with underscores)
→ 6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000
```

**Rules:**
- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` → `prompt_submit`, `delegate:agent_spawned` → `delegate_agent_spawned`, `session:resume` → `session_resume`
- Session nodes keep `session_id` as node_id (unchanged -- UUIDs have no colons, only hyphens)
- This is a protocol-level change -- ALL implementations use the same IDs
- Filesystem safe on all platforms (Linux, macOS, Windows)

**Impact:**
- `make_node_id` in `utils.py` changes output format
- All DuckDB stored data is affected (node_id in nodes table, source/target in edges table, node_id in search_index)
- No production data exists yet so this is safe
- All tests referencing the old colon-based format need updating

### Universal Edge ID Format (New)

New function `make_edge_id` in `utils.py`, shared across all protocol implementations.

**Format:**
```
{source_id}==[{edge_type}]=={target_id}
```

**Example:**
```
6afb3613-7041-4735-9c0f-c2171452ed18==[HAS_STEP]==6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000
```

**Why this separator scheme works:**
- `==` never appears in node IDs (which use `__`, `_`, and `-`)
- `[` and `]` are filesystem-safe on all platforms
- `>` was considered but is illegal on Windows (redirect operator)
- The brackets make edge type visually distinct and unambiguous
- Parsing: split on `==[` and `]==` to get source, edge_type, target

**Filesystem operations for graph navigation:**
```bash
# All edges FROM a session
ls edges/ | grep '^6afb3613.*=='

# All edges TO a node
ls edges/ | grep '==6afb3613.*__prompt_submit.*\.json$'

# All HAS_STEP edges
ls edges/ | grep '==\[HAS_STEP\]=='

# All SUBSESSION_OF edges
ls edges/ | grep '==\[SUBSESSION_OF\]=='
```

### FileGraphStore Implementation

New class `FileGraphStore` implementing the `GraphStore` protocol using the filesystem.

**Storage root:** Configured via `graph_store.config.location` -- a directory path.

**Directory structure:**
```
{location}/
  nodes/
    {node_id}.json
  edges/
    {source_id}==[{edge_type}]=={target_id}.json
```

Flat structure. No nesting by session. No index files. The filenames ARE the navigation.

**Non-blocking writes (protocol requirement):** Same buffer pattern as DuckDB -- `upsert_node`/`upsert_edge` write to in-memory buffer, return immediately. `flush()` writes JSON files to disk via `run_in_executor`.

**Buffer-first reads:** Check buffer, fall back to reading the JSON file from disk via `run_in_executor`.

**Merge semantics on flush:** When flushing a node that already exists as a file on disk, read the existing file, merge labels (union) and properties (update), write back.

**No search_index:** The file store doesn't have FTS. Value searches use `grep`/`jq` directly on the JSON files. This is a feature, not a limitation -- simplest thing that works for debugging and inspection.

**`execute_query` raises `NotImplementedError`:** Same as in-memory GraphState. SQL/PGQ is a DuckDB capability. File store users search with filesystem tools.

**Constructor:**
```python
class FileGraphStore:
    def __init__(self, location: str) -> None:
        # location is required, no default
        # expand ~, create nodes/ and edges/ subdirs
```

**`close()`:** Calls `flush()`. No connection to close (it's files).

### Config Layout Change

Each store type has its own config shape under a nested `config` key:

```yaml
# File store (default type)
config:
  graph_store:
    type: "file"
    config:
      location: "~/.amplifier/context-intelligence/graph"

# DuckDB store
config:
  graph_store:
    type: "duckdb"
    config:
      connection: "~/.amplifier/context-intelligence/graph.duckdb"

# DuckDB in-memory
config:
  graph_store:
    type: "duckdb"
    config:
      connection: ":memory:"

# Future Neo4j
config:
  graph_store:
    type: "neo4j"
    config:
      uri: "bolt://localhost:7687"
      username: "neo4j"
      password: "..."
```

**Key decisions:**
- Default type is `"file"` (not `"duckdb"`)
- File store REQUIRES `location` -- fails loud with ValueError if missing
- DuckDB takes `connection` -- defaults to `":memory:"` if missing
- Each implementation defines its own config contract
- The containing app is expected to provide the path

### Factory Update

The factory reads `graph_store.type` and passes `graph_store.config` as `**kwargs` to the implementation constructor:

```python
def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    store_type = store_config.get("type", "file")
    impl_config = store_config.get("config", {})
    if store_type == "file":
        from .file_store import FileGraphStore
        return FileGraphStore(**impl_config)
    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore
        return DuckDBGraphStore(**impl_config)
    raise ValueError(f"Unknown graph_store type: {store_type}")
```

Each implementation's `__init__` takes its own kwargs. Factory doesn't need to know the shape of each implementation's config.

### Test Fixture Update

The `conftest.py` fixture currently creates `HookStateService(raw_config={})`. With `"file"` as default type and no `location`, this will raise `ValueError`. Tests use explicit DuckDB in-memory config:

```python
@pytest.fixture
def services():
    return HookStateService(raw_config={
        "graph_store": {
            "type": "duckdb",
            "config": {"connection": ":memory:"}
        }
    })
```

DuckDB in-memory for tests -- fast, no files on disk, disposable. File store is for production/debugging use.

## Data Flow

### Write path (FileGraphStore):
1. Handler calls `upsert_node(node_id, labels, properties)` or `upsert_edge(source, target, edge_type, properties)`
2. Data written to in-memory buffer, returns immediately (non-blocking)
3. `flush()` called (by coordinator or explicitly)
4. For each buffered node: check if `nodes/{node_id}.json` exists on disk → if yes, read and merge (labels union, properties update) → write JSON via `run_in_executor`
5. For each buffered edge: write `edges/{source}==[{edge_type}]=={target}.json` via `run_in_executor`

### Read path (FileGraphStore):
1. Check in-memory buffer first
2. If not in buffer, read `nodes/{node_id}.json` or `edges/{edge_id}.json` from disk via `run_in_executor`

## Error Handling

- **Missing location config:** `FileGraphStore` constructor raises `ValueError` immediately if `location` is not provided
- **Missing location config (factory):** Factory with default type `"file"` and empty `impl_config` → `FileGraphStore(**{})` → `TypeError` on missing `location` parameter
- **Disk write failures:** `flush()` propagates filesystem errors (permission denied, disk full) to caller
- **Missing files on read:** Return `None` / empty result (node doesn't exist yet)
- **`execute_query` called on file store:** Raises `NotImplementedError`

## Testing Strategy

- **Unit tests use DuckDB in-memory** -- fast, disposable, no filesystem side effects
- **FileGraphStore gets its own test suite** using `tmp_path` pytest fixture for isolated filesystem tests
- **ID format tests** verify `make_node_id` and `make_edge_id` produce the new format
- **All existing tests updated** to match new ID format (colon → underscore, `__` separator)
- **Integration tests** for the factory with both store types

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| `__` as node ID segment separator | Filesystem safe on all OS. Not in UUIDs (hyphens), not in event names (colons, underscores used singly). |
| Colons in event names → underscores | `prompt:submit` → `prompt_submit`. Filesystem safe. |
| `==[{edge_type}]==` as edge separator | `==` never in node IDs. `[]` makes edge type visually distinct. All filesystem safe including Windows. |
| Session nodes keep raw session_id | Session_id is the universal foreign key. No transformation needed (UUIDs are filesystem-safe). |
| Flat nodes/ and edges/ directories | No session nesting. Edges can cross sessions (SUBSESSION_OF). No index files to corrupt. Filenames ARE the navigation. |
| Default store type is "file" | Lightweight, portable, zero dependencies beyond Python stdlib. DuckDB is opt-in. |
| File store requires explicit location | No silent defaults. Fail loud. The containing app provides the path. |
| Nested config per store type | Each implementation defines its own config shape. Factory passes `**kwargs`. Clean separation. |
| ID generation in utils.py | `make_node_id` and `make_edge_id` are protocol-level, shared across ALL implementations. Enables reconciliation between stores. |
| No search_index in file store | FTS is a DuckDB concern. File store users use grep/jq. |

## Files Changed (this atomic change)

| File | Change |
|------|--------|
| `utils.py` | `make_node_id` output format change (colons → underscores, `__` separator). New `make_edge_id` function. |
| `duckdb_store.py` | Constructor takes `connection` kwarg (unchanged signature but called via `**impl_config`). |
| `file_store.py` | NEW -- `FileGraphStore` class implementing `GraphStore` protocol. |
| `store_factory.py` | Updated factory -- nested config, `**impl_config` pattern, default type `"file"`. |
| `services.py` | `HookStateService` passes nested config to factory. |
| `behaviors/context-intelligence.yaml` | Config layout change if needed. |
| `tests/conftest.py` | Explicit DuckDB in-memory config for test fixture. |
| All test files | Updated node ID assertions to match new format. |
| `skills/context-intelligence-graph-search/SKILL.md` | Updated to reflect new ID formats (standing rule). |

## Open Questions

1. **JSON file format** -- Should node/edge JSON files include the ID in the file content (redundant with filename) or just properties/labels? Recommendation: include for self-containment.
2. **File locking on flush** -- When flushing a node that exists on disk, we read-merge-write. Concurrent access (unlikely but possible) could cause data loss. Is this a concern?
3. **Large prompt texts** -- PromptStep files could be large (up to 9,575 chars empirically). Is that a concern for the file store, or fine since it's one file per node?
