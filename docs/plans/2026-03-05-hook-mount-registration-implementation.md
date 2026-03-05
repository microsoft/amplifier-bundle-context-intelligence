# Hook Mount and Registration Flow — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Implement the deterministic 6-state mount flow that discovers events, matches them to 7 protocol-conforming handlers, and registers each non-excluded event to exactly one handler — all inside a compliant Amplifier bundle.

**Architecture:** A hook module (`hook-context-intelligence`) exposes `mount()` as its entry point. Mount creates a `HookStateService` (containing `GraphState` + `HookConfig`), instantiates 7 handlers conforming to an `EventHandler` protocol, discovers events via the coordinator's contribution channels, and registers handlers deterministically. The mount flow is a 6-state machine: INIT → STATE_CREATED → HANDLERS_INSTANTIATED → EVENTS_DISCOVERED → SPECIFIC_REGISTERED → DEFAULT_REGISTERED → READY.

**Tech Stack:** Python ≥3.11, hatchling build, uv dependency management, pytest + pytest-asyncio, no runtime dependencies (amplifier-core is a peer provided by host).

**Design Doc:** `docs/plans/2026-03-05-hook-mount-registration-design.md`

---

## File Structure (target)

```
amplifier-bundle-context-intelligence/
├── bundle.md
├── behaviors/
│   └── context-intelligence.yaml
├── modules/
│   └── hook-context-intelligence/
│       ├── pyproject.toml
│       └── amplifier_module_hook_context_intelligence/
│           ├── __init__.py
│           ├── protocol.py
│           ├── services.py
│           ├── handlers/
│           │   ├── __init__.py
│           │   ├── session.py
│           │   ├── orchestrator_run.py
│           │   ├── step.py
│           │   ├── recipe_step.py
│           │   ├── tool_execution.py
│           │   ├── event.py
│           │   └── default.py
│           └── mount.py
├── docs/
│   ├── plans/
│   │   ├── 2026-03-05-hook-mount-registration-design.md
│   │   └── 2026-03-05-hook-mount-registration-implementation.md
│   ├── hook-mount-registration-flow.dot
│   └── hook-event-discovery-and-dispatch.dot
└── README.md
```

Tests live at `modules/hook-context-intelligence/tests/` (sibling to the package):

```
modules/hook-context-intelligence/tests/
├── conftest.py
├── test_bundle.py
├── test_module_loading.py
├── test_protocol.py
├── test_services.py
├── test_handlers.py
└── test_mount_flow.py
```

---

## Phase 1 — Bundle Skeleton

### Task 1: Create pyproject.toml

**Files:**
- Create: `modules/hook-context-intelligence/pyproject.toml`

**Step 1: Create the pyproject.toml**

Create `modules/hook-context-intelligence/pyproject.toml`:

```toml
[project]
name = "amplifier-module-hook-context-intelligence"
version = "0.1.0"
description = "Context intelligence hook — event-driven property graph builder for Amplifier sessions"
requires-python = ">=3.11"
license = "MIT"

dependencies = []

[project.entry-points."amplifier.modules"]
hook-context-intelligence = "amplifier_module_hook_context_intelligence:mount"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = true

[tool.hatch.build.targets.wheel]
packages = ["amplifier_module_hook_context_intelligence"]

[dependency-groups]
dev = [
    "amplifier-core",
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pyyaml>=6.0",
    "pyright>=1.1",
    "ruff>=0.4",
]

[tool.uv.sources]
amplifier-core = { git = "https://github.com/microsoft/amplifier-core", branch = "main" }

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "basic"

[tool.ruff]
target-version = "py311"
line-length = 100
```

**Step 2: Create the virtual environment and sync**

Run from the module directory:

```bash
cd modules/hook-context-intelligence && uv sync
```

Expected: Clean install, `.venv/` created, dev dependencies resolved.

**Step 3: Commit**

```bash
cd modules/hook-context-intelligence && git add pyproject.toml && git commit -m "feat: add pyproject.toml for hook-context-intelligence module"
```

---

### Task 2: Create Python package with `__init__.py` and minimal `mount()`

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`
- Test: `modules/hook-context-intelligence/tests/test_mount.py`

**Step 1: Write the failing tests**

Create `modules/hook-context-intelligence/tests/__init__.py` (empty file).

Create `modules/hook-context-intelligence/tests/test_mount.py`:

```python
"""Tests for the mount() entry point — basic contract."""

from __future__ import annotations

import inspect


def test_module_type_is_hook():
    from amplifier_module_hook_context_intelligence import __amplifier_module_type__

    assert __amplifier_module_type__ == "hook"


def test_mount_is_coroutine():
    from amplifier_module_hook_context_intelligence import mount

    assert inspect.iscoroutinefunction(mount)


def test_mount_signature_accepts_coordinator_and_config():
    from amplifier_module_hook_context_intelligence import mount

    sig = inspect.signature(mount)
    params = list(sig.parameters.keys())
    assert params[0] == "coordinator"
    assert params[1] == "config"


async def test_mount_returns_cleanup_callable():
    from unittest.mock import MagicMock

    from amplifier_module_hook_context_intelligence import mount

    coordinator = MagicMock()
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock())
    coordinator.collect_contributions = MagicMock(return_value=[])
    coordinator.get_capability = MagicMock(return_value=None)

    result = await mount(coordinator, config={})

    # mount must return None or a callable (cleanup function)
    assert result is None or callable(result)
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_mount.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_module_hook_context_intelligence'`

**Step 3: Write minimal implementation**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`:

```python
"""Amplifier module: context-intelligence hook.

Observes orchestrator events and builds a property graph representing
sessions, runs, steps, tool executions, and system events.

Listed under ``hooks:`` in behavior YAML. The entry point is named
``hook-context-intelligence`` and the module declares
``__amplifier_module_type__ = "hook"`` so the kernel classifies it as
a hook via explicit type declaration (tier 1).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

__amplifier_module_type__ = "hook"

logger = logging.getLogger(__name__)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> Callable | None:
    """Mount the context-intelligence hook module.

    Args:
        coordinator: The ModuleCoordinator provided by the kernel.
        config: Configuration dict from the behavior YAML.

    Returns:
        A cleanup callable, or None if nothing to clean up.
    """
    logger.info("context-intelligence hook: mount called (stub)")
    return None
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_mount.py -v
```

Expected: 4 passed.

**Step 5: Commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "feat: add minimal mount() entry point with module type declaration"
```

---

### Task 3: Create behavior YAML

**Files:**
- Create: `behaviors/context-intelligence.yaml`

**Step 1: Create the behavior YAML**

Create `behaviors/context-intelligence.yaml`:

```yaml
bundle:
  name: context-intelligence-behavior
  version: 0.1.0
  description: |
    Context intelligence hooks for building a property graph
    from orchestrator events.

hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      exclude_events:
        - "content_block:delta"
        - "thinking:delta"
        - "session-naming:*"
        - "orchestrator:rate_limit_delay"
        - "provider:request"
        - "provider:response"
        - "provider:error"
        - "provider:tool_sequence_repaired"
        - "provider:background_status"
        - "provider:incomplete_continuation"
      log_level: "${CI_LOG_LEVEL:WARNING}"
```

**Step 2: Commit**

```bash
git add behaviors/context-intelligence.yaml && git commit -m "feat: add context-intelligence behavior YAML declaring hook module"
```

---

### Task 4: Create bundle.md

**Files:**
- Create: `bundle.md`

**Step 1: Create the thin bundle**

Create `bundle.md`:

```markdown
---
bundle:
  name: context-intelligence
  version: 0.1.0
  description: >
    Context intelligence: event-driven property graph builder
    for Amplifier sessions.

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: context-intelligence:behaviors/context-intelligence
---

# Context Intelligence

---

@foundation:context/shared/common-system-base.md
```

**Step 2: Commit**

```bash
git add bundle.md && git commit -m "feat: add thin bundle.md with foundation include and behavior"
```

---

### Task 5: Validate bundle structure

**Files:**
- Create: `modules/hook-context-intelligence/tests/test_bundle.py`
- Create: `modules/hook-context-intelligence/tests/test_module_loading.py`

**Step 1: Write the bundle validation tests**

Create `modules/hook-context-intelligence/tests/test_bundle.py`:

```python
"""Validation tests for the context-intelligence Amplifier bundle structure."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
MODULE_ROOT = Path(__file__).parent.parent


