# Workflow Pattern Signals — Reference

JSONL-observable signal definitions for detecting failure and degradation patterns
in context-intelligence session data. All signals are detectable from `events.jsonl`
on disk and queryable via the CI property graph.

Companion to: `workflow-pattern-analysis/SKILL.md`

---

## Authoritative JSONL Field Reference

Verified field names from `tool:pre` and `tool:post` events in `events.jsonl`.

**`tool:pre` and `tool:post` shared fields:**

| Field | Type | Meaning |
|---|---|---|
| `data.tool_name` | string | Tool identifier: `"bash"`, `"read_file"`, `"grep"`, `"delegate"`, … |
| `data.tool_input` | dict | Tool-specific args: `{command}`, `{file_path}`, `{agent}`, `{pattern}`, … |
| `data.tool_call_id` | string | `"toolu_…"` — stable ID joining `tool:pre` ↔ `tool:post` |
| `data.parallel_group_id` | UUID | Groups calls fired in the same parallel batch |

**`tool:post` additional fields:**

| Field | Type | Meaning |
|---|---|---|
| `data.result.success` | bool | True on success |
| `data.result.error` | null \| object | Error detail on failure |
| `data.result.output` | string \| dict | Tool output payload |

**`context:compaction` event fields** (ground truth for P1 analysis):

| Field | Meaning |
|---|---|
| `before_tokens` / `after_tokens` | Token count before and after compaction fires |
| `budget` | Total model token budget (e.g. 963,104 for Claude Sonnet) |
| `strategy_level` | 1 / 2 / 3 — **level 3 = strategy 2 already failed** (cheapest canary) |
| `messages_removed` / `messages_truncated` | Compaction aggressiveness |
| `target_tokens` | Target size after compaction (~50% of budget) |

Compaction trigger threshold: ~80% of `budget`. Each compaction attempt targets ~50%.

---

## Signal Set

### S1 — Context Compaction Storm (Pattern: Forgetful Marathon)

```bash
grep -c '"context:compaction"' <events.jsonl>
```

| Threshold | Severity |
|---|---|
| ≥ 3 | Candidate |
| ≥ 10 | Severe |

**Graph field**: `Event.event_type = 'context:compaction'`

**Scope**: Root sessions only. Compaction never appears in sub-sessions — they inherit
fresh context windows and exit before saturating.

**What it points to**: The session accumulated so much content in its context window that
the system had to force-compress it to continue. This is almost always caused by large
delegate results returning from sub-agents — each delegation pours its full synthesised
output back into the root context as a single tool result. After several such rounds the
window fills to ~80% of the token budget and compaction fires. Each compaction discards
older messages, so the agent progressively loses its earlier work and may start repeating
itself or forgetting prior findings.

**Risk trajectory**: Sessions with S1 ≥ 3 frequently spiral — each compaction causes the
agent to re-investigate things it already covered, generating more tool results, which
triggers the next compaction sooner. Left unaddressed the session eventually stalls or
produces incoherent output.

**Etiology split** (determines intervention surface):
- **P1a — Tool-result accumulation**: `tool_result` share of last `llm:request` before
  compaction ≥ 40%. Delegation is the gating condition — no root session reaches S1 from
  pure file reads alone. `delegate result.output` size drives most compaction storms.
  Skill-addressable.
- **P1b — Thinking-block accumulation**: `thinking` block share ≥ 30%. Present when
  `interleaved_thinking=true` — all prior thinking blocks are preserved across turns and
  never trimmed by compaction. Model-config intervention, not skill-addressable.

---

### S2 — Session Resume Storm (Pattern: Forgetful Marathon amplifier)

```bash
grep -c '"session:resum\|"session:restore' <events.jsonl>
```

| Threshold | Severity |
|---|---|
| ≥ 3 | Candidate |

**What it points to**: The session was interrupted and resumed multiple times. Each resume
reloads the prior transcript but without any tool results that were in-flight, creating
gaps in continuity. Frequent resumes suggest the session was fragile — either the user
was interrupted, a network connection dropped, or the agent crashed and had to be
restarted. Sessions with many resumes often produce inconsistent output because each
resume starts with slightly different context.

**Risk trajectory**: High resume counts compound with S1 (compaction) — each resume adds
another session:start event and may skip compaction state, leaving the agent unaware of
how much context has already been consumed.

---

### S3 — LLM Iteration Runaway (Pattern: Stuck Sub-Agent)

```bash
grep -c '"orchestrator:iteration\|"llm:response"' <events.jsonl>
```

| Threshold | Severity |
|---|---|
| ≥ 20 | Candidate |
| ≥ 40 | Severe |

**Graph field**: `Event.event_type CONTAINS 'orchestrator:iteration'`

**What it points to**: The session made far more LLM calls than a focused, converging
workflow requires. A healthy scoped task typically uses 5–15 LLM calls. When S3 fires
at candidate (≥ 20) the agent is likely exploring without a clear stopping condition —
reading files, running commands, and asking the LLM for the next step without ever
declaring "done". At severe (≥ 40) the agent is almost certainly stuck in a loop:
each LLM response produces a new tool call, whose result produces another LLM call,
with no convergence toward a deliverable.

