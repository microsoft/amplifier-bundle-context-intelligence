# Graph Forest-Aware Storage Design

> **Goal:** Redesign the graph storage layer to be forest-aware — a "graph forest" is a named
> collection of sessions sharing the same storage scope. The module receives the forest name
> through its configuration object at mount time and scopes all storage operations accordingly.

**Date:** 2026-03-09

---

## Background

The context-intelligence hook module persists session graphs to a storage backend, but currently
has no concept of partitioning those graphs into named scopes. In app-cli scenarios, different
projects need isolated graph storage — the project slug maps naturally to a forest name. The
module itself has no project awareness; it receives the forest name through its config access
object at mount time.

Key principles driving this design:

- **No environment variables in the bundle** — everything comes from the config access object
  (coordinator config at mount time).
- **The module MUST be aware of what graph forest it's dealing with** — forest name is stamped
  on all writes and used to scope all queries.
- **Forest is a cross-protocol concept** — it applies to all storage backends (file, DuckDB,
  in-memory), not just one.

## Approach

Add `graph_forest_name` as a first-class concept at the protocol level. Every `GraphStore`
implementation exposes its forest name as a read-only property set at construction time.
`QueryableStore.execute_query` gains an optional parameter for cross-forest queries. The
factory reads the forest name from config and passes it to all backends. No env vars anywhere.

---

## Protocol Changes

The `GraphStore` protocol in `graph_store.py` gains one new read-only property:

```python
@property
def graph_forest_name(self) -> str: ...
```

Every implementation (`FileGraphStore`, `DuckDBGraphStore`, `GraphState`) must expose this.
It is set at construction time and is immutable for the lifetime of the store instance.

The basic read/write operations (`upsert_node`, `upsert_edge`, `get_node`, `get_edge`,
`flush`, `close`) remain unchanged. Point lookups by ID do not need forest filtering — IDs
are globally unique thanks to the `session_id__event__epoch` format.

`QueryableStore.execute_query` gains an optional parameter:

```python
async def execute_query(
    self,
    query: str,
    params: dict | None = None,
    dialect: str | None = None,
    graph_forest_name: str | None = None,  # NEW
) -> list[dict]: ...
```

**Forest filtering semantics:**

| `graph_forest_name` value | Behavior |
|---------------------------|----------|
| `None` (default) | Scope to the store's own forest (whatever was set at construction) |
| Explicit string | Scope to that specific forest |
| `"*"` | All forests — no forest filter applied (cross-forest queries) |

The five non-negotiable guarantees (non-blocking writes, buffer-first reads, explicit flush
lifecycle, close guarantees, failure isolation) are unchanged.

---

## Config Schema & Mount Flow

### Before (current)

```yaml
config:
  exclude_events: []
  log_level: "${CI_LOG_LEVEL:WARNING}"
  graph_store:
    type: "file"
    config:
      location: "~/.amplifier/graph"
```

### After (new)

```yaml
config:
  exclude_events: []
  log_level: "WARNING"
  graph_store:
    type: "file"
    graph_forest_name: "default"
    config:
      graph_store_root: "~/.amplifier/graphs"
```

### Key Changes

- **`log_level`** — no more env var interpolation. Plain string from config. App-cli overrides
  via bundle overlay config, not env vars.
- **`graph_forest_name`** — lives at the `graph_store` level, not inside backend-specific
  `config`. This is cross-protocol. Defaults to `"default"`.
- **`graph_store_root`** — replaces `location` for the file backend. Backend-specific, stays
  inside `config`.
- **`location`** — removed. The factory computes `graph_store_root / graph_forest_name`
  internally.
- **DuckDB backend** — `config` would have `connection` (the database file path); forest
  scoping is handled via column filtering, not directory layout.

### Mount Flow

`create_graph_store()` factory reads `graph_store.graph_forest_name` (defaulting `"default"`)
from the config object, then passes it alongside the backend-specific config to whichever
backend it constructs. No env var lookups anywhere.

**App-cli integration:** when app-cli knows the project slug, it sets `graph_forest_name` via
bundle overlay config. The module never imports anything from app-cli.