class TestBundleRoot:
    """Validate bundle.md exists and has correct frontmatter."""

    def test_bundle_md_exists(self):
        assert (REPO_ROOT / "bundle.md").is_file()

    def test_bundle_md_has_frontmatter(self):
        content = (REPO_ROOT / "bundle.md").read_text()
        assert content.startswith("---")
        parts = content.split("---", 2)
        assert len(parts) >= 3, "bundle.md must have YAML frontmatter between --- delimiters"
        fm = yaml.safe_load(parts[1])
        assert fm["bundle"]["name"] == "context-intelligence"
        assert "version" in fm["bundle"]
        assert "description" in fm["bundle"]

    def test_bundle_md_includes_foundation(self):
        content = (REPO_ROOT / "bundle.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        includes = fm.get("includes", [])
        bundle_refs = [i["bundle"] for i in includes if "bundle" in i]
        assert any("amplifier-foundation" in ref for ref in bundle_refs)

    def test_bundle_md_includes_behavior(self):
        content = (REPO_ROOT / "bundle.md").read_text()
        fm = yaml.safe_load(content.split("---", 2)[1])
        includes = fm.get("includes", [])
        bundle_refs = [i["bundle"] for i in includes if "bundle" in i]
        assert any(
            "context-intelligence:behaviors/context-intelligence" in ref for ref in bundle_refs
        )

    def test_no_root_pyproject_toml(self):
        """Bundles are configuration, not Python packages — no root pyproject.toml."""
        assert not (REPO_ROOT / "pyproject.toml").exists()


class TestBehaviorYaml:
    """Validate behavior YAML structure."""

    def _load_behavior(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_behavior_yaml_exists(self):
        assert (REPO_ROOT / "behaviors" / "context-intelligence.yaml").is_file()

    def test_behavior_has_hooks_section(self):
        data = self._load_behavior()
        assert "hooks" in data, "Behavior YAML must have a hooks: section"

    def test_behavior_hook_module_name(self):
        data = self._load_behavior()
        hook_specs = data.get("hooks", [])
        assert len(hook_specs) >= 1
        assert hook_specs[0]["module"] == "hook-context-intelligence"

    def test_behavior_hook_has_source(self):
        data = self._load_behavior()
        hook_spec = data["hooks"][0]
        assert "source" in hook_spec, "Hook spec must have a source field"

    def test_behavior_hook_has_config(self):
        data = self._load_behavior()
        hook_spec = data["hooks"][0]
        assert "config" in hook_spec, "Hook spec must have a config field"
        config = hook_spec["config"]
        assert "exclude_events" in config

    def test_behavior_hook_is_in_hooks_section_not_tools(self):
        """__amplifier_module_type__ is 'hook', so the module belongs under hooks:."""
        data = self._load_behavior()
        hook_modules = [h["module"] for h in data.get("hooks", [])]
        assert "hook-context-intelligence" in hook_modules
        tool_modules = [t["module"] for t in data.get("tools", [])]
        assert "hook-context-intelligence" not in tool_modules
```

**Step 2: Write the module loading validation tests**

Create `modules/hook-context-intelligence/tests/test_module_loading.py`:

```python
"""Tests for module loading, entry point resolution, and YAML consistency.

Verifies the full contract between the pyproject.toml entry point, the bundle
YAML behavior file, and the module's mount() function — including the explicit
``__amplifier_module_type__`` declaration that the kernel uses (tier 1) for
type classification.
"""

import importlib.metadata
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
MODULE_ROOT = Path(__file__).parent.parent


class TestEntryPointDiscovery:
    """Verify the module is discoverable via Python entry points."""

    def test_hook_entry_point_exists(self):
        """The hook entry point must be registered in amplifier.modules."""
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        ep_names = [ep.name for ep in eps]
        assert "hook-context-intelligence" in ep_names

    def test_hook_entry_point_loads_mount_function(self):
        """The entry point must resolve to a callable mount() function."""
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        hook_ep = next(ep for ep in eps if ep.name == "hook-context-intelligence")
        mount_fn = hook_ep.load()
        assert callable(mount_fn)

    def test_hook_entry_point_target_is_correct(self):
        """The entry point must point to the correct Python path."""
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        hook_ep = next(ep for ep in eps if ep.name == "hook-context-intelligence")
        assert hook_ep.value == "amplifier_module_hook_context_intelligence:mount"


class TestModuleTypeClassification:
    """Verify the module declares the correct type for kernel classification."""

    def test_module_type_is_hook(self):
        """__amplifier_module_type__ must be 'hook'."""
        import amplifier_module_hook_context_intelligence

        assert amplifier_module_hook_context_intelligence.__amplifier_module_type__ == "hook"

    def test_naming_convention_matches_explicit_type(self):
        """Both __amplifier_module_type__ and name-based guessing agree on 'hook'.

        The entry point name 'hook-context-intelligence' contains 'hook',
        so _guess_from_naming() returns 'hook' (tier 2). The module also
        declares __amplifier_module_type__ = 'hook' (tier 1). Both agree.
        """
        import amplifier_module_hook_context_intelligence

        explicit_type = amplifier_module_hook_context_intelligence.__amplifier_module_type__
        assert explicit_type == "hook"

        module_id = "hook-context-intelligence"
        type_mapping = {
            "orchestrat": "orchestrator",
            "loop": "orchestrator",
            "provider": "provider",
            "tool": "tool",
            "hook": "hook",
            "context": "context",
        }
        guessed_type = "tool"  # default fallback
        for keyword, mod_type in type_mapping.items():
            if keyword in module_id.lower():
                guessed_type = mod_type
                break

        assert guessed_type == "hook"
        assert explicit_type == guessed_type


class TestBundleYamlEntryPointConsistency:
    """Verify the bundle YAML module name matches the entry point exactly."""

    def _load_behavior_yaml(self) -> dict:
        path = REPO_ROOT / "behaviors" / "context-intelligence.yaml"
        return yaml.safe_load(path.read_text())

    def test_behavior_yaml_module_matches_entry_point(self):
        """The module name in behavior YAML must match the entry point exactly."""
        data = self._load_behavior_yaml()
        hook_specs = data.get("hooks", [])
        assert len(hook_specs) >= 1
        module_name = hook_specs[0]["module"]
        assert module_name == "hook-context-intelligence"

    def test_entry_point_resolution_would_succeed(self):
        """Simulate the kernel's exact entry point lookup logic."""
        module_id = "hook-context-intelligence"
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        mount_fn = None
        for ep in eps:
            if ep.name == module_id:
                mount_fn = ep.load()
                break
        assert mount_fn is not None, (
            f"Entry point lookup for '{module_id}' failed. "
            f"Available: {[ep.name for ep in eps]}"
        )


class TestPyprojectStructure:
    """Validate pyproject.toml has correct structure for Amplifier modules."""

    def _load_pyproject(self) -> dict:
        import tomllib

        path = MODULE_ROOT / "pyproject.toml"
        with open(path, "rb") as f:
            return tomllib.load(f)

    def test_has_amplifier_modules_entry_points(self):
        data = self._load_pyproject()
        eps = data["project"]["entry-points"]["amplifier.modules"]
        assert isinstance(eps, dict)
        assert len(eps) >= 1

    def test_hook_entry_point_format(self):
        """Entry point must be module_id = 'package.path:function'."""
        data = self._load_pyproject()
        eps = data["project"]["entry-points"]["amplifier.modules"]
        hook_ep = eps["hook-context-intelligence"]
        assert ":" in hook_ep, "Entry point must use 'module:attr' format"
        module_path, attr = hook_ep.split(":")
        assert attr == "mount"
        assert module_path == "amplifier_module_hook_context_intelligence"

    def test_no_runtime_dependencies(self):
        """Hook module has zero runtime dependencies (amplifier-core is a peer)."""
        data = self._load_pyproject()
        deps = data["project"].get("dependencies", [])
        assert deps == [], f"Expected zero runtime dependencies, got: {deps}"

    def test_hatchling_build_backend(self):
        data = self._load_pyproject()
        assert data["build-system"]["build-backend"] == "hatchling.build"

    def test_uv_package_true(self):
        data = self._load_pyproject()
        assert data["tool"]["uv"]["package"] is True
```

**Step 3: Run all tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: All tests pass (bundle structure validated).

**Step 4: Commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "test: add bundle structure and module loading validation tests"
```

---

## Phase 2 — Handler Protocol + HookStateService

### Task 6: Define EventHandler protocol

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/protocol.py`
- Create: `modules/hook-context-intelligence/tests/test_protocol.py`

**Step 1: Write the failing tests**

Create `modules/hook-context-intelligence/tests/test_protocol.py`:

```python
"""Tests for the EventHandler protocol."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult


def test_event_handler_is_runtime_checkable():
    """EventHandler must be a runtime-checkable Protocol."""
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    assert hasattr(EventHandler, "__protocol_attrs__") or hasattr(
        EventHandler, "_is_runtime_protocol"
    )


def test_conforming_class_passes_isinstance():
    """A class with handled_events, __call__, and services passes isinstance check."""
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    class FakeHandler:
        handled_events: set[str] = {"test:event"}
        services = None  # type: ignore

        async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
            return HookResult(action="continue")

    handler = FakeHandler()
    assert isinstance(handler, EventHandler)


def test_missing_handled_events_fails_isinstance():
    """A class missing handled_events does NOT pass isinstance check."""
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    class BadHandler:
        services = None  # type: ignore

        async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
            return HookResult(action="continue")

    handler = BadHandler()
    assert not isinstance(handler, EventHandler)


def test_missing_call_fails_isinstance():
    """A class missing __call__ does NOT pass isinstance check."""
    from amplifier_module_hook_context_intelligence.protocol import EventHandler

    class BadHandler:
        handled_events: set[str] = {"test:event"}
        services = None  # type: ignore

    handler = BadHandler()
    assert not isinstance(handler, EventHandler)
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_protocol.py -v
```

Expected: FAIL — `ModuleNotFoundError` for `protocol` module.

**Step 3: Write the implementation**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/protocol.py`:

```python
"""EventHandler protocol — the contract all handlers conform to.

Every handler in the context-intelligence hook module implements this
protocol. It is deliberately minimal:

- ``handled_events`` — what events this handler owns (immutable after construction)
- ``__call__`` — how the handler is invoked with an event
- ``services`` — access to shared services (HookStateService)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from amplifier_core.models import HookResult


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for all context-intelligence event handlers."""

    handled_events: set[str]
    """The set of event names this handler owns.

    Immutable after construction. Used during mount to match
    discovered events to handlers.
    """

    services: Any
    """HookStateService instance injected at construction.

    Typed as Any here to avoid circular imports. The concrete type
    is HookStateService from services.py.
    """

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a dispatched event.

        Args:
            event: The event name (e.g. "session:start").
            data: Event-specific data dict.

        Returns:
            HookResult(action="continue") for observational hooks.
        """
        ...
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_protocol.py -v
```

Expected: 4 passed.

**Step 5: Commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "feat: add EventHandler protocol (runtime-checkable)"
```

---

### Task 7: Define HookStateService, GraphState, and HookConfig

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py`
- Create: `modules/hook-context-intelligence/tests/test_services.py`

**Step 1: Write the failing tests**

Create `modules/hook-context-intelligence/tests/test_services.py`:

```python
"""Tests for HookStateService, GraphState, and HookConfig."""

from __future__ import annotations


class TestHookConfig:
    """HookConfig holds exclusion filters and settings."""

    def test_construction_with_empty_config(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={})
        assert config.exclude_events == set()

    def test_construction_with_exclude_events(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={
            "exclude_events": ["content_block:delta", "thinking:delta"]
        })
        assert config.exclude_events == {"content_block:delta", "thinking:delta"}

    def test_is_excluded_exact_match(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={"exclude_events": ["session:start"]})
        assert config.is_excluded("session:start") is True
        assert config.is_excluded("session:end") is False

    def test_is_excluded_wildcard_match(self):
        from amplifier_module_hook_context_intelligence.services import HookConfig

        config = HookConfig(raw_config={"exclude_events": ["session-naming:*"]})
        assert config.is_excluded("session-naming:foo") is True
        assert config.is_excluded("session-naming:bar") is True
        assert config.is_excluded("session:start") is False


class TestGraphState:
    """GraphState provides node/edge operations and session tracking."""

    def test_construction(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph.current_session is None
        assert graph.current_run is None
        assert graph.current_step is None
        assert graph.step_counter == 0

    def test_upsert_node_creates_node(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        graph.upsert_node("s1", labels={"Session"}, properties={"started": True})
        node = graph.get_node("s1")
        assert node is not None
        assert node["labels"] == {"Session"}
        assert node["properties"]["started"] is True

    def test_upsert_node_updates_existing(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        graph.upsert_node("s1", labels={"Session"}, properties={"started": True})
        graph.upsert_node("s1", labels={"Session"}, properties={"ended": True})
        node = graph.get_node("s1")
        assert node is not None
        assert node["properties"]["started"] is True
        assert node["properties"]["ended"] is True

    def test_upsert_edge_creates_edge(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        graph.upsert_edge("s1", "r1", edge_type="CONTAINS_RUN", properties={})
        edge = graph.get_edge("s1", "r1", edge_type="CONTAINS_RUN")
        assert edge is not None

    def test_get_nonexistent_node_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph.get_node("nonexistent") is None

    def test_get_nonexistent_edge_returns_none(self):
        from amplifier_module_hook_context_intelligence.services import GraphState

        graph = GraphState()
        assert graph.get_edge("a", "b", edge_type="X") is None


class TestHookStateService:
    """HookStateService is the service container for all handlers."""

    def test_construction(self):
        from amplifier_module_hook_context_intelligence.services import (
            GraphState,
            HookConfig,
            HookStateService,
        )

        service = HookStateService(raw_config={})
        assert isinstance(service.graph, GraphState)
        assert isinstance(service.config, HookConfig)

    def test_graph_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={})
        service.graph.upsert_node("test", labels={"Test"}, properties={})
        assert service.graph.get_node("test") is not None

    def test_config_accessible(self):
        from amplifier_module_hook_context_intelligence.services import HookStateService

        service = HookStateService(raw_config={"exclude_events": ["foo:bar"]})
        assert service.config.is_excluded("foo:bar") is True
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_services.py -v
```

Expected: FAIL — `ModuleNotFoundError` for `services` module.

**Step 3: Write the implementation**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py`:

```python
"""Shared services for all context-intelligence handlers.

HookStateService is the top-level service container injected into every handler.
It provides access to:
- graph: GraphState — node/edge retrieval, upsert, session tracking
- config: HookConfig — exclusion filters, settings
"""

from __future__ import annotations

import fnmatch
from typing import Any


class HookConfig:
    """Configuration for the context-intelligence hook.

    Holds exclusion filters, verbosity settings, and connection parameters
    parsed from the behavior YAML config dict.
    """

    def __init__(self, raw_config: dict[str, Any]) -> None:
        self._raw = raw_config
        self._exclude_patterns: set[str] = set(raw_config.get("exclude_events", []))

    @property
    def exclude_events(self) -> set[str]:
        """The set of event exclusion patterns (may contain wildcards)."""
        return self._exclude_patterns

    def is_excluded(self, event: str) -> bool:
        """Check if an event matches any exclusion pattern.

        Supports exact match and fnmatch-style wildcards (e.g. "session-naming:*").
        """
        for pattern in self._exclude_patterns:
            if fnmatch.fnmatch(event, pattern):
                return True
        return False


class GraphState:
    """In-memory property graph state.

    Storage-agnostic — defines operations, not the backend.
    The actual persistence layer will be plugged in later.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}

        # Per-session tracking
        self.current_session: str | None = None
        self.current_run: str | None = None
        self.current_step: str | None = None
        self.step_counter: int = 0
        self.pending_delegate_tool_call_id: str | None = None

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve an existing node by ID, or None if not found."""
        return self._nodes.get(node_id)

    def upsert_node(
        self,
        node_id: str,
        labels: set[str],
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update a node. Properties are merged (not replaced)."""
        existing = self._nodes.get(node_id)
        if existing is not None:
            existing["labels"] |= labels
            existing["properties"].update(properties)
            return existing
        node = {"id": node_id, "labels": set(labels), "properties": dict(properties)}
        self._nodes[node_id] = node
        return node

    def get_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
    ) -> dict[str, Any] | None:
        """Retrieve an existing edge, or None if not found."""
        return self._edges.get((source, target, edge_type))

    def upsert_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update an edge. Properties are merged (not replaced)."""
        key = (source, target, edge_type)
        existing = self._edges.get(key)
        if existing is not None:
            existing["properties"].update(properties)
            return existing
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
            "properties": dict(properties),
        }
        self._edges[key] = edge
        return edge


class HookStateService:
    """Top-level service container shared across all handlers.

    Extensible — additional services can be added later without
    changing the handler protocol.
    """

    def __init__(self, raw_config: dict[str, Any]) -> None:
        self.config = HookConfig(raw_config)
        self.graph = GraphState()
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_services.py -v
```

Expected: All passed.

**Step 5: Commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "feat: add HookStateService, GraphState, and HookConfig"
```

---

## Phase 3 — Handler Implementations (stubs conforming to protocol)

### Task 8: Create handlers package and shared test fixtures

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/__init__.py`
- Create: `modules/hook-context-intelligence/tests/conftest.py`

**Step 1: Create the handlers package `__init__.py`**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/__init__.py`:

```python
"""Event handlers for the context-intelligence hook module.

Seven handlers, each conforming to the EventHandler protocol:
- SessionHandler — :Session nodes
- OrchestratorRunHandler — :OrchestratorRun and :Step:PromptStep nodes
- StepHandler — :Step:AssistantStep nodes
- RecipeStepHandler — :Step:RecipeStep nodes
- ToolExecutionHandler — :ToolExecution nodes
- EventHandler — :Event:ContextCompaction, :Event:CancelRequested, etc.
- DefaultHandler — :Event:{DerivedFullScope} (dynamic labels)
"""
```

**Step 2: Create shared test fixtures**

Create `modules/hook-context-intelligence/tests/conftest.py`:

```python
"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService


@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService with empty config for testing."""
    return HookStateService(raw_config={})
```

**Step 3: Commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "feat: add handlers package and shared test fixtures"
```

---

### Task 9: Implement all 7 handlers and validate protocol conformance

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/session.py`
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py`
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py`
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/recipe_step.py`
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py`
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/event.py`
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py`
- Create: `modules/hook-context-intelligence/tests/test_handlers.py`

**Step 1: Write the failing tests**

Create `modules/hook-context-intelligence/tests/test_handlers.py`:

```python
"""Tests for all 7 handlers — protocol conformance, event claims, disjointness."""

from __future__ import annotations

from typing import Any

import pytest
from amplifier_core.models import HookResult

from amplifier_module_hook_context_intelligence.handlers.default import DefaultHandler
from amplifier_module_hook_context_intelligence.handlers.event import SystemEventHandler
from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.recipe_step import RecipeStepHandler
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.handlers.step import StepHandler
from amplifier_module_hook_context_intelligence.handlers.tool_execution import (
    ToolExecutionHandler,
)
from amplifier_module_hook_context_intelligence.protocol import EventHandler
from amplifier_module_hook_context_intelligence.services import HookStateService


# ---- All handler classes for parametrized tests ----

ENTITY_HANDLER_CLASSES = [
    SessionHandler,
    OrchestratorRunHandler,
    StepHandler,
    RecipeStepHandler,
    ToolExecutionHandler,
    SystemEventHandler,
]

ALL_HANDLER_CLASSES = ENTITY_HANDLER_CLASSES + [DefaultHandler]


# ---- Protocol conformance ----


class TestProtocolConformance:
    """Every handler must satisfy the EventHandler protocol."""

    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    def test_handler_conforms_to_protocol(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        assert isinstance(handler, EventHandler)

    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    def test_handler_has_handled_events_set(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        assert isinstance(handler.handled_events, set)

    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    def test_handler_has_services(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        assert handler.services is services

    @pytest.mark.parametrize("handler_cls", ALL_HANDLER_CLASSES)
    async def test_handler_returns_hook_result(self, handler_cls, services: HookStateService):
        handler = handler_cls(services)
        # Use a known event from the handler's set, or a synthetic one for DefaultHandler
        events = handler.handled_events
        event = next(iter(events)) if events else "test:synthetic"
        result = await handler(event, {"timestamp": "2026-01-01T00:00:00Z"})
        assert isinstance(result, HookResult)
        assert result.action == "continue"


# ---- Event claims ----


class TestEventClaims:
    """Verify each handler claims the correct events."""

    def test_session_handler_events(self, services: HookStateService):
        handler = SessionHandler(services)
        assert handler.handled_events == {
            "session:start", "session:fork", "session:end", "session:resume",
        }

    def test_orchestrator_run_handler_events(self, services: HookStateService):
        handler = OrchestratorRunHandler(services)
        assert handler.handled_events == {
            "prompt:submit", "execution:start", "execution:end", "orchestrator:complete",
        }

    def test_step_handler_events(self, services: HookStateService):
        handler = StepHandler(services)
        expected = {
            "provider:request", "llm:response",
            "llm:request:*", "llm:response:*",
            "content_block:*",
        }
        assert handler.handled_events == expected

    def test_recipe_step_handler_events(self, services: HookStateService):
        handler = RecipeStepHandler(services)
        assert handler.handled_events == {
            "recipe:step_started", "recipe:step_completed", "recipe:approval:*",
        }

    def test_tool_execution_handler_events(self, services: HookStateService):
        handler = ToolExecutionHandler(services)
        assert handler.handled_events == {
            "tool:pre", "tool:post", "tool:error",
            "delegate:agent_spawned", "delegate:agent_completed",
            "delegate:context_inherited", "delegate:session_resumed",
        }

    def test_system_event_handler_events(self, services: HookStateService):
        handler = SystemEventHandler(services)
        assert handler.handled_events == {
            "context:compaction", "cancel:requested", "cancel:completed",
        }

    def test_default_handler_starts_empty(self, services: HookStateService):
        handler = DefaultHandler(services)
        assert handler.handled_events == set()


# ---- Disjointness invariant ----


class TestDisjointness:
    """Entity handlers must have non-overlapping event sets."""

    def test_entity_handler_events_are_disjoint(self, services: HookStateService):
        """No two entity handlers claim the same event."""
        all_events: list[str] = []
        for handler_cls in ENTITY_HANDLER_CLASSES:
            handler = handler_cls(services)
            all_events.extend(handler.handled_events)

        assert len(all_events) == len(set(all_events)), (
            f"Duplicate events found: "
            f"{[e for e in all_events if all_events.count(e) > 1]}"
        )

    def test_claimed_events_union(self, services: HookStateService):
        """The union of all entity handler events is the full claimed set."""
        claimed: set[str] = set()
        for handler_cls in ENTITY_HANDLER_CLASSES:
            handler = handler_cls(services)
            claimed |= handler.handled_events
        # Should have events from all 6 entity handlers
        assert len(claimed) >= 18, f"Expected at least 18 claimed events, got {len(claimed)}"
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_handlers.py -v
```

Expected: FAIL — `ModuleNotFoundError` for handler modules.

**Step 3: Implement all 7 handlers**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/session.py`:

```python
"""SessionHandler — owns :Session node lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class SessionHandler:
    """Handles session lifecycle events: start, fork, end, resume."""

    handled_events: set[str] = frozenset({
        "session:start",
        "session:fork",
        "session:end",
        "session:resume",
    })

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a session event. Stub — returns continue."""
        return HookResult(action="continue")
```

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py`:

```python
"""OrchestratorRunHandler — owns :OrchestratorRun and :Step:PromptStep lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class OrchestratorRunHandler:
    """Handles orchestrator run events: prompt submit, execution start/end, complete."""

    handled_events: set[str] = frozenset({
        "prompt:submit",
        "execution:start",
        "execution:end",
        "orchestrator:complete",
    })

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle an orchestrator run event. Stub — returns continue."""
        return HookResult(action="continue")
```

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py`:

```python
"""StepHandler — owns :Step:AssistantStep lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class StepHandler:
    """Handles LLM step events: provider requests, LLM responses, content blocks."""

    handled_events: set[str] = frozenset({
        "provider:request",
        "llm:response",
        "llm:request:*",
        "llm:response:*",
        "content_block:*",
    })

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a step event. Stub — returns continue."""
        return HookResult(action="continue")
```

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/recipe_step.py`:

```python
"""RecipeStepHandler — owns :Step:RecipeStep lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class RecipeStepHandler:
    """Handles recipe step events: step started, completed, approval."""

    handled_events: set[str] = frozenset({
        "recipe:step_started",
        "recipe:step_completed",
        "recipe:approval:*",
    })

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a recipe step event. Stub — returns continue."""
        return HookResult(action="continue")
```

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py`:

```python
"""ToolExecutionHandler — owns :ToolExecution lifecycle events."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class ToolExecutionHandler:
    """Handles tool execution and delegation events."""

    handled_events: set[str] = frozenset({
        "tool:pre",
        "tool:post",
        "tool:error",
        "delegate:agent_spawned",
        "delegate:agent_completed",
        "delegate:context_inherited",
        "delegate:session_resumed",
    })

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a tool execution event. Stub — returns continue."""
        return HookResult(action="continue")
```

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/event.py`:

```python
"""SystemEventHandler — owns known system events (compaction, cancellation)."""

from __future__ import annotations

from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class SystemEventHandler:
    """Handles known system events with full-scope labels.

    Creates nodes like :Event:ContextCompaction, :Event:CancelRequested, etc.
    Labels preserve full event scope — no abbreviation.
    """

    handled_events: set[str] = frozenset({
        "context:compaction",
        "cancel:requested",
        "cancel:completed",
    })

    def __init__(self, services: HookStateService) -> None:
        self.services = services

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle a system event. Stub — returns continue."""
        return HookResult(action="continue")
```

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py`:

```python
"""DefaultHandler — catches all unclaimed, non-excluded events."""

from __future__ import annotations

import re
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService


class DefaultHandler:
    """Handles unclaimed events with dynamically derived :Event:{FullScope} labels.

    The handled_events set starts empty — it is populated at mount time
    with the unclaimed events after all entity handlers have claimed theirs.

    Label derivation: "context:compaction" → "ContextCompaction",
    "delegate:agent_spawned" → "DelegateAgentSpawned".
    """

    handled_events: set[str]

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self.handled_events = set()

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle an unclaimed event with a derived label. Stub — returns continue."""
        return HookResult(action="continue")

    @staticmethod
    def derive_label(event_name: str) -> str:
        """Derive a PascalCase label from an event name.

        Examples:
            "context:compaction" → "ContextCompaction"
            "delegate:agent_spawned" → "DelegateAgentSpawned"
            "llm:request:raw" → "LlmRequestRaw"
        """
        # Split on : and _, then PascalCase each part
        parts = re.split(r"[:_]", event_name)
        return "".join(part.capitalize() for part in parts if part)
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_handlers.py -v
```

Expected: All passed.

**Step 5: Commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "feat: add all 7 handlers conforming to EventHandler protocol"
```

---

## Phase 4 — Mount Flow (6-state machine)

### Task 10: Implement the mount flow state machine

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py`
- Create: `modules/hook-context-intelligence/tests/test_mount_flow.py`
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`

**Step 1: Write the failing tests**

Create `modules/hook-context-intelligence/tests/test_mount_flow.py`:

```python
"""Tests for the 6-state mount flow state machine.

Each state transition is independently testable with a mock coordinator.
"""

from __future__ import annotations

import fnmatch
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from amplifier_core.models import HookResult

from amplifier_module_hook_context_intelligence.mount import MountFlow, MountState
from amplifier_module_hook_context_intelligence.protocol import EventHandler
from amplifier_module_hook_context_intelligence.services import HookStateService


# ---- Mock coordinator factory ----


def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    capability_events: list[str] | None = None,
) -> MagicMock:
    """Create a mock coordinator with configurable event discovery.

    Args:
        contributed_events: List of lists returned by collect_contributions.
            Each inner list simulates one module's event contribution.
        capability_events: Events returned by the legacy get_capability channel.
    """
    coordinator = MagicMock()
    coordinator.config = {}

    # hooks.register returns an unregister callable
    coordinator.hooks = MagicMock()
    unregister_fns: list[MagicMock] = []

    def _register_side_effect(*args, **kwargs):
        unreg = MagicMock()
        unregister_fns.append(unreg)
        return unreg

    coordinator.hooks.register = MagicMock(side_effect=_register_side_effect)
    coordinator._unregister_fns = unregister_fns  # for test assertions

    # collect_contributions returns list of contributions
    if contributed_events is None:
        contributed_events = []
    coordinator.collect_contributions = AsyncMock(return_value=contributed_events)

    # get_capability returns a callable or None
    if capability_events is not None:
        coordinator.get_capability = MagicMock(return_value=lambda: capability_events)
    else:
        coordinator.get_capability = MagicMock(return_value=None)

    return coordinator


# ---- State 1 → 2: INIT → STATE_CREATED ----


class TestInitToStateCreated:
    """INIT → STATE_CREATED: HookStateService is constructed."""

    def test_mount_flow_starts_at_init(self):
        flow = MountFlow(config={})
        assert flow.state == MountState.INIT

    def test_create_services(self):
        flow = MountFlow(config={"exclude_events": ["foo:bar"]})
        flow.create_services()
        assert flow.state == MountState.STATE_CREATED
        assert isinstance(flow.services, HookStateService)
        assert flow.services.config.is_excluded("foo:bar")


# ---- State 2 → 3: STATE_CREATED → HANDLERS_INSTANTIATED ----


class TestStateCreatedToHandlersInstantiated:
    """STATE_CREATED → HANDLERS_INSTANTIATED: all 7 handlers constructed."""

    def test_instantiate_handlers(self):
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        assert flow.state == MountState.HANDLERS_INSTANTIATED
        assert len(flow.entity_handlers) == 6
        assert flow.default_handler is not None

    def test_all_handlers_conform_to_protocol(self):
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        for handler in flow.entity_handlers:
            assert isinstance(handler, EventHandler)
        assert isinstance(flow.default_handler, EventHandler)

    def test_claimed_events_computed(self):
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        assert len(flow.claimed_events) >= 18

    def test_default_handler_starts_empty(self):
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        assert flow.default_handler.handled_events == set()


# ---- State 3 → 4: HANDLERS_INSTANTIATED → EVENTS_DISCOVERED ----


class TestHandlersInstantiatedToEventsDiscovered:
    """HANDLERS_INSTANTIATED → EVENTS_DISCOVERED: events discovered and filtered."""

    async def test_discover_events_from_contributions(self):
        coordinator = _make_coordinator(
            contributed_events=[
                ["session:start", "session:end"],
                ["tool:pre", "tool:post"],
            ]
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert flow.state == MountState.EVENTS_DISCOVERED
        assert "session:start" in flow.remaining_events
        assert "tool:pre" in flow.remaining_events

    async def test_discover_events_from_legacy_capability(self):
        coordinator = _make_coordinator(
            capability_events=["custom:event1", "custom:event2"]
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "custom:event1" in flow.remaining_events

    async def test_discover_events_union_of_both_channels(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
            capability_events=["custom:event"],
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "session:start" in flow.remaining_events
        assert "custom:event" in flow.remaining_events

    async def test_exclusion_filter_applied(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "content_block:delta"]]
        )
        flow = MountFlow(config={"exclude_events": ["content_block:delta"]})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "session:start" in flow.remaining_events
        assert "content_block:delta" not in flow.remaining_events

    async def test_exclusion_wildcard_filter(self):
        coordinator = _make_coordinator(
            contributed_events=[["session-naming:foo", "session-naming:bar", "session:start"]]
        )
        flow = MountFlow(config={"exclude_events": ["session-naming:*"]})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert "session-naming:foo" not in flow.remaining_events
        assert "session-naming:bar" not in flow.remaining_events
        assert "session:start" in flow.remaining_events

    async def test_empty_discovery_is_valid(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        assert flow.state == MountState.EVENTS_DISCOVERED
        assert flow.remaining_events == set()


# ---- State 4 → 5: EVENTS_DISCOVERED → SPECIFIC_REGISTERED ----


class TestEventsDiscoveredToSpecificRegistered:
    """EVENTS_DISCOVERED → SPECIFIC_REGISTERED: entity handlers registered."""

    async def test_register_specific_handlers(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]]
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        assert flow.state == MountState.SPECIFIC_REGISTERED
        # session:start and session:end → SessionHandler, tool:pre → ToolExecutionHandler
        assert coordinator.hooks.register.call_count >= 3

    async def test_only_remaining_events_registered(self):
        """Events not in remaining_events (not discovered) are not registered."""
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]]
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        # Only session:start was discovered, so only 1 registration
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "session:start" in registered_events
        assert "session:end" not in registered_events

    async def test_wildcard_event_matching(self):
        """Handler with 'llm:request:*' should match 'llm:request:anthropic'."""
        coordinator = _make_coordinator(
            contributed_events=[["llm:request:anthropic", "llm:request:openai"]]
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "llm:request:anthropic" in registered_events
        assert "llm:request:openai" in registered_events


# ---- State 5 → 6: SPECIFIC_REGISTERED → DEFAULT_REGISTERED → READY ----


class TestSpecificRegisteredToReady:
    """SPECIFIC_REGISTERED → DEFAULT_REGISTERED → READY: default handler gets the rest."""

    async def test_register_default_handler(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "custom:unknown_event"]]
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        assert flow.state == MountState.READY
        # custom:unknown_event is unclaimed → registered to default handler
        registered_events = [c.args[0] for c in coordinator.hooks.register.call_args_list]
        assert "custom:unknown_event" in registered_events

    async def test_default_handler_events_populated(self):
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "custom:one", "custom:two"]]
        )
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        assert "custom:one" in flow.default_handler.handled_events
        assert "custom:two" in flow.default_handler.handled_events


