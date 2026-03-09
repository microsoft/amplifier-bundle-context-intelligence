# FileGraphStore — On-Disk Session Graph

Quick reference for how the filesystem-based backend (`FileGraphStore`) represents session graphs on disk and how to navigate them.

See `file-store-disk-layout.dot` for a visual diagram of the full structure.

## Forest-Aware Scoping

The store uses a two-level scoping model: a **graph store root** shared by all projects, and a **forest name** that isolates each project's graph into its own subdirectory.

```python
FileGraphStore(graph_store_root="~/.amplifier/graphs", graph_forest_name="my-project")
# creates: ~/.amplifier/graphs/my-project/nodes/
#          ~/.amplifier/graphs/my-project/edges/
```

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `graph_store_root` | `~/.amplifier/graphs` | Shared root for all forests |
| `graph_forest_name` | `"default"` | Project-specific subdirectory (the "forest") |

The `graph_forest_name` is a **first-class protocol property** — immutable after construction, required by all `GraphStore` implementations. The app-cli typically sets it to the project slug:

```yaml
# Hook config (injected by app-cli or bundle overlay)
graph_store:
  type: file
  graph_forest_name: "my-project"   # top-level key, NOT inside config:
  config:
    graph_store_root: "~/.amplifier/graphs"
```

### Forest Guarantees

1. `graph_forest_name` is set at construction and immutable.
2. All writes are scoped to this forest.
3. Point lookups (`get_node`/`get_edge`) are forest-agnostic (IDs are globally unique).

## Disk Layout

```
~/.amplifier/graphs/                          ← graph_store_root (shared)
├── my-project/                               ← graph_forest_name (one per project)
│   ├── nodes/                                ← FLAT — all sessions mixed
│   │   ├── {session_A}.json
│   │   ├── {session_A}__prompt_submit__{epoch_ms}.json
│   │   ├── {session_A}__execution_start__{epoch_ms}.json
│   │   ├── {session_A}__tool_pre__{epoch_ms}.json
│   │   ├── {session_B}.json                  ← different session, same dir
│   │   ├── {session_B}__prompt_submit__{epoch_ms}.json
│   │   └── ...
│   └── edges/                                ← FLAT — all sessions mixed
│       ├── {session_A}==[HAS_RUN]=={session_A}__execution_start__{ms}.json
│       ├── {session_A}__execution_start__{ms}==[HAS_STEP]=={...prompt_submit...}.json
│       ├── {session_B}==[HAS_RUN]=={session_B}__execution_start__{ms}.json
│       └── ...
├── other-project/                            ← separate forest, fully isolated
│   ├── nodes/
│   └── edges/
└── default/                                  ← fallback forest when no name configured
    ├── nodes/
    └── edges/
```

**There are no per-session subdirectories.** Within a forest, session identity is encoded as a filename prefix, not a directory level. No indexes, no manifests, no append logs — every node and every edge is an independent, self-contained JSON file.

## ID Conventions

| Entity | ID Pattern | Example |
|--------|-----------|---------|
| Session node | raw `session_id` (UUID) | `55c8841a-abcd-...` |
| Other nodes | `{session_id}__{event_name}__{epoch_ms}` | `55c8841a...__prompt_submit__1737972001000` |
| Edge file | `{source_id}==[{EDGE_TYPE}]=={target_id}` | `55c8841a...==[HAS_RUN]==...execution_start...` |

Event names have colons replaced with underscores (`prompt:submit` → `prompt_submit`). The `==[` / `]==` separators in edge IDs never appear in node IDs, making edge filenames unambiguously parseable back into `(source, target, type)`.

## JSON Schemas

**Node** (`nodes/{node_id}.json`):
```json
{
  "id": "55c8841a-test",
  "labels": ["Root", "Session"],
  "properties": {
    "started_at": "2026-01-15T10:00:00Z",
    "status": "running",
    "agent_name": "explorer"
  }
}
```

**Edge** (`edges/{edge_id}.json`):
```json
{
  "source": "55c8841a-test",
  "target": "55c8841a-test__execution_start__1737972000000",
  "type": "HAS_RUN",
  "properties": { "seq": 1, "occurred_at": "2026-01-15T10:00:00Z" }
}
```

Labels are stored as sorted arrays on disk (Python sets in memory). Properties are open-ended dicts; upserting merges additively (new keys added, existing keys overwritten, missing keys preserved).

## Graph Topology

