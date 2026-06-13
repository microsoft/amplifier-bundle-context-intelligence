---
name: context-intelligence-evaluation-methodology
version: 1.0.0
description: Use when deciding how to measure a context-intelligence tool signal — metric design across quality/efficiency/efficacy axes, artifact-metric avoidance via precursor measurement, A/B and statistical-N discipline, and test-data fidelity.
user-invocable: false
allowed-tools: read_file, glob, grep, delegate, load_skill, todo
model_role: reasoning
license: MIT
---

# Context Intelligence Evaluation Methodology

Mode-only skill for *how to measure*. It complements — and never restates —
`context-intelligence-eval-design` (which owns scenario mechanics and the two-layer
structural/behavioral structure) and `digital-twin-universe` (which owns the DTU machinery).

## Scope

### In scope

- **Metric design.** Choose metrics across three axes: **quality** (did it detect what the user
  means?), **efficiency** (token/tool cost to detect), **efficacy** (does detection drive the
  right outcome?).
- **Measure the precursor, not only the failure.** Prefer leading indicators (e.g. bounded vs
  climbing context growth) over lagging ones (e.g. a final timeout). The precursor is testable
  in a short window; the full failure often is not.
- **A/B + statistical-N discipline.** A single green run is not proof of a behavioral change.
  Compare a control arm against a treatment arm; require N independent trials and report the
  pass rate, not a single anecdote.
- **Test data fidelity.** Validate on **real sessions** where available, or on **faithfully
  modeled synthetic** corpora that preserve the distribution that matters (e.g. heavy-tail file
  sizes), never on data hand-shaped to pass.

### Out of scope (point elsewhere — do not restate)

- **Scenario mechanics, the two-layer structure, DTU profile templates / Gitea URL rewrite** →
  `context-intelligence-eval-design`.
- **DTU lifecycle, launch/exec/profiles** → the `digital-twin-universe` skill.
- **"DTU-as-default" rationale and the "artifact-as-success" anti-pattern** → already authoritative
  in `context-intelligence-eval-design` and
  `context-intelligence:context/context-intelligence-primitives-reference.md`. Reference them;
  do not repeat them.

## Method

1. **Name the user-meaningful outcome** the metric must reflect (from `domain-concepts.md`),
   not what a detector happens to emit.
2. **Pick the cheapest axis that discriminates.** If a deterministic efficiency signal (token
   growth, tool-call count) separates good from bad behavior, prefer it over an LLM-judged
   quality rubric.
3. **Identify the precursor.** Ask: what climbs *before* the failure? Measure that in a bounded
   window.
4. **Design the A/B.** Control = current behavior; treatment = the change. Hold everything else
   identical. Decide N and the pass threshold *before* running.
5. **Choose the corpus.** Real sessions if available; otherwise synthetic that preserves the
   load-bearing distribution. State the fidelity assumption explicitly.

> **Apply (do not restate) the event-semantics principle:** when a metric depends on what an
> event *means*, consult the authoritative ecosystem expert agents for that event's semantics —
> the principle is named once in
> `context-intelligence:context/context-intelligence-strategy.md`. Do not restate it here.
