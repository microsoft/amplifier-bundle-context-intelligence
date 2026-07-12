---
name: context-intelligence-graph-query
description: >
  Use when querying the context-intelligence property graph for session history,
  tool call traces, LLM iteration analysis, execution scale metrics, agent
  delegation trees, skill loading, and recipe orchestration. Covers all graph
  layers, cross-layer SOURCED_FROM joins, SST navigation, blob handling, and
  verified Cypher patterns.
license: MIT
metadata:
  version: "2.2.0"
  changelog:
    - "2.2.0: Corrected the Section 2 workspace/working_dir claims — workspace is a lifted, reliable (~100%) scoping slug, not the literal working directory; working_dir is a sparsely-populated (~7%, 238/3,259 sessions) payload-only field with no canonical per-session source, and must never be used to scope a population. Consolidated one visible+correct in-graph APOC payload-parse pattern: dot-access on a JSON-string field (e.g. e.data.working_dir) raises a type error, not a silent null — parse in-graph with apoc.convert.fromJsonMap and return only the scalar needed. De-duplicated the Section 6 copy into a pointer at this pattern."
    - "2.1.0: Reporting guidance — agent-level / self-delegation answers must STATE the resolved self→actor breakdown (root/main vs named) as an explicit standalone finding, not fold it into a blended statistic. Tightened the Section 8 agent-rollup: project Session/FORKED (the labels that exist) and roll up by the Session.agent property; agents are NOT a projectable Agent graph."
    - "2.0.1: Schema accuracy fix: corrected Prompt/SkillLoad/Orchestrator property names, RecipeStep/RecipeRun property placement, RecipeStep→RecipeRun edge (SPAWNED), removed phantom L1 edges, fixed example queries — all validated against the live graph."
---

## When the graph server is not configured

If the context-intelligence server is unavailable or not configured for this
session, graph-based analysis cannot proceed here. Delegate immediately to
`session-navigator` for all session analysis, event lookup, and delegation
tree tracing — do not attempt Cypher queries against a server that isn't
reachable.

---

# Context Intelligence Graph Query

This skill equips you to navigate and extract insights from the context-intelligence
property graph using the `graph_query` tool. The graph holds a complete record of
every Amplifier session — what happened, when, how things connect, and at what scale.

**Read the live schema from the server; read the meaning from this skill.** The
server can always tell you the current shapes — labels, relationship types, property
names. It CANNOT tell you what those shapes mean, where they lie about their meaning,
or how to scope, join, and bound a query safely. This skill deliberately does not
freeze a schema dictionary (it goes stale and misleads). It keeps the grammar: the
scoping levers, the traps, the cross-layer joins, and the verified patterns.

---

## Section 1 — Introspect the Live Schema First

Before writing analytical Cypher against an unfamiliar area of the graph, ask the
server what it actually contains. These calls are cheap and always current.

```cypher
CALL db.labels()                          // every node label present
CALL db.relationshipTypes()               // every edge type present
CALL db.schema.nodeTypeProperties()       // property names + types per label
CALL db.schema.visualization()            // label/edge connectivity overview
```

To learn the real properties on a specific label (names AND which are populated),
sample a few live nodes rather than trusting any documented list:

```cypher
MATCH (n:ToolCall) WITH n LIMIT 25
UNWIND keys(n) AS k
RETURN k AS property, count(*) AS present_on_n_nodes
ORDER BY present_on_n_nodes DESC
```

**The three-layer shape (stable orientation, not a dictionary):**

- **Data layer 1 — raw events.** Every kernel event preserved as an `:Event` node
  (`:ToolPreEvent`, `:LlmResponseEvent`, `:PromptSubmitEvent`, …). Answers *what
  happened and when*. `node_id` uses a `__` (double-underscore) separator.
- **Data layer 2 — semantic entities.** Events assembled into `Session`,
  `OrchestratorRun`, `Iteration`, `ContentBlock`, `ToolCall`, `Prompt`, and more,
  connected by typed relationships. Answers *what ran, how, at what scale*. `node_id`
  uses a `::` (double-colon) separator (a few types — `ToolCall`, concept nodes — use
  a bare identifier).