---

## Components

### FileGraphStore

The constructor changes from receiving a single `location: str` to:

```python
FileGraphStore(graph_store_root: str, graph_forest_name: str)
```

Internally it computes the working directory: `Path(graph_store_root) / graph_forest_name`.
That is where `nodes/` and `edges/` live. The store creates the directory tree on first flush
if it doesn't exist.

The `graph_forest_name` property returns the stored forest name. Nothing else about behavior
changes — flat namespace, atomic writes, buffer-first reads, merge-on-flush — all unchanged.

**Resulting layout:**

```
~/.amplifier/graphs/
  my-app/                   <- graph_forest_name = "my-app"
    nodes/
      sess-A__prompt_submit__1741270343000.json
      sess-B__session_start__1741270400000.json
    edges/
      sess-A__...==[HAS_STEP]==sess-A__....json
  default/                  <- graph_forest_name = "default" (fallback)
    nodes/
    edges/
```

### DuckDBGraphStore

The constructor gains `graph_forest_name: str` alongside the existing `connection: str`.

**Schema changes** — the `nodes`, `edges`, and `search_index` tables each gain a
`graph_forest_name VARCHAR NOT NULL` column:

| Table | Columns |
|-------|---------|
| `nodes` | `node_id` PK, `graph_forest_name`, `session_id`, `labels`, `occurred_at`, `properties` |
| `edges` | (`source`, `target`, `edge_type`) PK, `graph_forest_name`, `session_id`, `occurred_at`, `seq`, `properties` |
| `search_index` | `node_id`, `graph_forest_name`, `session_id`, `field_name`, `content`, `occurred_at` |

**Write path:** `upsert_node` and `upsert_edge` buffer entries using the store's own
`graph_forest_name`. No API change — the forest value comes from the store instance, not from
the caller.

**Query path** — `execute_query` applies forest filtering:

- `graph_forest_name=None` → scope to the store's own forest (whatever it was constructed with)
- `graph_forest_name="specific-name"` → scope to that forest
- `graph_forest_name="*"` → no forest filter, cross-forest query

For SQL queries, the store injects a `WHERE graph_forest_name = ?` clause (or omits it for
`"*"`). For PGQ queries, per-edge-type materialized tables are filtered at materialization
time — PGQ rebuild scopes to the active forest by default.

**FTS** — `search_index` queries also get the forest filter. BM25 results scoped to current
forest unless the caller opts into cross-forest.

### Factory (`store_factory.py`)

1. Read `graph_forest_name` from `graph_store` config level (not from backend-specific
   `config`). Default to `"default"` if absent.
2. Pass it to every backend constructor as a named argument:
   - `FileGraphStore(graph_store_root=..., graph_forest_name=...)`
     — root defaults to `~/.amplifier/graphs`
   - `DuckDBGraphStore(connection=..., graph_forest_name=...)`
     — connection defaults to `:memory:`
   - `GraphState(graph_forest_name=...)`
     — stores the name, no behavioral change
3. No env var lookups — factory reads from the config dict it receives from coordinator at
   mount time.
4. `HookConfig` gains awareness of `graph_forest_name` as a resolved, validated string.

The factory remains the single place where "config dict → store instance" happens. Backends
never parse config themselves.

### GraphState (In-Memory Test Helper)

Minimal change:

- `graph_forest_name: str` constructor parameter (defaulting to `"default"`)
- Read-only `graph_forest_name` property returning it

No behavioral change. Protocol compliance only. Test fixtures pass
`graph_forest_name="test"` when constructing `GraphState` instances.

---

## Skill & Documentation Updates

### DuckDB Skill