Common causes:
- **Goal too vague**: The agent doesn't know what "done" looks like so it keeps exploring
- **Exploration without exit**: Searching a codebase without a specific target
- **Delegation fan-out**: Each sub-agent returns a result that triggers more questions
- **LLM keeps generating new sub-tasks**: The model keeps elaborating instead of concluding

**Risk trajectory**: S3 co-fires with S1 (compaction) when the high iteration count
produces large tool results that fill the context window. It co-fires with S8 (bash
bursts) in the "investigation runaway" pattern. High S3 is the primary leading indicator
of eventual context saturation even when S1 hasn't fired yet.

---

### S4 Family — Tool Call Patterns (Pattern: Stuck Sub-Agent)

> **The naive "consecutive same tool name" heuristic is retired** — 48% false-positive
> rate at corpus scale (legitimate parallel bulk reads trigger it). Literal
> `(tool_name, tool_input)` identity also never fires in stuck sessions — stuck agents
> vary their inputs on each call. Use the four sub-signals below.

#### S4a — Exploration Loop (primary detector, parallel fan-out variant)

Session matches if **all** of:
1. `count(tool:pre) ≥ 20`
2. `multi_tool_groups / total_groups ≥ 0.30`
   where `multi_tool_group` = `parallel_group_id` shared by ≥ 2 `tool:pre` events
3. Top parallel-group shape (sorted multiset of `tool_name + bash-first-word | file-ext`)
   accounts for ≥ 15% of all parallel groups
4. Top shape is an exploration shape: `bash:find`, `bash:grep`, `bash:ls`,
   `read_file:.<ext>` repeated within the multiset

Fires on: high parallel fan-out with non-converging exploration strategy.
Does not fire on: sequential scoped-task sub-agents (≤ 2.6% multi-tool groups observed).

#### S4b — Ritual Volume (primary detector, serial variant)

Session matches if **all** of:
1. `count(tool:pre) ≥ 40`
2. ≥ 30% of all `bash` tool:pre events have first non-comment token ∈
   `{echo, STEP, Step, Check, Note}` (instrumentation commentary, not work)
3. S3 also fires (iteration count ≥ 20)

Fires on: sessions that narrate each step as a bash echo before executing it.

#### S4c — Exact-Duplicate Guardrail (precision booster)

Same `(tool_name, tool_input)` signature appearing **≥ 4 times** anywhere in the session.
Near-zero false-positive rate. Catches true retry-loop bugs where a call doesn't change state.

```python
import json, hashlib
sigs = {}
for line in open(events_path):
    e = json.loads(line)
    if e.get('event') == 'tool:pre':
        d = e['data']
        key = hashlib.md5(json.dumps(
            (d.get('tool_name'), d.get('tool_input', {})),
            sort_keys=True).encode()).hexdigest()
        sigs[key] = sigs.get(key, 0) + 1
fires = any(v >= 4 for v in sigs.values())
```

#### S4d — No-Progress Repetition (highest precision)

Same `(tool_name, tool_input)` hash AND same `(result.success, sha256(result.output))`
hash appearing **≥ 3 times**. "Asking the same question, getting the same answer."
Rarest signal — near-zero at corpus scale unless a genuine loop bug exists.
Output identity alone is unreliable (different inputs can return the same error string) —
only meaningful when paired with input identity.

---

### S5 — Stale Running (Pattern: Quiet Quit)

`metadata.json` `status == "running"` AND `last_event_at` more than 2 hours before
the most recent session's `last_event_at` in the corpus.

Not queryable via event grep — derive from `metadata.json` fields.

---

### S6 — Explicit Cancellation (Pattern: Quiet Quit)

```bash
grep -c '"session:cancel\|"user:interrupt' <events.jsonl>
```
Threshold: ≥ 1.

**What it points to**: The session was stopped before it finished — either by the user
pressing Ctrl-C, by a timeout, or by the orchestrator hitting a hard limit. A single
cancellation (count = 1) is often benign (user interrupted to adjust direction). Multiple
cancellations (count ≥ 3) suggest a recurring problem: the agent is either taking too
long on a sub-task, producing output the user doesn't want, or stuck in a state that
requires manual intervention to recover.

When S6 fires on a *sub-session* and the sub-session was later retried, it typically
means the parent agent gave the sub-agent a poorly-scoped task (too broad, wrong tools,
insufficient context) and had to restart it.

**Risk trajectory**: S6 combined with S3 (high iterations) is the classic "I had to kill
it" pattern — the agent was looping and the user terminated it manually. The session may
have produced partial results that are inconsistent with the final state.

---

### S7 — Shotgun Read Bursts (Secondary)

≥ 5 `read_file` `tool:pre` events within the same `parallel_group_id`, OR
≥ 5 consecutive `read_file` `tool:pre` events in sequence.