- **Foundation layer — above the kernel.** Delegation trees (`Delegation`, `Agent`),
  skill loads (`SkillLoad`), recipe orchestration (`RecipeRun`, `RecipeStep`,
  `Recipe`).

All layers coexist in one graph, bridged by **`SOURCED_FROM`** edges: every layer-2
entity points back to the layer-1 event(s) that produced it (Section 4). Use
`db.schema.nodeTypeProperties()` to confirm exact property names before you rely on
them — the sections below cover only the meaning the server cannot self-describe.

---

## Section 2 — Scoping (Mandatory, and Not Self-Describing)

Scoping is the single most error-prone part of querying this graph, and the server
cannot tell you how its client scopes. Get this right first.

### The `workspace` tool argument — NOT a Cypher param

**The `graph_query` tool reads a top-level `workspace` argument.** It injects that
value as `$workspace` into your Cypher. Do **not** try to set the workspace through
`params: {"workspace": ...}` — the tool does not read the workspace from `params`, and
a mis-set workspace silently scopes to a tiny `"default"` decoy partition instead of
the real one. If your counts look implausibly small (a handful of sessions where you
expected hundreds), you are almost certainly scoped to the decoy.

```cypher
// Correct: reference $workspace; set the workspace via the tool's top-level
// `workspace` argument, not via params.
MATCH (s:Session {workspace: $workspace})
RETURN count(s) AS session_count
```

### The two real scoping levers

Both live as first-class properties on nodes — confirm them by sampling:

- **`created_by`** — *who* produced the data (present on every node; the data is
  typically single-user).
- **`workspace`** — *where*: a **lifted, dash-slugified label/slug** (e.g.
  `-mnt-linuxdata-workspaces-...-dashboard-ui`), reliable at ~100% coverage. It is a **label,
  not the literal working directory**: the true directory lives in the event `data` payload as
  `working_dir`, and the **same `working_dir`** (`/home/user/project`) can map to **different**
  `workspace` slugs (`myproject`, `proj`, `multi`). Treat `workspace` as the reliable scoping
  slug; treat `working_dir` as a **payload field, sparsely populated (~7% of sessions)** with no
  canonical per-session source — read it via the APOC parse in Section 8, but do **not** scope by
  it. The `/cypher` request's own `workspace` scope is a second, orthogonal server-side filter
  layered on top.

**Always anchor scope on the first `MATCH`** so the workspace index is used:

```cypher
// CORRECT — workspace on the anchor node
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun)
RETURN run.orchestrator_name, run.started_at

// INCORRECT — workspace on a downstream node misses the index
MATCH (s:Session {node_id: $session_id})
      -[:HAS_EXECUTION]->(run:OrchestratorRun {workspace: $workspace})
RETURN run.orchestrator_name, run.started_at
```

To discover which workspaces exist before scoping, aggregate across all of them
(use sparingly — it skips the partition index):

```cypher
MATCH (s:Session)
WHERE s.workspace <> ''
RETURN s.workspace, s.created_by, count(s) AS sessions
ORDER BY sessions DESC
LIMIT 25
```

---

## Section 3 — Traps: Where the Shapes Lie About Their Meaning

These are the highest-value items in this skill. Each is a place where reading the
schema literally leads you to a **wrong answer that looks rigorous**. The server will
happily hand you the misleading shape; only domain knowledge corrects it.

### Trap 1 — Delegation lineage: use `FORKED` + `Session.parent_id`, not the edge walk

The true delegation/fork lineage is carried by the **`FORKED` edge** together with
**`Session.parent_id`**. This is the authoritative source for delegation depth and
ancestry. Verified live: the fork lineage reaches **max depth 3**.

