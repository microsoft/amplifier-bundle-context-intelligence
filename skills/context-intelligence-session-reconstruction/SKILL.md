---
name: context-intelligence-session-reconstruction
version: 1.0.0
description: Reconstruct local Amplifier session files from the context-intelligence graph server — events.jsonl, transcript.jsonl, and metadata.json
license: MIT
---

# Context Intelligence Session Reconstruction

Local Amplifier session files (`events.jsonl`, `transcript.jsonl`, `metadata.json`) can be reconstructed from the context-intelligence Neo4j graph server when the local files are missing, broken, or incomplete. This skill covers when and how to run the reconstruction tool safely.

---

## When to Use

Use reconstruction when local session files are absent or unusable but the graph server holds the session data:

- **Missing files**: Sessions exist in the graph but have no local files — for example, after a disk wipe, new machine setup, or accidental deletion
- **Broken resume**: `amplifier resume` shows "unnamed", "unknown", or empty session names — reconstructing `metadata.json` populates bundle, model, and session name fields
- **Unknown bundles**: Sessions that show no bundle information in the resume list — reconstruction extracts bundle info from the graph's `session_start` blob

---

## When NOT to Use

- **Server unreachable**: If the graph server is not reachable, reconstruction cannot proceed. Verify server reachability first with `python scripts/context-intelligence.py status`. If the server is down, fall back to `session-navigator` for local file navigation.
- **Pre-hook sessions**: Sessions that predate the `hook-context-intelligence` hook have no data in the graph. Reconstruction cannot create files for these sessions — there is no source data to read from.
- **Real-time data**: Reconstruction reads a snapshot of what is stored in the graph. It is not a live stream. Do not use it to monitor an active session or capture events as they happen.

---

## Prerequisites

**1. Graph server must be reachable**

Verify with the status subcommand before running reconstruction:

```bash
python scripts/context-intelligence.py status
```

A healthy server responds with `"status": "ok"`. If the server is unreachable, this command will report the error. Do not proceed with reconstruction until status is `ok`.

**2. API key must be configured**

Three methods — use whichever fits your environment:

| Method | How |
|--------|-----|
| Environment variable | `export AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY=<key>` |
| CLI flag | Pass `--api-key <key>` to any subcommand |
| Settings file | Add `context_intelligence_api_key: <key>` to `~/.amplifier/settings.yaml` |

The CLI resolves in this order: flag → env var → `~/.amplifier/settings.yaml`.

**3. Sessions must have been captured by the hook**

Only sessions that were active while `hook-context-intelligence` was configured will have data in the graph. Pre-hook sessions cannot be reconstructed.

---

## Usage Patterns

All patterns use:

```bash
python scripts/context-intelligence.py reconstruct [OPTIONS]
```

### Fix broken resume list (fastest, recommended first)

Reconstructs only `metadata.json` without touching `events.jsonl` or `transcript.jsonl`. Use this when `amplifier resume` shows unnamed or unknown sessions.

```bash
python scripts/context-intelligence.py reconstruct --metadata-only
```

### Preview what would be written (dry run)

Shows what files would be created or updated without writing anything. Add `--verbose` for detailed per-session output.

```bash
python scripts/context-intelligence.py reconstruct --dry-run --verbose
```

### Reconstruct a single session

Limits reconstruction to one session by ID or prefix. Use when you know exactly which session needs repair.

```bash
python scripts/context-intelligence.py reconstruct --session <session-id-or-prefix> --force
```

The `--force` flag overwrites any existing files for that session. Omit `--force` to skip files that already exist.

### Full reconstruction — skip existing files

Reconstructs all sessions in the project, skipping sessions where files already exist. Safe to run repeatedly.

```bash
python scripts/context-intelligence.py reconstruct
```

### Full reconstruction — overwrite all files

Reconstructs all sessions and overwrites existing files. Use only when explicitly requested or when existing files are known to be corrupt.

```bash
python scripts/context-intelligence.py reconstruct --force
```

### Reconstruct with raw LLM data (large output)

The `--resolve-blobs` flag inlines full blob payloads into `events.jsonl`, including complete LLM request/response bodies. This produces significantly larger files (~19MB for a typical long session with many LLM turns).

```bash
python scripts/context-intelligence.py reconstruct --resolve-blobs
```

> Use `--resolve-blobs` only when you need access to raw LLM input/output. For most use cases — resume list repair, event counts, delegation trees — reconstructing without blobs is sufficient and much faster.

---

## Verification

After reconstruction, verify the output files were written correctly:

**Check line counts for events and transcript:**

```bash
wc -l ~/.amplifier/projects/{workspace}/sessions/{session-id}/context-intelligence/events.jsonl
wc -l ~/.amplifier/projects/{workspace}/sessions/{session-id}/context-intelligence/transcript.jsonl
```

A reconstructed `events.jsonl` should have at least a few lines (session start, prompts, tool calls). Zero lines indicates the session had no recoverable events.

**Validate metadata JSON is well-formed:**

```bash
cat ~/.amplifier/projects/{workspace}/sessions/{session-id}/context-intelligence/metadata.json | python3 -m json.tool
```

This confirms the file is valid JSON. Check that `bundle`, `model`, and `session_id` fields are populated.

**Confirm sessions appear in the resume list:**

```bash
amplifier session list
```

Sessions with successfully reconstructed `metadata.json` should now appear with names, bundle info, and model rather than "unknown" or empty fields.

---

## Known Limitations

- **Streaming events not recoverable (~39%)**: `content_block:start` and `content_block:end` events are not stored in the graph — they account for approximately 39% of hook-logging events. Reconstructed `events.jsonl` files will be missing these events. This does not affect session content, only streaming telemetry.

- **Delegate events may be incomplete**: `delegate:agent_spawned` and `delegate:agent_completed` events depend on `data_delegate_*` graph properties that may be null for sessions that were in progress when captured. These events may appear with partial data in the reconstructed files.

- **Session names are approximations**: Session names are generated at runtime by `hooks-session-naming` from the first user prompt. That output is not stored in the graph. Reconstruction generates names from the same source (the first prompt), but the exact phrasing may differ from the original session name.

- **Pre-hook sessions have no graph data**: Sessions that predate the `hook-context-intelligence` hook have no entries in the graph. Reconstruction will produce empty output for these sessions — there is no data to read. Use `session-navigator` to work with pre-hook sessions directly from local JSONL files if they still exist on disk.
