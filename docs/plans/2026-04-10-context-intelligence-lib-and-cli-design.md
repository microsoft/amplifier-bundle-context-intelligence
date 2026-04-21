# Context Intelligence Root-Level Library and Unified CLI Design

## Goal

Add a root-level `context_intelligence/` Python library package to `amplifier-bundle-context-intelligence`, following the exact pattern of `amplifier_foundation/` in `amplifier-foundation`. This library becomes the shared substrate for a unified `context-intelligence` CLI tool and future consumers (hooks, agents, Amplifier tool modules). The first CLI subcommand is `reconstruct` (reconstructing session files from the Neo4j graph), with additional thin-wrapper subcommands for `upload`, `status`, and `query`.

## Background

We built a ~1000-line Python script (`ci-reconstruct-sessions.py`) that reconstructs Amplifier session files (`events.jsonl`, `transcript.jsonl`, `metadata.json`) from a context-intelligence Neo4j graph server. It queries the graph via HTTP (`POST /cypher`), resolves blobs (`GET /blobs/{session_id}`), transforms graph node data into the hook-logging and SessionStore formats, and writes files to `~/.amplifier/projects/{slug}/sessions/{session_id}/`.

After extensive comparison against original session files from a live session, the script achieves:

- **93% event coverage** (of recoverable non-streaming events) in `events.jsonl`
- **100% message alignment** in `transcript.jsonl` with correct format (`tool_call` naming, `tool_calls` arrays, `visibility` on thinking blocks)
- **`metadata.json`** reconstruction with bundle, model, turn_count, working_dir, and auto-generated session names from first prompts

The remaining ~7% gap is `content_block:start/end` streaming telemetry and `delegate:agent_spawned/completed` events, which are not stored in the graph at all.

The repo already has a `tool-context-intelligence-upload` module that provides a `context-intelligence-upload` CLI for replaying events TO the server. It follows a similar lib+CLI pattern internally. This design unifies CLI tooling under a single `context-intelligence` command with subcommands.

## Approach

We evaluated three approaches:

| Option | Description | Verdict |
|--------|-------------|---------|
| **A: Shared code in hook module** | Put library code in `modules/hook-context-intelligence/` (the existing "hub module") | Rejected — couples the hook to CLI/library concerns, doesn't scale as more CLI features are added |
| **B: Root-level `context_intelligence/`** | Create the library at repo root, matching the `amplifier_foundation/` pattern | **Chosen** — enables reuse by modules/hooks/agents/CLIs, anticipates growth, follows established ecosystem pattern |
| **C: `modules/lib-context-intelligence/`** | Put the library under `modules/` as its own package | Rejected — no precedent in the ecosystem; the root-level package pattern is established |

The `amplifier-foundation` repository establishes the canonical pattern: a root-level Python package (`amplifier_foundation/`) with a root `pyproject.toml`, consumed by thin CLI scripts in `scripts/` that use a `sys.path` insertion trick. Modules under `modules/` remain independent packages with their own `pyproject.toml` files and can optionally import from the root library as a path dependency.

## Architecture

### File Structure

```
amplifier-bundle-context-intelligence/
├── pyproject.toml                           # ROOT: declares context_intelligence as installable
│
├── context_intelligence/                    # THE LIBRARY
│   ├── __init__.py                          # Flat re-export of public API, __all__
│   ├── py.typed                             # PEP 561 marker
│   ├── client.py                            # CIClient: HTTP transport
│   ├── config.py                            # resolve_config(): env/settings/defaults
│   ├── reconstruct/                         # Session reconstruction subpackage
│   │   ├── __init__.py                      # Re-exports: extract_events, extract_transcript, etc.
│   │   ├── events.py                        # Graph → hook-logging events.jsonl format
│   │   ├── transcript.py                    # Graph → SessionStore transcript.jsonl format
│   │   ├── metadata.py                      # Graph → metadata.json format
│   │   └── discover.py                      # Session discovery and workspace slug derivation
│   └── upload/                              # Upload subpackage (placeholder for future migration)
│       └── __init__.py
│
├── scripts/
│   └── context-intelligence.py              # THIN CLI wrapper with subcommands
│
├── modules/                                 # UNCHANGED individual module packages
│   ├── hook-context-intelligence/           # Existing — event capture hook
│   ├── tool-graph-query/                    # Existing — graph_query Amplifier tool
│   ├── tool-blob-read/                      # Existing — blob_read Amplifier tool
│   └── tool-context-intelligence-upload/    # Existing — upload CLI (continues independently)
│
├── context/                                 # Context awareness files
│   ├── context-intelligence-awareness.md    # UPDATE: add CLI tools section
│   ├── session-reconstruction.md            # NEW: reconstruction workflow reference
│   └── agents/
│       └── reconstruction-knowledge.md      # NEW: agent knowledge for reconstruction
│
├── skills/
│   └── context-intelligence-session-reconstruction/
│       └── SKILL.md                         # NEW skill
│
├── agents/
│   └── graph-analyst.md                     # UPDATE: @mention reconstruction context
│
├── behaviors/
│   └── context-intelligence.yaml            # NO CHANGE
│
└── bundle.md                                # NO CHANGE
```