```cypher
// Authoritative lineage depth via the FORKED edge
MATCH path = (root:Session {workspace: $workspace})-[:FORKED*]->(leaf:Session)
WHERE root.parent_id IS NULL
RETURN max(length(path)) AS max_fork_depth, count(path) AS lineages
```

`Session.parent_id` lets you reconstruct the same chain by property when an edge walk
is inconvenient (each child names its parent):

```cypher
MATCH (child:Session {workspace: $workspace})
WHERE child.parent_id IS NOT NULL
RETURN child.node_id, child.parent_id
```

**Two known undercounts — do NOT trust either for depth:**

- **`HAS_SUBSESSION` is a single hop only.** It materializes only the immediate
  parent→child link; each sub-session is ingested as its own root, so a multi-hop walk
  `(:Session)-[:HAS_SUBSESSION*]->()` collapses to depth 1. It is a one-level lookup,
  **not** the delegation tree.
- **The `Delegation` property-join (`sub_session_id → parent_session_id`) undercounts
  beyond hop 1.** It is more complete than the `HAS_SUBSESSION` walk (it finds a second
  hop), which is exactly why it is dangerous: it *looks* like the answer and reports
  depth 2, but it still misses the deeper lineage that `FORKED`/`parent_id` reveals.
  Use it to enumerate individual delegations, not to measure tree depth.

### Trap 2 — `context_depth` / `context_scope` are inheritance modes, NOT tree depth

On `Delegation` nodes, `context_depth` and `context_scope` describe **how much parent
context a delegation inherited** — an inheritance mode with values like `none`,
`recent`, `all`. The name invites reading it as nesting/tree depth. It is not.
Self-delegations are almost always `none`; regular delegations spread across
`none` / `recent` / `all`.

### Trap 3 — Tool data lives on `ToolCall`, not `ToolExecution`

`:ToolCall` carries the tool data you want: `tool_name`, `tool_input`, `started_at`,
`ended_at`, `result_success`, `result_error`, `result_output`. `:ToolExecution` is
**property-less — a dead end**; do not route tool queries through it. `ToolCall` is the
correct, simple source (verified: `tool_name` and `started_at` populated on 100% of
`ToolCall` nodes in a real workspace).

```cypher
MATCH (t:ToolCall {workspace: $workspace})
RETURN t.tool_name AS tool, count(*) AS n
ORDER BY n DESC LIMIT 12
```

### Trap 4 — `self` is a self-delegation marker, never a real agent

Self-delegation is recorded explicitly as **`Delegation.is_self_delegation = true`**
(equivalently the child `Session.agent = 'self'`). Never infer it by string-matching
`tool_input` or comparing names.

