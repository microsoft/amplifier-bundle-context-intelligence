# context-intelligence-recover

`context-intelligence-recover` is a dedicated, durable recovery command for
native Context Intelligence captures. Version 0.2 uses a compact V2
checkpoint: one artifact row and one cursor per current destination, never an
event payload or per-event delivery row. It reads only:

```text
<root>/<project>/sessions/<session>/context-intelligence/events.jsonl
```

and the adjacent native metadata. It never uses the ordinary event endpoint.

## Install

The `@main` command is the normal channel once the CLI, hook, and shared-library
changes have merged together. It does not select an unmerged candidate.

```bash
uv tool install "amplifier-module-tool-context-intelligence-recover @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-context-intelligence-recover"
```

Checkout project resolution (`uv sync --frozen` from this module) uses the declared
editable repository-root library and sibling hook. Its package-only uv override
replaces conflicting direct/transitive Git requirements for the library, whether
or not `--dev` is selected. Standalone `pip` / `uv tool install` and non-editable
wheels do not apply these checkout project settings; their production dependencies
still reference Git `@main`.

For an unmerged candidate, pin the CLI, shared library, and hook to the same full
published revision, matching the checkout. A candidate CLI with the library left
on `@main` is not sufficient:

```bash
REV='<full-40-character-PR-revision>'
printf '%s\n' \
  "amplifier-bundle-context-intelligence @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@${REV}" \
  "amplifier-module-hook-context-intelligence @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@${REV}#subdirectory=modules/hook-context-intelligence" \
  > ci-candidate-overrides.txt
uv tool install --overrides ci-candidate-overrides.txt \
  "amplifier-module-tool-context-intelligence-recover @ git+https://github.com/microsoft/amplifier-bundle-context-intelligence@${REV}#subdirectory=modules/tool-context-intelligence-recover"
```

## Use

```bash
context-intelligence-recover --once
context-intelligence-recover --path ~/saved-captures --once
context-intelligence-recover --watch
context-intelligence-recover --dry-run
```

There are intentionally no server URL or credential flags. The command resolves
the effective hook configuration for each recorded working directory by merging
the user-level settings with that project's local settings. It applies the
hook's include/exclude matcher to the recorded working directory and fans
eligible records out to every matching destination. If that effective
configuration cannot be resolved safely, the artifact remains blocked. It first
requires the target's `native-recovery` version
capability; a target without it remains blocked and is never sent through an
ordinary event endpoint.

By default, checkpoints are private SQLite files under
`~/.local/state/context-intelligence/recovery/<job-id>/`. A job records facts
only in `<state-dir>/<job-id>.v2.sqlite3`; a private lock sidecar coordinates
runners. It never opens, migrates, removes, or changes an existing V1
`<job-id>.sqlite3`. The local JSON summary reports `legacy_checkpoint_present`
when that V1 file exists.

The checkpoint stores only source/metadata hashes and stat facts, record count,
native artifact facts, and compact source cursors (ordinal and byte offset).
Unchanged artifacts use their durable stat-and-hash snapshot; delivery reads
only the next bounded (16 MiB maximum) line through a verified descriptor. A
source or metadata stat change triggers one full rehash before delivery and
quarantines the artifact on mismatch. Event bodies are never persisted in the
checkpoint, and source paths are never sent to a target or emitted in local
summaries.
Use `--job-id` to resume the same job or `--state-dir` to place the checkpoint
elsewhere.

### Permanently abandon one catch-up job

When the operator has abandoned a particular catch-up, first stop its producer and
retire its service/timer or other automatic launcher. Older/custom producers do
not understand retirement records and must remain disabled; this command does
not manage services or authorize stopping unrelated work.

```bash
context-intelligence-recover --state-dir /path/to/job-state --job-id old-catch-up \
  --retire-job --reason 'Catch-up abandoned; its old launcher is retired'
```

This requires an existing supported V2 checkpoint and its existing, unlocked
runner lock. It records an explicit abandonment bound to that exact job/file,
without deleting or changing artifact rows, cursor positions, failures or unknown
delivery outcomes. Repeating the command returns the original retirement receipt.
Current recovery clients refuse that job before scanning or delivering anything,
including after restart. There is no automatic resume or cursor reset. Separate
server receipt and source-enrollment databases are untouched.

The matching pruner releases only this abandoned job's retention barrier. Other
jobs, capture/writer locks, inactivity/age policy and replay-prevention receipts
still apply. Retirement itself neither prunes a capture nor proves successful
delivery or absence of a writer. A renamed/copied checkpoint or invalid retirement
record fails closed; do not remove the retirement record to revive the job.

`--once` returns nonzero whenever deferred, blocked, or quarantined work
remains. `--watch` honors `Retry-After` before retrying and re-reads
configuration on each cycle.

Watch mode keeps its five-minute full-discovery interval. Already-discovered
captures blocked by a maintenance lock are retried individually on roughly
one-second intervals, without rescanning the capture tree. After lock release,
recovery rechecks replay safety and reconciles routing before continuing from
acknowledged offsets; completed captures stop contributing transient pending
work. Delivery still honors `Retry-After`; local lock retries do not shorten
persisted server deferrals or guarantee a one-second completion time.

`--dry-run` (also `--plan`) inventories and evaluates routing without issuing
any HTTP requests. Recovery scans only exact
`<project>/sessions/<session>/context-intelligence/events.jsonl` candidates,
streams them one binary line at a time, and retains invalid or unsafe captures
as explicit deferred/quarantined inventory states. Source artifacts are never
modified or deleted.

Each runner cycle submits at most one source record to a logical configured
destination. Logical destinations remain independent even if they share an
endpoint URL. A `202`
(including a duplicate acknowledgement) advances only that target's cursor.
Lost acknowledgements leave it unchanged, while `429` and transient `5xx`
responses defer the target. Authentication, missing capability, and unsupported
target responses block it; invalid/conflicting recovery responses quarantine the
current cursor while all other cursor state remains durable.
