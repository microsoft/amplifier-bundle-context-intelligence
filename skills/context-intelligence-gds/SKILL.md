---
name: context-intelligence-gds
description: >
  Use when an analysis over the context-intelligence graph is about to be written as
  naive Cypher — reach for Neo4j GDS (Graph Data Science) or APOC instead when the
  question is really a complex topology search: pathfinding, reachability,
  centrality/influence, community/clustering, topological/structural similarity, or
  finding similar event PATHS / trajectories (with or without a time dimension). Names the
  advantages of the graph-data-science package and the best-fit scenarios for it, so
  you stop hand-rolling traversals and re-deriving structure one hop at a time. This
  is the WHEN/WHY layer; the graph-query skill's "Push Work to the Database" section
  is the HOW.
license: MIT
metadata:
  version: "1.1.0"
  changelog:
    - "1.1.0: Added the 'Complex topology searches' section — centrality, topological/structural similarity (nodeSimilarity/KNN/FastRP embeddings), and similar event PATHS / trajectories with-or-without a time dimension (set-similarity vs transition-signature), plus pathfinding and a non-lineage projection recipe. These are the hard searches the skill exists to route off naive Cypher."
    - "1.0.0: Initial — GDS/APOC scenario-selection and advantages, avoid-naive-Cypher decision map, three reusable primitives tuned to this graph."
---

# Context Intelligence — Graph Data Science First

The context-intelligence graph is large (millions of nodes across thousands of
sessions). Naive Cypher tends to **re-derive, one hop and one round-trip at a time,
what a graph algorithm computes in a single server-side pass**. This skill trains the
reflex: recognize the *shape* of the question, and when it is a graph-algorithm shape,
reach for **GDS or APOC** instead of improvising Cypher.

> **This skill is WHEN and WHY. The HOW lives next door.** The graph-query skill owns
> the schema meaning, the traps, and the worked projection queries — its **Section 8,
> "Push Work to the Database (APOC / GDS)"** has the concrete `gds.graph.project →
> algorithm → gds.graph.drop` lifecycle and the `Session`/`FORKED` + agent-rollup
> example. Load `context-intelligence-graph-query` for mechanics; use this skill to
> decide *whether the algorithm is the right tool at all.*

---

## The naive-query smell test

You are probably about to write naive Cypher where GDS/APOC would be better if you catch
yourself doing any of these:

- **Guessing edge types to walk a tree** (`-[:HAS_SUBSESSION|FORKED|HAS_FORK]->`) or
  repeatedly stepping `Session.parent_id` one hop at a time to rebuild a delegation tree.
- **`collect()` then `size([x IN … WHERE …])`** to count how many things are reachable /
  match — reconstructing an algorithm's output by hand.
- **A giant `WHERE node_id IN [ …50 literals… ]`** pasted into the query text (client-side
  set-building shoved into Cypher).
- **`UNWIND range(0, size(list)-2)`** to compute consecutive gaps / pairwise math.
- **Variable-length `-[*1..n]->`** followed by client-side filtering of the paths.
- Pulling many rows to the client to **iterate and aggregate** what the DB could reduce.

Each of these is a tell that the question has a *graph-algorithm shape*.

---

## What the graph-data-science package buys you (the advantages)

| Advantage | Naive Cypher | GDS / APOC |
|---|---|---|
| **One pass, not many round-trips** | N queries + client stitching | project once, compute server-side, return a bounded result |
| **Scales to the whole graph** | degrades / times out on deep or wide traversals | algorithms are built for millions of nodes |
| **Correct, not approximate** | hand-rolled shortest-path / "influence" is easy to get subtly wrong | `gds.shortestPath`, `gds.pageRank`, `gds.betweenness` are the real thing |
| **Less context churn** | large client-side pulls flood the session | the payload never crosses the wire; only the answer does |
| **Stable, reusable shapes** | every question becomes a new bespoke query | a handful of parameterized primitives cover most questions |

---

## Best-fit scenarios → the right tool

Match the *question shape* to the tool. This is the core of the skill.

| The question is really… | Naive trap | Reach for | Why it wins |
|---|---|---|---|
| **Full delegation subtree / all descendants of a session** | one-hop `parent_id` walks, edge-type guessing | `apoc.path.expandConfig(root, {relationshipFilter:'FORKED>', minLevel:1, maxLevel:20})`, or `gds.bfs` | one call returns the whole bounded lineage; respects Trap 1 (lineage is `FORKED`, *not* `HAS_SUBSESSION`) |
| **How are two sessions connected / shortest path** | manual `-[*1..n]->` + filtering | `gds.shortestPath.*` (or `apoc.path.expandConfig`) | correct pathfinding in one pass |
| **Which agent/session is a hub / most influential** | `collect` + `size` degree-counting | `gds.pageRank` / `gds.degree` / `gds.betweenness` over `Session`/`FORKED`, rolled up by the `Session.agent` property | real centrality; see graph-query §8 for the exact projection + agent-rollup (agents are a property to roll up by, **not** a projectable graph) |
| **Clusters / communities of related sessions** | can't express it naively | `gds.louvain` / `gds.wcc` | community structure in one call |
| **Sessions similar by shared tools/skills** | pairwise `collect` intersections | `gds.nodeSimilarity` over a `Session`→`Tool`/`Skill` projection | scalable similarity instead of O(n²) hand-joins |
| **Reach a JSON field inside `Event.data`** | `e.data.working_dir` (raises a type error) | `apoc.convert.fromJsonMap(e.data).working_dir` | correct *and* lean — see graph-query §8's authoritative pattern |
| **Operate over a known set of ids** | 50-id `IN [ … ]` literal | pass `$ids` and `UNWIND $ids AS id` | one reusable shape; for heavy scans, `apoc.periodic.iterate` |
| **Consecutive-pair math (inter-event gaps)** | `UNWIND range(0,size-2)` | `apoc.coll.pairsMin(list)` | expresses the intent directly |

