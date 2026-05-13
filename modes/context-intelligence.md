---
mode:
  name: context-intelligence
  description: >
    Investigation, evidence gathering, and design artifact production for
    context intelligence-aware components. Covers Cypher query design, JSONL
    navigation patterns, domain signal interpretation, and design document
    production.
    advertised: false
  default_action: block

  tools:
    safe:
      - graph_query
      - blob_read
      - read_file
      - glob
      - grep
      - delegate
      - load_skill
      - todo
    warn:
      - bash
      - write_file
      - edit_file
---

# Context Intelligence Mode

This mode is for investigation, evidence gathering, and design artifact production for context intelligence-aware components. Use it to design Cypher queries, explore JSONL patterns, interpret domain signals, and produce design documents. Iteration is normal — expect to cycle through investigation, design, and refinement multiple times before producing final artifacts.

## On Mode Entry

Run the following immediately on entering this mode:

1. Create the investigation folder structure:
   ```bash
   mkdir -p .context-intelligence-investigation/queries .context-intelligence-investigation/diagrams
   ```

2. Read all `.md` files in `.context-intelligence-investigation/` via `read_file` before doing anything else — these files contain prior investigation findings, verified Cypher snippets, and domain signals accumulated from previous sessions.

## Investigation Tools

| Tool | Purpose |
|------|---------|
| `graph_query` | Run Cypher queries against the context intelligence property graph |
| `blob_read` | Resolve `ci-blob://` URIs returned by graph queries |
| `delegate` → `context-intelligence:graph-analyst` | Graph-powered session and event analysis; automatically falls back to `session-navigator` when the graph server is unreachable |
| `bash` *(warn)* | Shell operations — file inspection, JSONL grep, environment checks |
| `read_file` / `glob` / `grep` | Navigate local JSONL files and session artifacts |
| `load_skill` | Load context intelligence query patterns and JSONL navigation skills |

### Block 4 — Mandatory facilitator gate

Before writing any artifact, delegate to the facilitator. This is MANDATORY — do not write any file without running the facilitator first.

```python
delegate(
    agent="context-intelligence:context-intelligence-design-facilitator",
    instruction="""
Synthesize the investigation findings gathered so far.

Output all design artifacts to .context-intelligence-investigation/ in the workspace root.

Investigation findings:
[paste your findings here]
""",
    context_depth="recent",
    context_scope="agents",
)
```

The facilitator will:
1. Review and validate your investigation findings
2. Identify gaps or ambiguities in the evidence
3. Propose the correct artifact shapes and file structure
4. Draft initial content for `.md`, `.cypher`, and `.dot` files

Do NOT write `.md`, `.cypher`, or `.dot` files yourself before the facilitator has run.

## What This Mode Produces

This mode produces only design artifacts — no implementation code:

- `.md` — findings, domain signals, JSONL navigation approaches, design documents
- `.cypher` — verified Cypher query files
- `.dot` — architecture and data flow diagrams

**Never** produce Python, YAML, TOML, shell scripts, or other implementation code in this mode.

Output folder: `.context-intelligence-investigation/` at the workspace root.

```
.context-intelligence-investigation/
├── findings.md
├── domain-signals.md
├── jsonl-approaches.md
├── design.md
├── queries/
│   └── *.cypher
└── diagrams/
    └── *.dot
```

`design.md` follows the upload tool pattern: shared core library + agent tool wrapper + CLI wrapper, with dependencies on `context_intelligence.client` and `context_intelligence.config`.

@context-intelligence:context/dual-path-library-template.md

@context-intelligence:context/jsonl-event-schema.md

## When Ready to Build

Once investigation and design artifacts are complete:

1. Save all final artifacts to `.context-intelligence-investigation/` before exiting this mode.
2. Exit this mode.
3. Start `/brainstorm` to design the final output shape — only after brainstorm does the implementation plan make sense.

- If superpowers is available: suggest `/brainstorm`
- If systems-design mode is available: suggest `/systems-design` as an alternative