# ---- Key invariant ----


class TestKeyInvariant:
    """Every discovered non-excluded event gets exactly one handler."""

    async def test_total_registrations_equals_remaining_events(self):
        events = [
            "session:start", "session:end",
            "tool:pre", "tool:post",
            "custom:event1", "custom:event2",
        ]
        coordinator = _make_coordinator(contributed_events=[events])
        flow = MountFlow(config={})
        flow.create_services()
        flow.instantiate_handlers()
        await flow.discover_events(coordinator)
        flow.register_specific_handlers(coordinator)
        flow.register_default_handler(coordinator)
        assert coordinator.hooks.register.call_count == len(flow.remaining_events)

    async def test_deterministic_registrations(self):
        """Running mount flow twice with same inputs produces same registrations."""
        events = ["session:start", "tool:pre", "custom:event"]
        for _ in range(2):
            coordinator = _make_coordinator(contributed_events=[events])
            flow = MountFlow(config={})
            flow.create_services()
            flow.instantiate_handlers()
            await flow.discover_events(coordinator)
            flow.register_specific_handlers(coordinator)
            flow.register_default_handler(coordinator)

        # Both runs should produce the same number of registrations
        assert coordinator.hooks.register.call_count == 3


# ---- Full mount integration ----


class TestFullMount:
    """Test the complete mount() function end-to-end."""

    async def test_mount_returns_cleanup(self):
        events = ["session:start", "tool:pre"]
        coordinator = _make_coordinator(contributed_events=[events])
        flow = MountFlow(config={})
        cleanup = await flow.run(coordinator)
        assert flow.state == MountState.READY
        assert cleanup is not None
        assert callable(cleanup)

    async def test_cleanup_calls_unregister(self):
        events = ["session:start"]
        coordinator = _make_coordinator(contributed_events=[events])
        flow = MountFlow(config={})
        cleanup = await flow.run(coordinator)
        assert coordinator.hooks.register.call_count == 1
        cleanup()
        # Unregister function should have been called
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()

    async def test_mount_with_no_events_reaches_ready(self):
        coordinator = _make_coordinator()
        flow = MountFlow(config={})
        cleanup = await flow.run(coordinator)
        assert flow.state == MountState.READY
        assert coordinator.hooks.register.call_count == 0
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_mount_flow.py -v
```

Expected: FAIL — `ModuleNotFoundError` for `mount` module.

**Step 3: Implement the mount flow**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py`:

