---
name: context-intelligence-tool-design
version: 1.0.0
description: Detection strategy classification and primitive selection for context-intelligence signals. Classifies each signal as deterministic, probabilistic, llm-evaluated, or hybrid. Selects the correct Amplifier primitive using the cheapest-sufficient-capability principle. Applies the shared library + thin wrapper pattern for deterministic and probabilistic signals. Declares reasoning_requirement and model_role for LLM-evaluated signals. Designed to be loaded by the context-intelligence-tool-designer at Phase 2 entry, in a sub-session with context_depth="none", scoped to exactly one signal at a time.
user-invocable: false
allowed-tools: read_file, glob, grep, delegate, load_skill, todo
model_role: reasoning
license: MIT
---

# Context Intelligence Tool Design

Phase 2 specialist skill, called by the context-intelligence-tool-designer agent via self-delegation with `context_depth="none"`, scoped to one signal (or one logical batch of related signals) at a time.

## Companion Reference

@context-intelligence:context/context-intelligence-primitives-reference.md

The companion contains:
- Primitive taxonomy
- Reduce-AI-dependency decision order
- Shared library + thin wrapper pattern
- Routing matrix roles

Treat as authoritative reference — do not duplicate.

## Scope

### In Scope

- Classify detection strategy for a signal (deterministic / probabilistic / llm-evaluated / hybrid)
- Select the correct Amplifier primitive for the signal
- Populate the five enrichment fields: `detection_strategy`, `detection_notes`, `ai_dependency`, `reasoning_requirement`, `suggested_primitive`

### Out of Scope

- Investigating signals
- Refining concept definitions
- Designing evaluation scenarios

Anything that cannot be classified due to a vague concept → return a structured gap entry, do not investigate.

## Per-Signal Classification Process

### Step 1: Read the Signal Entry

Read the signal entry from `domain-signals.md` — read only that entry, not the full file.

### Step 2: Apply Detection Strategy Tier Decision

Apply detection strategy tier decision in the following exact order:

**Deterministic first** — event structure parsing only:
- Event type presence
- Field value match
- Count threshold
- Time window
- Sequence

**Probabilistic second** — pattern matching + thresholds:
- Regex
- Ratio
- Shape
- Size threshold

**LLM-evaluated only if neither deterministic nor probabilistic works.** Before committing to pure LLM-evaluated, ask whether deterministic feature extraction can narrow scope. If yes → choose hybrid.

### Step 3: Shared Library + Thin Wrapper (Deterministic / Probabilistic)

For deterministic or probabilistic signals, specify three surfaces of the shared library + thin wrapper pattern:

1. **Shared library function**: `context_intelligence/{signal_name}.py`
2. **Thin module wrapper**: `modules/tool-{signal-name}/`
3. **CLI subcommand**: `scripts/context-intelligence.py --{subcommand-name}`

### Step 4: LLM Signal Declaration (LLM-evaluated / Hybrid)

For LLM-evaluated or hybrid signals:
- Declare `reasoning_requirement` using routing matrix: `fast` / `general` / `reasoning` / `coding`
- Specify the corresponding `model_role` in the artifact recommendation
- For hybrid signals: deterministic feature extraction lives in the shared library; the classification call is LLM-dependent

### Step 5: Populate `suggested_primitive`

Populate `suggested_primitive` with a complete implementation path:
- **Code-based signals**: concrete file paths
- **LLM-based signals**: artifact shape (skill / agent with `model_role` / recipe step)

## Detection Strategy Decision Table

| Strategy | Definition | Code Pattern |
|---|---|---|
| Deterministic | Event structure parsing only — no ML, no thresholds, no regex | Field access, type check, count compare, time diff, sequence match |
| Probabilistic | Pattern matching + configurable thresholds — regex, ratio, shape, size | `re.match`, ratio calc, size compare, configurable threshold |
| LLM-evaluated | Requires language understanding, semantic judgment, or rubric evaluation | Skill or agent call with `model_role` declaration |
| Hybrid | Deterministic feature extraction narrows scope, then LLM classifies | Shared lib extracts features; LLM call receives structured input |

## Output Format

The following fields are ready to write into `domain-signals.md`:

- `detection_strategy` — one of: `deterministic` / `probabilistic` / `llm-evaluated` / `hybrid`
- `detection_notes` — brief rationale for the chosen strategy
- `ai_dependency` — `none` / `optional` / `required`
- `reasoning_requirement` — routing matrix role: `fast` / `general` / `reasoning` / `coding` (omit for deterministic/probabilistic)
- `suggested_primitive` — complete implementation path or artifact shape

## Gap Handling

If a signal cannot be classified due to ambiguity (missing definition, unspecified boundary, insufficient probe data):

**DO NOT investigate or guess.**

Return a structured gap entry with the following fields:

```yaml
- Concept: <signal name>
  Gap type: missing-signal | ambiguous-definition | insufficient-data
  Question: <specific question that must be answered to proceed>
  Blocks: <what classification decision is blocked>
  Resolution: ""
```

The tool-designer appends the gap entry to `signal-gaps.md` and continues with other signals. Gaps never block progress.
