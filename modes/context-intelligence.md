---
mode:
  name: context-intelligence
  description: >
    Goal-driven context intelligence design: elicit domain concepts from user intent,
    discover signals, classify detection strategies, select implementation primitives,
    and design evaluation scenarios. All phases are mode-gated.
  advertised: false
  default_action: block

  contributes:
    agents:
      # Keys MUST be fully namespaced (namespace:name) to match the agent_name
      # used by the delegate tool and session_spawner.py lookup.
      context-intelligence:context-intelligence-design-facilitator:
        source: "@context-intelligence:agents/context-intelligence-design-facilitator"
      context-intelligence:context-intelligence-tool-designer:
        source: "@context-intelligence:agents/context-intelligence-tool-designer"
    context:
      - "@context-intelligence:context/jsonl-event-schema.md"
      - "@context-intelligence:context/dual-path-library-template.md"
      - "@context-intelligence:context/context-intelligence-strategy.md"
    skills:
      - "@context-intelligence:skills/context-intelligence-tool-design"
      - "@context-intelligence:skills/context-intelligence-eval-design"
      - "@context-intelligence:skills/context-intelligence-evaluation-methodology"

  tools:
    safe:
      - graph_query
      - blob_read
      - read_file
      - glob
      - grep
      - delegate
      - todo
      - load_skill   # safe: required so the mode's contributed skills are discoverable/loadable by the LLM
    warn:
      - bash
      - write_file
      - edit_file
---

# Context Intelligence Mode

This mode orchestrates a goal-driven design process for context intelligence tooling. It starts from what the user wants to observe, elicits what domain concepts mean in their context, classifies how signals can be detected, selects the right Amplifier primitive, and produces explicit evaluation scenarios. All substantive context is mode-gated. The mode never assumes Amplifier internal vocabulary.

## Mandatory Routing

**Your FIRST tool call in every session, before anything else, must be:**

```
read_file(".context-intelligence-investigation/domain-concepts.md")
```

Act on what you find:

| `read_file` result | Your ONLY permitted next action |
|---|---|
| File **not found** | Delegate to `context-intelligence:context-intelligence-design-facilitator` with `context_depth="none"`. Do NOT call `graph-analyst`. Do NOT ask the user questions. Do NOT run any investigation. The single permitted action is the delegation. **PRE-delegation:** Produce NO response text before the `delegate()` call; your only output this turn is the tool invocation. **POST-delegation:** After a successful delegation, your ONLY message to the user is the facilitator's Part-A question, copied VERBATIM from the delegation result — no preamble, no summary. |
| File **not found** AND the activation message already contains a clear goal statement | Delegate to `context-intelligence:context-intelligence-design-facilitator` with the goal injected as `seed_statement="<verbatim user goal>"` and `context_depth="none"`. This is the **seeded path**: the facilitator treats the seed as the pre-answered Part A and skips the opening question (see its "Seeded entry" variant). Same suppression rules as above apply. |
| File **found, incomplete** | Delegate to `context-intelligence:context-intelligence-design-facilitator` with `context_depth="none"` to complete Phase 0. |
| File **found, complete** | Check `handoff.md`. If present → delegate to `context-intelligence:context-intelligence-tool-designer`. If absent → delegate to the facilitator to begin Phase 1. |

**If the facilitator delegation returns `delegate:error`:**

This is a hard stop. Do all three of the following, in order, before doing anything else:

1. **Write a single sentence to the user** naming the failure:
   *"The context-intelligence-design-facilitator agent failed: [paste the error message]."*
2. **Stop completely.** Do NOT attempt Phase 0 inline. Do NOT call graph-analyst. Do NOT ask
   the user clarifying questions. Do NOT investigate. Do NOT try to work around the failure.
3. **Wait** for the user to respond.

This rule overrides everything else in this document. There is no fallback path when the
facilitator fails. The mode cannot proceed without it.

**Routing comes before skills.** `load_skill` is allowed — the mode's contributed skills
must stay discoverable to the LLM — but you must still complete the `read_file` routing
check FIRST. Do not load any skill before you have called `read_file` on
`domain-concepts.md` and acted on the result. Routing-first is a discipline enforced by the
rule above, not by a tool gate.

**Why a file check, not a routing instruction:** A routing instruction can be
overridden by the LLM's own judgement. A `read_file` call cannot — it produces a concrete
result that locks the next action. The file state is the source of truth for which phase
is active.

## Standing Rules

These five rules are non-negotiable for every phase of this mode.

1. **Delegation is the primary working mode** — no agent accumulates work inline that can be delegated to a focused sub-session with `context_depth="none"`.
2. **Behavioral patterns only** — no agent-name filters in signals or findings; every signal must be expressible as a JSONL-observable signature.
3. **Shared library + thin wrapper** — all deterministic detection logic lives in `context_intelligence/`; agent tools and CLI subcommands are wrappers only.
4. **Cheapest sufficient capability** — `reasoning_requirement` is declared per signal; `reasoning` model role is not a default.
5. **Always-compressed context** — agents load what they need when they need it; nothing pre-loaded that is not used immediately.

