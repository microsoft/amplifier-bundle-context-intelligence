---
name: workflow-pattern-analysis
version: 1.0.0
description: >
  Analyse failure and success patterns across many runs of a specific workflow using
  context-intelligence session data. Use when you want to answer: "How is <workflow>
  failing?", "What does a successful run look like vs a failing one?", "Which steps are
  the most common failure points?", or "What patterns appear consistently across sessions?"
  Designed to run inside the context-intelligence-design mode. Guides the full
  investigation lifecycle: corpus enumeration, failure signal detection, JSONL payload
  inspection, and structured findings production via the dual-agent loop
  (context-intelligence:graph-analyst → context-intelligence:context-intelligence-design-facilitator).
  Triggers on: workflow failure patterns, session failure analysis, compare successful and
  failing runs, what patterns appear across sessions, session behaviour investigation,
  how is the workflow failing, success patterns, failure signals, identify session signals.
user-invocable: true
allowed-tools:
  - bash
  - delegate
  - read_file
  - write_file
model_role: reasoning
license: MIT
---

# Workflow Pattern Analysis

Systematic investigation of failure and success patterns across many runs of a specific
workflow using context-intelligence session data.

Answers three questions:
1. What failure patterns appear consistently across many runs?
2. What does a successful run look like vs a failing one?
3. Which specific steps or delegations are the most common failure points?

Follows the **dual-agent loop** established from direct investigation of Amplifier
session corpora:
- `context-intelligence:graph-analyst` — all corpus queries and JSONL inspection
- `context-intelligence:context-intelligence-design-facilitator` — structured artifact
  production and quality gating (mandatory gate before any finding is treated as curated)

Produces a findings package in `{{output_path}}`:
- `findings.md` — failure patterns (P1–Pn) with severity, frequency, mechanism, evidence
- `domain-signals.md` — JSONL-observable signal definitions with corpus-anchored thresholds
- `session-index.md` — full session inventory with signal scores
- `diagrams/failure-patterns.dot` + `.png` — pattern taxonomy diagram

## Companion Reference Files

At the start of the investigation load these files from this skill's directory:

```
read_file("${SKILL_DIR}/signals-reference.md")   — signal definitions S1–S9 + S4 family
read_file("${SKILL_DIR}/cypher-patterns.md")     — Cypher query templates Q1–Q7
```

Also load from the bundle context when querying the graph or scanning JSONL:

```
@context-intelligence:context/graph-model-reference.md   — CI graph schema
@context-intelligence:context/jsonl-event-schema.md      — on-disk JSONL event schema
@context-intelligence:context/safe-extraction-patterns.md — safe JSONL extraction patterns
```

## Inputs

- `{{workflow}}`: The workflow to analyse — e.g. "amplifier CLI sessions", "git-ops
  agent runs", "recipe orchestration", "resolver setup flow". Can be a workspace name,
  session characteristic pattern, or a description of the session type.
- `{{output_path}}`: (Optional) Where to write findings artifacts.
  Default: `.context-intelligence-investigation/`

## Steps

### 1. Load companion files and set up workspace

Load the companion reference files:
```
read_file("${SKILL_DIR}/signals-reference.md")
read_file("${SKILL_DIR}/cypher-patterns.md")
```

Create the output directory structure:
```bash
mkdir -p {{output_path}}/queries {{output_path}}/diagrams
mkdir -p findings/
```

`findings/` holds raw analyst output (intermediate). `{{output_path}}/` holds curated
artifacts produced by the facilitator.

Then clarify what a "run" means for `{{workflow}}`:
- What session property identifies a run? (workspace slug, duration range, recipe name,
  event count range, session characteristics)
- Scope: `:RootSession` only, `:ForkedSession` only, or both?
- Time range: all time or bounded?

Express the scope as an observable session property filter — not as a named-agent filter.

**Success criteria**: Companion files loaded. Output directories exist. Run definition
expressible as a Cypher WHERE clause or metadata field filter.

### 2. Enumerate the full corpus before investigating

**Before drilling into any specific pattern**, build the full corpus inventory.

