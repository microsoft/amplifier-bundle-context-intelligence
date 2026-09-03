---
name: context-intelligence-graph-query
description: >
  Use when querying the context-intelligence property graph for session history,
  tool call traces, LLM iteration analysis, execution scale metrics, agent
  delegation trees, skill loading, and recipe orchestration. Covers all graph
  layers, cross-layer SOURCED_FROM joins, SST navigation, blob handling, and
  verified Cypher patterns.
license: MIT
visibility:
  priority: 5
  summary: Query the context-intelligence property graph for session history, tool traces, delegation trees, skills, and recipe orchestration via verified Cypher patterns.
metadata:
  version: "2.5.0"
  changelog:
    - "2.5.0: Elevated the anti-multi-count guidance scattered across Trap 6 (cost) and Trap 8
      (tokens) into a single named cross-cutting rule (Section 3: \"Rule \u2014 one owner per numeric
      fact: never multi-count cost or tokens by over-reading events\") \u2014 every usage value is
      emitted once per LLM turn, owned by :LlmResponseEvent, and echoed onto ContentBlockEndEvent /
      ProviderResponseEvent; aggregating across the wider set sums it 2\u20133x over. Trimmed Trap 6
      and Trap 8 to reference the rule instead of each restating the mechanism; both traps keep
      only their field-specific specifics (cost_usd is a STRING requiring toFloat; token/cache
      fields are INTEGER with the correct cache field names). Section 8 queries unchanged."
    - "2.4.0: Added Trap 8 (token & cache usage fields on :LlmResponseEvent are payload INTEGERs — unlike cost_usd's STRING — but still require pinning to :LlmResponseEvent; an unpinned :Event sweep replays the same usage map via ContentBlockEndEvent/ProviderResponseEvent and inflates every total by ~3.4x, measured: cost 3.43x, input 3.31x, output 3.63x, cache 3.39x) with a validated combined cost+token aggregation and a per-model breakdown pattern in Section 8. Documented the correct cache field names (cache_read_input_tokens, cache_creation_input_tokens — complete) versus the sparse/wrong-typed alternatives (cache_read_tokens, cache_creation), and reinforced that Iteration.usage_input/usage_output/usage_cache_write (Trap 7) must never be used for token or cost aggregation — they are last-write-wins corrupted and Iteration carries no cost field at all."
    - "2.3.0: Corrected the working_dir population figure from an inaccurate ~7% (238/3,259 sessions) down to its true measured value: ~0% queryable in-graph (0 of 4,504 / 0 of 9,086 sessions — not a lifted Session property); workspace remains ~100% and the only valid scoping lever. Added Trap 6 (cost_usd totals: a JSON-string payload field, not a lifted property; a bare sum() returns HTTP 500; must pin to :LlmResponseEvent to avoid triple-counting against ContentBlockEndEvent / ProviderResponseEvent) with a validated APOC aggregation pattern in Section 8. Added Trap 7 (Iteration.node_id MERGEs across orchestrator runs, corrupting iteration-scoped counts/aggregates by up to +66%) with caveats on every affected iteration-based query pattern. Added a validated temporal-join pattern (Section 7) for per-prompt/per-run tool-call attribution — more accurate than the structural run→Iteration→ToolCall path and unaffected by the Trap 7 MERGE bug."
    - "2.2.0: Corrected the Section 2 workspace/working_dir claims — workspace is a lifted, reliable (~100%) scoping slug, not the literal working directory; working_dir is ~0% queryable in-graph (0 of 4,504 / 0 of 9,086 sessions — not a lifted Session property) payload-only field with no canonical per-session source, and must never be used to scope a population. Consolidated one visible+correct in-graph APOC payload-parse pattern: dot-access on a JSON-string field (e.g. e.data.working_dir) raises a type error, not a silent null — parse in-graph with apoc.convert.fromJsonMap and return only the scalar needed. De-duplicated the Section 6 copy into a pointer at this pattern."
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

### Reading results & citing the source (required)

`graph_query` returns `{"source": {"name", "url", "origin"}, "rows": [...]}` on
success. Read your data from `rows`. ALWAYS report `source.name` (its `origin` is
`source`, `destination`, or `env`) in your answer — the user must know which
endpoint the outcome came from.

On **failure**, `source` is present only when an endpoint was actually chosen:
**endpoint-level** errors (`connection_error`, `timeout`, `http_status`,
`decode_error`, and input-validation errors like a missing/invalid `query` that
occur *after* selection) carry `error.source` — cite it. **Selection/config**
errors that occur *before* any endpoint is chosen (`ambiguous_source_selection`,
`unknown_source`, `source_misconfigured`, `configuration_error`) have **no**
`source` — there is no single endpoint to name, so report that selection failed
rather than inventing one.

To discover what you can reach, call `graph_query` with `list_sources: true` — it
returns the connectable set (`{"connectable_set": [{name, url, origin}, ...]}`)
without running a query. To target a specific endpoint (a configured read source
OR a hook upload destination), pass `source: "<name>"`.

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
  slug; treat `working_dir` as a **payload field, ~0% queryable in-graph (0 of 4,504 / 0 of
  9,086 sessions — not a lifted Session property)** with no canonical per-session source — read
  it via the APOC parse in Section 8, but do **not** scope by it. The `/cypher` request's own
  `workspace` scope is a second, orthogonal server-side filter layered on top.

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

### Resolving "my" — use `whoami`, don't guess

`created_by` scopes by *who produced the data*, but the graph never tells you
who "you" are. When a request is scoped to the acting user themselves —
"my sessions", "sessions I created", "what have I been working on" — call the
`whoami` tool for the relevant server before filtering on `created_by`:

```python
whoami()                       # default endpoint
whoami(source="prod")          # a specific configured endpoint
```

It returns `{"contributor_id": "<github-id-or-null>", "source": {...}}`. Use
`contributor_id` as the value to match against `created_by`:

```cypher
MATCH (s:Session {workspace: $workspace, created_by: $contributor_id})
RETURN count(s) AS my_sessions
```

**Null case.** A null `contributor_id` means auth is disabled (or otherwise
unknown) on that server — there is no acting-user identity to filter by. Do
**not** guess or fall back to some other field. Ask the user how they want to
scope the query instead (e.g. by name, by workspace, or "show everyone's").

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
// Authoritative lineage depth via the FORKED edge.
// Bound the path (never bare `*` — Section 7): *1..20 is generous headroom over the
// observed max depth of 3, and the RETURN is aggregate-only (no rows materialized).
MATCH path = (root:Session {workspace: $workspace})-[:FORKED*1..20]->(leaf:Session)
WHERE root.parent_id IS NULL
RETURN max(length(path)) AS max_fork_depth, count(path) AS lineages
```

`Session.parent_id` lets you reconstruct the same chain by property when an edge walk
is inconvenient (each child names its parent):

```cypher
MATCH (child:Session {workspace: $workspace})
WHERE child.parent_id IS NOT NULL
RETURN child.node_id, child.parent_id
ORDER BY child.node_id LIMIT 200   // bounded (Section 7); anchor on a node_id to walk one specific chain
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

### Rule — one owner per numeric fact: never multi-count cost or tokens by over-reading events

This is a cross-cutting rule, not a single trap — Traps 6 and 8 below are both instances of it.

Every numeric usage value — dollars (`cost_usd`) and every token field (`input_tokens`,
`output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`) — is emitted **once**
per LLM turn and is **owned** by the `:LlmResponseEvent` node. The identical `usage` map is then
**echoed** onto other events of the same turn: `ContentBlockEndEvent` (streaming replays it ~2.33x
per response) and `ProviderResponseEvent` (a third, lower-layer copy).

**Consequence:** any aggregation that reads a set *wider* than the owning label — an unpinned
`:Event` / `:SST_EVENT` sweep, or a cross-layer join through `ContentBlockEndEvent` /
`ProviderResponseEvent` — sums the same dollars and tokens 2–3x over. This is **over-reading the
events**, not a rounding error. Measured inflation ~3.4x on the production graph (a true ~$45.5K
reads as a phantom ~$156K); reproduced on an isolated DTU at 2.1x–3.9x across fields.

**The rule, stated generally:** a numeric fact has exactly **one owning event layer** — aggregate
it only from that layer. For LLM cost and tokens, the owner is `:LlmResponseEvent`. Concretely:
always `MATCH (e:LlmResponseEvent)`; never `sum()` a usage field across a broader label set, and
never reach the same figure through a cross-layer join. If a numeric value appears on more than one
label, those are **copies, not addends** — pick the owner. This applies identically to dollars and
tokens, and to any future numeric usage field added to the payload.

### Trap 6 — `cost_usd` is a JSON string nested in the payload, not a lifted property

An instance of the **Rule** above — for cost, the owner is `:LlmResponseEvent`. The trap-specific
wrinkle here is typing, not scoping:

There is **no top-level lifted cost property** anywhere in the graph. The real value lives at
`data.usage.cost_usd` on `:LlmResponseEvent`, serialized as a **JSON string** — not a number, and
not a top-level field. A bare `sum(e.cost_usd)` or `sum(e.data.usage.cost_usd)` **returns HTTP
500** (Section 6: `data` is a string, dot-access into it type-errors; the nested numeric value also
arrives as a JSON string, not a Cypher number, so it must be parsed *and* cast with `toFloat()`).

**Validated** (returns `$44,886.32` over 63,758 events):

```cypher
MATCH (e:LlmResponseEvent) WHERE e.data CONTAINS 'cost_usd'
WITH apoc.convert.fromJsonMap(e.data) AS d
RETURN sum(toFloat(d.usage.cost_usd)) AS total_cost_usd
```

See Section 8 for the full worked pattern.

### Trap 7 — `Iteration.node_id` MERGEs across orchestrator runs

`Iteration.node_id` is composed as `"{session_id}::iteration::{N}"` — but `N` (`iteration_number`)
**restarts at 1 for every `OrchestratorRun`**, not once per session. Because the MERGE key omits
the run, **one `Iteration` node absorbs iterations from every run that reused the same number** —
observed up to **15 run parents on a single `Iteration` node**.

This corrupts anything that aggregates through `Iteration`:

- **Last-write-wins property garbage** on `usage_input`, `usage_output`, `usage_cache_write`, and
  `message_count` — whichever run's ingest landed last overwrites the others' values.
- **`count(DISTINCT Iteration)` undercounts** — iterations from different runs that share a number
  collapse into one node.
- **`run → Iteration → ToolCall` traversals overcount tool calls by +1.1% to +66%** — a `ToolCall`
  reached through the merged `Iteration` looks like it belongs to every run that touches that node.

**Every query pattern below that groups or counts through `Iteration` inherits this bug** — see the
caveat noted at each: Section 4's `SOURCED_FROM` example, and Section 7's "Full conversation turn
trace," "Tool usage per iteration," "Failed tool calls," and "Skills active per iteration."

**What to do instead:**
1. For **tool-call attribution** to a prompt or run, use the **temporal join** pattern in Section 7
   — it never touches `Iteration` and is unaffected by this bug.
2. For **token or cost counts**, never trust `Iteration.usage_*` / `message_count` — recompute
   directly from `LlmResponseEvent` payloads using the validated Trap 8 / Section 8 aggregation
   pattern (`Iteration` carries no cost field at all, and its token counters are last-write-wins
   corrupted).

**Trust marker:** a bare `node_id` of the form `"{session_id}::iteration::{N}"` with no
orchestrator-run segment is **pre-fix data — suspect**. A `node_id` that additionally scopes to
the run (i.e. the run is part of the key, not just `N`) is **post-fix — trustworthy**. Check the
literal `node_id` shape before trusting an `Iteration` aggregate.

### Trap 8 — Token & cache usage: payload integers, still owned by `:LlmResponseEvent`

An instance of the **Rule** above (before Trap 6) — for tokens, the owner is also
`:LlmResponseEvent`, exactly as for cost. Token and cache-usage figures live at
`data.usage.{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`
on `:LlmResponseEvent`, parsed the same way as cost (Trap 6) via `apoc.convert.fromJsonMap(e.data)`.
The trap-specific wrinkle is the opposite typing quirk:

- **Typing is the opposite of `cost_usd`.** The token fields deserialize as **INTEGER**, not a
  JSON string — `toInteger()` on them is defensive/optional, not required. `cost_usd` (Trap 6) is
  the **STRING** one; `toFloat()` there is mandatory. Don't let the cost pattern's mandatory cast
  make you assume tokens need the same cast for the same reason — they don't need it to avoid an
  error, only to normalize type. Correct typing does **not** protect you from wrong scoping —
  validated inflation when the Rule is violated: cost 3.43x, input tokens 3.31x, output tokens
  3.63x, cache tokens 3.39x.
- **Use the complete cache fields, not the sparse/wrong-typed ones.** `cache_read_input_tokens` and
  `cache_creation_input_tokens` are complete. Do **not** sum `cache_read_tokens` (sparse — NULL on
  many events) or `cache_creation` (a **MAP**, not a number — summing it type-errors or silently
  drops). `cache_write_tokens` is close to `cache_creation_input_tokens` but diverges ~1.5%; prefer
  `cache_creation_input_tokens` as the validated figure.
- **Never aggregate from `Iteration`.** `Iteration.usage_input` / `usage_output` /
  `usage_cache_write` are the same Trap 7 last-write-wins corruption (undercounts 10–14%), and
  `Iteration` carries **no cost field at all** — there is no `Iteration`-based cost figure to fall
  back to even approximately. Recompute both tokens and cost from `LlmResponseEvent` payloads.
- **NULL and zero-cost caveat.** ~4% of events have a null `cost_usd` (`sum()` ignores nulls, so the
  total stays correct — just don't expect `count(*)` to equal the events actually priced). Some
  models/providers are non-priced and report `$0` cost with real, non-zero token counts. Always
  report `llm_calls` and the token columns alongside cost so this gap is visible rather than hidden
  inside a dollar figure.

See Section 8 for the validated combined cost+token aggregation and per-model breakdown.

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

> **Caveat (Trap 7):** `iter.iteration_number` here can be a cross-run merged node (up to 15 run
> parents observed) — do not treat this join as an authoritative per-run tool-call count. For
> per-prompt/per-run attribution, prefer the temporal join pattern in Section 7.

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
result = blob_read(uri="ci-blob://SESSION_ID/EVENT_KEY")  # -> {"path": "...", "source": {name, url, origin}}
```

Then extract only the fields you need with `jq` — never load a whole blob into
context. `$BLOB_PATH` below is the `path` value returned by `blob_read`; use it
verbatim rather than constructing a path, since the blob store lives under the OS
temp directory and differs by platform:

```bash
jq 'keys' "$BLOB_PATH"                    # structure first
jq '.messages[-1].content' "$BLOB_PATH"
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

> **Caveat (Trap 7):** `iter.iteration_number` here can be a merged node spanning multiple
> orchestrator runs — do not trust it as a pure per-run counter. For attributing tool calls to a
> specific prompt/run, prefer the temporal join pattern below.

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

> **Caveat (Trap 7):** counts and groupings by `iter.iteration_number` above inherit the
> `Iteration`-MERGE bug (one node can span up to 15 runs) — treat this as an approximate, not
> authoritative, per-iteration tool count.

**Per-prompt (or per-run) tool-call attribution — temporal join, no edges (preferred over the
`Iteration`-based counts above; see Trap 7):**

There is no direct edge from `ToolCall` to `Prompt` or `OrchestratorRun`. The reliable way to
attribute a tool call to the prompt (or run) that was active when it fired is a **temporal join**
on `tc.started_at` — this is *more* accurate than the structural `run → Iteration → ToolCall`
path above, which inflates counts by +4–20% due to the Trap 7 `Iteration`-MERGE bug and silently
drops prompts:

```cypher
// Per-prompt tool-call attribution — temporal, no new edges.
// Swap (p:Prompt)/p.occurred_at for (p:OrchestratorRun)/p.started_at for per-run.
MATCH (tc:ToolCall {session_id:$sid}) WHERE tc.started_at IS NOT NULL
OPTIONAL MATCH (p:Prompt {session_id:$sid}) WHERE p.occurred_at <= tc.started_at
WITH tc, p ORDER BY p.occurred_at DESC
WITH tc, head(collect(p)) AS last_prompt
RETURN coalesce(last_prompt.node_id,'<orphan: no preceding prompt>') AS prompt_id,
       count(tc) AS tool_calls
ORDER BY prompt_id
```

**Caveats:**
- Use `tc.started_at` directly — it equals `ToolPreEvent.occurred_at` on 100% of rows. Do **not**
  join via `SOURCED_FROM` for this: it fans out to *both* `tool:pre` and `tool:post` events for
  the same call and double-counts.
- Report an explicit **orphan bucket** (`<orphan: no preceding prompt>`) rather than dropping
  those rows — ~0.2% of tool calls occur in sub-agent sessions with no `Prompt` node, and a few
  precede the session's first prompt.
- Filter the ~0.007% of `ToolCall` rows with a null `started_at` (the `WHERE` above already does
  this).
- This is a single-writer timeline — no clock skew, no tied timestamps observed in validation.

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

> **Caveat (Trap 7):** `iter.iteration_number` may be a cross-run merged node — a failed call
> attributed to "iteration 3" may actually belong to iteration 3 of a *different* run. Cross-check
> against the temporal join pattern above when per-run precision matters.

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

> **Caveat (Trap 7):** `iter.iteration_number` can be a cross-run merged node — a skill load shown
> under "iteration 2" may actually have loaded during a different run's iteration 2.

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

  **Agent-level, not session-level — agents are a PROPERTY to roll up by, not a projectable graph.**
  "Which *agent* is a hub" ≠ "which *session* is a hub": one session can be highly central
  while its agent is not. `Agent` concept nodes *do* exist (Section 1; Gotcha 4), but they
  carry only inbound `HAS_AGENT` edges from sessions — there is **no Agent→Agent edge
  structure to project**, so GDS centrality has nothing to traverse at the agent level. Do NOT
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
a scoping lever: it is ~0% queryable in-graph (0 of 4,504 / 0 of 9,086 sessions — not a lifted
Session property) with no canonical per-session source, so use it to read one session's
directory, never to scope a population.)

### Aggregate a numeric JSON-string payload field: total cost (Trap 6)

The same parse-in-graph idiom extends past reading a scalar to **aggregating** a numeric value
buried inside a JSON string. `cost_usd` lives at `data.usage.cost_usd` on `:LlmResponseEvent`,
itself a string inside the parsed map — cast it with `toFloat()` before summing, and pin the
label to avoid the triple-count described in Trap 6:

```cypher
MATCH (e:LlmResponseEvent) WHERE e.data CONTAINS 'cost_usd'
WITH apoc.convert.fromJsonMap(e.data) AS d
RETURN sum(toFloat(d.usage.cost_usd)) AS total_cost_usd
```

**Rules:**
- Pre-filter with `WHERE e.data CONTAINS 'cost_usd'` before parsing — cheaper than parsing every
  `LlmResponseEvent` just to find the field absent.
- Pin the label to `:LlmResponseEvent` — do not sweep `:SST_EVENT` or join through
  `ContentBlockEndEvent` / `ProviderResponseEvent` for the same figure (Trap 6).
- Always `toFloat()` the nested value — it deserializes from JSON as a string, and a bare
  `sum()` over it returns HTTP 500, not a wrong number.

### Aggregate token + cost usage from LlmResponseEvent payloads (Trap 8)

Token and cache-usage fields sit alongside `cost_usd` in the same `data.usage` map, but they
deserialize as **INTEGER**, not a JSON string — `toInteger()` is defensive here, not required the
way `toFloat()` is for `cost_usd`. The scoping danger is identical to Trap 6: an unpinned `:Event`
sweep also matches `ContentBlockEndEvent` and `ProviderResponseEvent`, each replaying the same
response-level `usage` map, and inflates every total by **~3.4x** regardless of field type.

**Validated combined cost + tokens (canonical — aggregate == independent reduce == hand-summed
subset):**

```cypher
MATCH (e:LlmResponseEvent)
WHERE e.data CONTAINS 'input_tokens'
WITH apoc.convert.fromJsonMap(e.data) AS d
RETURN count(*)                                            AS llm_calls,
       sum(toFloat(d.usage.cost_usd))                      AS total_cost_usd,
       sum(toInteger(d.usage.input_tokens))                AS input_tokens,
       sum(toInteger(d.usage.output_tokens))               AS output_tokens,
       sum(toInteger(d.usage.cache_read_input_tokens))     AS cache_read_tokens,
       sum(toInteger(d.usage.cache_creation_input_tokens)) AS cache_creation_tokens
```

**Per-model breakdown:**

```cypher
MATCH (e:LlmResponseEvent)
WHERE e.data CONTAINS 'input_tokens'
WITH apoc.convert.fromJsonMap(e.data) AS d
RETURN d.model AS model, d.provider AS provider, count(*) AS calls,
       round(sum(toFloat(d.usage.cost_usd)), 2)            AS cost_usd,
       sum(toInteger(d.usage.input_tokens))                AS input_tokens,
       sum(toInteger(d.usage.output_tokens))                AS output_tokens,
       sum(toInteger(d.usage.cache_read_input_tokens))     AS cache_read,
       sum(toInteger(d.usage.cache_creation_input_tokens)) AS cache_creation
ORDER BY cost_usd DESC LIMIT 15
```

Add `{workspace: $workspace}` to the `MATCH (e:LlmResponseEvent ...)` anchor for a per-workspace
variant of either query.

**Validated global scale (real corpus snapshot, reader's sanity check):** ~68k LLM calls → ~$45.5K
cost, input ~4.50B tokens, output ~80.6M tokens, cache_read ~4.62B tokens, cache_creation ~8.05B
tokens. The same query with the `:LlmResponseEvent` pin removed reports ~$156K (~3.4x) — wrong,
for the exact reason given in Trap 8.

**Rules:**
- Use `cache_read_input_tokens` and `cache_creation_input_tokens` — both complete. Do **not** sum
  `cache_read_tokens` (sparse/NULL on many events) or `cache_creation` (a MAP, not a number).
  `cache_write_tokens` is close to `cache_creation_input_tokens` but diverges ~1.5% — prefer
  `cache_creation_input_tokens`.
- Never source tokens or cost from `Iteration.usage_*` (Trap 7) — last-write-wins corrupted, and
  `Iteration` has no cost field at all.
- Report `llm_calls` alongside cost and tokens — ~4% of events have a null `cost_usd` (`sum()`
  ignores nulls so the total is still correct), and some non-priced models report `$0` cost with
  real token counts. Surfacing `llm_calls` and the token columns makes that gap visible instead of
  hiding it inside a dollar figure.

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