**`'self'` is a marker, not an identity** — it means "this session spawned a fresh
sub-context of *itself*," so `self` is never the real actor. Before any per-agent stat
(centrality, counts, durations), **resolve it up the fork chain** to the nearest ancestor
whose `agent <> 'self'`. That actor is often the **root/main session** (`agent IS NULL` —
the user's top-level session, not a named sub-agent); occasionally a named agent that
self-delegated. Empirically the chain is a single hop (no `self`→`self`→…).

```cypher
// Resolve every self-session to the actor that actually spawned it
MATCH (s:Session {workspace: $workspace, agent: 'self'})
OPTIONAL MATCH pth = (anc:Session)-[:FORKED*1..20]->(s)
WHERE anc.agent IS NULL OR anc.agent <> 'self'
WITH s, anc, pth ORDER BY length(pth) ASC
WITH s, head(collect(coalesce(anc.agent, 'root/main session'))) AS actor
RETURN coalesce(actor, 'unresolved') AS real_actor, count(*) AS self_delegations
ORDER BY self_delegations DESC
```

Self-delegation is itself a **first-class signal** (an actor extending its own context —
continuation / context management): count it *as* self-delegation when that is the
question, and *resolve* it when you need the real actor. A raw `self` bucket in a
per-agent aggregate is a bug — it attributes real work to a non-existent agent.

**Report the resolved breakdown as an explicit finding — do not bury it in a blended
stat.** The resolve-self join above only pays off if its result reaches the answer as
its *own stated fact*. For any agent-level or self-delegation question, **lead with the
resolved `self`→actor split as a standalone sentence** — how many, and what percent, of
self-delegations resolve to the **root/main session continuing itself** (`agent IS NULL`)
versus to **named agents** — *before* any roll-up or combined figure. Computing the
number correctly but folding it into a mixed "X% of all delegations are root-launched"
statistic hides the very fact the question asked for. This is a general output-shape
principle: surface the load-bearing breakdown first, then add supporting context — state
it, don't just compute it.

```cypher
// The plain self-delegation split (the signal itself)
MATCH (d:Delegation {workspace: $workspace})
RETURN d.is_self_delegation AS self_delegation, count(*) AS n
ORDER BY n DESC
```

### Trap 5 — Timestamps are ZONED DATETIME; durations are computed

Every `*_at` property (`started_at`, `ended_at`, `occurred_at`, …) and `last_updated`
— on nodes and on the edges that carry `occurred_at` (`HAS_EVENT`, `HAS_SUBSESSION`,
`FORKED`) — is stored as a native Neo4j **`ZONED DATETIME`**, not a string.

- ❌ `WHERE s.started_at > '2026-05-01'` — comparing to a string literal silently
  returns nothing (no error raised).
- ✅ `WHERE s.started_at > datetime('2026-05-01')` — wrap every literal in `datetime()`.
- ✅ Rolling windows: `WHERE s.started_at > datetime() - duration('P30D')`.

**Durations are not stored — compute them** with `duration.between()`:

```cypher
MATCH (t:ToolCall {workspace: $workspace})
WHERE t.started_at IS NOT NULL AND t.ended_at IS NOT NULL
WITH t, duration.between(t.started_at, t.ended_at) AS d
RETURN t.tool_name AS tool, d.seconds AS secs
ORDER BY secs DESC LIMIT 8
```

---

## Section 4 — Cross-Layer Joins (SOURCED_FROM)

Data layer 1 (raw events) and data layer 2 (semantic entities) coexist in one graph.
The canonical bridge is the **`SOURCED_FROM`** edge: every layer-2 entity points to the
layer-1 event(s) that produced it. Use layer 1 for exact raw fields/timeline; use
layer 2 for structure, scale, and causation; move between them with `SOURCED_FROM`.

```cypher
// Semantic ToolCall → its originating raw event (canonical)
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
MATCH (tc)-[:SOURCED_FROM]->(pre:ToolPreEvent)
RETURN iter.iteration_number AS iteration,
       tc.tool_name          AS tool,
       tc.result_success     AS succeeded,
       pre.occurred_at       AS event_fired_at
ORDER BY pre.occurred_at
LIMIT 25
```

**Fallback joins** (only when `SOURCED_FROM` is absent — older sessions ingested before
the handler existed; check with the query in Gotcha 5):

- **ToolCall direct match** — `:ToolCall.node_id` is the provider's `tool_call_id`,
  which `:ToolPreEvent` also lifts as `tool_call_id`. Join on that shared key. Works
  for `ToolCall` only.

  ```cypher
  MATCH (e:ToolPreEvent {workspace: $workspace, tool_call_id: $tool_call_id})
  MATCH (tc:ToolCall {node_id: e.tool_call_id})
  RETURN e.tool_name, e.occurred_at, tc.result_success, tc.result_output
  ```

- **Session containment** — join through the shared `:Session`: layer 1 attaches raw
  events via `HAS_EVENT`; layer 2 attaches entities via `HAS_EXECUTION`/`HAS_PART`.
  Correlate on a shared scalar (e.g. `iteration_number`).

---

## Section 5 — SST Navigation (Reasoning by Semantic Type)

Layer-2 nodes carry an **SST type label** that classifies each node by its fundamental
character, letting you query across entity boundaries without knowing specific labels
in advance. Confirm the current partition with `db.labels()`; the meaning is:

- **`:SST_EVENT`** — a temporal, bounded occurrence (Session, OrchestratorRun,
  Iteration, ContentBlock, ToolCall, Prompt, Delegation, SkillLoad, RecipeRun, …).
- **`:SST_THING`** — a persistent resource/artifact (e.g. MountPlan).
- **`:SST_CONCEPT`** — an abstract, reusable identity (Orchestrator, Agent, Recipe).

Layer-2 edges carry an **`sst_semantic`** property expressing the abstract relationship
independent of concrete edge type: `CONTAINS` (containment), `LEADS_TO` (causal/
sequential), `EXPRESSES` (attribution), `NEAR` (concurrency). This lets you query
causation or containment uniformly.

```cypher
// All temporal events in the most recent session, mixed types sorted correctly
MATCH (s:Session {workspace: $workspace})
WITH s ORDER BY s.started_at DESC LIMIT 1
MATCH (s)-[:HAS_EXECUTION|HAS_PART*1..3]->(e:SST_EVENT)
RETURN labels(e) AS types,
       coalesce(e.started_at, e.occurred_at) AS at
ORDER BY at
LIMIT 50
```

> **Two temporal keys across `:SST_EVENT`:** span nodes (Iteration, ContentBlock,
> ToolCall, Session, OrchestratorRun) store `started_at`; occurrence nodes (Prompt,
> Cancellation, ContextCompaction) store `occurred_at`. When you sweep a mixed set,
> use `coalesce(e.started_at, e.occurred_at)` in both projection and `ORDER BY` so
> occurrence nodes are not silently null and sorted out of order.

**Hierarchy traversal** — reach the full Session → Run → Iteration → ContentBlock tree
with a bounded variable-length path. Bound it (`*1..3`) to prevent runaway fanout:

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION|HAS_PART*1..3]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN tc.tool_name, tc.started_at, tc.result_success
ORDER BY tc.started_at
LIMIT 50
```

---

## Section 6 — Text Extraction: Bounded Always, Blobs Carefully

Large text fields are the fastest way to destroy your context window. **One unbounded
`p.prompt` query returned 1.21M tokens and overflowed the context.** Treat every large
text field with discipline.

### Firm rule — never return full text of many rows

For any large text field (`prompt`, `data`, `data.content`, `tool_input`,
`result_output`), you MUST bound it — no exceptions:

- Add a small `LIMIT`, **and**
- Truncate with `substring(field, 0, 200)`, or return only `size(field)` to gauge
  scale first, or aggregate (`count`, `collect` of a truncated projection).

```cypher
// WRONG — full prompt text over many rows → context overflow
MATCH (p:Prompt {workspace: $workspace}) RETURN p.prompt