Delegate to `context-intelligence:graph-analyst` to:
- Find all sessions matching `{{workflow}}` scope using **Q1** from `cypher-patterns.md`
- Split `:RootSession` from `:ForkedSession` — failure rates differ structurally
  (roots ~73% vs corpus ~55%)
- Compute: session count by type, median duration, median event count

**Graph vs disk**:
- **Graph server is preferred** — Cypher returns cross-session pattern matches as atomic
  operations far more efficiently than per-file scanning. Use Q1–Q7 from `cypher-patterns.md`.
- **Disk scan is the explicit alternative** when the user needs comprehensive coverage of
  ALL on-disk sessions regardless of graph indexing state. Disk scan entry:
  ```bash
  find ~/.amplifier/projects -path "*/context-intelligence/metadata.json" 2>/dev/null
  ```
  Filter to `format == "context-intelligence"` AND `version == "1.0.0"`.
  Follow `@context-intelligence:context/safe-extraction-patterns.md` — never load full
  `llm:request` lines.
- Validate coverage: compare graph session count against disk count and inform the user
  if the graph holds a small fraction. The signal prevalence rates from a graph sample
  can still be directionally valid even if the absolute corpus is larger — confirm this
  before deciding whether a full disk scan is needed.

Write raw output to `findings/session-corpus-raw.md`.

**Success criteria**: Corpus total confirmed (root count, sub count, workspace count).
User is aware of graph vs disk coverage situation.

### 3. Apply signal set — failing and successful runs

Delegate to `context-intelligence:graph-analyst` to apply signals **S1–S9** from
`signals-reference.md` to all sessions in scope. Use **Q2–Q5** from `cypher-patterns.md`.

For each signal compute:
- Prevalence in **failing** runs (% of failing sessions where signal fires)
- Prevalence in **successful** runs (% of successful sessions where signal fires)
- **Delta** — the gap between the two rates

The delta is the primary finding. Signals with high delta discriminate between failure
and success for this workflow. Signals that fire equally in both groups are structural
characteristics of the workflow type, not failure indicators.

Classify each run by outcome before scoring:
- **Successful**: `status = completed`, compound signal score = 0
- **Degraded**: `status = completed`, compound signal score ≥ 1
- **Failed**: `status = cancelled` OR (`status = running` AND stale > 2h), score ≥ 2

Write raw signal scores to `findings/session-signal-scores-raw.md`.

**Success criteria**: Per-signal prevalence table with delta column. Top compound-failure
sessions ranked. At least one signal with delta ≥ 20 percentage points between failing
and successful groups.

### 4. Produce initial structured artifacts

Delegate to `context-intelligence:context-intelligence-design-facilitator` with the raw
findings from Steps 2–3.

Instruct the facilitator to write:
- `{{output_path}}/findings.md` — patterns (P1–Pn) with severity, frequency, mechanism,
  cross-cutting structural findings (Xn), and candidate skill implications (Cn)
- `{{output_path}}/domain-signals.md` — signal definitions calibrated to this workflow
  with corpus-anchored thresholds
- `{{output_path}}/session-index.md` — full session inventory with root/sub split,
  outcome classification, and compound failure scores
- `{{output_path}}/diagrams/failure-patterns.dot` — taxonomy with severity swimlanes and
  prevalence numbers on pattern nodes

**Success criteria**: All four artifacts written and internally consistent. Corpus numbers
and signal thresholds consistent across all files.

### 5. Iterative investigation loop

Continue investigating specific patterns as the user steers. Each round follows the
same dual-agent shape — repeat as many times as needed:

**a.** Delegate to `context-intelligence:graph-analyst` with the specific question.
Analyst writes raw findings to `findings/`.

**b.** Delegate to `context-intelligence:context-intelligence-design-facilitator` to
update the structured artifacts. Facilitator calls do not need to be 1:1 with analyst
calls — consolidate related findings into a single facilitator update.

---

**Common investigation rounds and their targets:**

