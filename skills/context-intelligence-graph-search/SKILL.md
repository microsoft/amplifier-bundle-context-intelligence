---
name: context-intelligence-graph-search
description: SQL and PGQ query patterns for the context-intelligence graph store backed by DuckDB
version: 0.1.0
license: MIT
---

# Context Intelligence Graph Search

Query patterns for searching and traversing the context-intelligence graph stored in DuckDB.
Covers plain SQL, DuckDB full-text search (FTS), and ISO/IEC SQL/PGQ property-graph queries
via the DuckPGQ extension.

---

## Schema

### `nodes`

| Column | Type | Constraints |
|--------|------|-------------|
| `node_id` | `VARCHAR` | `PRIMARY KEY` |
| `session_id` | `VARCHAR` | `DEFAULT ''` |
| `labels` | `VARCHAR[]` | |
| `occurred_at` | `TIMESTAMP` | |
| `properties` | `JSON` | |

### `edges`

| Column | Type | Constraints |
|--------|------|-------------|
| `source` | `VARCHAR` | |
| `target` | `VARCHAR` | |
| `edge_type` | `VARCHAR` | |
| `session_id` | `VARCHAR` | `DEFAULT ''` |
| `occurred_at` | `TIMESTAMP` | |
| `seq` | `INTEGER` | |
| `properties` | `JSON` | |

Primary key: `(source, target, edge_type)`.

### `search_index`

| Column | Type | Constraints |
|--------|------|-------------|
| `node_id` | `VARCHAR` | `NOT NULL` |
| `session_id` | `VARCHAR` | `NOT NULL` |
| `field_name` | `VARCHAR` | `NOT NULL` |
| `content` | `VARCHAR` | `NOT NULL` |
| `occurred_at` | `TIMESTAMP` | |

---

## Property Graph Overlay (DuckPGQ)

The property graph is created on demand (not at startup) using the DuckPGQ extension.
Install and load the extension first:

```sql
INSTALL duckpgq;
LOAD duckpgq;
```

Then create the property graph when needed:

```sql
CREATE PROPERTY GRAPH context_graph
VERTEX TABLES (
    nodes
        KEY (node_id)
        LABEL Session   IN labels ('Session'),
        LABEL Root       IN labels ('Root'),
        LABEL Step       IN labels ('Step'),
        LABEL PromptStep IN labels ('PromptStep'),
        LABEL Event      IN labels ('Event')
)
EDGE TABLES (
    edges
        SOURCE KEY (source) REFERENCES nodes (node_id)
        DESTINATION KEY (target) REFERENCES nodes (node_id)
        LABEL HAS_RUN        WHERE edge_type = 'HAS_RUN',
        LABEL HAS_STEP       WHERE edge_type = 'HAS_STEP',
        LABEL NEXT           WHERE edge_type = 'NEXT',
        LABEL TRIGGERED      WHERE edge_type = 'TRIGGERED',
        LABEL PARALLEL_WITH  WHERE edge_type = 'PARALLEL_WITH',
        LABEL SPAWNED        WHERE edge_type = 'SPAWNED',
        LABEL SUBSESSION_OF  WHERE edge_type = 'SUBSESSION_OF',
        LABEL HAS_EVENT      WHERE edge_type = 'HAS_EVENT'
);
```

---

## Label System

Every node carries one or more labels in the `labels VARCHAR[]` column.

| Label | Meaning |
|-------|---------|
| `Session` | Fundamental execution boundary; one Amplifier session |
| `Root` | Top-level session with no parent |
| `Subsession` | Child session with a parent |
| `ForkedSession` | Session created via `session:fork` (inherits parent context) |
| `Resumed` | Session that was resumed from a prior run |
| `OrchestratorRun` | One `execution:start` to `execution:end` bracket (one user turn) |
| `Step` | A unit of work within an OrchestratorRun |
| `PromptStep` | The causal trigger step (iteration 0); carries the user prompt or delegation instruction |
| `AssistantStep` | An LLM iteration step within an interactive OrchestratorRun |
| `RecipeStep` | An LLM iteration step within a recipe-spawned session |
| `ToolExecution` | One `tool:pre` to `tool:post` pair; a single tool invocation |
| `Delegation` | A ToolExecution that spawned a child session via the delegate tool |
| `Event` | Any lifecycle or custom event not part of the core structural chain |

### Querying by label

```sql
-- All session nodes
SELECT * FROM nodes WHERE list_contains(labels, 'Session');

-- All PromptStep nodes
SELECT * FROM nodes WHERE list_contains(labels, 'PromptStep');

-- Nodes with multiple labels (e.g. Session + Root)
SELECT * FROM nodes
 WHERE list_contains(labels, 'Session')
   AND list_contains(labels, 'Root');
```

---

## Edge Types

| Edge Type | From | To | Meaning |
|-----------|------|----|---------|
| `HAS_RUN` | Session | OrchestratorRun | Session contains ordered orchestrator runs |
| `HAS_STEP` | OrchestratorRun | Step | Run contains ordered steps (LLM iterations) |
| `NEXT` | Step | Step | Sequential causal ordering within a run |
| `TRIGGERED` | Step | ToolExecution | Step triggered these tool executions |
| `PARALLEL_WITH` | ToolExecution | ToolExecution | Concurrent execution in same parallel group |
| `SPAWNED` | ToolExecution | Session | Delegation created a child session |
| `SUBSESSION_OF` | Session | Session | Child session to parent lineage |
| `HAS_EVENT` | Session / OrchestratorRun / Step | Event | Attaches lifecycle/custom events to their scope |

