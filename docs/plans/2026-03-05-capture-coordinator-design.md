# Capture Coordinator on HookStateService and MountFlow

## Goal

Store a reference to the coordinator on `HookStateService` so that handlers and the service layer can access it at event-handling time, not just at mount time. This enables future lazy evaluation of settings and properties using a config-first, coordinator-fallback resolution pattern.

## Background

Currently `MountFlow` receives the coordinator as a parameter to several methods (`discover_events()`, `register_specific_handlers()`, `register_default_handler()`, `run()`) but never stores it. `HookStateService` holds only `.config` and `.graph`. Once mount completes, the coordinator reference is discarded and handlers have no path to it.

Handlers need coordinator access at event-handling time for lazy resolution of settings and properties. The established pattern from prior experience is: resolve from config first, fall back to coordinator if config doesn't have the value.

## Approach

Capture the coordinator reference during mount and store it on `HookStateService`. Minimal change, no new dependencies, backward compatible. We store it now but don't use it yet -- the lazy evaluation patterns will come in a future pass.

## Components

### 1. HookStateService gains coordinator parameter

```python
class HookStateService:
    """Top-level service container shared across all handlers."""

    def __init__(self, raw_config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = HookConfig(raw_config)
        self.graph = GraphState()
        self.coordinator = coordinator
```

- `coordinator: Any = None` -- optional, defaults to `None` so existing tests that create `HookStateService(raw_config={})` don't break
- Handlers access coordinator via `self.services.coordinator`

### 2. MountFlow.create_services() receives and passes coordinator

```python
def create_services(self, coordinator: Any) -> None:
    """INIT -> STATE_CREATED: Instantiate HookStateService from config."""
    self.services = HookStateService(self._config, coordinator=coordinator)
    self.state = MountState.STATE_CREATED
```

### 3. MountFlow.run() passes coordinator to create_services()

```python
async def run(self, coordinator: Any) -> Callable:
    self.create_services(coordinator)  # now receives coordinator
    self.instantiate_handlers()
    await self.discover_events(coordinator)
    self.register_specific_handlers(coordinator)
    self.register_default_handler(coordinator)
    ...
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| `coordinator: Any = None` default | Backward compatible -- existing tests and code that don't pass coordinator continue to work |
| Store on HookStateService, not MountFlow | Handlers access services, not the mount flow. The coordinator needs to be reachable from handler code |
| No usage yet | We're capturing it for future lazy evaluation. Resolution pattern will be: config first, coordinator fallback |
| Type is `Any` | Matches existing pattern throughout the codebase -- coordinator is typed as `Any` in `mount()`, `MountFlow`, and `protocol.py` |

## Testing Strategy

- Verify `HookStateService` stores coordinator when passed
- Verify `HookStateService.coordinator` is `None` when not passed (backward compat)
- Verify `MountFlow` passes coordinator through to `HookStateService`
- Verify handlers can access coordinator via `self.services.coordinator`

## Open Questions

None -- this is a capture-only change. The lazy evaluation resolution pattern (config first, coordinator fallback) will be designed when we build the features that need it.
