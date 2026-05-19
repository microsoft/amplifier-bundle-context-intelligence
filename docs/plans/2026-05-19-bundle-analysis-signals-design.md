# Bundle Analysis Signals Redesign

## Goal

Make all five bundle-usage signal types — agents, skills, modes, recipes, and tools — produce correct counts in `bundle_analysis/signals.py`, via a single processing pipeline shared by the graph source and the JSONL source.

## Background

`context_intelligence/bundle_analysis/signals.py` currently only extracts agent signals correctly. Skills, modes, recipes, and tools silently return zero. The root cause: `_SESSION_QUERY_MAP` expects result rows to expose `bundle` and `skill_invocations` columns that the Tier B Cypher queries (S04–S08, S09, S12, S15) never return. The Tier B Python processing — the step that converts raw events into bundle attributions — was designed but never implemented. The JSONL fallback in `jsonl_signals.py` has the same gap: agent-only coverage.

The current code therefore makes five separate Cypher round-trips per scope, returns rows the consumer cannot interpret, and silently zeroes four out of five signal types. This redesign collapses both sources onto one Python processor that emits a uniform `{bundle: {agents, skills, modes, recipes, tools}}` map.

## Approach

Three clean layers, one execution path regardless of source:

```
run_signals(client, workspace, session_id, base_path)
    │
    ├─ 1. FETCH — produces List[RawSignalEvent]
    │       ├─ GraphFetcher   — Cypher: one unified query per scope
    │       └─ JSONLFetcher   — streams events.jsonl, filters relevant events
    │
    ├─ 2. PROCESS — shared Python, source-agnostic
    │       ├─ parse tool_input JSON strings → signal keys
    │       └─ map signal keys → bundle names via AttributionIndexes
    │
    └─ 3. AGGREGATE — count per bundle per component type
            → {bundle: {agents, skills, modes, recipes, tools}}
```

**Source selection** (lean on what is available):
- Graph client configured and server reachable → `GraphFetcher` (fast, handles remote sessions).
- Server unreachable (all fetches throw) → `JSONLFetcher` (complete, always on disk, no duplicates).
- Graph returning empty results is **authoritative** — no JSONL fallback triggered for empty.

**Why one unified Cypher query replaces the current five (S01/S04/S05/S03/S08 and friends):** five separate round-trips where S04–S15 returned raw blobs the Python layer was supposed to process but never did. One unified query → one round-trip, same Python processor as JSONL, consistent maintenance.

**Unchanged:** `inventory.py`, `gap.py`, and the `run_bundle_analysis` public interface.

## Architecture

```
context_intelligence/bundle_analysis/
├── __init__.py              ← run_bundle_analysis (passes inventory into run_signals)
├── inventory.py             ← unchanged
├── gap.py                   ← unchanged
├── bundle_usage_tool.py     ← unchanged
├── signals.py               ← thin orchestrator (~40 lines)
├── fetchers.py              ← RawSignalEvent + GraphFetcher + JSONLFetcher (NEW)
├── processor.py             ← AttributionIndexes + process_events (NEW)
└── queries/
    ├── session_signals.cypher    (NEW)
    └── workspace_signals.cypher  (NEW)
```

Data shape: both fetchers emit `list[RawSignalEvent]`. The processor never knows which source produced its input.

## Components

### `RawSignalEvent` — common currency

```python
@dataclass
class RawSignalEvent:
    kind: Literal["agent_spawned", "tool_pre"]
    agent: str | None       # "foundation:explorer"  — agent_spawned only
    tool_name: str | None   # "load_skill", "mode", "recipes", etc. — tool_pre only
    tool_input: dict | None # parsed from JSON string — tool_pre only
```

### `GraphFetcher` — one unified Cypher query per scope

Replaces S01, S03, S04, S05, S08, S09, S12, S15 (and their workspace counterparts).

Session-scope query:

```cypher
MATCH (s:Session {session_id: $session_id})-[:HAS_EVENT]->(e)
WHERE e:DelegateAgentSpawnedEvent
   OR (e:ToolPreEvent AND e.tool_name IN [
        'load_skill', 'mode', 'recipes',
        'graph_query', 'blob_read', 'dot_graph', 'team_knowledge',
        'terminal_inspector', 'generate_image',
        'mcp_deepwiki_read_wiki_structure', 'mcp_deepwiki_read_wiki_contents',
        'mcp_deepwiki_ask_question'
      ])
   OR (e:ToolPreEvent AND e.tool_name STARTS WITH 'mcp_')
RETURN
  CASE WHEN e:DelegateAgentSpawnedEvent THEN 'agent_spawned' ELSE 'tool_pre' END AS kind,
  e.agent      AS agent,
  e.tool_name  AS tool_name,
  e.tool_input AS tool_input_json
```

