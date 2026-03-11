# ConfigResolver Design

## Goal

Introduce a `ConfigResolver` service that centralizes all configuration resolution for the context-intelligence hook. Every config value follows a consistent lazy fallback chain: **explicit hook config → coordinator.config → sensible default**. This eliminates scattered config reads, aligns the hook with how the app-cli populates coordinator config, and makes the hook composable with any Amplifier application that stamps project metadata into the coordinator.

## Background — Why This Is Needed

### Current State

The hook's `mount()` function reads config values directly from the raw config dict passed by the runtime. It hardcodes `base_path` default to `~/.amplifier/projects`, derives `project_slug` independently via `coordinator.get_capability("session.working_dir")`, and reads `graph_forest_name` from a nested config key. The hook **never reads `coordinator.config`** even though the app-cli stamps `project_slug`, `project_dir`, `project_name`, and `working_dir` into it.

### What the App-CLI Provides

The app-cli's `session_runner.py` stamps these keys into `coordinator.config` (= `session.config`):

- `project_slug` — CWD-derived filesystem key (e.g., `-home-user-repos-myapp`)
- `project_dir` — full CWD path
- `project_name` — `Path(cwd).name`
- `working_dir` — same as `project_dir`
- `root_session_id`, `application_host`, `bundle_name`

The app-cli's `SessionStore` constructs paths as `~/.amplifier/projects/{project_slug}/sessions/{session_id}/`. The hook should align with this instead of duplicating path logic.

### The Problem

Config reading is scattered across `__init__.py`, `logging_handler.py`, `graph_data_hook.py`, and `services.py`. Each component reads raw config independently with its own defaults. There's no consistent fallback to coordinator values. The `base_path` config key duplicates what the coordinator already knows. The `graph_forest_name` defaults to `"default"` instead of the project slug.

## Approach

Extract all config resolution into a single `ConfigResolver` class instantiated once in `mount()`. Every property follows the same three-step lazy chain (config → coordinator.config → default), with results cached after first resolution. All downstream components receive the resolver instead of raw config dicts, eliminating direct config or coordinator access throughout the codebase.

## Architecture

```
mount()
  │
  ├─ config dict (from bundle YAML)
  ├─ coordinator (from runtime)
  │
  └──► ConfigResolver (single instance)
         │
         ├──► LoggingHandler      — consumes session_dir()
         ├──► GraphDataHook       — consumes forest_name, neo4j_config
         ├──► Neo4jGraphStore     — consumes forest_name, neo4j_config
         └──► HookStateService    — consumes exclude_events, enable_graph
```

The resolver is the **only** component that touches raw config or `coordinator.config`. All other components receive resolved values through its properties.

## The Three Resolution Chains

All chains follow identical structure: **config → coordinator → default**.

### `base_path` (filesystem storage root)

```
1. config.get("base_path")                         → explicit in hook config
2. coordinator.config.get("base_path")              → if app or another hook stamps it
3. fallback: "~/.amplifier/projects"
```

### `project_slug` (filesystem partition key / path segment)

```
1. config.get("project_slug")                       → explicit in hook config
2. coordinator.config.get("project_slug")            → app-cli stamps this from CWD
3. fallback: "default"
```

### `forest_name` (graph partition identity)

The forest chain is one step longer because it has an intermediate `project` key:

```
1. config["graph_store"].get("graph_forest_name")   → explicit in graph store config
2. config.get("project")                            → project key in hook config
3. coordinator.config.get("project_slug")            → app-cli stamps this from CWD
4. fallback: "default"
```

### Storage Path Construction (after resolution)

```
{base_path}/{project_slug}/sessions/{session_id}/context-intelligence/
```

## Components

### ConfigResolver

```python
class ConfigResolver:
    """Lazy config resolution: explicit config → coordinator.config → default.
    
    Single source of truth for all hook configuration. No component should
    read raw config or coordinator.config directly.
    """
    
    def __init__(self, config: dict, coordinator: Any):
        self._config = config
        self._coordinator = coordinator
        self._cache = {}
    
    @property
    def base_path(self) -> Path:
        """Resolve base_path: config → coordinator → ~/.amplifier/projects"""
    
    @property
    def project_slug(self) -> str:
        """Resolve project_slug: config → coordinator → 'default'"""
    
    @property
    def forest_name(self) -> str:
        """Resolve forest: graph_store config → config.project → coordinator.project_slug → 'default'"""
    
    @property
    def enable_graph(self) -> bool: ...
    
    @property
    def graph_store_config(self) -> dict | None: ...
    
    @property
    def neo4j_config(self) -> dict | None: ...
    
    @property
    def exclude_events(self) -> set[str]: ...
    
    @property
    def log_level(self) -> str: ...
    
    def session_dir(self, session_id: str) -> Path:
        """Construct full session directory path from resolved values."""
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"
```

Each property: checks cache → checks config → checks coordinator.config → returns default.

### How Components Consume the Resolver

**`mount()` becomes thin wiring:**

```python
async def mount(coordinator, config=None):
    config = config or {}
    resolver = ConfigResolver(config, coordinator)
    logging_handler = LoggingHandler(resolver)
    if resolver.enable_graph and resolver.graph_store_config:
        store = create_neo4j_store(resolver)
        graph_hook = GraphDataHook(store, ...)
```

**`LoggingHandler`** receives the resolver, calls `resolver.session_dir(session_id)`.

**`_create_neo4j_store`** receives the resolver, calls `resolver.forest_name` and `resolver.neo4j_config`.

**`HookStateService`** receives the resolver instead of raw config dict.

No component reads raw config or coordinator directly.

## Data Flow

1. Runtime calls `mount(coordinator, config)`.
2. `mount()` creates a single `ConfigResolver(config, coordinator)`.
3. Resolver is passed to each component as its sole config source.
4. When a component first accesses a property (e.g., `resolver.base_path`):
   - Resolver checks its internal cache — returns immediately if cached.
   - Checks `config.get("base_path")` — returns and caches if present.
   - Checks `coordinator.config.get("base_path")` — returns and caches if present.
   - Returns and caches the hardcoded default.
5. Subsequent accesses to the same property return the cached value.

## Updated Config Example

```yaml
hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      # All path/identity values below are optional — resolved lazily:
      #   config → coordinator.config → default
      # base_path: "~/.amplifier/projects"
      # project_slug: "my-project"
      # project: "my-project"
      exclude_events: []
      log_level: "WARNING"
      enable_graph: false
      graph_store:
        type: "neo4j"
        # graph_forest_name: "my-project"
        config:
          uri: '${NEO4J_URI:-bolt://localhost:7687}'
          username: '${NEO4J_USERNAME:-neo4j}'
          password: '${NEO4J_PASSWORD}'
          database: '${NEO4J_DATABASE:-neo4j}'
```

## Error Handling

- If `coordinator.config` is not accessible (e.g., coordinator doesn't expose `.config`), the resolver catches the `AttributeError` and falls through to the default. The hook never crashes due to a missing coordinator capability.
- If `config["graph_store"]` is missing or not a dict, `forest_name` safely skips that step and continues the chain.
- All resolution is lazy — properties that are never accessed never trigger fallback logic.

## Testing Strategy

1. **Unit tests for ConfigResolver**: each chain independently, caching behavior, graceful coordinator fallback when coordinator.config is absent or empty.
2. **Integration tests**: mount with minimal config + mock coordinator, mount with explicit overrides that short-circuit the chain.
3. **Existing tests continue passing** — same defaults as current hardcoded values, so behavior is identical when no config or coordinator values are present.

## Open Questions

None — all sections validated. Ready for implementation.