```python
"""Mount flow — 6-state deterministic state machine.

States:
    INIT → STATE_CREATED → HANDLERS_INSTANTIATED → EVENTS_DISCOVERED
    → SPECIFIC_REGISTERED → DEFAULT_REGISTERED (= READY)

Key invariant: Every discovered non-excluded event gets exactly one handler.
"""

from __future__ import annotations

import enum
import fnmatch
import logging
from collections.abc import Callable
from typing import Any

from .handlers.default import DefaultHandler
from .handlers.event import SystemEventHandler
from .handlers.orchestrator_run import OrchestratorRunHandler
from .handlers.recipe_step import RecipeStepHandler
from .handlers.session import SessionHandler
from .handlers.step import StepHandler
from .handlers.tool_execution import ToolExecutionHandler
from .protocol import EventHandler
from .services import HookStateService

logger = logging.getLogger(__name__)


class MountState(enum.Enum):
    """States of the mount flow."""

    INIT = "init"
    STATE_CREATED = "state_created"
    HANDLERS_INSTANTIATED = "handlers_instantiated"
    EVENTS_DISCOVERED = "events_discovered"
    SPECIFIC_REGISTERED = "specific_registered"
    READY = "ready"


class MountFlow:
    """Deterministic mount flow state machine.

    Usage:
        flow = MountFlow(config)
        cleanup = await flow.run(coordinator)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self.state = MountState.INIT

        # Populated during state transitions
        self.services: HookStateService | None = None
        self.entity_handlers: list[EventHandler] = []
        self.default_handler: DefaultHandler | None = None
        self.claimed_events: set[str] = set()
        self.remaining_events: set[str] = set()
        self._unregister_fns: list[Callable[[], None]] = []

    # ---- State transitions ----

    def create_services(self) -> None:
        """INIT → STATE_CREATED: create HookStateService."""
        assert self.state == MountState.INIT
        self.services = HookStateService(raw_config=self._config)
        self.state = MountState.STATE_CREATED
        logger.info("Mount flow: services created")

    def instantiate_handlers(self) -> None:
        """STATE_CREATED → HANDLERS_INSTANTIATED: create all 7 handlers."""
        assert self.state == MountState.STATE_CREATED
        assert self.services is not None

        self.entity_handlers = [
            SessionHandler(self.services),
            OrchestratorRunHandler(self.services),
            StepHandler(self.services),
            RecipeStepHandler(self.services),
            ToolExecutionHandler(self.services),
            SystemEventHandler(self.services),
        ]
        self.default_handler = DefaultHandler(self.services)

        # Aggregate claimed events from entity handlers
        self.claimed_events = set()
        for handler in self.entity_handlers:
            self.claimed_events |= handler.handled_events

        self.state = MountState.HANDLERS_INSTANTIATED
        logger.info(
            "Mount flow: %d entity handlers instantiated, %d claimed events",
            len(self.entity_handlers),
            len(self.claimed_events),
        )

    async def discover_events(self, coordinator: Any) -> None:
        """HANDLERS_INSTANTIATED → EVENTS_DISCOVERED: discover and filter events."""
        assert self.state == MountState.HANDLERS_INSTANTIATED
        assert self.services is not None

        discovered: set[str] = set()

        # Channel 1: canonical contribution channel
        contributions = await coordinator.collect_contributions("observability.events")
        for contribution in contributions:
            if isinstance(contribution, list):
                discovered.update(contribution)
            elif isinstance(contribution, str):
                discovered.add(contribution)

        # Channel 2: legacy capability
        cap = coordinator.get_capability("observability.events")
        if cap is not None and callable(cap):
            legacy_events = cap()
            if isinstance(legacy_events, list):
                discovered.update(legacy_events)

        # Apply exclusion filter once
        self.remaining_events = {
            event for event in discovered
            if not self.services.config.is_excluded(event)
        }

        self.state = MountState.EVENTS_DISCOVERED
        logger.info(
            "Mount flow: %d events discovered, %d after exclusion",
            len(discovered),
            len(self.remaining_events),
        )

    def register_specific_handlers(self, coordinator: Any) -> None:
        """EVENTS_DISCOVERED → SPECIFIC_REGISTERED: register entity handlers."""
        assert self.state == MountState.EVENTS_DISCOVERED

        for handler in self.entity_handlers:
            for pattern in handler.handled_events:
                if "*" in pattern:
                    # Wildcard pattern — match against remaining events
                    for event in sorted(self.remaining_events):
                        if fnmatch.fnmatch(event, pattern):
                            unreg = coordinator.hooks.register(
                                event, handler, priority=90,
                                name=f"ci-{type(handler).__name__}",
                            )
                            self._unregister_fns.append(unreg)
                else:
                    # Exact match
                    if pattern in self.remaining_events:
                        unreg = coordinator.hooks.register(
                            pattern, handler, priority=90,
                            name=f"ci-{type(handler).__name__}",
                        )
                        self._unregister_fns.append(unreg)

        self.state = MountState.SPECIFIC_REGISTERED
        logger.info(
            "Mount flow: entity handlers registered (%d registrations)",
            len(self._unregister_fns),
        )

    def register_default_handler(self, coordinator: Any) -> None:
        """SPECIFIC_REGISTERED → READY: register default handler for unclaimed events."""
        assert self.state == MountState.SPECIFIC_REGISTERED
        assert self.default_handler is not None

        # Compute which events were claimed via registration (including wildcard matches)
        registered_events: set[str] = set()
        for handler in self.entity_handlers:
            for pattern in handler.handled_events:
                if "*" in pattern:
                    for event in self.remaining_events:
                        if fnmatch.fnmatch(event, pattern):
                            registered_events.add(event)
                else:
                    if pattern in self.remaining_events:
                        registered_events.add(pattern)

        unclaimed = self.remaining_events - registered_events

        for event in sorted(unclaimed):
            unreg = coordinator.hooks.register(
                event, self.default_handler, priority=90,
                name="ci-DefaultHandler",
            )
            self._unregister_fns.append(unreg)
            self.default_handler.handled_events.add(event)

        self.state = MountState.READY
        logger.info(
            "Mount flow: default handler registered for %d unclaimed events. READY.",
            len(unclaimed),
        )

    # ---- Full run ----

    async def run(self, coordinator: Any) -> Callable[[], None]:
        """Execute the full mount flow and return a cleanup function."""
        self.create_services()
        self.instantiate_handlers()
        await self.discover_events(coordinator)
        self.register_specific_handlers(coordinator)
        self.register_default_handler(coordinator)

        unregister_fns = list(self._unregister_fns)

        def cleanup() -> None:
            """Unregister all handlers."""
            for unreg in unregister_fns:
                unreg()
            logger.info("context-intelligence hook: cleanup complete")

        return cleanup
```

