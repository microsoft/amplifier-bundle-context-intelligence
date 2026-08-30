---
name: context-intelligence-derived-metrics
description: >
  Use when a graph query returns structurally-identical rows hiding different
  intent — many delegations all "none/conversation", look-alike sessions,
  identical tool calls. Derives an INTERPRETIVE metric by joining a second graph
  layer onto the blanket shape (flagship: transport × sub-session skill payload =
  "flavour"). Complements graph-query (READ the graph) by making blanket reads
  MEAN something.
license: MIT
metadata:
  version: "1.0.0"
  changelog:
    - "1.0.0: Initial skill. The blanket-data problem and the three moves (spot
       the blanket, find the discriminating join, recompute with the derived
       dimension). Flagship flavour metric (transport × in-subsession skill
       payload) with verified Cypher. Worked example: council-panel attribution
       collapsing self-delegation from ~42% to ~12% of delegations once the
       derived dimension is applied. Generalization to magnitude-based and
       artifact-based derived metrics."
---

# Context Intelligence Derived Metrics

> **Prerequisite:** load `context-intelligence-graph-query` first. That skill owns
> the schema, scoping levers, size discipline, and blob rules. This skill assumes
> you can already run scoped queries safely; it only teaches the *interpretive
> layer* on top. Verify every node/edge/property named here against the live
> schema (`CALL db.schema.nodeTypeProperties()`) — read the live schema from the
> server, read the *meaning* from this skill.

## Section 1 — The blanket-data problem

Raw graph shapes are often **blanket**: many rows share one structural signature
but carry completely different intent. A single-layer query cannot tell them
apart, so the aggregate lies by omission — the mode hides sub-populations.

The canonical case: delegation transport. Across a real corpus, ~70% of all
delegations were `context_depth="none"`, `context_scope="conversation"`. Read flat,
that says "clean-slate delegation dominates." But that one bucket contained at
least three *different behaviors* wearing the same clothes:

- **Pure task-offloading** — spin up a fresh worker for an independent unit of work.
- **Context-sink** — park token-heavy exploration in a disposable agent so the
  parent stays lean.
- **Council lenses** — a review panel fanning six persona lenses out cold.

Transport args alone (`agent`, `context_depth`, `context_scope`, `is_self_delegation`)
put all three in the same cell of the table. The distinction is real and it
matters — but it lives in a **different layer** than the one you queried.

> A derived metric is what you build when the shape you can see is not the meaning
> you need. You attach a second, correlated signal to the blanket shape until the
> sub-populations separate.

## Section 2 — The three moves

Every derived metric is the same three moves. Do them in order.

**Move 1 — Spot the blanket.** A query returns many rows with the same structural
signature and you find yourself unable to say *why* each one happened. The tell:
a dominant mode (one `(depth, scope)` cell, one tool name, one session label) that
you suspect contains distinct intents. If you can't narrate a row from its own
fields, it's blanket.

**Move 2 — Find the discriminating join.** Identify a *second* graph signal that
correlates with the intent you care about and is reachable from the blanket row.
It comes in three flavours:

| Join type | Second signal | Separates |
|---|---|---|
| **Categorical (payload)** | what SKILL loaded in the spawned sub-session | council lens vs plain worker |
| **Magnitude** | token / iteration / tool-call *volume* inside the sub-session | context-sink vs light offload |
| **Artifact** | what the sub-session PRODUCED (commit, PR, file) | continuity handoff vs throwaway |

The best join is *causally meaningful*, not merely correlated (Section 6).

**Move 3 — Recompute with the derived dimension.** Segment the blanket population
by the new signal, then **recompute the headline stat with the derived population
removed**. The delta is the finding. "Self-delegation is 42% of delegations"
becomes "…but 82% of that is one review panel; strip it out and self-delegation is
12%." The recompute-with-removed step is non-negotiable — it's how you prove the
derived dimension was load-bearing rather than decorative.

## Section 3 — Flagship: the "flavour" metric

**Definition.** The *flavour* of a sub-session is `(transport) × (payload)`:

- **Transport** = the `Delegation` that spawned it: `is_self_delegation`,
  `context_depth`, `context_scope`, `agent`.