// RIGHT — measure scale first
MATCH (p:Prompt {workspace: $workspace})
RETURN count(p) AS n, max(size(p.prompt)) AS longest_chars

// RIGHT — bounded preview when you do need text
MATCH (p:Prompt {workspace: $workspace})
WHERE p.session_id = $session_id
RETURN substring(p.prompt, 0, 200) AS prompt_preview
ORDER BY p.occurred_at
LIMIT 25
```

### The `data` field is a JSON string, not a Cypher map

`:Event.data` is a serialized JSON string. Dot notation (`e.data.tool_name`) does **not**
work in Cypher — it raises a type-mismatch error, not a silent null. Prefer **lifted
properties** (`tool_name`, `tool_call_id`, `model`, `provider`, …) when they exist; when you
need a raw field that is **only** in the payload, parse it **in-graph with APOC** — see the
one authoritative pattern **"Lift payload fields in-graph with APOC" in Section 8**. Always bounded.

**Inline vs blob — know which you have.** Some payloads live inline; some are offloaded.
For example, orchestrator steering text lives **inline** at
`OrchestratorSteeringInjectedEvent.data.content` (a JSON string — parse it, no blob
hop). Do not assume a `ci-blob://` indirection where the text is actually inline.

### `ci-blob://` references and the `/blobs` endpoint

