# Reconstruction Knowledge — Agent Context

<!-- Loaded on-demand by graph-analyst via @mention — not composed into bundles via @-reference.
     Place in context/agents/ for targeted agent discoverability; do NOT add to bundle.md or behavior YAML. -->

Operational knowledge for invoking the session reconstruction tool from `graph-analyst`.
Covers when to invoke, safe invocation patterns, what files are created, and other available subcommands.

---

## Tool Location

The unified CLI lives at `scripts/context-intelligence.py` relative to the bundle root.

**Subcommand invocation example:**

```bash
python scripts/context-intelligence.py reconstruct [OPTIONS]
```

All subcommands accept `--server-url` and `--api-key` flags (or env vars /
`~/.amplifier/settings.yaml` entries).

---

## Safe Invocation Patterns

Always start conservative and escalate only if needed.

### 1. Dry-run first (recommended first step)

```bash
python scripts/context-intelligence.py reconstruct --dry-run
```

Shows what would be written without touching the filesystem. Run this before any other
pattern to understand scope.

### 2. Metadata-only (fastest fix for resume list issues)

```bash
python scripts/context-intelligence.py reconstruct --metadata-only
```

Reconstructs only `metadata.json` — the fastest fix when `amplifier resume` shows
"unnamed" or "unknown" sessions. Does not touch `events.jsonl` or `transcript.jsonl`.

### 3. Single session, targeted

```bash
python scripts/context-intelligence.py reconstruct --session <session-id-or-prefix>
```

Limits reconstruction to one session. Use when you know exactly which session needs repair
rather than reconstructing all sessions in the project.

### 4. Full reconstruction with force

```bash
python scripts/context-intelligence.py reconstruct --force
```

Overwrites all existing files. Only use this pattern when the user **explicitly requests**
it — see "When to Guide vs Invoke" below.

---

## What It Creates

Files are written under:

```
~/.amplifier/projects/{workspace-slug}/sessions/{session-id}/
```

| File | Typical Size | Description |
|------|-------------|-------------|
| `events.jsonl` | 10–200KB | Hook-logging event stream; each line is a JSON event object |
| `transcript.jsonl` | 5–100KB | Conversation transcript in SessionStore format |
| `metadata.json` | <1KB | Session metadata: bundle, model, turn count, timestamps |

> **Note:** The reconstruction tool writes to the session root, not the `context-intelligence/`
> subdirectory. These are the foundation-layer files consumed by `amplifier resume`.

---

## When to Guide vs Invoke

### Guide (do NOT invoke automatically)

When a user **asks about missing sessions, unnamed sessions, or wants to understand
reconstruction** — explain the process and the patterns above. Do not run any command
automatically.

Example triggers for guidance-only response:
- "Why do my sessions show as unnamed?"
- "How do I fix missing session files?"
- "What does reconstruction do?"

### Invoke (via bash, only when explicitly asked)

Only invoke `scripts/context-intelligence.py reconstruct` when the user **explicitly asks
you to run it** — phrases like "run reconstruction", "fix my sessions now", "reconstruct
the session files".

Always start with `--dry-run` unless the user explicitly says to write files.

### Never use `--force` unless explicitly requested

**Never run `--force` unless the user explicitly requests it.** The `--force` flag
overwrites existing files. If files already exist and the user has not asked you to
overwrite them, skip `--force` entirely.

---

## Other CLI Subcommands

The same `scripts/context-intelligence.py` script exposes additional subcommands:

| Subcommand | Purpose |
|------------|---------|
| `status` | Check graph server health and session statistics |
| `query` | Run ad-hoc Cypher queries against the graph |
| `upload` | Replay local session events to the server |

Example usage:

```bash
# Check server connectivity and session count
python scripts/context-intelligence.py status

# Run a Cypher query
python scripts/context-intelligence.py query --cypher "MATCH (s:Session) RETURN count(s)"

# Upload sessions to the graph
python scripts/context-intelligence.py upload
```
