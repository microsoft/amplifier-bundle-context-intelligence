# Navigation / Context-Budget Discipline

> **AUTHORITATIVE SOURCE — do not duplicate these rules elsewhere; point here instead.**
> Scope: **local JSONL navigation budget only.** This is the single home for the bounded,
> convergent navigation rules. Consuming agents `@mention` this file (loading); always-on and
> mode-layer files reference it by path only (non-loading).

**These rules are MANDATORY. Every local JSONL navigation MUST follow them. They override any
open-ended guidance in any consuming agent.**

## Rule 1 — Probe before you dig

Before any deep search, run exactly **ONE** bounded existence/enumeration probe:

- **For a specific session ID:** `ls <workspace>/sessions/ | grep <id>` (one command).
- **For "what exists here":** one count + one head — `ls | wc -l`, then `ls | head`.

If the probe returns nothing → the target is not here. Go directly to **Rule 3 (Convergence on absent)**. **Do NOT escalate.**

## Rule 2 — Progressive, budgeted expansion (max 3 strategies)

Only if the probe is **genuinely ambiguous** (a partial or fuzzy match is plausible), climb this **fixed ladder** and **STOP at the end**:

**(a)** Exact-ID existence — the probe from Rule 1.  
**(b)** ONE partial-ID `find -maxdepth 2`.  
**(c)** ONE bounded cross-workspace `jq` over `metadata.json` files (head-limited).

**Do not invent further permutations beyond (c).**

## Rule 3 — Convergence on absent (HARD STOP)

After completing the ladder with no hit:

1. State which strategies you tried.
2. Conclude: **"session/data not found"**.
3. **RETURN your result immediately.**

Do NOT retry. Do NOT broaden the search. Do NOT delegate upward to `detective`.

> **Delegation constraint:** You MAY delegate ONCE to another session-data-analysis-capable agent (if one is available in the host environment) ONLY for data that is **present-but-hard** to extract locally — **never for absent data**.

## Rule 4 — Tool-call budget

**Hard ceiling: ~8 bash/jq calls per navigation task.** If you approach this limit, summarize what you have found so far and return — do not continue searching.

## Rule 5 — Summarize and discard between steps

After each extraction step, retain only the 1–2 facts you needed. Never re-read or re-dump the same data. Never hold raw JSONL output across steps. **Goal: flat context growth regardless of corpus size.**

## Rule 6 — Every events.jsonl extraction is head-limited

Every `jq -c 'select(...)'` over an `events.jsonl` MUST end with `| head -N` (default `head -20`).

```bash
# CORRECT
jq -c 'select(.event == "tool:error") | {event, ts: .timestamp, error: .data.error}' events.jsonl | head -20

# WRONG — no head limit, will dump unbounded output into context
jq -c 'select(.event == "tool:error") | {event, ts: .timestamp, error: .data.error}' events.jsonl
```

---

> **Calibration note:** The budgets above (3 strategies, ~8 calls, `head -20`) are calibrated defaults that the evaluation harness will tune over time.

---

## Why This Discipline Exists — the Context-as-Safety-Budget Principle

The operating principle behind all six rules is one property: **flat context growth regardless
of corpus size.** Treat context as a *safety budget*, not a workspace — every byte you let
accumulate is spent permanently for the rest of the session.

**The death-spiral "why."** Unbounded accumulation is the *precursor* to the
context-compaction death-spiral. When a navigation task lets raw JSONL pile up turn over turn,
the session eventually crosses the compaction threshold. Each compaction retains bloated
context, the next turn re-accumulates on top of it, and the cost-per-turn climbs instead of
falling — a long session can spend hours in this spiral and never converge. The bounded probe
(Rule 1), the capped ladder (Rule 2), the hard stop (Rule 3), the call ceiling (Rule 4),
summarize-and-discard (Rule 5), and head-limited extraction (Rule 6) exist for one reason: to
make sure a navigation task can never *seed* that spiral. Converge early; hold almost nothing;
never re-read.