Workspace-scope variant matches by workspace instead of session:

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_EVENT]->(e)
WHERE ...   -- same predicate
RETURN ...  -- same projection
```

Python converts each row → `RawSignalEvent(kind, agent, tool_name, json.loads(tool_input_json))`.

### `JSONLFetcher` — streaming parser

| Scope | Path |
|---|---|
| Session | `~/.amplifier/projects/{workspace}/sessions/{session_id}/context-intelligence/events.jsonl` |
| Workspace | Glob `~/.amplifier/projects/{workspace}/sessions/*/context-intelligence/events.jsonl` |

For each line:
- `event == "delegate:agent_spawned"` or `"delegate:agent_resumed"` → `RawSignalEvent(kind="agent_spawned", agent=data.agent)`
- `event == "tool:pre"` and `data.tool_name` is in the relevant set (same list as the Cypher predicate) → `RawSignalEvent(kind="tool_pre", tool_name=data.tool_name, tool_input=json.loads(data.tool_input))`
- Everything else is dropped.

The `data.tool_input` JSON string is structurally identical to the `e.tool_input` value the Cypher query projects, so the processor downstream is source-agnostic.

### `AttributionIndexes` — built once per run from inventory

```python
@dataclass
class AttributionIndexes:
    skill_to_bundle: dict[str, str]  # "brainstorming" → "superpowers"
    mode_to_bundle:  dict[str, str]  # "bundle-usage"  → "context-intelligence"
    tool_to_bundle:  dict[str, str]  # "graph_query"   → "context-intelligence"
```

Built by `build_attribution_indexes(inventory)`:
- For each bundle, map each declared skill name → bundle name.
- For each bundle, map each declared mode name → bundle name.
- `tool_to_bundle` is **derived from the inventory** by scanning `behaviors/*.yaml` files in each bundle's cache directory. The scan reads the `tools:` section and applies the naming convention: strip the `tool-` module prefix and replace `-` with `_` to get the invocable tool name. Example: `tool-graph-query` (module name) → `graph_query` (invocable name). `mcp_*` tools follow a different pattern — their invocable names are generated by the MCP integration. These are also present in the behavior YAML `tools:` section and derivable from it. This means the mapping is fully dynamic: no hardcoding, no maintenance list. As bundles add new tools, the attribution indexes update automatically on the next inventory scan.

### `SignalProcessor` — five cases, shared for both sources

```python
def process(events, indexes) -> dict[str, BundleSignals]:
    for event in events:
        if event.kind == "agent_spawned":
            # Direct: "foundation:explorer" → split on ":"
            bundle = event.agent.split(":")[0]
            component = "agents"

        elif event.kind == "tool_pre":
            name = event.tool_name
            inp  = event.tool_input or {}

            if name == "load_skill":
                # skill_name → skill_to_bundle lookup
                bundle = indexes.skill_to_bundle.get(inp.get("skill_name", ""))
                component = "skills"

            elif name == "mode" and inp.get("operation") == "set":
                # {"operation":"set","name":"bundle-usage"} → mode_to_bundle lookup
                bundle = indexes.mode_to_bundle.get(inp.get("name", ""))
                component = "modes"

            elif name == "recipes" and inp.get("operation") == "execute":
                # "@context-intelligence:recipes/foo.yaml" → extract bundle prefix
                bundle = _bundle_from_recipe_path(inp.get("recipe_path", ""))
                component = "recipes"

            else:
                # known bundle-contributed or mcp_ tools
                bundle = indexes.tool_to_bundle.get(name)
                component = "tools"

        if bundle is None:
            continue   # unknown attribution → skip silently
        counts[bundle][component] += 1
```

`_bundle_from_recipe_path` strips the `@bundle:` prefix from paths like `@context-intelligence:recipes/foo.yaml`. Paths without an `@bundle:` prefix are skipped — no bundle attribution is possible.

`bundle is None` → skip. No errors, no defaults, no `unknown` bucket.

### `signals.py` — thin orchestrator

```python
async def run_signals(client, workspace, session_id, base_path, inventory):
    indexes = build_attribution_indexes(inventory)

    # 1. Try graph (fast, handles remote sessions)
    if client.server_url:
        events, server_ok = await _fetch_from_graph(client, workspace, session_id)
        if server_ok:          # server responded, even with zero rows → authoritative
            return process_events(events, indexes)
        # server_ok=False: all fetches threw → fall through to JSONL

    # 2. JSONL (complete, always on disk for local sessions, no duplicates)
    events = _fetch_from_jsonl(workspace, session_id, base_path)
    return process_events(events, indexes)
```

`server_ok = True` when the Cypher call returned normally. `server_ok = False` only when the call threw. A graph empty result is authoritative and does not trigger JSONL fallback.

## Data Flow

1. `run_bundle_analysis` calls `scan_cache` → `inventory`.
2. `run_bundle_analysis` calls `run_signals(client, workspace, session_id, base_path, inventory)`.
3. `run_signals` builds `AttributionIndexes` from inventory.
4. `run_signals` selects source (graph if reachable, JSONL otherwise) and fetches `list[RawSignalEvent]`.
5. `process_events(events, indexes)` walks each event, attributes it to a bundle + component, increments the counter.
6. `run_signals` returns `{bundle: BundleSignals(agents=N, skills=N, modes=N, recipes=N, tools=N)}`.
7. `gap.py` consumes the map. Public interface of `run_bundle_analysis` is unchanged.

### Scope vs source matrix

| Scope | Graph | JSONL |
|---|---|---|
| Session | Query by `session_id` | One file: `sessions/{id}/context-intelligence/events.jsonl` |
| Workspace | Query by `workspace` | Glob: `sessions/*/context-intelligence/events.jsonl` |

## Error Handling

- **Graph fetch throws** → `server_ok=False` → fall through to JSONL. One exception kills the entire graph attempt; we do not retry per-query because there is only one query.
- **Graph returns empty** → `server_ok=True`, events list is `[]`. Authoritative. Processor returns empty counts. No fallback.
- **JSONL file missing** (session scope) → empty event list, empty counts. Not an error.
- **JSONL line fails to parse** → skip that line, continue. No crash on malformed events.
- **Unknown bundle attribution** (skill/mode/tool not in indexes, recipe path without `@bundle:` prefix) → skip event silently. No `unknown` bucket. No log noise.

## Testing Strategy

New tests:

- **`test_fetchers.py`**
  - `GraphFetcher`: mock client, verify the unified Cypher query is sent with correct parameters; verify row-to-`RawSignalEvent` conversion (kind dispatch, JSON parsing of `tool_input_json`).
  - `JSONLFetcher`: write fixture event files, verify filtering by event type and `tool_name`, verify session-scope single file and workspace-scope glob.

- **`test_processor.py`**
  - Five component cases: agents, skills, modes, recipes, tools each produce the correct attribution.
  - `build_attribution_indexes`: skill/mode declarations are correctly aggregated; tool-to-bundle includes the maintained set.
  - `_bundle_from_recipe_path`: `@bundle:path` → `bundle`, plain path → `None`.
  - Unknown attribution (skill/mode/tool not in indexes) → event skipped, no exception.

Updated tests:

- **`test_signals.py`**
  - Fallback triggers when graph fetch raises.
  - No fallback when graph returns empty (authoritative).
  - Source selection: missing `client.server_url` → JSONL directly.

## Code Changes Summary

**New files:**

```
context_intelligence/bundle_analysis/
├── fetchers.py
├── processor.py
└── queries/
    ├── session_signals.cypher
    └── workspace_signals.cypher
