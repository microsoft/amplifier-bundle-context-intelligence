# Context Intelligence Primitives Reference

Compressed reference for choosing the right Amplifier primitive when implementing a context-intelligence signal detector. Loaded on demand by the `context-intelligence-tool-design` skill as a companion file at Phase 2 entry. Not pre-loaded by any agent. Not a mode `@mention`.

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
