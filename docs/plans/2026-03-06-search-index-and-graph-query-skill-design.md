# Search Index and SQL/PGQ Skill Design

## Goal

Add full-text search capability to the context-intelligence graph store, and create a SQL/PGQ skill that teaches LLMs how to generate queries against the graph schema.

## Background

The context-intelligence bundle captures session events into a property graph stored in DuckDB. The graph structure (nodes, edges, property graph overlay via DuckPGQ) supports traversal queries, but there is no way to search the **text content** of sessions -- prompts, responses, tool results, thinking blocks. Agents need to find sessions by content ("which session discussed comic-strip-bundle?") and then optionally walk the graph from those results ("find the root session that spawned it").

Two capabilities are missing:

1. **Full-text search** over session content with BM25 ranking.
2. **A skill** that teaches LLMs the full schema and how to compose FTS + PGQ queries, so agents can generate correct queries without understanding the database internals.

## Approach

- Separate `search_index` table alongside `nodes` and `edges` in DuckDB.
- DuckDB FTS extension with BM25 ranking on the `content` column.
- `field_name` column distinguishes searchable fields (`prompt_text`, `response_text`, `tool_result`, `thinking`, etc.). One node can have multiple searchable fields, each as a separate row.
- Queries compose FTS results with DuckPGQ graph traversal via CTEs when relationship walking is needed.
- A SQL/PGQ skill shipped with the bundle teaches agents the full schema and query patterns.
- Standing rule: any schema change MUST update the skill. Enforced in `duckdb_store.py` docstring and skill cross-reference.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Handlers (PromptStep, AssistantStep, etc.)             │
│  ─ create nodes + edges in write buffer                 │
│  ─ insert searchable text into search_index buffer      │
└──────────────┬──────────────────────────────────────────┘
               │ buffer-first writes
               ▼
┌─────────────────────────────────────────────────────────┐
│  DuckDBGraphStore                                       │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────────┐ │
│  │ nodes buffer  │ │ edges buffer │ │ search_index    │ │
│  │              │ │              │ │ buffer          │ │
│  └──────┬───────┘ └──────┬───────┘ └───────┬─────────┘ │
│         └────────────────┼─────────────────┘           │
│                    flush (single txn)                   │
│                          │                              │
│  ┌───────────────────────▼─────────────────────────┐   │
│  │  DuckDB                                          │   │
│  │  ┌────────┐  ┌────────┐  ┌──────────────┐      │   │
│  │  │ nodes  │  │ edges  │  │ search_index │      │   │
│  │  └────┬───┘  └────┬───┘  └──────────────┘      │   │
│  │       └─────┬─────┘                              │   │
│  │    session_graph                                 │   │
│  │    (PGQ overlay)     FTS index (rebuilt async)   │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
               │
               │ query-time composition
               ▼
┌─────────────────────────────────────────────────────────┐
│  Agent Query Generation (via SQL/PGQ skill)             │
│  ─ Pattern 1: Direct FTS (no graph traversal)           │
│  ─ Pattern 2: FTS + PGQ traversal (CTE composition)     │
│  ─ Pattern 3: Pure PGQ (graph structure only)            │
└─────────────────────────────────────────────────────────┘
```

## Components

### Search Index Table

New table in DuckDB, created alongside `nodes` and `edges`:

```sql
CREATE TABLE IF NOT EXISTS search_index (
    node_id     VARCHAR NOT NULL,
    session_id  VARCHAR NOT NULL,
    field_name  VARCHAR NOT NULL,
    content     VARCHAR NOT NULL,
    occurred_at TIMESTAMP
);
```

Column definitions:

| Column | Purpose |
|--------|---------|
| `node_id` | Links back to the graph node that produced this content |
| `session_id` | Scoping, same as on nodes/edges |
| `field_name` | What kind of content: `"prompt_text"`, `"response_text"`, `"tool_result"`, `"thinking"`, etc. One node can have MULTIPLE searchable fields, each as a separate row. |
| `content` | The actual searchable text |
| `occurred_at` | When it was produced |

FTS index on the content column:

```sql
PRAGMA create_fts_index('search_index', 'content');
```

This gives BM25-ranked full-text search with tokenization and stemming.

### Who Writes to search_index

The handlers. When a handler creates a node with searchable text content, it also inserts the content into the search_index. For example, the PromptStep handler creates the PromptStep node AND writes the prompt text to search_index with `field_name = "prompt_text"`.

This is done in the same buffer-first write path as nodes and edges -- the search_index insert goes into the write buffer and is flushed alongside graph data.

### FTS Index Rebuild is NOT in the Hot Path

The FTS index (`PRAGMA create_fts_index`) is an expensive operation. It MUST NOT happen during handler event processing. It is a signalled background operation, queued after flushes. Between index rebuilds, newly inserted content is not FTS-searchable -- this is acceptable for analysis-time queries.

### SQL/PGQ Skill

A skill file shipped with the bundle, discoverable by any agent via `load_skill`.

Location:

```
amplifier-bundle-context-intelligence/
    skills/
        context-intelligence-graph-search/
            SKILL.md
