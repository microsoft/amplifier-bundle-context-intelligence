# `context/` — Agent-Loaded Reference Material

This directory holds **domain reference material loaded into Amplifier agent/skill context at runtime** — not human setup docs. The bundle's behaviors and agents `@`-mention these files so a running agent (e.g. `graph-analyst`, `session-navigator`) has the schemas and patterns it needs.

If you are a **human** getting set up or troubleshooting, you want [`../docs/`](../docs/) and the [repository README](../README.md#quick-start) instead. If you are an **agent** (or authoring one), this is your reference shelf.

## Contents

| File | Reference material |
|------|--------------------|
| [event-schema.md](event-schema.md) | All 51+ Amplifier session events and their shapes. |
| [jsonl-event-schema.md](jsonl-event-schema.md) | The `events.jsonl` on-disk schema contract. |
| [graph-model-reference.md](graph-model-reference.md) | The Neo4j graph model targeted by Cypher queries. |
| [safe-extraction-patterns.md](safe-extraction-patterns.md) | Safe `bash`/`jq`/`grep` patterns for navigating raw JSONL. |
| [navigation-budget-discipline.md](navigation-budget-discipline.md) | Keeping session navigation within a context budget. |
| [session-reconstruction.md](session-reconstruction.md) | Reconstructing a session from captured events. |
| [context-intelligence-primitives-reference.md](context-intelligence-primitives-reference.md) | Core context-intelligence primitives. |
| [context-intelligence-strategy.md](context-intelligence-strategy.md) | The strategy/approach behind context intelligence. |
| [dual-path-library-template.md](dual-path-library-template.md) | Copy-paste template every generated dual-path tool should follow. |
| [agents/session-storage-knowledge.md](agents/session-storage-knowledge.md) | Session storage knowledge injected into agents. |

### Diagrams

| Diagram | Shows |
|---------|-------|
| [config-resolution.dot](config-resolution.dot) / `.png` | The `HookConfigResolver` `config → coordinator → default` fallback chain. |
| [session-disk-layout.dot](session-disk-layout.dot) | On-disk session directory structure. |
| [delegation-strategy.dot](delegation-strategy.dot) | `graph-analyst` → `session-navigator` delegation logic. |