---

## Complex topology searches — the hard ones this skill exists for

These are the searches naive Cypher either **cannot express** or does at O(n²) with
wrong/approximate results. When the question is one of these, do NOT hand-roll it —
project the right graph and run the algorithm.

**1. Centrality / influence** — "which sessions, agents, or tools are structurally most important?"
- `gds.pageRank` · `gds.betweenness` · `gds.degree` · `gds.closeness` · `gds.eigenvector`.
- Project the relevant graph (`Session`/`FORKED` for delegation influence; a tool
  co-occurrence graph for tool influence), stream, roll up by the property you care
  about, `gds.graph.drop`.

**2. Topological / structural similarity** — "which sessions are shaped alike?"
- **By shared neighbors** (same tools/skills/agents used): project a **bipartite**
  `Session → Tool` (or `→Skill`/`→Agent`) graph → `gds.nodeSimilarity` (Jaccard/overlap)
  or `gds.knn`.
- **By structural role** (behave alike even without sharing exact neighbors):
  `gds.fastRP` (or `gds.node2vec`) node embeddings → `gds.knn` for nearest structural
  neighbors. This finds role-equivalent sessions.

**3. Similar event PATHS / trajectories — the sequence question, with or without time**
This is the "find similar paths of events" search. The time dimension decides the tool:
- **WITHOUT time (bag of steps):** reduce each session to its *multiset* of event/tool
  types, then `gds.nodeSimilarity`/`gds.knn` over the `Session → Tool` projection. Order
  is ignored — "used A, B, C" matches "used C, B, A".
- **WITH time (ordered trajectory):** order matters, so build the ordering first. Sort
  each session's events by `started_at`, form consecutive pairs with
  `apoc.coll.pairsMin(ordered_list)`, and materialise a **transition signature** —
  `(:Session)-[:HAS_TRANSITION]->(:TypePair {a,b})`. Then either `gds.nodeSimilarity`
  over that transition projection, or `gds.fastRP` + `gds.knn` to cluster whole
  trajectories. This distinguishes `A→B→C` from `A→C→B`, which set-similarity cannot.
- Extract one session's ordered path with `apoc.path.expandConfig` or a time-ordered
  `MATCH` — never a client-side reconstruction.

**4. Pathfinding / reachability** — shortest, k-shortest, all-reachable.
- `gds.shortestPath.dijkstra` · `gds.allShortestPaths` · Yen's k-shortest ·
  `gds.bfs`/`gds.dfs`; or `apoc.path.expandConfig` for filtered reach.

**Projection recipe (similarity/trajectory graphs are not stored edges — build them):**
```cypher
// bipartite Session→ToolCall for similarity (Cypher projection, GDS 2.x)
// confirm the Session→ToolCall reach against the graph-query skill / live schema first
CALL gds.graph.project.cypher('sess-tool',
  'MATCH (n) WHERE n:Session OR n:ToolCall RETURN id(n) AS id',
  'MATCH (s:Session)-[:HAS_EXECUTION|HAS_PART*1..3]->(tc:ToolCall)
   RETURN id(s) AS source, id(tc) AS target');
CALL gds.nodeSimilarity.stream('sess-tool')
  YIELD node1, node2, similarity
RETURN gds.util.asNode(node1).node_id AS a, gds.util.asNode(node2).node_id AS b, similarity
ORDER BY similarity DESC LIMIT 20;
CALL gds.graph.drop('sess-tool') YIELD graphName;   // always release (in-memory only, no data touched)
```

---

## Three primitives that replace most naive Cypher here

Default to these instead of re-deriving structure:

1. **The lineage walker** — `apoc.path.expandConfig` over `FORKED` (or a `Session`/`FORKED`
   GDS projection for tree analytics). This one primitive replaces the single largest
   class of hand-rolled queries on this graph.
2. **The JSON-lift reflex** — never touch `Event.data` with dot-access; parse in-graph
   with `apoc.convert.fromJsonMap` and return only the scalar (graph-query §8 is authoritative).
3. **The parameterized id-set** — `UNWIND $ids`, never a pasted literal list. Collapses a
   long tail of "unique" queries into one reused shape.

---

## When naive Cypher IS the right tool (don't over-reach)

GDS/APOC is not always the answer. A **single plain Cypher statement** already wins for:

- counts and existence checks (`RETURN count(*)`),
- single-session scalar lookups,
- group-bys / distributions over one hop (`GROUP BY tool_name`),
- schema probes (`db.labels()`, `db.relationshipTypes()`).

Reach for the algorithm when the naive alternative means **many round-trips, a large
client-side pull, or reconstructing an algorithm by hand** — not for a plain aggregate.

---

## Guardrails

- **Probe first:** `RETURN gds.version()` / `CALL gds.list()`; `CALL apoc.help('path')`.
  Both plugins run on the live target (Neo4j Community — GDS works).
- **Always release projections:** `gds.graph.drop('g')` when done.
- **Bound the returned result** even when the compute is server-side — `count` / aggregate
  / `LIMIT`. Pushing work to the DB does not excuse an unbounded `RETURN`.
- **Project the labels that exist** — `Session` (nodes), `FORKED` (relationships). Agents
  are a **property** (`Session.agent`) to roll up by, not a projectable graph.
- **Honor the schema traps** — especially Trap 1 (delegation lineage is `FORKED` +
  `Session.parent_id`, and `HAS_SUBSESSION` is a single hop). The graph-query skill owns
  these; load it before writing the query.
