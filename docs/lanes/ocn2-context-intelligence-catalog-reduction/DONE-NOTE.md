# Lane ocn2 — skills catalog reduction for `amplifier-bundle-context-intelligence`

**Item:** `model_performance-ocn2` (project `model_performance`)
**Branch:** `lane/ocn2-context-intelligence-catalog-reduction` (base `c614dc4`)
**Date:** 2026-09-07
**Outcome:** **A — RESOLVED at the landing stage (draft PR).** Every deliverable is
DONE except one *sub*-item of the before/after render, recorded NOT-POSSIBLE at the
$0 cap with its exact blocker and the recipe to close it (§5). Spend: **$0.00** —
no LLM call was made by any measurement in this lane.

---

## 1. Result in one paragraph

The bundle's always-on skills catalog goes from **5 model-invocable entries to 3**.
`blob-reading` (136 lines) moved verbatim into the `context-intelligence-graph-query`
skill as the level-3 reference file `blob-reading.md` and is no longer mounted as a
skill of its own; `context-intelligence-session-reconstruction` — a fallback-path
skill you reach for deliberately, after local session files are already known to be
broken — now carries `disable-model-invocation: true`, so it leaves the "Available
skills" index and appears in the "User-invoked skills" section instead, still loading
by name. Measured from a real mounted session with no LLM call: **5 visible → 3
visible + 1 user-invoked**, block size **1,046 → 981 bytes** per turn. Nothing was
deleted: every line of blob-reading survives at its new path (git records an R100
rename), and no skill was removed from the repo — 11 SKILL.md files before, 10 after,
with the eleventh's content living inside one of the ten.

## 2. What changed

| file | change |
|---|---|
| `skills/blob-reading/SKILL.md` → `skills/context-intelligence-graph-query/blob-reading.md` | **moved** (git rename). Body verbatim; the skill frontmatter is replaced by a level-3 reference header pointing back at the parent skill. |
| `skills/context-intelligence-graph-query/SKILL.md` | Section 6 now points at `blob-reading.md`; `metadata.version` 2.5.0 → **2.6.0** with a changelog entry naming exactly what was absorbed. |
| `skills/context-intelligence-session-reconstruction/SKILL.md` | `disable-model-invocation: true` + a frontmatter comment saying why (fallback path, reached by name); version 1.0.0 → **1.1.0**. |
| `behaviors/context-intelligence-analysis.yaml` | the `skills/blob-reading` mount is gone, with a comment saying where the content went; layer description updated. |
| `agents/graph-analyst.md` | "Load skill: blob-reading" → `read_file("@context-intelligence:skills/context-intelligence-graph-query/blob-reading.md")`. |
| `docs/context-intelligence-exploration-guide.md` | 3 references re-pointed (scenario E, its failure hint, the troubleshooting row). |
| `README.md` | repo tree shows `blob-reading.md` under the graph-query skill. |