**Step 4: Update `__init__.py` to use MountFlow**

Modify `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`.

Replace the `mount` function body. The full file should read:

```python
"""Amplifier module: context-intelligence hook.

Observes orchestrator events and builds a property graph representing
sessions, runs, steps, tool executions, and system events.

Listed under ``hooks:`` in behavior YAML. The entry point is named
``hook-context-intelligence`` and the module declares
``__amplifier_module_type__ = "hook"`` so the kernel classifies it as
a hook via explicit type declaration (tier 1).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

__amplifier_module_type__ = "hook"

logger = logging.getLogger(__name__)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> Callable | None:
    """Mount the context-intelligence hook module.

    Executes the 6-state deterministic mount flow:
    INIT → STATE_CREATED → HANDLERS_INSTANTIATED → EVENTS_DISCOVERED
    → SPECIFIC_REGISTERED → DEFAULT_REGISTERED (READY)

    Args:
        coordinator: The ModuleCoordinator provided by the kernel.
        config: Configuration dict from the behavior YAML.

    Returns:
        A cleanup callable that unregisters all handlers.
    """
    from .mount import MountFlow

    flow = MountFlow(config=config or {})
    cleanup = await flow.run(coordinator)
    return cleanup
```

**Step 5: Run all mount flow tests**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_mount_flow.py -v
```

Expected: All passed.

**Step 6: Run the full test suite**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: All tests pass across all test files.

**Step 7: Commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "feat: implement 6-state mount flow state machine with wildcard matching"
```

