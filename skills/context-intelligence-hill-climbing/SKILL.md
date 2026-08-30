---
name: context-intelligence-hill-climbing
description: 'Use when an investigation needs more than one step — track it in todo as a hill climb so progress and dead leads leave an audit trail. Governs HOW you track the climb, not how you query or extract — that stays in graph-query / session-navigation.'
version: 0.1.0
license: MIT
---

# Context Intelligence Hill-Climbing

You already navigate well — bounded probes, count-first, `SKIP`/`LIMIT`, `jq | head`, and the
budget discipline. This skill does **not** change any of that. Its one job is to make a multi-step
investigation **track itself in the `todo` tool** so that (a) you always know the next best step and
when the plan needs adjusting, and (b) anyone reading the run afterwards can see exactly *how* the
answer was reached.

Think of the todo list as your **climb log / belief-state**: the open questions you're climbing
toward, the findings you've banked, and the leads you've abandoned.

## When to use this

- **Use it** for any investigation that will take **more than one probe** — provenance tracing,
  cross-session synthesis ("how do we work?"), anything where you'll refine or escalate.
- **Skip it** for a genuine **single-shot lookup** (one count, one bounded query, done). Do not add
  todo ceremony to a one-liner — that's overhead, not an audit trail.

## The climb, tracked in todo

1. **Seed the open questions.** Before your first real probe, turn the task into `todo` items —
   one per open question you must answer. This is the belief-state at the start of the climb.
   *(e.g. "Identify the root cause", "Find who FIRST produced the design", "Reconstruct the causal chain".)*

2. **Take the next best step, then record.** After each probe, update the list immediately:
   - mark an open question answered, and **write the finding into the item** (the fact + where you
     found it — the citation), not just a checkmark;
   - if the probe opened a new question, **add a todo** for it;
   - if a lead was a dead end, **mark it dead** with one line of why (so you — and the reader — don't
     re-walk it). Do not silently drop it.

3. **Adjust out loud.** When a finding changes the plan (a lead dies, the shape is different than
   assumed, you must escalate the tool tier), the todo list is where that pivot is recorded. Drift
   that isn't in the log is drift nobody can audit.

4. **Converge and stop.** You're done when every open-question todo is answered **or** the budget is
   hit. On genuinely absent data, mark the item resolved-as-absent — do not spin.

5. **The final todo state IS the audit trail.** Before answering, the list should read as a legible
   account of the climb: questions asked, findings banked (with citations), leads abandoned. Your
   final answer should be consistent with it.

## What this skill does NOT do

- It does **not** tell you how to query, page, bound, or extract. For the graph surface, that's
  `context-intelligence-graph-query` (+ `context-intelligence-gds` for topology). For the JSONL
  surface, that's `context-intelligence-session-navigation` + the navigation-budget discipline. This
  skill sits *above* those and only governs the climb log.
- It does **not** add analytical steps you wouldn't otherwise take. If you'd answer in one probe,
  answer in one probe.

## Why it's worth the keystrokes

The todo trail turns an ephemeral investigation into a **reviewable** one: a later reader (or you,
resuming) can see which leads were tried, which were dead, and what evidence backs each finding —
without re-running the whole climb. Keep it lean; the log should help the work, never become the work.