```

Registered via the bundle's tool-skills config so it's discoverable by any agent.

The skill teaches:

**Schema knowledge:**

| Table | Columns |
|-------|---------|
| `nodes` | `node_id VARCHAR PK`, `session_id VARCHAR`, `labels VARCHAR[]`, `occurred_at TIMESTAMP`, `properties JSON` |
| `edges` | `source VARCHAR`, `target VARCHAR`, `edge_type VARCHAR`, `session_id VARCHAR`, `occurred_at TIMESTAMP`, `seq INTEGER`, `properties JSON`, PK `(source, target, edge_type)` |
| `search_index` | `node_id VARCHAR`, `session_id VARCHAR`, `field_name VARCHAR`, `content VARCHAR`, `occurred_at TIMESTAMP` |
| Property graph | `session_graph` defined over nodes/edges via DuckPGQ |

**Label system:** Nodes are typed by labels stored in `VARCHAR[]`. Use `list_contains(labels, 'PromptStep')` to filter by type. Labels include: `Session`, `Root`, `Subsession`, `ForkedSession`, `Resumed`, `OrchestratorRun`, `Step`, `PromptStep`, `AssistantStep`, `RecipeStep`, `ToolExecution`, `Delegation`, `Event`, plus `derive_label()` derived labels.

**Edge types:** `HAS_RUN` (Session → OrchestratorRun), `HAS_STEP` (Session/OrchestratorRun → Step), `NEXT` (Step → Step), `TRIGGERED` (Step → ToolExecution), `PARALLEL_WITH` (ToolExecution ↔ ToolExecution), `SPAWNED` (ToolExecution → Session), `SUBSESSION_OF` (Session → Session), `HAS_EVENT` (any scope → Event).

**Search index `field_name` values:**
- `"prompt_text"` -- user input or delegation instruction on PromptStep nodes
- Future: `"response_text"`, `"tool_result"`, `"thinking"`, etc. as handlers are implemented.

**Cross-reference:** The skill must state: *"Schema source of truth: `duckdb_store.py`. If this skill's schema doesn't match `duckdb_store.py`, the skill is stale."*

## Data Flow

### Write Path (Event → Search Index)

1. Handler receives event (e.g., `prompt_start`).
2. Handler creates node in the node write buffer.
3. Handler extracts searchable text, inserts into the search_index write buffer with appropriate `field_name`.
4. `flush()` writes nodes, edges, and search_index rows in a single transaction.
5. FTS index rebuild is signalled (not executed in-line).

### Query Path (Agent → Results)

1. Agent loads the SQL/PGQ skill via `load_skill`.
2. Agent generates a query using one of three patterns:
   - **Pattern 1 (Direct FTS):** Query the `search_index` directly with BM25. Answer is in `session_id`.
   - **Pattern 2 (FTS + PGQ):** FTS finds candidates in a CTE, PGQ walks graph from those candidates.
   - **Pattern 3 (Pure PGQ):** Graph structure query, no text search.
3. Query executes against DuckDB.

## Query Patterns

### Pattern 1: Direct FTS (No Graph Traversal)

Use when the answer is already in `search_index` — e.g., "find sessions where the prompt mentioned X."

```sql
SELECT session_id, node_id, field_name,
       fts_main_search_index.match_bm25(rowid, 'comic-strip-bundle') AS score
FROM search_index
WHERE score IS NOT NULL
  AND field_name = 'prompt_text'
ORDER BY score DESC;
```

PGQ is not needed because the answer IS the `session_id`.

### Pattern 2: FTS + PGQ Traversal

Use when FTS finds candidates but the answer requires walking graph relationships — e.g., "find the root session that had any descendant session matching X."

```sql
WITH matches AS (
    SELECT session_id,
           fts_main_search_index.match_bm25(rowid, 'comic-strip-bundle') AS score
    FROM search_index
    WHERE score IS NOT NULL AND field_name = 'prompt_text'
)
SELECT DISTINCT graph_result.root_session_id, m.score
FROM matches m,
     GRAPH_TABLE (session_graph
         MATCH (child:Session)-[:SUBSESSION_OF*]->(root:Session)
         WHERE child.node_id = m.session_id
           AND list_contains(root.labels, 'Root')
         COLUMNS (root.node_id AS root_session_id)
     ) graph_result
