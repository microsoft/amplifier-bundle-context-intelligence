# validate-agents v1.7.0 — run ON THE BRANCH (`lane/kv98-catalog-context-intelligence`)

Recipe: `/home/bkrabach/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/recipes/validate-agents.yaml`
(`validate-agents` v1.7.0, dependency pinned `git+https://github.com/microsoft/amplifier-foundation@v2.1.2`,
resolved revision `a27d5824517d078097b60d84779dd3eae80202cd`)
Run id: `run-07f9b20c4eab` · session `f3a955d0f8f74949-20260907-095216_recipe` · 2026-09-07

## Verdict (quoted verbatim from `final_report`)

> - **Overall Verdict**: ✅ **PASS**
> - **Agents Found**: 4 total across 1 location
> - **Quality Breakdown**: 4 good, 0 polish, 0 needs_work, 0 critical
> - **Issues**: 0 errors, 0 warnings, 0 required suggestions

## Discovered agent count for this repo

> ```
> Scanned: <repo>/**/agents/*.md, <repo>/agents/*.md, excluding .git, .venv, docs, node_modules, test-fixtures, tests
> Candidates: 6 files matched the scan
> Classified as agents: 4 across 1 locations
> Classified as NON-agents: 2 ({"no_frontmatter": 2})
> Classifier: frontmatter declares a top-level `meta:` key (docs/AGENT_AUTHORING.md)
> ```

| Location | Agents |
|----------|--------|
| agents/  | 4      |

Non-agents (matched the scan, correctly excluded — not errors):

| File | Reason not an agent |
|------|---------------------|
| context/agents/reconstruction-knowledge.md  | no_frontmatter |
| context/agents/session-storage-knowledge.md | no_frontmatter |

## Per-agent structural result (from `structural_results`)

| Agent | desc chars | desc tokens | example_count | commentary_count | errors | warnings |
|---|---|---|---|---|---|---|
| context-intelligence-design-facilitator | 598 | 149 | 0 | 0 | 0 | 0 |
| context-intelligence-tool-designer      | 599 | 149 | 0 | 0 | 0 | 0 |
| graph-analyst                            | 595 | 148 | 0 | 0 | 0 | 0 |
| session-navigator                        | 548 | 137 | 0 | 0 | 0 | 0 |

(Char counts here are the recipe's own, one higher than the raw description text
because the YAML `|` block scalar carries a trailing newline. Both readings are
under the ~600 budget.)

LLM phases were **skipped** — `requires_llm_analysis: false`, all agents met the
deterministic thresholds, and all carry explicit tool declarations.

## Correction to the goal's stated expectation

The goal said the recipe "must stay PASS". It was **not** PASS before this branch.
See `validate-agents-before-structural.json`: at `HEAD~1` graph-analyst carried 2
`<example>` blocks and session-navigator 1, each a structural **ERROR** under
v1.4.0+, classifying both agents `critical` and the run **FAIL**. This branch is
therefore **fail-before / pass-after**, not "stayed PASS".