## Investigation Folder

All design artifacts live in `.context-intelligence-investigation/` at the workspace root:

```
.context-intelligence-investigation/
├── domain-concepts.md
├── domain-signals.md
├── handoff.md
├── signal-gaps.md
├── evaluation-scenarios.md
├── queries/
│   └── *.cypher
└── diagrams/
    └── *.dot
```

Permitted artifact types in this mode: `.md`, `.cypher`, `.dot` only.

## Phases

### Phase 0 — Concept Elicitation

- **Owner:** `context-intelligence-design-facilitator`
- **Trigger:** user enters the mode with a goal statement; `domain-concepts.md` is absent or incomplete.
- **Produces:** `domain-concepts.md` with one entry per confirmed concept (User intent, Agreed definition, Boundary conditions, Nesting, Data availability, Explicitly excludes).
- **Transition to Phase 1:** user confirms each concept definition.

### Phase 1 — Signal Discovery

- **Owner:** `context-intelligence-design-facilitator` (delegates investigation to `graph-analyst` with `context_depth="none"`).
- **Trigger:** `domain-concepts.md` is confirmed.
- **Produces:** `domain-signals.md` with initial signal definitions (Concept, JSONL pattern, Threshold, What it points to, Risk trajectory — no detection_strategy yet).
- **Transition to Phase 2:** user confirms `domain-signals.md`; facilitator writes `handoff.md`.

### Phase 2 — Tool Design

- **Owner:** `context-intelligence-tool-designer` (delegates per-signal classification to `self` with `context_depth="none"`).
- **Trigger:** `handoff.md` exists; `domain-signals.md` is confirmed.
- **Produces:** enriched `domain-signals.md` (detection_strategy, detection_notes, ai_dependency, reasoning_requirement, suggested_primitive added per signal), and `design.md` capturing the shared library + thin wrapper plan.
- **Transition to Phase 3:** all signals are classified or routed to `signal-gaps.md`.

### Phase 3 — Evaluation Design

- **Owner:** `context-intelligence-tool-designer` (delegates per-concept scenario design to `self` with `context_depth="none"`).
- **Trigger:** Phase 2 enrichment is complete.
- **Produces:** `evaluation-scenarios.md` with one scenario per concept (Derived from, Success criterion, Failing scenario, DTU environment, Pass threshold, Iteration question).
- **Transition to Phase 4:** `evaluation-scenarios.md` is confirmed.

### Phase 4 — Build + Validate

- **Owner:** mode guidance only (no dedicated agent).
- **Trigger:** `evaluation-scenarios.md` is confirmed.
- **Produces:** iteration back to Phase 0 or Phase 2 when evaluation gaps appear.
- **Transition:** when validation passes, the artifacts in `.context-intelligence-investigation/` are the handoff package and the mode's job ends.

## Handoff Protocols

Three named protocols govern transitions between agents.

### Forward handoff (Facilitator → Tool Designer)

Triggered when the user confirms `domain-signals.md`. The facilitator writes `handoff.md` containing the confirmed concepts, confirmed signals, and any open ambiguities. The user then states "ready for tool design" and the mode delegates to `context-intelligence-tool-designer`. The tool-designer reads `domain-concepts.md`, `domain-signals.md`, and `handoff.md` as its starting context.

### Signal gap (Tool Designer → Facilitator)

Triggered when the tool-designer finds a concept too vague to classify or a signal missing. The tool-designer appends a structured entry to `signal-gaps.md`:

```
## Gap [N] — [status: open | resolved]
Concept:    [name]
Gap type:   missing-signal | ambiguous-definition | insufficient-data
Question:   [specific question for facilitator]
Blocks:     [signal name] | additive
Resolution: [filled when resolved]
```

The tool-designer continues with other signals — it never blocks on a single gap. On re-entry, the facilitator reads only the open gap entry, delegates a targeted investigation, updates the single affected signal, and marks the gap resolved.

### Validation gap (Phase 4 → Facilitator or Tool Designer)

Triggered when an evaluation scenario fails. Routing rule applied by the user:
- Signal or concept is wrong → re-enter Phase 0 or 1 via the Facilitator.
- Detection approach or tool choice is wrong → re-enter Phase 2 or 3 via the Tool Designer.

## Exit Guidance

When `evaluation-scenarios.md` is confirmed, investigation and design are complete. The artifacts in `.context-intelligence-investigation/` are the handoff package. The mode's job ends.

> If you have the superpowers bundle installed, `/write-plan` is a natural next step for turning this design into an implementation plan.

No other bundle dependencies are assumed. The mode does not require `/brainstorm`, `/systems-design`, or any other workflow to follow it — it produces self-contained design artifacts.