```

**Rewritten:**

- `signals.py` — thin orchestrator (~40 lines): select source → fetch → build indexes → process. Removes `_SESSION_QUERY_MAP`, `_WORKSPACE_QUERY_MAP`, `_select_query`, `_extract_cypher`.
- `__init__.py` — passes `inventory` into `run_signals` so attribution indexes can be built from already-scanned data.

**Deleted:**

- `jsonl_signals.py` — replaced by `JSONLFetcher` in `fetchers.py`.
- Old session-scope query files replaced by `session_signals.cypher`: `s01_agents_in_session.cypher`, `s03_recipe_execute_in_session.cypher`, `s04_skill_load_in_session.cypher`, `s05_mode_set_in_session.cypher`, `s08_used_bundles_in_session.cypher`, `s09_skill_loaded_in_session.cypher`, `s12_mode_changed_in_session.cypher`, `s15_bundle_contributed_tools_in_session.cypher`.
- Old workspace-scope query files replaced by `workspace_signals.cypher`.

**Kept unchanged:** `inventory.py`, `gap.py`, `bundle_usage_tool.py`. Public interface of `run_bundle_analysis` stays identical.

## Open Questions

- **Unattributed recipe paths.** Recipe `execute` calls whose `recipe_path` does not start with `@bundle:` are silently skipped. Acceptable for V1; revisit if usage analytics show many such calls being lost.
- **Workspace scope JSONL coverage.** Workspace-scope JSONL aggregation only covers sessions whose `events.jsonl` files are on local disk. The graph fetcher covers remote sessions too. When the server is unreachable, workspace-scope counts will silently exclude remote sessions. This is an acceptable degradation for V1 — it matches the existing fallback semantics — but worth flagging if cross-machine workspace usage becomes common.