### Root `pyproject.toml`

```toml
[project]
name = "amplifier-bundle-context-intelligence"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = true

[tool.hatch.build.targets.wheel]
packages = ["context_intelligence"]

[tool.hatch.metadata]
allow-direct-references = true

[dependency-groups]
dev = ["pytest>=8.0", "pyright>=1.1", "ruff>=0.4", "httpx>=0.28.1"]

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "basic"
include = ["context_intelligence", "tests"]
extraPaths = ["."]

[tool.ruff]
target-version = "py311"
line-length = 100
```

`httpx` is a runtime dependency for `CIClient` but is handled gracefully — the client falls back to stdlib `urllib.request` if httpx is not available. For the `sys.path` script scenario this degrades cleanly; for the installed package scenario, httpx would be declared as a dependency.

## Components

### Library: Three Levels

Following the `amplifier_foundation` layering convention:

#### Level 1 — Pure Transforms (zero I/O)

Functions that convert between data formats with no network or filesystem access:

- **`reconstruct/events.py`**: `_make_event_line(event_type, data_json, ...) -> dict` — transforms a graph node's JSON data into a hook-logging format line with proper field ordering (`ts`, `lvl`, `schema`, `event`, `session_id`, `redaction`, `data`)
- **`reconstruct/transcript.py`**: `_make_assistant_content(raw_content_blocks) -> list[dict]` — renames `tool_use` to `tool_call`, strips `caller` field, adds `visibility: "internal"` to thinking blocks, builds `tool_calls` arrays on assistant messages
- **`reconstruct/metadata.py`**: `_build_root_metadata(session_data, turn_count, blob_data) -> dict` — assembles the `metadata.json` structure with bundle, model, working_dir, turn_count, created/updated timestamps, and auto-generated session name from first prompt

#### Level 2 — Network I/O (HTTP only)

- **`client.py`**: `CIClient` class encapsulating all HTTP communication with the context-intelligence server:
  - `graph_query(cypher: str, params: dict) -> list[dict]` — `POST /cypher`
  - `list_blob_keys(session_id: str) -> list[str]` — `GET /blobs/{session_id}`
  - `fetch_blob(session_id: str, key: str) -> dict | None` — `GET /blobs/{session_id}/{key}`, returns `None` on 404
  - `health_check() -> dict` — server health and basic stats
- **`config.py`**: `resolve_config(args) -> Config` — resolution chain: CLI flags → environment variables (`AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL`, `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY`) → `~/.amplifier/settings.yaml` → defaults

#### Level 3 — Filesystem + Orchestration

- **`reconstruct/discover.py`**:
  - `discover_sessions(client, workspace, sessions_dir) -> tuple[list[GraphSession], list[str]]` — queries graph for all sessions in a workspace, scans disk for sessions not in the graph, returns both sets
  - `workspace_slug(project_dir: str) -> str` — converts `/home/user/dev/project` to `-home-user-dev-project`