When a payload exceeds the storage threshold, the server replaces `data` with a
`ci-blob://SESSION_ID/EVENT_KEY` URI. Resolve it via the blob store — the `/blobs`
endpoint (`GET {url}/blobs/{session_id}[/{key}]`), or the `blob_read` tool which
returns a local **file path**, not content:

```python
result = blob_read(uri="ci-blob://SESSION_ID/EVENT_KEY")  # -> {"file_path": "..."}
```

Then extract only the fields you need with `jq` — never load a whole blob into context:

```bash
jq 'keys' /tmp/ci-blobs/SESSION_ID/EVENT_KEY.json          # structure first
jq '.messages[-1].content' /tmp/ci-blobs/SESSION_ID/EVENT_KEY.json
```

**Rules:** check for a `ci-blob://` prefix before parsing as JSON; lifted properties
bypass blobs entirely; always bound before loading.

---

## Section 7 — Verified Query Patterns

These are validated against the live layer-2/foundation schema. All reference
`$workspace` (set via the tool's `workspace` argument, Section 2) and are bounded.

**Full conversation turn trace** — prompt → run → iterations → tool calls:

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_PART]->(p:Prompt)
      -[:TRIGGERS]->(run:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN p.occurred_at AS turn_start, iter.iteration_number AS iteration,
       tc.tool_name AS tool, tc.result_success AS succeeded
ORDER BY p.occurred_at, iter.iteration_number, tc.started_at
LIMIT 100
```

**Tool usage per iteration** — how many tools each LLM round fired:

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN iter.iteration_number AS iteration,
       collect(tc.tool_name) AS tools, count(tc) AS tool_count
ORDER BY iter.iteration_number
LIMIT 50
```

**Failed tool calls** — scope to one session; add `ORDER BY tc.started_at DESC` for
most-recent-first:

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)
      -[:HAS_PART]->(iter:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)
WHERE tc.result_success = false
RETURN iter.iteration_number AS iteration, tc.tool_name AS tool,
       tc.result_error AS error, tc.started_at AS failed_at
ORDER BY tc.started_at
LIMIT 50
```

**Delegations in a session** — which tool call triggered which agent (enumeration, not
depth — see Trap 1 for lineage/depth):

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(:Iteration)
      -[:HAS_TOOL_CALL]->(tc:ToolCall)-[:TRIGGERED]->(d:Delegation)
RETURN d.agent, d.sub_session_id, d.is_self_delegation, d.context_depth,
       d.started_at, tc.tool_name AS via_tool
ORDER BY d.started_at
LIMIT 50
```

**Skills active per iteration** — note skills loaded before the first request attach to
`Session`, not `Iteration` (Gotcha 6):

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(iter:Iteration)
      -[:HAS_SKILL_LOAD]->(sl:SkillLoad)
RETURN iter.iteration_number, sl.skill_name, sl.content_length, sl.started_at
ORDER BY iter.iteration_number, sl.started_at
LIMIT 100
```

**Recipe run trace:**

```cypher
MATCH (s:Session {workspace: $workspace, node_id: $session_id})
      -[:HAS_RECIPE_RUN]->(rr:RecipeRun)-[:HAS_STEP]->(step:RecipeStep)
OPTIONAL MATCH (step)-[:TRIGGERED]->(target)
RETURN rr.name, rr.status, step.name,
       labels(target) AS triggered_type, target.node_id AS triggered_id