- **Payload** = what happened *inside* the spawned sub-session. The single most
  discriminating payload signal is the `SkillLoad.skill_name`(s) that fired in
  that sub-session.

A `none/conversation` self-delegation is a **blank envelope** — its meaning comes
entirely from what skill gets loaded inside it. That is exactly why a review panel
can hide in plain sight: it shares a transport signature with ordinary parallel
fan-out, and only the skill payload reveals it is a debate panel convened cold.

### The join key

`Delegation.sub_session_id` equals the spawned session's `Session.node_id`. That
is the bridge from transport to payload. Skill loads hang off the sub-session two
ways (per graph-query Gotcha 6) — iteration-level and session-level — so match
both:

```cypher
// FLAVOUR TABLE — every delegation transport × the skill payload of its sub-session.
// Scope on the PARENT session (it carries workspace); see graph-query Section 2.
MATCH (parent:Session {workspace: $workspace})
      -[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(:Iteration)
      -[:HAS_TOOL_CALL]->(:ToolCall)-[:TRIGGERED]->(d:Delegation)
OPTIONAL MATCH (sub:Session {node_id: d.sub_session_id})
OPTIONAL MATCH (sub)-[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(:Iteration)
               -[:HAS_SKILL_LOAD]->(sl:SkillLoad)
OPTIONAL MATCH (sub)-[:HAS_SKILL_LOAD]->(slsess:SkillLoad)   // session-level loads
WITH d.is_self_delegation AS self,
     d.context_depth       AS depth,
     d.context_scope       AS scope,
     coalesce(sl.skill_name, slsess.skill_name) AS payload_skill
RETURN self, depth, scope, payload_skill, count(*) AS n
ORDER BY n DESC
LIMIT 100
```

Rows where `payload_skill IS NULL` are transport with no skill payload — plain
workers and context-sinks (separate those with a magnitude join, Section 5). Rows
where `payload_skill` is a named lens (e.g. `crusty-old-engineer`,
`restless-old-brian`) are the panel.

### The reverse rollup — for each skill, its transport signature

Flip the same result to ask "when this skill runs, how is it convened?" A skill
with one dominant transport signature has a strong flavour; a skill spread across
transports is convened many ways.

```cypher
// Same match as the flavour table, then group per skill.
// Cypher has no SQL window functions — compute the per-skill total with a
// collect/UNWIND two-pass, not OVER(PARTITION BY ...).
WITH payload_skill, d.is_self_delegation AS self,
     d.context_depth AS depth, d.context_scope AS scope, count(*) AS n
WHERE payload_skill IS NOT NULL
WITH payload_skill, sum(n) AS total,
     collect({self: self, depth: depth, scope: scope, n: n}) AS combos
UNWIND combos AS c
RETURN payload_skill, c.self AS self, c.depth AS depth, c.scope AS scope,
       c.n AS n, toFloat(c.n) / total AS share_within_skill
ORDER BY payload_skill, n DESC
```

Expect the panel lenses → ~100% `self / none / conversation` (convened cold by
design). A context-sink skill → named-agent `/ none`. A continuity skill → named
`/ recent` or `/ all`.

### Attribution discipline — attribute by CHILD, never by shared parent

The flavour join attributes by the **sub-session's** skill load — and that is the
*correct* attribution, not a convenience. The trap: an inline panel (a
`*-here` variant that loads into the CURRENT session instead of forking) shares
its parent session with that session's own real work — git-ops, builders, the lot.
If you attribute by the *parent* session's skill loads, you sweep all that
unrelated work into the panel's bucket and massively over-count. Attributing by
the child/sub-session that actually loaded the lens keeps the count clean and
excludes the inline variant's host-session noise automatically.

## Section 4 — Worked example (what the derived dimension revealed)

Applying the flavour metric to a real corpus of ~2,600 delegations:

| Metric | Blanket read | Council-driven | Derived read (council removed) |
|---|---|---|---|
| Self-delegation share | **42.0%** | 893 | **11.7%** |
| `none/conversation` share | **70.2%** | 893 | **54.6%** |

Reading:

- **893 delegations (34%) were one review panel** convening its lenses — invisible
  on the transport axis, obvious on the flavour axis (100% self / none /
  conversation with a persona `skill_name` payload).