### Querying edges

```sql
-- All steps belonging to a specific run
SELECT target FROM edges
 WHERE source = 'run-node-id' AND edge_type = 'HAS_STEP'
 ORDER BY seq;

-- Walk the NEXT chain from a PromptStep
WITH RECURSIVE chain AS (
    SELECT target AS step_id FROM edges
     WHERE source = 'prompt-step-id' AND edge_type = 'NEXT'
    UNION ALL
    SELECT e.target FROM edges e
      JOIN chain c ON e.source = c.step_id
     WHERE e.edge_type = 'NEXT'
)
SELECT * FROM chain;
```

---

## Search Index

The `search_index` table stores denormalized text content for full-text search.

### `field_name` values

| field_name | Source Label | Description |
|------------|-------------|-------------|
| `prompt_text` | `PromptStep` | The full user prompt or delegation instruction text |

New field_name values are added as handlers index additional content.
The mapping is defined in `_INDEXABLE_FIELDS` in `duckdb_store.py`.

---

## Query Patterns

### Pattern 1: Direct FTS with BM25

Use DuckDB full-text search to find nodes by text content. Requires building
the FTS index first (see Notes).

```sql
-- Build the FTS index (run once after flush, or when search_index changes)
PRAGMA create_fts_index('search_index', 'content');

-- Search for sessions mentioning "authentication"
SELECT si.node_id,
       si.session_id,
       si.field_name,
       si.content,
       score
  FROM (
    SELECT *, fts_main_search_index.match_bm25(rowid, 'authentication') AS score
      FROM search_index
  ) si
 WHERE score IS NOT NULL
 ORDER BY score DESC;
```

### Pattern 2: FTS + PGQ Traversal

Find nodes by text search, then traverse the graph from those nodes using
SQL/PGQ. Uses a CTE to feed FTS results into a GRAPH_TABLE query.

```sql
-- Find prompt steps matching a query, then walk to their parent session
WITH hits AS (
    SELECT node_id, fts_main_search_index.match_bm25(rowid, 'refactor auth') AS score
      FROM search_index
     WHERE score IS NOT NULL
)
SELECT hit_node, session_node, score
  FROM hits, GRAPH_TABLE (context_graph
    MATCH (s:Session)-[r:HAS_STEP]->(step)
    WHERE step.node_id = hits.node_id
    COLUMNS (step.node_id AS hit_node, s.node_id AS session_node)
  ) gt
 JOIN hits ON gt.hit_node = hits.node_id
 ORDER BY hits.score DESC;
```

### Pattern 3: Pure PGQ (no text search)

Structural graph traversal without text search. Useful for navigating
session hierarchies, delegation chains, and step sequences.

```sql
-- Find all steps in a session's runs
SELECT run_id, step_id
  FROM GRAPH_TABLE (context_graph
    MATCH (s:Session)-[hr:HAS_RUN]->(r)-[hs:HAS_STEP]->(step)
    WHERE s.node_id = 'my-session-id'
    COLUMNS (r.node_id AS run_id, step.node_id AS step_id)
  );

-- Find delegation chains: session -> tool -> child session
SELECT parent_id, tool_id, child_id
  FROM GRAPH_TABLE (context_graph
    MATCH (parent)-[hr:HAS_RUN]->(run)-[hs:HAS_STEP]->(step)
          -[t:TRIGGERED]->(te)-[sp:SPAWNED]->(child)
    COLUMNS (
        parent.node_id AS parent_id,
        te.node_id     AS tool_id,
        child.node_id  AS child_id
    )
  );

-- Walk subsession lineage
SELECT child_id, parent_id
  FROM GRAPH_TABLE (context_graph
    MATCH (child)-[sub:SUBSESSION_OF]->(parent)
    COLUMNS (child.node_id AS child_id, parent.node_id AS parent_id)
  );
```

---

## Notes

### FTS index rebuild timing

The DuckDB FTS index is static. After new rows are flushed to `search_index`,
the FTS index must be rebuilt before queries will find the new content:

```sql
PRAGMA drop_fts_index('search_index');
PRAGMA create_fts_index('search_index', 'content');
```

Rebuild after each `flush()` cycle that writes to `search_index`, or
batch rebuilds at query time if staleness is acceptable.

### Property graph creation timing

The `CREATE PROPERTY GRAPH` DDL is executed on demand when a PGQ query is
first needed, not at startup. This avoids overhead when only plain SQL
queries are used. If the underlying tables change schema, drop and recreate:

```sql
DROP PROPERTY GRAPH IF EXISTS context_graph;
CREATE PROPERTY GRAPH context_graph ...;
```

### JSON property access syntax

Node and edge properties are stored as `JSON` columns. Use DuckDB JSON
access operators to extract values:

```sql
-- Arrow operator (returns JSON)
SELECT properties->'status' FROM nodes WHERE node_id = 'abc';

-- Double-arrow operator (returns VARCHAR)
SELECT properties->>'status' FROM nodes WHERE node_id = 'abc';

-- json_extract for nested access
SELECT json_extract(properties, '$.metadata.agent_name') FROM nodes;

-- In WHERE clauses
SELECT * FROM nodes
 WHERE properties->>'status' = 'completed';
```