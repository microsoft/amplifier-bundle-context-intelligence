# Context Intelligence JSONL Event Schema Contract

This document defines the on-disk JSONL schema written by the
`hook-context-intelligence` module. Every tool or library that reads
context intelligence session files must comply with this contract.

---

## Filesystem Layout

```
~/.amplifier/projects/{project_slug}/sessions/{session_id}/context-intelligence/
    events.jsonl       — append-only event log (Data Layer 1)
    metadata.json      — session-level metadata
```

`project_slug` is resolved by the hook from `coordinator.config['project_slug']`,
then from the working directory (slugified, e.g. `-home-user-repos-app`),
then `"default"`. Tools must accept the session directory path explicitly
rather than re-deriving it.

---

## Event Line Format

Every line in `events.jsonl` is a single compact JSON object with sorted keys:

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `data` | object | Raw kernel event payload | The event-specific payload (varies per `event` type) |
| `event` | string | Raw kernel event | Event type name (e.g. `tool:pre`, `tool:post`, `delegate:agent_spawned`, `mode:enter`) |
| `timestamp` | string (ISO 8601) | Raw kernel event | Event time |
| `workspace` | string | Injected by hook | Workspace identifier (NOT present in the raw kernel event — added by `LoggingHandler`) |

No version is embedded in event lines; the version belongs to `metadata.json`.

---

## `metadata.json` Format

| Field | Type | Description |
|-------|------|-------------|
| `format` | string | Always `"context-intelligence"` |
| `version` | string | Schema version, currently `"1.0.0"` |
| `session_id` | string | Session identifier |
| `workspace` | string | Workspace identifier |
| `started_at` | string | First event timestamp |
| `last_event_at` | string | Most recent event timestamp (added by V1 hook fix; updated after every event append) |
| `status` | string | Session status (e.g. `"running"`, `"completed"`) |

Additional optional fields may appear: `parent_id`, `working_dir`, `agent_name`,
`recipe_name`, `recipe_step`, `parallel_group_id`, `ended_at`.

---

## Required Version Check

Every JSONL probe must include this assertion **before** reading event lines.
Silent acceptance of an unknown schema produces wrong answers — fail loudly.

```python
import json
from pathlib import Path

_SUPPORTED_JSONL_VERSION = "1.0.0"

def assert_jsonl_compatible(session_dir: Path) -> None:
    meta = json.loads((session_dir / "metadata.json").read_text())
    if meta.get("format") != "context-intelligence":
        raise RuntimeError(f"unexpected format: {meta.get('format')!r}")
    if meta.get("version") != _SUPPORTED_JSONL_VERSION:
        raise RuntimeError(
            f"unsupported version: {meta.get('version')!r} "
            f"(expected {_SUPPORTED_JSONL_VERSION})"
        )
```

The dual-path library template (`context/dual-path-library-template.md`)
packages this as `_assert_jsonl_compatible` and calls it automatically in
`_via_jsonl`. Use the template rather than reimplementing this check.

---

## What Data Layer 1 JSONL Cannot Provide Without the Graph

The JSONL path is a graceful Data Layer 1 baseline. It cannot reconstruct
everything the graph provides:

- **Semantic grouping** — Data Layer 2 entities like `OrchestratorRun`,
  `Iteration`, `ContentBlock`, `ToolCall`, `Prompt` are derived by the
  graph's assembly logic from raw events. JSONL grep can approximate but
  not reproduce this assembly across all 15 typed Data Layer 2 relationships.

- **Delegation trees beyond a single hop** — Foundation Layer `Delegation`
  nodes capture multi-level delegation chains as a graph; JSONL gives you
  the discrete `delegate:agent_spawned` events but reconstructing the full
  tree requires careful reassembly.

- **Cross-session aggregation** — answering "across my last 50 sessions"
  requires reading 50 directories and joining; the graph answers this with
  a single Cypher query.

---

## The Two-Tier Contract

JSONL gives you the correct always-available Data Layer 1 baseline. The
graph gives you more. Tools produced by the context-intelligence-design mode
must explicitly design which questions are answerable from each tier and
accept that the answers will differ — the JSONL fallback is graceful
degradation, not full equivalence.

Load the `context-intelligence-session-navigation` skill for the full
inventory of event types (41+ canonical types), payload field structures,
and safe extraction sizes.
