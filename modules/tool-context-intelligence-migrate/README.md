# tool-context-intelligence-migrate

A one-time, **re-runnable** command-line tool that migrates legacy file-only Amplifier
sessions into the [Context Intelligence (CI)](https://github.com/microsoft/amplifier-context-intelligence)
world with **zero data loss**.

Before CI, each Amplifier session was logged to a single `events.jsonl` file on disk by the
`hooks-logging` module. This tool converts those historical sessions into the exact on-disk
shape a CI-native session has, lands their full history in the CI server graph, verifies the
upload, and only then deletes the now-redundant legacy `events.jsonl`. The end-state of a
migrated session is structurally identical to a session that had used CI from the start.

It is a **CLI tool** — not a mounted Amplifier module and not a daemon. It exposes no
in-session tools; you run it from your shell.

---

## What it does

For every session under the projects root, the tool:

1. **Classifies** the session into a bucket (see below).
2. **Skips** any session that is still live (never touches in-progress work).
3. **Transforms** the legacy `events.jsonl` into the CI on-disk shape
   (`context-intelligence/events.jsonl` + `context-intelligence/metadata.json`).
4. **Archives** the original legacy `events.jsonl` to a tar before any deletion.
5. **Uploads** the events to the CI server (delegates to `tool-context-intelligence-upload`;
   idempotent, so re-runs are safe).
6. **Verifies** the upload landed correctly.
7. **Deletes** the legacy `events.jsonl` — and only that file — gated on verification.
8. **Records** every phase transition to an append-only JSONL ledger for resumability.

**Dry-run is the default.** The tool makes zero changes on disk unless you pass `--apply`.

### Session buckets

| Bucket | Meaning | Action |
|--------|---------|--------|
| `pre_ci` | Legacy `events.jsonl` only; no `context-intelligence/` directory yet | Full transform → upload → verify → gated delete |
| `double` | Both legacy `events.jsonl` AND `context-intelligence/events.jsonl` present | Ensure in server, verify, delete legacy `events.jsonl` |
| `ci_only` | Only `context-intelligence/events.jsonl`; legacy already gone | Verify in server; no file changes |
| `live` | Recently modified or no terminal `session:end` event | **Skipped entirely** |

---

## Safety guarantees

* **Never touches a live session** — anything modified within the safety window (default 24h)
  or lacking a terminal `session:end` is classified `live` and skipped.
* **Never deletes** `transcript.jsonl`, `metadata.json`, or `config.md`. The only file ever
  deleted is the legacy top-level `events.jsonl`.
* **Archives before deleting** — the legacy `events.jsonl` is tarred into the archive directory
  first.
* **Gated delete** — the legacy file is removed only after **both verify gates pass** (graph
  event-count parity, and zero `$blob_error` markers in the graph) **and** the new CI
  `events.jsonl` is confirmed a content-superset of the legacy file. If any check fails,
  nothing is deleted.
* **Idempotent** — a second run skips sessions already marked complete in the ledger. Safe to
  re-run after an interruption.

---

## Installation

### As part of the bundle (recommended)

This module ships in the `amplifier-bundle-context-intelligence` bundle:

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main
```

### As a standalone CLI

Install with `uv` to add `context-intelligence-migrate` to your PATH:

```bash
uv tool install "amplifier-module-tool-context-intelligence-migrate @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-context-intelligence-migrate"
```

Or install into the current environment:

```bash
uv pip install "amplifier-module-tool-context-intelligence-migrate @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-context-intelligence-migrate"
```

---

## Usage

```
context-intelligence-migrate [--projects-root PATH] [--server-url URL] [--api-key KEY]
                             [--apply] [--yes] [--safety-window-hours N]
                             [--ledger PATH] [--archive-dir PATH]
```

### Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--projects-root PATH` | optional | Root directory of per-project session directories. Default: `~/.amplifier/projects` |
| `--server-url URL` | required* | Base URL of the CI server. Resolved via: CLI flag > `AMPLIFIER_CI_SERVER_URL` env var > `settings.yaml`. |
| `--api-key KEY` | required* | Bearer token for the CI server. Resolved via: CLI flag > `AMPLIFIER_CI_API_KEY` env var > `settings.yaml`. |
| `--apply` | optional | Perform the destructive migration. **Absent ⇒ dry-run** (classify and print the plan, change nothing). |
| `--yes` | optional | Skip the interactive confirmation prompt. Requires `--apply`. Useful for scripted runs. |
| `--safety-window-hours N` | optional | Sessions modified within the last N hours are treated as `live` and skipped. Default: `24.0`. |
| `--ledger PATH` | optional | Append-only JSONL ledger recording every phase transition. Used to resume runs idempotently. Default: `~/.amplifier/migrate-ledger.jsonl` |
| `--archive-dir PATH` | optional | Directory for pre-deletion tars. Default: `~/.amplifier/migrate-archive` |
| `-h` | — | Compact help (usage + flag list) and exit. |
| `--help` | — | Full documentation and exit. |

\* Required unless supplied via environment variable or `settings.yaml`.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Migration completed; no failures. |
| `1` | Migration completed with at least one session failure. |
| `2` | Bad invocation or preflight failure (server unreachable / bad credentials). |

---

## Examples

**Preview what would happen (dry-run — the default, changes nothing):**

```bash
context-intelligence-migrate \
    --server-url http://localhost:8000 \
    --api-key "$AMPLIFIER_CI_API_KEY"
```

**Perform the migration, with interactive confirmation:**

```bash
context-intelligence-migrate \
    --server-url http://localhost:8000 \
    --api-key "$AMPLIFIER_CI_API_KEY" \
    --apply
```

**Scripted run (no prompt):**

```bash
context-intelligence-migrate \
    --server-url http://localhost:8000 \
    --api-key "$AMPLIFIER_CI_API_KEY" \
    --apply --yes
```

**Using environment variables for credentials:**

```bash
export AMPLIFIER_CI_SERVER_URL=http://localhost:8000
export AMPLIFIER_CI_API_KEY=...
context-intelligence-migrate --apply
```

---

## Recommended workflow

1. **Dry-run first.** Run without `--apply` and read the per-bucket report to confirm the plan
   looks right.
2. **Apply.** Re-run with `--apply`. The tool transforms, uploads, verifies, and only then
   deletes legacy files — archiving each first.
3. **Re-run freely.** The run is idempotent; a second pass skips completed sessions and is a
   safe no-op for already-migrated work.

If a run is interrupted, just run it again — the ledger lets it resume without redoing or
double-deleting anything.

---

## Relationship to other components

* **`tool-context-intelligence-upload`** — this tool delegates the actual server upload to it
  (imported, not shelled out). The idempotency guarantee that makes re-runs safe comes from
  there.
* **`hook-context-intelligence`** — defines the CI on-disk shape this tool transforms legacy
  sessions into, so a migrated session matches a CI-native one. That module is the canonical
  source for the event-record and `metadata.json` byte-level contracts this tool reproduces.