---

## Phase 5 — Integration Validation

### Task 11: Full integration test suite

**Files:**
- These tests already exist from Tasks 5 (test_bundle.py, test_module_loading.py)
- Verify: all tests pass in a single run

**Step 1: Run the complete test suite**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v --tb=short
```

Expected: All tests pass. Verify the count covers:
- `test_mount.py` — 4 tests (module type, coroutine, signature, returns callable)
- `test_bundle.py` — ~10 tests (bundle.md, behavior YAML structure)
- `test_module_loading.py` — ~10 tests (entry points, type classification, YAML consistency, pyproject)
- `test_protocol.py` — 4 tests (runtime checkable, conformance, non-conformance)
- `test_services.py` — ~12 tests (HookConfig, GraphState, HookStateService)
- `test_handlers.py` — ~15 tests (protocol conformance, event claims, disjointness)
- `test_mount_flow.py` — ~15 tests (each state transition, invariants, full flow)

**Step 2: Run linting and type checks**

```bash
cd modules/hook-context-intelligence && uv run ruff check amplifier_module_hook_context_intelligence/
cd modules/hook-context-intelligence && uv run ruff format --check amplifier_module_hook_context_intelligence/
cd modules/hook-context-intelligence && uv run pyright amplifier_module_hook_context_intelligence/
```

Expected: No errors. Fix any issues found.

**Step 3: Verify entry point resolution end-to-end**

```bash
cd modules/hook-context-intelligence && uv run python -c "
import importlib.metadata
eps = importlib.metadata.entry_points(group='amplifier.modules')
hook_ep = next(ep for ep in eps if ep.name == 'hook-context-intelligence')
mount_fn = hook_ep.load()
print(f'Entry point: {hook_ep.name} → {hook_ep.value}')
print(f'mount is callable: {callable(mount_fn)}')
import inspect
print(f'mount is coroutine: {inspect.iscoroutinefunction(mount_fn)}')
print('SUCCESS: entry point resolves correctly')
"
```

Expected output:
```
Entry point: hook-context-intelligence → amplifier_module_hook_context_intelligence:mount
mount is callable: True
mount is coroutine: True
SUCCESS: entry point resolves correctly
```

**Step 4: Final commit**

```bash
cd modules/hook-context-intelligence && git add -A && git commit -m "test: complete integration validation — all tests pass, entry point resolves"
```

---

## Appendix: Quick Reference

### Run all tests
```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