**Signal calibration** — when a signal fires at an unexpectedly high or low rate,
inspect actual JSONL event payloads before adjusting any threshold. Key field reference
(from `signals-reference.md` §Authoritative JSONL field reference):
- `data.tool_name`, `data.tool_input`, `data.tool_call_id`, `data.parallel_group_id`
- `data.result.{success, error, output}` (tool:post only)
- Hash `(tool_name, tool_input)` to test for S4c — literal same-input essentially never
  fires in stuck sessions. Use S4a (parallel-group shape concentration) and S4b (ritual
  instrumentation-bash share) as primary stuck-agent detectors.

**Compaction causal analysis** — inspect `context:compaction` event payload (ground truth:
`before_tokens`, `after_tokens`, `budget`, `strategy_level`). Inspect the last `llm:request`
before each compaction for context composition — extract character lengths per region,
never render full message content:
- Regions: `messages.tool_result`, `messages.thinking`, `system`, `tools`, `messages.text`
- `strategy_level ≥ 3` = canary: strategy 2 already failed
- Two etiologies: P1a tool-result accumulation (delegation-driven, skill-addressable) vs
  P1b thinking-block accumulation (interleaved_thinking=true, model-config issue)

**Delegation pattern analysis** — inspect `delegate tool:post` result envelope:
`{agent, response, session_id, status}`. Measure `len(result.response)`.
Delegation is the gating condition for severe compaction: no root session reaches
S1 (≥3 compactions) from pure file reads alone. Synthesis-output sub-agents
(result > 30K chars) are the compaction multiplier.

**Success criteria per round**: Raw findings written, structured artifacts refreshed,
signal thresholds validated against corpus evidence rather than assumed.

### 6. Produce Cypher query set for ongoing monitoring

Delegate to `context-intelligence:graph-analyst` to produce a reusable, workflow-specific
Cypher query set using templates from `cypher-patterns.md`:

- **Q-enum**: Enumerate all runs with outcome classification
- **Q-signals**: Detect each high-delta failure signal
- **Q-compare**: Compare signal prevalence between successful and failing runs
- **Q-risk**: Identify in-progress sessions at risk (trajectory)
- **Q-points**: Failure point localisation — events preceding failure signals

Write the query set to `{{output_path}}/queries/{{workflow}}-cypher.md`.

**Success criteria**: Working Cypher queries tested against the graph server, scoped to
this workflow's session definition.

### 7. Close the investigation

Verify the final artifact state is consistent:
- `findings.md` — all patterns have corpus-anchored prevalence and causal mechanisms
- `domain-signals.md` — all thresholds validated against actual JSONL payloads, not assumed
- `session-index.md` — inventory reflects the final corpus scope and outcome classification
- `diagrams/failure-patterns.dot` — updated and re-rendered to PNG

**Success criteria**: Complete, consistent findings package ready to inform skill design
decisions for the context-intelligence bundle or any downstream action.

---

## Standing Rules

1. **Enumerate the full corpus before investigating.** Anchor all signals to
   population-level denominators. Do not sample and claim corpus-level conclusions.

2. **Graph-first; disk as explicit mode.** Both available via graph-analyst. Offer disk
   scan when the user needs full coverage or suspects under-indexing.

3. **Always split root vs sub-sessions.** Roots and subs have structurally different
   failure profiles. Separate denominators for all prevalence calculations.

4. **Always compare failing vs successful runs.** The delta between groups is the finding.
   Signals that fire in both groups equally are workflow characteristics, not failure signals.

5. **JSONL-observable behavioral signatures only — never named-agent filters.** Express
   every pattern as event field conditions observable in any session (see `signals-reference.md`
   §Behavioral classification). Named instances are traceability evidence only.

6. **S4 family only.** The consecutive-same-tool-name heuristic is retired (48% FPR at
   corpus scale). Use S4a, S4b, S4c, S4d from `signals-reference.md`.

7. **Facilitator is the mandatory quality gate.** Every wave of raw analyst findings must
   pass through the facilitator before being treated as curated findings. Facilitator calls
   do not need to be 1:1 with analyst calls — consolidate related findings.