ORDER BY step.started_at
LIMIT 50
```

### Size discipline (the cardinal rule)

The graph can hold thousands of sessions. **Every traversal query MUST have a `LIMIT`.**
Work as a funnel:

1. **Count first.** `RETURN count(...)` costs almost nothing — run it before any wide
   query over an unknown population.
2. **Anchor on a session** (`node_id: $session_id` or `WHERE s.node_id IN [...]`)
   before traversing; an un-anchored `MATCH (s:Session {workspace: $workspace})` spans
   every session and multiplies rows.
3. **Filter and aggregate in Cypher**, not in the client — every unneeded row is wasted
   context.
4. **Bound variable-length paths** (`*1..3`), never bare `*`.
5. **Paginate** with `ORDER BY <stable key> SKIP n LIMIT m` when you genuinely need more
   than one page.

---

## Section 8 — Push Work to the Database (APOC / GDS)

**Prefer one server-side computation over many client round-trips.** When an analysis
needs a deep or variable-length traversal, path-finding, centrality/influence, community
structure, or a multi-step aggregation that would otherwise mean pulling rows to the
client and iterating, do the work **inside the graph**. It is faster and keeps the
protocol un-chatty — fewer tool calls, less context churn. Both plugins are installed and
**run on the live target** (Neo4j Community here — GDS works). Rule of thumb: **if
answering the question the naive way means many round-trips or a large client-side pull,
push that computation into the database.** Probe once, compute server-side, and still
**bound the RETURNED result** (count / aggregate / `LIMIT`).

Probe availability: `CALL apoc.help('path')`, `RETURN gds.version()` (or `CALL gds.list()`).

- **APOC — in-graph helpers.** `apoc.convert.fromJsonMap(e.data)` parses a `data` JSON
  string into a map without a client `jq` hop. `apoc.path.expandConfig(...)` runs
  configurable in-graph traversals — subject to the **same 1-hop caveat as
  `HAS_SUBSESSION`** (Trap 1): for delegation depth, expand over `FORKED`/`parent_id`.
  Still bound your output.
- **GDS — real graph analytics in one pass.** For centrality/influence, community/cluster
  detection, or pathfinding **across many sessions**, project once and run the algorithm
  server-side instead of reconstructing the graph client-side. Always `gds.graph.drop`
  the projection when done:

  ```cypher
  CALL gds.graph.project('g', 'Session', 'FORKED')
  YIELD nodeCount, relationshipCount;

  CALL gds.pageRank.stream('g')          // or gds.degree / gds.betweenness (influence)
  YIELD nodeId, score
  RETURN gds.util.asNode(nodeId).node_id AS session, score
  ORDER BY score DESC LIMIT 20;

  CALL gds.wcc.stream('g')               // or gds.louvain (community structure)
  YIELD componentId
  RETURN componentId, count(*) AS size ORDER BY size DESC LIMIT 20;

  CALL gds.graph.drop('g') YIELD graphName;   // always release the projection
  ```

  **Agent-level, not session-level — agents are a PROPERTY, not a projectable graph.**
  "Which *agent* is a hub" ≠ "which *session* is a hub": one session can be highly central
  while its agent is not. There is **no `Agent` node to project** — do NOT
  `gds.graph.project.cypher(...)` an agent-level graph; it returns an empty projection and
  is a dead end. Instead **project the labels that exist** — `Session` (nodes) and `FORKED`
  (relationships) — run the algorithm over *sessions*, then **roll the per-session scores up
  by the `Session.agent` property in the `RETURN`**. For the named-agent-hub ranking, drop
  the `self` marker and the root/main session (`agent IS NULL`) — a "which named agent"
  question is about named agents:

  ```cypher
  // 'g' is the Session / FORKED projection from above — agents are NOT projected;
  // agent-level results come from aggregating session scores by the s.agent PROPERTY.
  CALL gds.pageRank.stream('g') YIELD nodeId, score
  WITH gds.util.asNode(nodeId) AS s, score
  WHERE s.agent IS NOT NULL AND s.agent <> 'self'   // named agents only (see Trap 4 for 'self')
  RETURN s.agent AS agent, round(sum(score), 2) AS agent_influence, count(*) AS sessions
  ORDER BY agent_influence DESC LIMIT 20
  ```

  Excluding `self` here answers "which *named* agent is a hub" — it does **not** make `self`
  vanish from the story. If the question also touches self-delegation, resolve `self` up the
  `FORKED` chain (Trap 4) and **state that root/main-vs-named breakdown as its own finding**;
  don't let the exclusion above silently drop it.

  Skip GDS for a simple count or grouping that one plain Cypher statement already does —
  reach for it when the naive alternative is iterative client-side fetching.

---

### Lift payload fields in-graph with APOC (the one authoritative pattern)

**Problem.** `Event.data` (and nested payloads) is a serialized JSON **string**, not a Cypher
map. Dot-access like `e.data.working_dir` **fails loudly** — Neo4j raises:

```
Neo.ClientError.Statement.TypeError: Type mismatch: expected a map but was String(...)
```

You **must** parse the string before reading a field. (Do **not** confuse this with the
silent-null trap in **Trap 5** — that one is comparing a *zoned datetime to a string literal*,
which returns `null` with no error. This APOC case is a *loud* error, a different failure mode.)

**Pattern — parse in-graph, project only the scalar you need:**

```cypher
WITH apoc.convert.fromJsonMap(e.data) AS d
RETURN d.working_dir AS working_dir      // one scalar, not the whole payload
```

Use `apoc.convert.fromJsonList(...)` for arrays. **Never** pull the full `data` string to the
client to parse with `jq`.

**Two wins:**
- **Correctness** — dot-access on the string errors out; parsing with APOC is the *only* way to
  read the real value.
- **Leanness** — the parse **and** the field-selection happen server-side; the payload never
  crosses the wire or enters context — same discipline as Section 6 ("never return full text").

**Rule.** Prefer lifted first-class properties when they exist; when a field is **only** in the
payload, lift it in-graph with APOC and return just the scalar. (`working_dir` is the worked
example here — proven, and the whole point of the pattern — but per Section 2, it is **not**
a scoping lever: it is sparsely populated (~7% of sessions) with no canonical per-session
source, so use it to read one session's directory, never to scope a population.)

---

## Gotchas

1. **Layer-2 / foundation nodes only exist if handlers ran, or the feature was used.**
   Sessions ingested before a handler was deployed, or with no delegation/skills/recipes,
   simply lack those nodes. Use `OPTIONAL MATCH` when joining them against arbitrary
   sessions.

2. **`result_success = false` is the error path;** `result_error` holds the message. A
   null `result_success` means the post/error event hasn't been processed — the call is
   in-flight or its handler didn't run.

3. **`ENABLES` edges are sparse.** The `OrchestratorRun → next Prompt` edge exists only
   for multi-turn chains (N prompts ⇒ N−1 edges). Do not use its presence to judge clean
   session termination.

4. **Concept nodes (`Agent`, `Recipe`, `Orchestrator`) are shared across sessions,**
   merged by name. Querying `(a:Agent)` without a session anchor spans everything — reach
   them through the session (`HAS_AGENT` from the sub-session, `HAS_RECIPE` from a
   `RecipeRun`).

5. **`SOURCED_FROM` may be absent on older sessions.** Detect and fall back to the
   Section 4 fallback joins:

   ```cypher
   MATCH (n:SST_EVENT) WHERE NOT (n)-[:SOURCED_FROM]->() AND NOT n:Session
   RETURN labels(n), count(*)
   ```

6. **`SkillLoad` may attach to `Session`, not `Iteration`.** Skills loaded before the
   first request have no active iteration. Add
   `OPTIONAL MATCH (s)-[:HAS_SKILL_LOAD]->(sl:SkillLoad)` to catch session-level loads.

7. **`IncompleteSession` is a health marker, not a terminal label.** It reached
   `session:end` with no captured `session:start`/`fork`, carries `has_terminal: false`,
   and none of `:RootSession`/`:SubSession`/`:ForkedSession`. Exclude it from normal
   terminal-session queries with `WHERE NOT s:IncompleteSession`. A spike in its count
   signals upstream event loss.

8. **The node MERGE key is `{node_id, workspace}`.** The same logical entity (e.g. an
   Orchestrator named `loop-streaming`) exists as a separate node per workspace. A
   cross-workspace query returns one node per workspace — account for this when
   aggregating.