### Run a specific test file
```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_mount_flow.py -v
```

### Run a specific test
```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_handlers.py::TestProtocolConformance::test_handler_conforms_to_protocol -v
```

### Sync dependencies after pyproject.toml changes
```bash
cd modules/hook-context-intelligence && uv sync
```

### Lint and format
```bash
cd modules/hook-context-intelligence && uv run ruff check --fix amplifier_module_hook_context_intelligence/
cd modules/hook-context-intelligence && uv run ruff format amplifier_module_hook_context_intelligence/
```

### All file paths (relative to repo root)

| File | Purpose |
|------|---------|
| `bundle.md` | Thin bundle entry point |
| `behaviors/context-intelligence.yaml` | Behavior declaring the hook module |
| `modules/hook-context-intelligence/pyproject.toml` | Package config with uv, hatchling |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py` | mount() + module type |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/protocol.py` | EventHandler protocol |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py` | HookStateService, GraphState, HookConfig |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/__init__.py` | Handlers package |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/session.py` | SessionHandler |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py` | OrchestratorRunHandler |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py` | StepHandler |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/recipe_step.py` | RecipeStepHandler |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py` | ToolExecutionHandler |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/event.py` | SystemEventHandler |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py` | DefaultHandler |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py` | MountFlow state machine |
| `modules/hook-context-intelligence/tests/conftest.py` | Shared fixtures |
| `modules/hook-context-intelligence/tests/test_mount.py` | mount() contract tests |
| `modules/hook-context-intelligence/tests/test_bundle.py` | Bundle structure validation |
| `modules/hook-context-intelligence/tests/test_module_loading.py` | Entry point + YAML consistency |
| `modules/hook-context-intelligence/tests/test_protocol.py` | EventHandler protocol tests |
| `modules/hook-context-intelligence/tests/test_services.py` | Services tests |
| `modules/hook-context-intelligence/tests/test_handlers.py` | Handler conformance + disjointness |
| `modules/hook-context-intelligence/tests/test_mount_flow.py` | State machine transition tests |