- **Self-delegation was ~82% a panel artifact.** The flat "self-delegation
  dominates" finding was an aggregate illusion; outside the panel, self-delegation
  is a marginal ~12% tactic.
- **`none/conversation` survived as a genuine default** (54.6%) but dropped from a
  landslide to a plurality once the derived population was removed.

The Move-3 recompute is the whole finding. Without it you would have shipped
"self-delegation dominates," which is false.

## Section 5 — The metric generalizes (other derived dimensions)

Flavour is one instance. The same three moves build others:

**Context-sink vs offload (magnitude join).** Both are `none/conversation` with a
NULL skill payload — categorically identical. Discriminate on *work volume inside
the sub-session*: sum iterations, tool calls, or token/`content_length` proxies.
High volume + broad file reads = context-sink (the parent offloaded token cost);
low volume = light task-offload. Here the discriminating signal is a *magnitude*,
not a category.

```cypher
// Magnitude proxy: iteration + tool-call volume inside each spawned sub-session.
MATCH (parent:Session {workspace: $workspace})
      -[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(:Iteration)
      -[:HAS_TOOL_CALL]->(:ToolCall)-[:TRIGGERED]->(d:Delegation)
WHERE d.context_depth = 'none' AND d.context_scope = 'conversation'
MATCH (sub:Session {node_id: d.sub_session_id})
OPTIONAL MATCH (sub)-[:HAS_EXECUTION]->(:OrchestratorRun)-[:HAS_PART]->(it:Iteration)
OPTIONAL MATCH (it)-[:HAS_TOOL_CALL]->(tc:ToolCall)
RETURN d.agent AS agent,
       count(DISTINCT it) AS iterations,
       count(tc)          AS tool_calls
ORDER BY tool_calls DESC
LIMIT 50
```

**Continuity handoff (artifact join).** `recent` / `all` transport paired with what
the sub-session PRODUCED — a commit, a PR, a written file. Transport says "this
agent inherited context"; the artifact says "…because it had to author something
faithful to the session's history." That pairing is the signature of a genuine
continuity handoff versus a needlessly heavy context pass.

The recipe never changes: **blanket shape → discriminating second signal (category,
magnitude, or artifact) → segment and recompute with the derived population
removed.**

## Section 6 — Discipline and traps

- **Recompute-with-removed is mandatory.** A derived dimension you don't subtract
  out is just a colorful column. The finding is the delta to the headline.
- **Causal, not spurious.** Before trusting a join, ask whether the second signal
  *explains* the intent or merely co-occurs. The child-vs-parent attribution trap
  (Section 3) is a spurious-correlation trap: the parent-session join correlates
  but does not attribute.
- **Verify the live schema.** `Delegation.sub_session_id`, `Session.node_id`, and
  the `SkillLoad` attachment points are the load-bearing joins — confirm them with
  `CALL db.schema.nodeTypeProperties()` before relying on them. Note two properties
  seen live but NOT documented in `context-intelligence-graph-query` at time of
  writing: a `resolved_agent` on `Delegation` and a `context` (e.g. `"fork"`) on
  `SkillLoad`. Treat them as *verify-before-cite*; the queries here deliberately
  depend only on documented properties (`agent`, `skill_name`) so they work either
  way.
- **Size discipline still applies.** Derived-metric queries aggregate — return
  counts and small grouped rows, never the full text of many rows. Everything in
  graph-query Section 6 holds.
- **Scope honestly.** Corpus mix skews absolute numbers (a dev-heavy workspace
  inflates git-ops and panel traffic). Report the scope with the metric, and offer
  the single-workspace recompute.

## Section 7 — When to reach for this

- A query's headline is dominated by one structural mode and you can't narrate why
  each row happened → build a flavour/derived metric before reporting the mode.
- Someone asks "what is this agent/session actually *doing*" and the transport
  fields don't answer it → the answer lives in the payload/magnitude/artifact
  layer.
- You're about to report an aggregate ("X dominates", "most delegations are Y") →
  run the recompute-with-removed once to check the aggregate isn't an artifact of a
  single hidden sub-population.

Do NOT reach for it for a genuinely homogeneous population, or when the raw fields
already narrate the row (a `recent`/`all` git-ops delegation explains itself). A
derived metric you compute for everything stops being a signal.
