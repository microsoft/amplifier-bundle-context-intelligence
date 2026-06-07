# Context Intelligence Primitives Reference

Compressed reference for choosing the right Amplifier primitive when implementing a context-intelligence signal detector. Loaded on demand by the `context-intelligence-tool-design` skill as a companion file at Phase 2 entry. Not pre-loaded by any agent. Not a mode `@mention`.

## Why This Reference Exists — Design Philosophy

The rules below are not abstract preferences. They were stated by the user as the foundational design brief for context-intelligence tooling, and reinforced through corrections every time the agent over-reached. **Read this section first.** It captures the reasoning under which every subsequent rule makes sense; without it, the rules can be followed mechanically but applied wrongly.

### The foundational direction

> "The discovery of the signal must be driven by what the user is trying to achieve, and this also means not giving as a fact that the terms used map directly to any of the Amplifier concepts."

> "Some signals can be identified deterministically (parsing, event type, start-end delimiter) but others might need a more fuzzy approach that could require some LLM work. For example, to identify user steering means to look for prompt submissions but then evaluate the payload and see for 'strong user steering due to failure to converge'."

> "Part of the `context-intelligence` mode should include guidance for tool creation — what to use: context files, agents, skills, bespoke tools, or recipes — all primitives that can help address the goal to transform signal definitions and metrics in an efficient and contextualised process."

> "In such guidance we want to drive for reducing AI dependency, meaning that having the right coded tools or scripts or CLI tooling to reuse or combine with recipes provides on-rail execution of the detection or remediation."

### One-paragraph synthesis

Your purpose is to turn signal definitions into an **efficient, contextualised process** by selecting the right primitive for each signal. Prefer **deterministic code** wherever the signal is structural; reach for **LLM** only where the signal is genuinely semantic; specify **which** `model_role` when LLM is justified. The **recipe is glue** that orchestrates primitives — not a substitute for them. Where code is chosen, **one implementation lives in a shared library** exposed through three thin wrappers (agent tool, CLI, module). The runtime must work **without the graph** and **without design-time agents**; dual-mode-with-disk-fallback is mandatory. Everything you produce is **gated behind the mode**. Domain terms are **never assumed equal to Amplifier internal types** — clarify the domain before classifying.

### What "on rail" means (and why it matters)

LLM steps are off-rail by definition: each call introduces non-determinism, latency variance, and a cost that scales with context size. Deterministic code is on-rail — same input, same output, fast, cheap, reproducible. The phrase *"on rail execution"* is the user's. Apply it as a heuristic:

- When the signal's truth lives in the **structure** of events (a count, a type presence, a sequence, a timestamp comparison, a regex on a stable field) → on-rail. Deterministic code.
- When the signal's truth lives in the **semantics** of natural-language payload content (intent, quality, judgment of "convergence vs flounder") → off-rail by necessity. LLM justified.
- When the signal has **both** halves → split the signal. Deterministic filter narrows the candidate set; LLM judges only the narrowed payload content. This is the **hybrid** path and is strongly preferred over pure LLM evaluation.

The user's worked example, verbatim:

> "To identify user steering: look for prompt submissions, then evaluate the payload and see for 'strong user steering due to failure to converge'."

Two halves: find prompt submissions = deterministic event-type filter; judge "strong steering" = LLM. Split, then route each half to its cheapest sufficient primitive.

### Recipes are glue, not value

A recipe is an orchestration container. Its value is in the primitives it composes — shared-library functions, CLI invocations, agent tool calls. A recipe with an LLM prompt at every step is a token-bill, not a tool. A recipe that wraps deterministic Python with a single LLM-judgment step at the right place is a lever.

The user is cost-accountable: *"How much token usage goes in the current run vs doing it with skill only?"* This is a real test. Before you suggest a recipe step that calls an LLM, ask whether the same outcome could be reached by a deterministic Python step calling the shared library. If yes, prefer the deterministic step.

### Cheapest-sufficient as a downward reflex

The user's design corrections in the prior session followed one shape: **the agent reached up the tier ladder, the user pulled it down**. Examples, verbatim from the corrections:

| Agent over-reached | User pulled down to |
|---|---|
| "Delegate to `context-intelligence-design-facilitator` for findings authoring." | *"The facilitator agent won't be available in runtime or production environments; the graph analyst is the only one you can rely upon."* And the Python tooling must work without even that. |
| "Query the graph (Cypher only)." | *"The Python tooling code must be able to function in the absence of the Cypher endpoint."* Data is on disk under `~/.amplifier/projects/`. |
| "LLM-evaluate the whole user-steering signal." | *"look for prompt submissions"* (deterministic) *"but then evaluate the payload and see for 'strong user steering due to failure to converge'"* (LLM, but only on the narrowed payload). |

The downward reflex is the operative habit: **before recommending a primitive at tier N, ask whether tier N−1 would suffice. Repeat until the answer is no.**

### When reasoning IS justified

LLM reasoning is justified when the signal requires **semantic judgment of natural-language content** — meaning of a payload, intent of a phrase, quality of an outcome (e.g. "strong user steering due to failure to converge", "agent gave up vs agent finished").

LLM reasoning is **NOT** justified when the signal is structural — event counts, type presence, sequence ordering, time-window detection, ratio thresholds, regex matches on payload fields with stable schemas.

Calibration rule (from the design brief): reason by exception, not by default. Reach for LLM reasoning only when multi-step inference is genuinely required.

### Per-signal model routing — for agents your design proposes

When your `suggested_primitive` for a signal proposes a new **agent** to be composed into the runtime path (LLM-evaluated or hybrid signals), that recommendation MUST specify the `model_role` for the proposed agent. This is the user's own framing:

> "It would be interesting to also capture what kind of reasoning power is needed for signals with AI dependency — this can drive some interesting options in generating the optimal artifact, like an agent with a model role attached, taking advantage of the routing matrix rather than a one-size-fits-all approach."

Per-signal calibration:

- Same signal class, different cognitive load → different roles.
- Don't propose a `reasoning`-tier agent when a `general`-tier skill would suffice.
- Don't propose a `fast`-tier classifier for what is actually multi-step inference.

The Routing Matrix table further down is the lookup. Use it deliberately, per signal — never as one-size-fits-all. This guidance is about the agents YOU recommend in `suggested_primitive`; it is not about your own agent's `model_role`.

### Anti-patterns the user named explicitly

- **Specific agent names in queries or findings.** *"Calling out specific agents in queries and investigations is not a scalable and reusable finding."* Patterns generalise; named-agent queries don't. Encode the structural pattern, not the actor.
- **Conflating user domain terms with Amplifier internal types.** *"The discovery of the signal must be driven by what the user is trying to achieve, and this also means not giving as a fact that the terms used map directly to any of the Amplifier concepts."* "Session" doesn't necessarily mean `Session`; a domain "session" can be a slice, a phase, or a composition the user defined.
- **Artifact-as-success.** Producing a `.md` or `.yaml` file is not the success criterion. The success criterion is what happens when it runs in the DTU. *"No Python tests for checking content in text and configuration files — end-to-end tests with scenarios run inside the DTU."* — the user said it twice in ALL CAPS in the prior session.
- **Recipe-as-default.** Reaching for a recipe before asking whether a deterministic shared-library function would suffice. The recipe orchestrates; the deterministic function is what gets orchestrated.

---

## Primitive Taxonomy — When to Use Each

| Primitive | Use when |
|---|---|
| `context_intelligence/{fn}.py` + CLI subcommand + thin module wrapper | Signal is deterministic or probabilistic; always the first choice. |
| Recipe step (inline prompt) | LLM needed but the task is simple classification; `reasoning_requirement: fast`. |
| Skill (loaded into context) | LLM needed for content understanding without deep inference; `reasoning_requirement: general`. |
| Dedicated agent (`model_role: reasoning`) | Multi-step inference required — convergence failure, steering intent, cross-signal correlation; `reasoning_requirement: reasoning`. |
| Dedicated agent (`model_role: coding`) | Structural analysis of code or tool output; `reasoning_requirement: coding`. |
| Context file | Standing reference knowledge agents need in every session under the mode. |

## Reduce AI Dependency — Decision Order

Apply this ordering for every signal. Stop at the first tier that works.

1. **Can the signal be detected by parsing event structure alone?** (Event type presence, field value, count threshold, time window, sequence detection.) → **Deterministic** → shared library function. No LLM at runtime.
2. **Can the signal be detected by pattern matching plus thresholds on field values?** (Regex on payload text, ratio calculations, shape analysis, size thresholds.) → **Probabilistic** → parameterised shared library function. No LLM at runtime.
3. **Only if neither works:** the signal requires **LLM evaluation**. Declare `reasoning_requirement` and choose the cheapest sufficient role. Before settling here, ask whether deterministic feature extraction can narrow the LLM's scope — that is the **hybrid** path and is strongly preferred over pure LLM evaluation.
4. **For hybrid signals:** deterministic feature extraction goes in the shared library. Only the classification call is LLM-dependent.

## Shared Library + Thin Wrapper Pattern

For deterministic and probabilistic signals (and the feature-extraction half of hybrid signals), the mandatory implementation pattern is three surfaces over one algorithm:

```
context_intelligence/{signal_name}.py     — core logic, pure Python, no Amplifier dependency
modules/tool-{signal-name}/               — thin mount() wrapper for agent tool use
scripts/context-intelligence.py           — gains a new --subcommand for CLI use
```

The shared library function holds all logic. The agent tool is the `mount()` + protocol adapter that calls it. The CLI subcommand is the argparse/click handler that calls it. Same algorithm, three surfaces, zero duplication.

**Wrapper form is chosen by consumer** (module for an agent consumer, CLI for a human/script
consumer). This is the mode's Standing Rule 3 applied per consumer; see **R1** in the
`context-intelligence-tool-design` skill for the decision — not restated here.

## Routing Matrix Roles for Context-Intelligence Signal Detection

When `reasoning_requirement` is not `none`, choose the model role:

| reasoning_requirement | Meaning | Artifact shape |
|---|---|---|
| `fast` | Binary classification or pattern presence/absence detection. | Recipe step with inline prompt, `fast` model role. |
| `general` | Content understanding without deep inference. | Skill loaded into agent context, `general` model role. |
| `reasoning` | Convergence failure detection, steering intent inference, multi-step correlation. | Dedicated agent, `model_role: reasoning`. |
| `coding` | Structural analysis of code, diffs, or tool output. | Agent or recipe step, `model_role: coding`. |

`reasoning` is not a default. Reach for it only when multi-step inference is genuinely required.

## Field Set Added to Each Signal Entry

Phase 2 enrichment adds these exact fields to each signal entry in `domain-signals.md`:

```
detection_strategy:    deterministic | probabilistic | llm-evaluated | hybrid
detection_notes:       [specific fields, patterns, or LLM prompt approach]
ai_dependency:         none | low | medium | high
reasoning_requirement: none | fast | general | reasoning | coding
suggested_primitive:   [context_intelligence/{fn}.py + wrapper + CLI | skill | agent[model_role] | recipe step]
```