**Not touched:** the 6 design-mode skills (`-derived-metrics`, `-eval-design`,
`-evaluation-methodology`, `-gds`, `-hill-climbing`, `-tool-design`). They are gated
behind the design mode and were never in the always-on catalog — verified, not
assumed (§3's `mounted_skill_sources` lists exactly what the behaviors mount).

## 3. Inventory reconciliation — the goal's "expected 5" is correct, with a caveat

The goal expected 5 skills. **On disk this repo carries 11 SKILL.md files**; exactly
**5 of them are mounted always-on** by the behaviors (`context-intelligence-navigation`
mounts 1, `context-intelligence-analysis` mounts 4). Those 5 are the goal's set, name
for name. The other 6 reach a session only through the design mode. So: goal correct
about the always-visible set, incomplete about the on-disk set — recorded here so the
next lane does not "discover" a discrepancy that is not one.

## 4. THE MEASUREMENT — before/after, real session, $0

Instrument: `render_visibility.py`, committed beside this note (mechanism borrowed from
lane kv98's `render_catalog.py`). It reads the skill list out of `behaviors/*.yaml`,
rewrites each `git+…@main#subdirectory=skills/X` source to `<checkout>/skills/X`,
mounts a real session, and captures the
`<system-reminder source="hooks-skills-visibility">` block. **No LLM call — $0.** It
refuses to report a number if rendered entries ≠ mounted sources, so a probe pointed at
the wrong tree fails loudly instead of quoting a confident wrong figure.

BEFORE = a worktree at this branch's base `c614dc4`. AFTER = this branch.

| | before | after |
|---|---:|---:|
| skills mounted always-on | 5 | **4** |
| **"Available skills" entries (model-invocable)** | **5** | **3** |
| "User-invoked skills" entries | 0 | **1** |
| visibility block, bytes/turn | 1,046 | **981** (−65 B, −6.2%) |
| `load_skill(skill_name="context-intelligence-session-reconstruction")` | loads, 7,281 chars, error `null` | **loads, 7,281 chars, error `null`** |

The two rendered blocks, the `load_skill` results and the summaries are in
`evidence/before/` and `evidence/after/`. The AFTER block, verbatim:

```
Available skills (use load_skill tool):

- **context-intelligence-graph-query**: …
- **context-intelligence-session-navigation**: …
- **workflow-pattern-analysis**: …

User-invoked skills (available via /command):

- **context-intelligence-session-reconstruction**: …
```

**The hidden skill still loads, byte-identical to before (7,281 chars, no error).**
That is a measurement through the real mounted `load_skill` tool, not an inference from
reading the visibility code.

## 5. NOT-POSSIBLE at $0 — the AFTER render on the owner's real 20-bundle app list

The BEFORE half is captured verbatim (`evidence/real-app-live-session-before.txt`):
the live block from this lane's own session on the owner's real app list, showing all
**5** context-intelligence entries in the model-invocable section and **0** in the
user-invoked section. The AFTER half is **not fundable at $0**, for a structural reason
worth recording:

* A composite probe that loads all 20 app behaviors and swaps the context-intelligence
  entry for a local checkout **does not measure the local checkout**. `amplifier-foundation`
  itself includes `context-intelligence-logging` and `context-intelligence-navigation`
  from `git+…@main`, so the cached published bundle wins skill-source resolution
  (observed: every resolved `skills_dir` pointed at
  `~/.amplifier/cache/skills/amplifier-bundle-context-intelligence-…/`). Measuring the
  branch on the real app therefore also requires overriding foundation, not just the
  app entry. (Two further app-composite gotchas found on the way, both recorded for the
  next lane: `load_bundle` alone does not expand `${VAR:default}`, so
  `hook-context-intelligence` fails protocol compliance on a literal log level and
  session init aborts — the app-cli's own `expand_env_vars` on the mount plan fixes it;
  and even with 78 skills mounted, the visibility hook rendered nothing under that
  composite, uninvestigated.)
* The honest cheap alternative — start one real session on the branch and read the
  block — costs a real LLM turn. Authority here is **$0.00**, so it was not bought.

**Recipe for whoever wants it (≈ 1 turn, cents):** point a source override at this
checkout for *both* `amplifier-bundle-context-intelligence` and the foundation bundle
that includes it, start one scratch session, and read the injected block. The count to
expect is the one §4 already measured deterministically: 3 visible + 1 user-invoked.

## 6. Gates run

| gate | result |
|---|---|
| `uv run --frozen pytest tests/ -q` (repo root suite) | **753 passed** |
| `uv run --frozen ruff check .` | All checks passed |
| `uv run --frozen ruff format --check .` | 143 files already formatted |
| before/after visibility probe | ran clean on both trees, self-check passed |
| hidden-skill load-by-name | passed on the AFTER tree (7,281 chars, no error) |

**Remote CI:** PR #110 shows only `license/cla` (pass) — `gh run list` reports **no
workflow run at all** for this branch ~3 minutes after push. The repo's `ci.yml`
triggers on `pull_request: branches: [main]`; the observed difference from lane kv98's
PR #109 (which did get a green CI run) is that this one is a **draft**. Expect CI to
start when the manager marks it ready for review — re-check then rather than reading
the CLA-only rollup as "CI passed".

`scripts/validate-full.sh` and a DTU run (AGENTS.md's seam gate for skill edits) were
**not** run: both need spend/time beyond the $0 authority, and this change moves no
code — it moves one markdown file, drops one mount line, and adds one frontmatter key.
The seam it does touch (skill visibility + load-by-name) is exactly what §4's probe
exercises against a real mounted session. Flagged plainly rather than skipped silently.

## 7. Merge notes for the manager

1. **Draft PR only — not merged**, per the landing-stage rule.
2. **Base is stale.** `origin/main` has moved to `a2dfa06` (lane kv98's PR #109, which
   also edits `agents/graph-analyst.md` and `skills/workflow-pattern-analysis/SKILL.md`).
   This branch is based on `c614dc4`. Expect a rebase; the only likely conflict is in
   `agents/graph-analyst.md`, where kv98 rewrote the frontmatter description and this
   lane rewrote the Section-2 "Load the Blob Reading Skill" body — different hunks.
3. **One external-contract change:** anything mounting
   `…amplifier-bundle-context-intelligence@main#subdirectory=skills/blob-reading` breaks
   after merge. Repo-wide grep found exactly one such reference — this repo's own
   `behaviors/context-intelligence-analysis.yaml` — and it is updated in this PR.