A session produces this logical hierarchy (all stored flat within the forest's two directories):

```
Session (Root)
  ├──[HAS_RUN]──→ OrchestratorRun
  │                  ├──[HAS_STEP]──→ Step (PromptStep)
  │                  │                  ├──[NEXT]──→ Step (AssistantStep)
  │                  │                  └──[TRIGGERED]──→ ToolExecution
  │                  │                                      └──[SPAWNED]──→ Session (Subsession)
  │                  │                                                        └──[SUBSESSION_OF]──→ Session (parent)
  │                  └──[HAS_STEP]──→ Step ...
  ├──[HAS_RUN]──→ OrchestratorRun ...
  └──[HAS_EVENT]──→ Event (ContextCompaction, SkillLoaded, etc.)
```

**8 edge types:**

| Edge Type | From → To | Purpose |
|-----------|-----------|---------|
| `HAS_RUN` | Session → OrchestratorRun | Session owns runs |
| `HAS_STEP` | OrchestratorRun → Step | Run contains steps |
| `NEXT` | Step → Step | Sequential ordering within a run |
| `TRIGGERED` | Step → ToolExecution | Step invoked a tool |
| `PARALLEL_WITH` | ToolExecution ↔ ToolExecution | Concurrent tool calls |
| `SPAWNED` | ToolExecution → Session | Delegation created a child session |
| `SUBSESSION_OF` | Session → Session | Child → parent link |
| `HAS_EVENT` | any → Event | Lifecycle events (compaction, cancellation) |

## Navigating the Graph from Disk

`FileGraphStore` exposes **only point lookups**: `get_node(id)` and `get_edge(source, target, type)`. There is no scan, no traversal, no query API. It does **not** implement `QueryableStore`.

To navigate the graph from the filesystem directly, use these shell patterns. All examples assume `FOREST` is set to the project's forest directory:

```bash
FOREST=~/.amplifier/graphs/my-project
```

### List all sessions in this forest

```bash
# Session nodes use raw UUIDs (no __ separator)
ls "$FOREST/nodes/" | grep -v '__'
```

### Find all nodes belonging to a session

```bash
SESSION="55c8841a-abcd-..."
ls "$FOREST/nodes/" | grep "^${SESSION}"
```

### Find all edges from/to a node

```bash
NODE_ID="55c8841a-abcd-..."
# Outgoing edges (node is source)
ls "$FOREST/edges/" | grep "^${NODE_ID}=="
# Incoming edges (node is target)
ls "$FOREST/edges/" | grep "==${NODE_ID}.json$"
```

### Traverse: Session → Runs → Steps → Tools

```bash
SESSION="55c8841a-abcd-..."

# 1. Find runs
ls "$FOREST/edges/" | grep "^${SESSION}==\[HAS_RUN\]"

# 2. Extract run node IDs (target field from the edge JSON)
for f in "$FOREST/edges/${SESSION}"==\[HAS_RUN\]==*.json; do
  jq -r '.target' "$f"
done

# 3. For each run, find its steps
RUN_ID="55c8841a-...execution_start__1737972000000"
ls "$FOREST/edges/" | grep "^${RUN_ID}==\[HAS_STEP\]"

# 4. For each step, find triggered tools
STEP_ID="55c8841a-...prompt_submit__1737972001000"
ls "$FOREST/edges/" | grep "^${STEP_ID}==\[TRIGGERED\]"
```

### Find delegation trees (child sessions)

```bash
# All SPAWNED edges (tool → child session)
ls "$FOREST/edges/" | grep "SPAWNED"

# All SUBSESSION_OF edges (child → parent)
ls "$FOREST/edges/" | grep "SUBSESSION_OF"
```

### List all forests (projects) on this machine

```bash
ls ~/.amplifier/graphs/
```

### Read a specific node

```bash
cat "$FOREST/nodes/55c8841a-abcd-....json" | jq .
```

## Write Mechanics

Writes are **buffered in memory** during the session. `flush()` persists buffers to disk using atomic `tempfile + os.replace` renames (no torn writes). Flush is triggered by lifecycle events (`orchestrator:complete`, `session:end`, buffer threshold). On flush, existing files are read-merged-written (additive). If flush fails, buffers are retained for retry.

## Limitations

- **No per-session directories** — All sessions within a forest share the same flat `nodes/` and `edges/` directories. Scoping to one session requires prefix-based filename filtering (`ls | grep "^{session_id}"`), which is O(n) over the directory.
- **No secondary indexes** — Finding nodes by label or property requires scanning the entire `nodes/` directory. Every read is O(1) by ID but enumeration is O(n).
- **No query API** — For SQL, full-text search, or graph traversal queries, use the `DuckDBGraphStore` backend which implements `QueryableStore` with SQL/PGQ support.
- **No cross-forest queries** — `FileGraphStore` reads only from its own forest. Cross-forest queries require the `DuckDBGraphStore` backend with `graph_forest_name="*"`.
