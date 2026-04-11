# Session Reconstruction

<!-- Loaded on-demand by agents via @mention — not composed into bundles via @-reference or context.include.
     Place in context/ for agent discoverability; do NOT add to bundle.md or behavior YAML. -->

Reconstructs local Amplifier session files (`events.jsonl`, `transcript.jsonl`,
`metadata.json`) from the context-intelligence Neo4j graph server.

## When to Use

- **Missing files**: Sessions exist in the graph but have no local files (e.g., after a disk wipe or new machine setup)
- **Corrupted files**: Local session files are broken and need to be rebuilt from the graph
- **Migration**: Moving sessions between machines or environments
- **Resume list repair**: `amplifier resume` shows "unnamed" or "unknown" for sessions — reconstructing `metadata.json` populates bundle, model, and session name

## Prerequisites

- Context-intelligence graph server must be reachable
- API key must be configured (via env var, CLI flag, or `~/.amplifier/settings.yaml`)
- Sessions must have been previously captured by the `hook-context-intelligence` hook

## CLI Reference

```
python scripts/context-intelligence.py reconstruct [OPTIONS]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--project-dir DIR` | Amplifier project directory | cwd |
| `--events-only` | Only reconstruct `events.jsonl` | all |
| `--transcript-only` | Only reconstruct `transcript.jsonl` | all |
| `--metadata-only` | Only reconstruct `metadata.json` | all |
| `--force` | Overwrite existing files | skip existing |
| `--dry-run` | Show what would be done without writing files | |
| `--resolve-blobs` | Inline full blob content in `events.jsonl` | off |
| `--session ID` | Reconstruct a single session (or prefix) only | all |
| `--verbose` | Detailed logging | |
| `--server-url URL` | CI server URL | env/settings |
| `--api-key KEY` | CI server API key | env/settings |

## Output Files

| File | Format | Written By |
|------|--------|------------|
| `events.jsonl` | Hook-logging format: `{ts, lvl, schema, event, session_id, data}` | `extract_events()` |
| `transcript.jsonl` | SessionStore format: `{role, content, metadata}` | `extract_transcript()` |
| `metadata.json` | CLI format: `{session_id, bundle, model, turn_count, created, ...}` | `extract_metadata()` |

## Known Limitations

- **Streaming telemetry not recoverable**: `content_block:start` / `content_block:end` events (~39% of hook-logging events) are not stored in the graph. Reconstructed `events.jsonl` files will be missing these events.
- **Delegate events may be incomplete**: `delegate:agent_spawned` / `delegate:agent_completed` events depend on `data_delegate_*` properties that may be null for in-progress sessions. These events may appear with partial data.
- **Session names are approximations**: Names are generated from the first user prompt since `hooks-session-naming` runs at runtime and its output is not stored in the graph. The generated names are reasonable approximations but may differ from the original session names.
- **Bundle info may show unknown**: Sessions that predate the CI hook or have no `session_start` blob will show "unknown" bundle in the reconstructed `metadata.json`.
