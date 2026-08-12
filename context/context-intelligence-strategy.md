# Context-Intelligence Design Strategy (Mode Orientation)

This thin orientation file is injected only when the context-intelligence mode is active. It
routes a tool/agent designer to the **single authoritative home** of each concept. It is a
**pure pointer table**: titles only, no restated rules — open each reference on demand with
`read_file` or `load_skill`. (These are NON-loading references; only `prospector` uses a
LOADING `@mention` for the discipline file, because it needs those rules resident while it
works.)

## Cross-cutting principle — event semantics (named once)

**Understand event semantics from the authoritative source.** Consult the relevant ecosystem
**expert agents** — the bundles/modules that emit those events — for event structure and meaning,
rather than guessing. Every skill that needs this principle references it *here* and does not
restate it.

## Pointer table (open on demand)

| Concept | Single home (non-loading reference) |
|---------|-------------------------------------|
| Bounded local-JSONL navigation budget (the 6 rules) | `context-intelligence:context/navigation-budget-discipline.md` |
| Detection-strategy classification + primitive selection (incl. R1 module-vs-CLI, R2, R3) | skill `context-intelligence-tool-design` |
| Primitive taxonomy, reduce-AI-dependency order, shared-library + thin-wrapper | `context-intelligence:context/context-intelligence-primitives-reference.md` |
| Measurement methodology (metric design, precursor metrics, A/B + statistical-N) | skill `context-intelligence-evaluation-methodology` |
| Evaluation scenario mechanics + two-layer structural/behavioral structure | skill `context-intelligence-eval-design` |
| Digital Twin Universe machinery (launch / exec / profiles) | skill `digital-twin-universe` |
| Flat-JSONL session navigation recipes | skill `context-intelligence-session-navigation` |
| Event-semantics authority principle | this file (above) |
