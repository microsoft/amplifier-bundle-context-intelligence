# Documentation Index

Human-facing operational documentation for `amplifier-bundle-context-intelligence`.

New here? Start with the [repository README](../README.md#quick-start) — install, verify, and orient — then come back here for depth.

## `docs/` vs `context/` — which is which

This repo keeps documentation in two directories with different audiences. Knowing the split saves you (and any agent) from guessing:

| Directory | Audience | Purpose |
|-----------|----------|---------|
| **`docs/`** (this folder) | Humans installing, configuring, operating, or troubleshooting the bundle | Setup, configuration reference, troubleshooting guides, worked examples. |
| **`context/`** | Amplifier **agents and skills** at runtime | Domain reference material loaded into agent context — event schema, Neo4j graph model, safe JSONL extraction patterns, library templates. See [`../context/`](../context/). |

Rule of thumb: if a **person** reads it to get set up or unstuck, it's in `docs/`. If an **agent** loads it to do its job, it's in `context/`.

## What's in `docs/`

| Document | Covers |
|----------|--------|
| [configuration-reference.md](configuration-reference.md) | **The full configuration & integration reference.** Every config key — `destinations`, authentication (static + Microsoft Entra), workspace resolution, embedding from Python, dispatch/timeout tuning, and the read-path query contract. |
| [context-intelligence-exploration-guide.md](context-intelligence-exploration-guide.md) | A curated tour of what's worth trying after setup — verifying the connection, testing capture, querying the graph. Not a formal test plan. |
| [troubleshooting.md](troubleshooting.md) | Forwarding warnings mapped symptom → cause → fix (degraded/retrying, token-unavailable, the sustained-401 circuit breaker). The first-stop guide. |
| [remote-server-troubleshooting.md](remote-server-troubleshooting.md) | Troubleshooting remote / Azure-deployed servers behind APIM + Entra — tuning knobs, the auth probe cookbook, recovering undelivered events. |
| [container-dns-troubleshooting.md](container-dns-troubleshooting.md) | The network/DNS layer below the hook — reaching the server from inside a DTU / Incus container (the `localhost`→gateway rewrite, the MagicDNS trap). |
| [examples/graph-exploration-walkthrough.md](examples/graph-exploration-walkthrough.md) | A worked narrative: mining "how do we actually work?" from the graph (Cypher → GDS clustering → embeddings). A template for what's possible. |

### Diagrams

| Diagram | Shows |
|---------|-------|
| [auth-flow.dot](auth-flow.dot) / `.png` | Authentication flow. |
| [dispatch-circuit-breaker.dot](dispatch-circuit-breaker.dot) / `.png` | Dispatch flow and circuit breaker state machine. |
| [dispatch-auto-recovery-lifecycle.dot](dispatch-auto-recovery-lifecycle.dot) / `.png` | The auto-recovery lifecycle: HEALTHY → DEGRADED → RECOVERY → OVERFLOW → SHUTDOWN. |
| [logging-handler-flow.dot](logging-handler-flow.dot) | Thin-forwarder logging handler architecture. |
| [examples/exploration-diagrams/](examples/exploration-diagrams/) | Diagrams supporting the graph-exploration walkthrough. |
