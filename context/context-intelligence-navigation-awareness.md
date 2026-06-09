# Context Intelligence — Session Navigation

You have access to the **session-navigator** agent for navigating Amplifier
session data directly from local JSONL files on disk. No graph server is
required — this is the universal offline/fallback path.

## Delegation

| Agent | Purpose |
|-------|---------|
| `context-intelligence:session-navigator` | Local JSONL session navigation: session discovery, event search, and navigation across stored session files using safe bash/jq/grep extraction patterns. |

Delegate session navigation and event-search tasks to `session-navigator`. It
reads session files directly and uses safe extraction patterns that avoid
loading large `events.jsonl` lines into context.

## Storage Layout

Session files live under `<BASE_PATH>/{slug}/sessions/{id}/`, where `BASE_PATH`
defaults to `~/.amplifier/projects`. Override with
`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` when sessions are stored elsewhere.