```bash
# First-pass count proxy
grep '"tool:pre"' <events.jsonl> | grep -c '"read_file"'
```
Threshold for proxy: ≥ 10 total `read_file` tool:pre = candidate for burst inspection.

---

### S8 — Sustained Bash Bursts (Secondary)

≥ 15 `bash` `tool:pre` events total as a proxy for sustained bash exploration bursts.

```bash
grep '"tool:pre"' <events.jsonl> | grep -c '"bash"'
```

**What it points to**: The session spent a disproportionate amount of time running shell
commands. This is a secondary signal — bash calls are often legitimate work. But when S8
fires alongside S3 (high LLM iterations), it reveals the "investigation runaway" pattern:
the agent repeatedly runs bash commands (find, grep, ls, git log) and feeds the results
back to the LLM, which generates more bash commands. The session is exploring rather than
executing — searching for something without a clear target, or profiling a system without
a concrete question to answer.

When S8 fires *without* S3, the bash calls are more likely to be meaningful work (e.g.,
a git-ops sub-agent committing changes). Context matters.

**Risk trajectory**: S8 + S3 is the signature of an exploration loop that will either
resolve (the agent finds what it's looking for) or exhaust the context budget. It is
not inherently fatal but signals the session may run significantly longer than necessary.

---

### S9 — Delegation-Driven Compaction (Leading Indicator for P1a)

**S9 fires in 7 of 13 severe compaction sessions before the first compaction event —
it is a leading indicator, not a lagging one.**

#### S9a — High delegation count
`count(delegate tool:pre) ≥ 5` in a root session.

**What it points to**: The root session orchestrated many sub-agents. Delegation itself
is not a problem — it is the intended pattern for complex work. S9a becomes significant
when combined with S1 or S3, because each delegation pours its full synthesised response
back into the root session's context as a single large tool result. A root session with
25 delegations, each returning a 10–30 KB response, accumulates 250–750 KB of tool
results in its context — enough to trigger compaction even on a large-context model.

S9a is the structural precondition for the Forgetful Marathon (S1). It does not cause
problems on its own, but it determines the ceiling for how much context can be consumed.

**Risk trajectory**: S9a + large delegate result sizes (S9b) = near-certain compaction
storm in long sessions. Monitor S9a as an early warning and inspect delegate result sizes
before the first compaction fires.

```bash
grep '"tool:pre"' <events.jsonl> | grep -c '"delegate"'
```

#### S9b — Synthesis-output delegate result
Any `delegate tool:post` where:
- `len(data.result.output) > 30,000 chars` AND
- `data.result.output` is not JSON-parseable AND
- Markdown code-fence density < 5% of total chars (predominantly narrative prose)

The delegate result envelope is: `{agent, response, session_id, status}`.
The `response` field carries the synthesised narrative. Sub-agent raw tool calls
and intermediate thinking do NOT propagate to the parent — only the final response does.

#### S9c-self — Recursive self-delegation
Any `delegate tool:pre` where `data.tool_input.agent == "self"`.
This is a structural JSONL field check, not a name filter — portable across any session.

**Corpus finding**: No root session reached S1 (≥ 3 compactions) from pure file reads
alone. Delegation is the gating condition. Correlation: `delegate_calls` r=0.37 vs
`read_file_calls` r=0.15 with compaction count.

---

## Behavioral Classification

Apply throughout. **Never use named-agent filters** — patterns must be expressible as
event field conditions observable in any session, past or future.

| Behavioral class | JSONL-observable signature |
|---|---|
| **Synthesis-output sub-agent** | `delegate result.output > 30K chars` AND not JSON-parseable AND code-fence density < 5% |
| **Scoped-task sub-agent** | `count(tool:pre) ≤ 10 AND count(llm:request) ≤ 8 AND max(result.output) < 10K` |
| **Parallel-exploration-burst sub-agent** | S4a fires |
| **Recursive self-delegation** | `tool_input.agent == "self"` (structural field check) |
| **High-volume compound-failure class** | `session_count > 200 AND compound_failure_rate > 25%` — derive from corpus at analysis time, do not hardcode |

Named agents appearing in findings are **traceability evidence only** — they document
which instances motivated a signal threshold. They are never used as selectors in queries.

---

## Disk Scanning Reference

When the graph server is under-indexed or the user needs comprehensive on-disk coverage:

```bash
# Discover all CI-compatible sessions on disk
find ~/.amplifier/projects -path "*/context-intelligence/metadata.json" 2>/dev/null
```

Filter each metadata.json to `format == "context-intelligence"` AND `version == "1.0.0"`
before treating the session as part of the corpus.

**Safe extraction rules** (from `@context-intelligence:context/safe-extraction-patterns.md`):
- Never load full `llm:request` lines — use grep -c for counts, streaming python with
  `json.loads(line)` for field extraction
- Extract sizes only from large fields: `len(str(data.get('system','')))`,
  `len(str(result.get('output','')))`
- Use `-c` flag for all grep counts to avoid printing matching lines