- **`reconstruct/__init__.py`**: Top-level extraction functions that compose Level 1 and Level 2:
  - `extract_events(client, workspace, session_id, resolve_blobs=False) -> list[dict]` — queries 7+ graph node types (Session, OrchestratorRun, PromptStep, AssistantStep, ToolExecution, Delegation, Event), builds event lines, sorts chronologically
  - `extract_transcript(client, workspace, session_id) -> list[dict]` — walks the Run → PromptStep → AssistantStep → ToolExecution chain, resolves LLM response blobs to extract content blocks, builds the message sequence
  - `extract_metadata(client, workspace, session_id) -> dict | None` — tries session_start blob → first llm_request blob → graph node properties fallback chain

### CLI: Unified Subcommand Router

`scripts/context-intelligence.py` uses the sys.path insertion trick (matching `amplifier-session.py` in `amplifier-foundation`) and delegates to library functions:

```python
_here = Path(__file__).resolve().parent
_root = _here.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from context_intelligence import ...  # noqa: E402
```

#### Subcommand: `reconstruct`

The primary deliverable. Reconstructs local session files from the graph server.

```
context-intelligence reconstruct --project-dir .
    [--events-only] [--transcript-only] [--metadata-only]
    [--resolve-blobs] [--force] [--dry-run] [--verbose]
    [--server-url URL] [--api-key KEY]
```

Calls `discover_sessions()` → loops sessions → `extract_events()` / `extract_transcript()` / `extract_metadata()` → writes to disk. Skips sessions that already have files unless `--force` is passed.

#### Subcommand: `upload`

Thin wrapper around the existing upload module. A convenience — the existing `context-intelligence-upload` command continues to work independently.

```
context-intelligence upload --path ~/.amplifier/projects/my-project
    [--server-url URL] [--api-key KEY] [--job-id ID]
```

Imports from `amplifier_module_tool_context_intelligence_upload.cli` and delegates.

#### Subcommand: `status`

New, trivial. Checks server health and displays session statistics.

```
context-intelligence status [--server-url URL] [--api-key KEY]
```

Calls `client.health_check()`, queries session counts by workspace, prints a summary table.

#### Subcommand: `query`

New, trivial. Runs ad-hoc Cypher queries against the graph without needing an Amplifier session.

```
context-intelligence query "MATCH (s:Session) RETURN count(s)"
    [--workspace SLUG] [--server-url URL] [--api-key KEY]
```

Calls `client.graph_query(cypher_string)`, prints JSON results to stdout.

## Data Flow

```
Neo4j Graph Server (http://localhost:8100)
    │
    ├── POST /cypher     → Session, OrchestratorRun, PromptStep,
    │                      AssistantStep, ToolExecution, Event nodes
    ├── GET /blobs/{id}  → list of ci-blob:// URI strings
    └── GET /blobs/{id}/{key} → blob data (JSON)
    │
    ▼
context_intelligence.client.CIClient
    │
    ├── graph_query(cypher)            → list[dict]
    ├── list_blob_keys(session_id)     → list[str]
    └── fetch_blob(session_id, key)    → dict | None
    │
    ▼
context_intelligence.reconstruct.*
    │
    ├── discover_sessions(client, workspace, sessions_dir)
    │       → (graph_sessions, disk_only_ids)
    ├── extract_events(client, workspace, session_id, resolve_blobs)
    │       → list[dict]           (hook-logging event lines)
    ├── extract_transcript(client, workspace, session_id)
    │       → list[dict]           (SessionStore message dicts)
    └── extract_metadata(client, workspace, session_id)
            → dict | None          (metadata.json structure)
    │
    ▼
scripts/context-intelligence.py  (thin CLI)
    │
    ├── write_jsonl(path, events)    → events.jsonl
    ├── write_jsonl(path, messages)  → transcript.jsonl
    └── write_json(path, metadata)   → metadata.json
    │
    ▼
~/.amplifier/projects/{slug}/sessions/{session_id}/
    ├── events.jsonl
    ├── transcript.jsonl
    └── metadata.json
```

## Context Awareness Wiring

### `context/context-intelligence-awareness.md` — Update

Add a new section:

```markdown
## CLI Tools

| Tool | Purpose |
|------|---------|
| `context-intelligence reconstruct` | Reconstruct local session files from the graph server |
| `context-intelligence upload` | Replay session events to the server |
| `context-intelligence status` | Check server health and session counts |
| `context-intelligence query` | Run ad-hoc Cypher queries against the graph |

**Usage:** `python scripts/context-intelligence.py <subcommand> [options]`
```

### `context/session-reconstruction.md` — New

Documents:

- When to use reconstruction (missing files, migration, disk failure recovery)
- Prerequisites (graph server reachable, API key configured)
- Full CLI reference for `reconstruct` subcommand with all flags
- Output format reference (`events.jsonl`, `transcript.jsonl`, `metadata.json`)
- Known limitations (streaming telemetry not in graph, delegate events may be incomplete)

### `context/agents/reconstruction-knowledge.md` — New

Agent-facing context file covering:

- How to find and invoke the CLI script
- Safe invocation patterns (dry-run first, then targeted, then full)
- What the tool creates and where files land
- When to guide users vs invoke directly

### `agents/graph-analyst.md` — Update

- Add `@context-intelligence:context/agents/reconstruction-knowledge.md` reference
- Add description: "When local session files are missing, guide the user to run `context-intelligence reconstruct` to restore them from the graph."

### `skills/context-intelligence-session-reconstruction/SKILL.md` — New

Skill covering:

- Prerequisites and installation
- Usage patterns (full project, single session, dry-run, metadata-only, resolve-blobs)
- Verification steps (diff against known-good files)

## Error Handling

| Scenario | Behavior |
|----------|----------|
| **Server unreachable** | `CIClient` catches connection errors, logs warning, returns empty results. Script continues to next session. |
| **Missing blobs** | `fetch_blob()` returns `None` on 404. Callers use fallback chains (e.g., metadata tries `session_start` blob → `llm_request` blob → partial metadata from graph properties). |
| **Malformed JSON** | Each `json.loads()` wrapped in try/except. Malformed graph data logged as warning, line skipped. |
| **Existing files** | Default: skip (no overwrite). `--force` flag enables overwrite. Logged either way. |
| **Disk-only sessions** | Sessions found on disk but not in graph get minimal metadata from directory timestamps. |
| **httpx not available** | `CIClient` falls back to stdlib `urllib.request`. No hard dependency at import time. |
| **Session not in graph** | Logged as info, skipped. Summary at end reports skipped count. |

## Testing Strategy

- **Unit tests** for Level 1 pure functions: `_make_event_line()`, `_make_assistant_content()`, `_build_root_metadata()` with fixture JSON inputs and expected outputs
- **Integration tests** for `CIClient` against a mock HTTP server (httpx mock transport or `responses` library)
- **End-to-end test**: Script runs against a live graph server, compares output against known-good session files (the comparison already performed for session `10d123eb` provides the baseline)
- **Format validation tests**: Verify output `events.jsonl` matches hook-logging schema, `transcript.jsonl` matches SessionStore schema, `metadata.json` matches CLI expectations

## Migration Path for Existing Upload CLI

The existing `tool-context-intelligence-upload` module continues to work independently with its `[project.scripts]` entry point (`context-intelligence-upload`). The new `context-intelligence upload` subcommand initially just imports and delegates to it. Over time, the upload module's library code (`session_graph.py`, `uploader.py`, `progress.py`) could migrate into `context_intelligence/upload/`, and the existing module would become a thin wrapper. This migration is **not** part of the initial implementation — it is future cleanup.

## Open Questions

1. **httpx as root dependency or optional?** The existing hook and upload modules both depend on httpx. Making it a root dependency simplifies the client but adds a dependency to the bundle package itself. Current approach: graceful fallback to urllib, httpx optional. Revisit once the library is consumed by more than just the CLI script.

2. **Should `context-intelligence` eventually become an installed CLI?** Currently using `sys.path` trick for portability. Could add `[project.scripts]` later if warranted. The upload CLI already uses `[project.scripts]` so there's precedent either way.

3. **Should the hook module eventually import from `context_intelligence/`?** The hook currently has its own `upload.py`, `config_resolver.py` which overlap with the library's `client.py` and `config.py`. These could migrate to the shared library. This is future work, not initial scope.
