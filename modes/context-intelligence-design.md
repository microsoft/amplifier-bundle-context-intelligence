---
mode:
  name: context-intelligence-design
  description: >
    Design-time mode for building context intelligence-aware artifacts.
    Exposes the context-intelligence-design-facilitator agent plus the
    design guidance context, skills, and tool policies for producing
    skills, context files, agents, recipes, CLIs, and other components
    that leverage context intelligence capabilities.

  agents:
    include:
      - context-intelligence:context-intelligence-design-facilitator

  tools:
    default_action: deny
    safe:
      - graph_query
      - blob_read
      - read_file
      - glob
      - grep
    warn:
      - write_file
      - edit_file
    block:
      - bash

  context:
    include:
      - context-intelligence:context/dual-path-library-template.md
      - context-intelligence:context/jsonl-event-schema.md
    scan:
      - path: .amplifier/context-intelligence/
        glob: "*.md"
        max_total_bytes: 51200
        on_overflow: truncate_and_warn
---

# Context Intelligence Design Mode

This mode is a design-time workshop for building context intelligence-aware Amplifier components and standalone tools. Enter it when you want to understand what context intelligence can observe about a specific runtime, identify gaps, and design artifacts that capture those observations.

## Lifecycle: Investigate → Design → Produce

Three-phase loop: **Investigate** — use `graph-analyst` and `session-navigator` to understand the current event surface and query patterns; **Design** — work with the `context-intelligence-design-facilitator` to map findings to component shapes; **Produce** — write the artifact and vendor it into the consuming project. Iterate as needed.

## What the Mode Adds

The mode adds the `context-intelligence-design-facilitator` agent and design-time guidance on top of the existing always-available context intelligence agents and tools (`graph-analyst`, `session-navigator`, `graph_query`, `blob_read`); nothing existing is removed or hidden.

## Graph Access and the `[]` Ambiguity

Both `graph_query` and `AsyncCIClient.cypher()` return `[]` for both "no rows" and "server unreachable" — a `[]` result is **always ambiguous**. Resolve the ambiguity with a probe (`RETURN 1 AS ok`, 2-second timeout, 60-second TTL cache) before trusting any empty result. The dual-path library template implements this correctly; always use it, never trust a raw `[]`.

## Data Layers

**Data Layer 1** — raw event JSONL on disk — is always available and is the baseline. **Data Layer 2** (`Session`, `OrchestratorRun`, `Iteration`, `ContentBlock`, `ToolCall`, `Prompt`) and the **Foundation Layer** (`Delegation`, `Agent`, `SkillLoad`, `RecipeRun`, `RecipeStep`, `Recipe`) enrich via the graph server when configured and reachable. The two tiers are **not equivalent**: the graph paths return semantic grouping, delegation trees, and cross-session aggregation that cannot be reconstructed from JSONL grep alone.

## Output Shapes

The output of a design session can be any Amplifier component or a standalone tool: skill (reusable query or extraction pattern), context file (domain awareness injected into agents), agent (specialist investigator for a specific runtime), recipe (repeatable multi-step workflow), docs (captured forensic findings), agent tool module (productized verified pattern), or CLI tool (standalone utility outside Amplifier sessions). The shape follows from what the investigation found and what the consuming project needs.

## Dual-Path Pattern

Every tool produced in this mode must implement both a graph path (preferred when the server is available, targeting Data Layer 2 / Foundation Layer) and a Data Layer 1 JSONL fallback (always available on disk). The two paths are not required to return identical results — JSONL is a graceful Data Layer 1 baseline; the graph path returns more. See `context/dual-path-library-template.md` for the complete implementation template.

## Vendoring Contract

Every artifact produced is **vendored into the consuming project**, never into the context intelligence bundle itself — the consuming project's owner is responsible for updating the artifact when the context intelligence schema changes. Every probe must read `metadata.json` first and assert `format == "context-intelligence"` and `version == "1.0.0"`, failing loudly on mismatch so stale copies cannot silently return wrong answers.

## Project Context

`.amplifier/context-intelligence/` in the workspace is auto-scanned on mode entry (`.md` files only, 50 KB cap, truncate-and-warn on overflow); save investigation findings, verified Cypher snippets, and runtime-specific event schemas there to accumulate project-specific context intelligence knowledge across design sessions.

## Resolver Post-Run Scenario

`[BLOCKED: requires Resolver SDK begin_phase(label) / end_phase(label) primitive]` — the Resolver SDK does not currently emit phase transition events needed for phase-level analysis; documented in scenarios but not deliverable in V1.

## Reference

- `context/dual-path-library-template.md` — complete dual-path Python library template with probe, schema check, and dispatcher
- `context/jsonl-event-schema.md` — context intelligence JSONL schema contract, `metadata.json` fields, and version check pattern
- Skill `context-intelligence-graph-query` — Data Layer 2 / Foundation Layer Cypher query patterns
- Skill `context-intelligence-session-navigation` — Data Layer 1 JSONL navigation patterns