`skills/context-intelligence-graph-search/SKILL.md` — update schema documentation to reflect
the new `graph_forest_name` column on `nodes`, `edges`, and `search_index`. Add query examples
showing forest-scoped queries and cross-forest queries with `"*"`. Document the default
behavior (queries scope to the store's forest unless explicitly overridden).

### Protocol Documentation

`context/graph-store-protocol.md` — gains a "Forest Awareness" section documenting:

- The `graph_forest_name` property contract on `GraphStore`
- Query-level filtering semantics on `QueryableStore` (`None` = store's forest, explicit
  string = that forest, `"*"` = all forests)
- The guarantee that point lookups remain forest-agnostic

### Behavior YAML

`behaviors/context-intelligence.yaml` — updated config block with inline documentation of the
`graph_forest_name` key and its default.

**Standing rule:** any schema change in `duckdb_store.py` must update the skill, and vice
versa. This rule extends to the forest column.

---

## Context File Updates

The following context/doc files need updating to reflect the forest-aware storage model:

| File | Change |
|------|--------|
| `context/graph-store-protocol.md` | Add "Forest Awareness" section: property contract, query filtering semantics (`None` / explicit / `"*"`), point lookups are forest-agnostic |
| `context/graph-store-lifecycle.dot` | Show forest name resolution during `INIT → STATE_CREATED` transition (factory reads config → resolves forest name → passes to backend constructor) |
| `context/hook-event-discovery-and-dispatch.dot` | Show config flow: coordinator config → `graph_store.graph_forest_name` → factory → backend. Remove any env var references |
| `context/read-path.dot` | Add forest name context — read path for queries shows forest filtering |
| `context/write-path.dot` | Add forest name context — write path stamps forest on DuckDB inserts |
| `skills/context-intelligence-graph-search/SKILL.md` | Schema updates with `graph_forest_name` column, query examples for forest-scoped and cross-forest |
| `behaviors/context-intelligence.yaml` | Updated config block |

---

## File Store Query Operations & Skill

Since `FileGraphStore` implements `GraphStore` only (not `QueryableStore`), agents using the
file backend need a different query path. Instead of SQL, they use filesystem operations —
`grep`, `jq`, glob patterns — guided by a skill.

### New Skill: `context-intelligence-file-search/SKILL.md`

This skill teaches agents how to query the flat JSON file store.

**Layout:**

```
{graph_store_root}/{graph_forest_name}/
  nodes/{node_id}.json
  edges/{source}==[{type}]=={target}.json
```

**Query patterns by use case:**

#### 1. Find Nodes by Label

```bash
grep -l '"Session"' nodes/*.json
```

Combine with `jq` for property filtering:

```bash
grep -rl '"PromptStep"' nodes/ | xargs jq 'select(.properties.prompt_text | test("auth"))'
```

#### 2. Find Edges by Type

Glob on the edge ID format:

```bash
ls edges/*==[HAS_STEP]==*
```

The `==[TYPE]==` separator pattern makes this trivial.

#### 3. Find Nodes for a Specific Session

```bash
ls nodes/{session_id}__*
```

Leverages the session prefix in node IDs.

#### 4. Traverse a Path

Start with a session node, find its `HAS_RUN` edges, follow to run nodes, find their
`HAS_STEP` edges, etc. Shell pipeline pattern documented step by step in the skill.

#### 5. Cross-Forest Queries

Navigate to `graph_store_root/` and glob across forest subdirectories:

```bash
ls */nodes/{session_id}__*
```

#### 6. Full-Text Search Across Properties

```bash
grep -rl "search_term" {forest}/nodes/ | xargs jq '.properties'
```

**Skill structure:**

- **Schema section** — directory layout, ID formats, JSON structure of a node/edge file
- **Query pattern catalog** — the 6 patterns above with copy-pasteable examples
- **Path resolution note** — all paths are relative to the resolved
  `graph_store_root/graph_forest_name/` — no env vars, no hardcoded `~/.amplifier/graphs/`

---

## Testing Strategy

- Existing tests updated to pass `graph_forest_name` to all store constructors
- `FileGraphStore` tests verify the `root/forest_name/nodes|edges/` directory structure
- `DuckDBGraphStore` tests verify forest column is populated on writes and filtered on queries
- Cross-forest query tests for DuckDB (write to two forests, verify scoped and `"*"` queries)
- Factory tests verify `graph_forest_name` is read from config and defaults to `"default"`
- `GraphState` tests verify property compliance

---

## Open Questions

None — all design questions were resolved during the brainstorming conversation.