ORDER BY m.score DESC;
```

FTS finds candidates with BM25 ranking → PGQ walks the graph to find their root ancestor. Each layer does what it's good at.

### Pattern 3: Pure PGQ (No Text Search)

Use for graph structure queries — e.g., "show me the step chain for session X."

```sql
FROM GRAPH_TABLE (session_graph
    MATCH (s:Session)-[:HAS_STEP]->(p:Step)
    WHERE s.node_id = 'abc123'
      AND list_contains(p.labels, 'PromptStep')
    COLUMNS (s.node_id AS session_id, p.properties AS step_props)
)
```

### The Principle

FTS for text discovery, PGQ for graph traversal. Use PGQ only when you need to walk relationships that FTS can't answer. Don't use PGQ to re-fetch data you already have from FTS.

## Error Handling

- **FTS index not yet built:** Queries against `fts_main_search_index.match_bm25()` will fail if the FTS index hasn't been created yet. The store should handle this gracefully — return empty results or trigger an index rebuild.
- **Search index write failure:** If search_index insert fails during flush, it should not block node/edge writes. Search is supplementary; the graph must remain consistent.
- **Stale FTS index:** Between rebuilds, newly inserted content is not searchable. This is documented and acceptable — queries return what's indexed, not what's buffered.

## Schema Impact on DuckDBGraphStore

1. **Add `search_index` table creation** in the constructor alongside nodes and edges.
2. **Add search_index write buffer** — `_search_buffer: list[dict]` for buffered search index inserts.
3. **`flush()` includes search_index writes** — inserts into search_index in the same transaction as nodes/edges.
4. **FTS index rebuild** — separate method, NOT called during flush. Called explicitly when needed (e.g., after a sequence of flushes, or on demand before a search query).
5. **Update `duckdb_store.py` docstring** with the standing rule about skill synchronization.

## Standing Rule: Skill Must Track Schema

**Rule:** When the DuckDB storage implementation or schema changes, the SQL/PGQ skill MUST be updated in the same change.

This includes:
- New tables added
- Columns added or changed on existing tables
- Property graph definition changes
- New label types on nodes
- New edge types
- New query patterns
- New `field_name` values in search_index

### Enforcement (Permanent — In the Bundle Submodule)

1. **`duckdb_store.py` docstring** — at the top of the file, explicitly states that any schema change requires updating the skill at `skills/context-intelligence-graph-search/SKILL.md`.
2. **`SKILL.md` cross-reference** — *"Schema source of truth: `duckdb_store.py`. If this skill's schema doesn't match `duckdb_store.py`, the skill is stale."*

Bidirectional pointers. The code says "update the skill", the skill says "check the code."

### Enforcement (Workspace Only — For This Session)

3. **`AGENTS.md`** — standing rule added under Project Notes for workspace visibility. This is supplementary; the permanent enforcement is in the bundle submodule.

## Testing Strategy

- **Search index writes:** Unit tests confirming handlers insert correct `field_name` + `content` pairs into the search buffer alongside node creation.
- **Flush includes search_index:** Integration test that flushes and verifies search_index rows land in DuckDB alongside nodes/edges.
- **FTS queries:** Integration tests running BM25 queries after an index rebuild, verifying ranked results against known inserted content.
- **FTS + PGQ composition:** Integration test that inserts a subsession hierarchy, indexes it, and runs a Pattern 2 query (FTS finds child, PGQ walks to root).
- **Skill accuracy:** Validate that every table, column, label, edge type, and field_name listed in the skill matches the actual schema in `duckdb_store.py`.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Separate `search_index` table | Keeps graph schema (nodes/edges) clean. FTS needs VARCHAR columns, not JSON. Search concern is separate from graph structure. |
| `field_name` column instead of `content_type` | Uses meaningful field names (`prompt_text`, `response_text`) rather than generic types. One node can have multiple searchable fields as separate rows. |
| No `content_type` or `label` columns on search_index | The node's labels in the nodes table tell you what it is. Don't duplicate the type system. |
| FTS + PGQ composition via CTEs | FTS finds text matches, PGQ walks relationships. Each does what it's good at. Don't use PGQ to re-fetch what FTS already found. |
| Skill shipped with bundle | LLMs can generate queries by loading the skill. Schema is learnable without a full database tutorial. |
| Standing rule enforced in code | Docstring in `duckdb_store.py` + cross-reference in SKILL.md. Not just in external docs that might not be loaded. |
| FTS index rebuild NOT in hot path | Signalled background operation. New content searchable after next rebuild, not immediately. Eventually consistent. |
| AGENTS.md updated for workspace only | The permanent enforcement is in the bundle submodule. AGENTS.md is for this workspace session. |

## Open Questions

1. **FTS index rebuild trigger** — When exactly should the FTS index be rebuilt? After N flushes? On explicit request? Before the first search query? Needs experimentation.
2. **Search result pagination** — BM25 returns scored results. Do we need `LIMIT`/`OFFSET` or is a score threshold enough?
3. **`field_name` standardization** — As more handlers are added, `field_name` values will accumulate. Should there be a registry or is convention sufficient?
4. **Property graph definition scope** — Does `CREATE PROPERTY GRAPH session_graph` need to include the `search_index` table, or is it only for nodes/edges? Current design keeps `search_index` out of the property graph definition since it's not a vertex/edge table — it's a search index. Queries compose via CTEs, not graph patterns.
