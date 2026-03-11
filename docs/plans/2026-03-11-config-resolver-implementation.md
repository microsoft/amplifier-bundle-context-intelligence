# ConfigResolver Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Centralize all configuration resolution for the context-intelligence hook into a single `ConfigResolver` class with lazy fallback chains: explicit hook config -> coordinator.config -> sensible default.

**Architecture:** A new `ConfigResolver` class is created once in `mount()` and passed to every downstream component (`LoggingHandler`, `GraphDataHook`, `HookStateService`). No component reads raw config or `coordinator.config` directly. All properties are cached after first resolution.

**Tech Stack:** Python 3.11+, pytest (async), pathlib, typing. No new dependencies.

**Design doc:** `docs/plans/2026-03-11-config-resolver-design.md`

---

## Repo Layout Reference

```
modules/hook-context-intelligence/
  amplifier_module_hook_context_intelligence/    # source package
    __init__.py          # mount() entry point, _resolve_project_slug(), _discover_events()
    config_resolver.py   # NEW — Task 1-4 create this
    graph_data_hook.py   # GraphDataHook + _create_neo4j_store()
    graph_store.py       # GraphStore protocol (DO NOT MODIFY)
    mount.py             # MountFlow state machine
    neo4j_store.py       # Neo4jGraphStore (DO NOT MODIFY)
    protocol.py          # EventHandler protocol
    services.py          # HookStateService, HookConfig, GraphState, SessionCursors
    utils.py
    handlers/
      __init__.py
      logging_handler.py # LoggingHandler
      default.py, event.py, orchestrator_run.py, recipe.py, session.py, step.py, tool_execution.py
  tests/
    conftest.py          # shared fixtures, Neo4j constants
    test_config_resolver.py  # NEW — Task 1-4 create this
    test_logging_handler.py
    test_graph_data_hook.py
    test_services.py
    test_mount.py
    test_mount_dispatcher.py
    test_mount_flow.py
    test_integration_mount.py
    ... (25 test files total)
```

**Working directory for ALL commands:**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence
```

**Test command prefix (use for every test run):**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest
```

---

## Task 1: Create ConfigResolver with `base_path` Resolution

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`
- Create: `modules/hook-context-intelligence/tests/test_config_resolver.py`

### Step 1: Write the failing tests

Create the test file `modules/hook-context-intelligence/tests/test_config_resolver.py` with this exact content:

```python
"""Tests for ConfigResolver — centralized config resolution with lazy fallback chains."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_coordinator(config: dict[str, Any] | None = None) -> MagicMock:
    """Build a mock coordinator with an optional .config dict."""
    coordinator = MagicMock()
    coordinator.config = config if config is not None else {}
    return coordinator


def _make_bare_coordinator() -> object:
    """Build a coordinator-like object WITHOUT a .config attribute."""
    return object()


# ---------------------------------------------------------------------------
# TestBasePathResolution
# ---------------------------------------------------------------------------
class TestBasePathResolution:
    """base_path: config -> coordinator.config -> ~/.amplifier/projects"""

    def test_config_value_wins(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"base_path": "/explicit/path"},
            coordinator=_make_coordinator({"base_path": "/coordinator/path"}),
        )
        assert resolver.base_path == Path("/explicit/path")

    def test_coordinator_fallback_when_config_absent(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={},
            coordinator=_make_coordinator({"base_path": "/coordinator/path"}),
        )
        assert resolver.base_path == Path("/coordinator/path")

    def test_default_when_both_absent(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.base_path == Path("~/.amplifier/projects").expanduser()

    def test_tilde_expanded(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"base_path": "~/custom/path"},
            coordinator=_make_coordinator(),
        )
        assert "~" not in str(resolver.base_path)
        assert resolver.base_path == Path("~/custom/path").expanduser()

    def test_cached_after_first_access(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        first = resolver.base_path
        second = resolver.base_path
        assert first is second

    def test_coordinator_without_config_attr_falls_back_to_default(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_bare_coordinator())
        assert resolver.base_path == Path("~/.amplifier/projects").expanduser()

    def test_returns_path_type(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert isinstance(resolver.base_path, Path)
```

### Step 2: Run tests to verify they fail

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py -v
```

Expected: All 7 tests FAIL with `ModuleNotFoundError: No module named 'amplifier_module_hook_context_intelligence.config_resolver'`

### Step 3: Write the minimal implementation

Create the file `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py` with this exact content:

```python
"""ConfigResolver — lazy config resolution: explicit config -> coordinator.config -> default.

Single source of truth for all hook configuration. No component should
read raw config or coordinator.config directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ConfigResolver:
    """Lazy config resolution with caching.

    Resolution chain for each property:
      1. Explicit hook config (from bundle YAML)
      2. coordinator.config (stamped by app-cli or other hooks)
      3. Hardcoded sensible default

    All resolved values are cached after first access.
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._cache: dict[str, Any] = {}

    def _coordinator_config_get(self, key: str) -> Any:
        """Safely read a key from coordinator.config.

        Returns None if the coordinator doesn't have a .config attribute
        or if .config is not a dict-like object.
        """
        try:
            return self._coordinator.config.get(key)
        except (AttributeError, TypeError):
            return None

    @property
    def base_path(self) -> Path:
        """Resolve base_path: config -> coordinator.config -> ~/.amplifier/projects"""
        if "base_path" not in self._cache:
            value = self._config.get("base_path")
            if value is None:
                value = self._coordinator_config_get("base_path")
            if value is None:
                value = "~/.amplifier/projects"
            self._cache["base_path"] = Path(value).expanduser()
        return self._cache["base_path"]
```

### Step 4: Run tests to verify they pass

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py -v
```

Expected: All 7 tests PASS.

### Step 5: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py modules/hook-context-intelligence/tests/test_config_resolver.py && git commit -m "feat: add ConfigResolver with base_path resolution chain"
```

---

## Task 2: Add `project_slug` Resolution to ConfigResolver

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`
- Modify: `modules/hook-context-intelligence/tests/test_config_resolver.py`

### Step 1: Write the failing tests

Append the following test class to the end of `modules/hook-context-intelligence/tests/test_config_resolver.py`:

```python
# ---------------------------------------------------------------------------
# TestProjectSlugResolution
# ---------------------------------------------------------------------------
class TestProjectSlugResolution:
    """project_slug: config -> coordinator.config -> 'default'"""

    def test_config_value_wins(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"project_slug": "from-config"},
            coordinator=_make_coordinator({"project_slug": "from-coordinator"}),
        )
        assert resolver.project_slug == "from-config"

    def test_coordinator_fallback_when_config_absent(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={},
            coordinator=_make_coordinator({"project_slug": "from-coordinator"}),
        )
        assert resolver.project_slug == "from-coordinator"

    def test_default_when_both_absent(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.project_slug == "default"

    def test_cached_after_first_access(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={"project_slug": "cached"}, coordinator=_make_coordinator())
        first = resolver.project_slug
        second = resolver.project_slug
        assert first is second

    def test_coordinator_without_config_attr_falls_back_to_default(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_bare_coordinator())
        assert resolver.project_slug == "default"

    def test_returns_str_type(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert isinstance(resolver.project_slug, str)
```

### Step 2: Run the new tests to verify they fail

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py::TestProjectSlugResolution -v
```

Expected: All 6 tests FAIL with `AttributeError: 'ConfigResolver' object has no attribute 'project_slug'`

### Step 3: Add the property to config_resolver.py

Add the following property to the `ConfigResolver` class in `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`, directly after the `base_path` property:

```python
    @property
    def project_slug(self) -> str:
        """Resolve project_slug: config -> coordinator.config -> 'default'"""
        if "project_slug" not in self._cache:
            value = self._config.get("project_slug")
            if value is None:
                value = self._coordinator_config_get("project_slug")
            if value is None:
                value = "default"
            self._cache["project_slug"] = value
        return self._cache["project_slug"]
```

### Step 4: Run tests to verify they pass

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py -v
```

Expected: All 13 tests PASS (7 from Task 1 + 6 new).

### Step 5: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py modules/hook-context-intelligence/tests/test_config_resolver.py && git commit -m "feat: add project_slug resolution to ConfigResolver"
```

---

## Task 3: Add `forest_name` Resolution to ConfigResolver

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`
- Modify: `modules/hook-context-intelligence/tests/test_config_resolver.py`

### Step 1: Write the failing tests

Append the following test class to the end of `modules/hook-context-intelligence/tests/test_config_resolver.py`:

```python
# ---------------------------------------------------------------------------
# TestForestNameResolution
# ---------------------------------------------------------------------------
class TestForestNameResolution:
    """forest_name: graph_store.graph_forest_name -> config.project -> coordinator.project_slug -> 'default'"""

    def test_graph_store_config_wins(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={
                "graph_store": {"graph_forest_name": "from-graph-store"},
                "project": "from-config-project",
            },
            coordinator=_make_coordinator({"project_slug": "from-coordinator"}),
        )
        assert resolver.forest_name == "from-graph-store"

    def test_config_project_fallback(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"project": "from-config-project"},
            coordinator=_make_coordinator({"project_slug": "from-coordinator"}),
        )
        assert resolver.forest_name == "from-config-project"

    def test_coordinator_project_slug_fallback(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={},
            coordinator=_make_coordinator({"project_slug": "from-coordinator"}),
        )
        assert resolver.forest_name == "from-coordinator"

    def test_default_when_all_absent(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.forest_name == "default"

    def test_graph_store_not_a_dict_skips_gracefully(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"graph_store": "not-a-dict", "project": "fallback-project"},
            coordinator=_make_coordinator(),
        )
        assert resolver.forest_name == "fallback-project"

    def test_graph_store_missing_forest_key_falls_through(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"graph_store": {"type": "neo4j"}, "project": "from-project-key"},
            coordinator=_make_coordinator(),
        )
        assert resolver.forest_name == "from-project-key"

    def test_cached_after_first_access(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        first = resolver.forest_name
        second = resolver.forest_name
        assert first is second

    def test_coordinator_without_config_attr_falls_back_to_default(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_bare_coordinator())
        assert resolver.forest_name == "default"
```

### Step 2: Run the new tests to verify they fail

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py::TestForestNameResolution -v
```

Expected: All 8 tests FAIL with `AttributeError: 'ConfigResolver' object has no attribute 'forest_name'`

### Step 3: Add the property to config_resolver.py

Add the following property to the `ConfigResolver` class, directly after the `project_slug` property:

```python
    @property
    def forest_name(self) -> str:
        """Resolve forest: graph_store config -> config.project -> coordinator.project_slug -> 'default'"""
        if "forest_name" not in self._cache:
            value = None
            # Step 1: graph_store.graph_forest_name
            graph_store = self._config.get("graph_store")
            if isinstance(graph_store, dict):
                value = graph_store.get("graph_forest_name")
            # Step 2: config.project
            if value is None:
                value = self._config.get("project")
            # Step 3: coordinator.config.project_slug
            if value is None:
                value = self._coordinator_config_get("project_slug")
            # Step 4: default
            if value is None:
                value = "default"
            self._cache["forest_name"] = value
        return self._cache["forest_name"]
```

### Step 4: Run tests to verify they pass

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py -v
```

Expected: All 21 tests PASS (13 prior + 8 new).

### Step 5: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py modules/hook-context-intelligence/tests/test_config_resolver.py && git commit -m "feat: add forest_name resolution to ConfigResolver (4-step chain)"
```

---

## Task 4: Add Remaining Properties + `session_dir()` Method

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`
- Modify: `modules/hook-context-intelligence/tests/test_config_resolver.py`

### Step 1: Write the failing tests

Append the following test classes to the end of `modules/hook-context-intelligence/tests/test_config_resolver.py`:

```python
# ---------------------------------------------------------------------------
# TestEnableGraph
# ---------------------------------------------------------------------------
class TestEnableGraph:
    """enable_graph: config.get('enable_graph', False) — no coordinator fallback."""

    def test_defaults_to_false(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.enable_graph is False

    def test_explicit_true(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={"enable_graph": True}, coordinator=_make_coordinator())
        assert resolver.enable_graph is True

    def test_returns_bool_type(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert isinstance(resolver.enable_graph, bool)


# ---------------------------------------------------------------------------
# TestGraphStoreConfig
# ---------------------------------------------------------------------------
class TestGraphStoreConfig:
    """graph_store_config: config.get('graph_store') — the full dict or None."""

    def test_returns_none_when_absent(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.graph_store_config is None

    def test_returns_dict_when_present(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        gs = {"type": "neo4j", "config": {"uri": "bolt://localhost:7687"}}
        resolver = ConfigResolver(config={"graph_store": gs}, coordinator=_make_coordinator())
        assert resolver.graph_store_config == gs


# ---------------------------------------------------------------------------
# TestNeo4jConfig
# ---------------------------------------------------------------------------
class TestNeo4jConfig:
    """neo4j_config: extracts uri, auth, database from graph_store_config."""

    def test_returns_none_when_no_graph_store(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.neo4j_config is None

    def test_extracts_full_config(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={
                "graph_store": {
                    "config": {
                        "uri": "bolt://localhost:7687",
                        "username": "neo4j",
                        "password": "secret",
                        "database": "mydb",
                    }
                }
            },
            coordinator=_make_coordinator(),
        )
        cfg = resolver.neo4j_config
        assert cfg is not None
        assert cfg["uri"] == "bolt://localhost:7687"
        assert cfg["auth"] == ("neo4j", "secret")
        assert cfg["database"] == "mydb"

    def test_auth_none_when_credentials_absent(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"graph_store": {"config": {"uri": "bolt://localhost:7687"}}},
            coordinator=_make_coordinator(),
        )
        cfg = resolver.neo4j_config
        assert cfg is not None
        assert cfg["auth"] is None

    def test_database_defaults_to_neo4j(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"graph_store": {"config": {"uri": "bolt://localhost:7687"}}},
            coordinator=_make_coordinator(),
        )
        cfg = resolver.neo4j_config
        assert cfg is not None
        assert cfg["database"] == "neo4j"

    def test_returns_none_when_graph_store_has_no_config_key(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"graph_store": {"type": "neo4j"}},
            coordinator=_make_coordinator(),
        )
        assert resolver.neo4j_config is None


# ---------------------------------------------------------------------------
# TestExcludeEvents
# ---------------------------------------------------------------------------
class TestExcludeEvents:
    """exclude_events: set(config.get('exclude_events', [])) — no coordinator fallback."""

    def test_defaults_to_empty_set(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.exclude_events == set()

    def test_returns_set_from_list(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"exclude_events": ["debug:*", "internal:trace"]},
            coordinator=_make_coordinator(),
        )
        assert resolver.exclude_events == {"debug:*", "internal:trace"}

    def test_returns_set_type(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert isinstance(resolver.exclude_events, set)


# ---------------------------------------------------------------------------
# TestLogLevel
# ---------------------------------------------------------------------------
class TestLogLevel:
    """log_level: config.get('log_level', 'WARNING') — no coordinator fallback."""

    def test_defaults_to_warning(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.log_level == "WARNING"

    def test_explicit_value(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={"log_level": "DEBUG"}, coordinator=_make_coordinator())
        assert resolver.log_level == "DEBUG"


# ---------------------------------------------------------------------------
# TestSessionDir
# ---------------------------------------------------------------------------
class TestSessionDir:
    """session_dir(session_id): base_path / project_slug / sessions / {id} / context-intelligence"""

    def test_composes_correct_path(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={"base_path": "/data/projects", "project_slug": "my-proj"},
            coordinator=_make_coordinator(),
        )
        result = resolver.session_dir("sess-001")
        expected = Path("/data/projects/my-proj/sessions/sess-001/context-intelligence")
        assert result == expected

    def test_uses_resolved_defaults(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        result = resolver.session_dir("abc-123")
        expected = Path("~/.amplifier/projects").expanduser() / "default" / "sessions" / "abc-123" / "context-intelligence"
        assert result == expected

    def test_returns_path_type(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert isinstance(resolver.session_dir("x"), Path)

    def test_uses_coordinator_values_in_path(self) -> None:
        from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver

        resolver = ConfigResolver(
            config={},
            coordinator=_make_coordinator({"base_path": "/coord/base", "project_slug": "coord-proj"}),
        )
        result = resolver.session_dir("s1")
        expected = Path("/coord/base/coord-proj/sessions/s1/context-intelligence")
        assert result == expected
```

### Step 2: Run the new tests to verify they fail

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py -k "TestEnableGraph or TestGraphStoreConfig or TestNeo4jConfig or TestExcludeEvents or TestLogLevel or TestSessionDir" -v
```

Expected: All tests FAIL with `AttributeError` — properties don't exist yet.

### Step 3: Add all remaining properties and session_dir method

Add the following properties and method to the `ConfigResolver` class in `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`, directly after the `forest_name` property:

```python
    @property
    def enable_graph(self) -> bool:
        """Whether graph generation is enabled. Hook-specific, no coordinator fallback."""
        return bool(self._config.get("enable_graph", False))

    @property
    def graph_store_config(self) -> dict[str, Any] | None:
        """The full graph_store config dict, or None if not configured."""
        return self._config.get("graph_store")

    @property
    def neo4j_config(self) -> dict[str, Any] | None:
        """Extract Neo4j connection details from graph_store_config.

        Returns a dict with keys: uri, auth, database.
        Returns None if graph_store or its nested config key is not present.
        """
        gs = self.graph_store_config
        if gs is None:
            return None
        impl_config = gs.get("config")
        if impl_config is None:
            return None
        uri = impl_config.get("uri")
        if uri is None:
            return None
        username = impl_config.get("username")
        password = impl_config.get("password")
        auth = (username, password) if username and password else None
        database = impl_config.get("database", "neo4j")
        return {"uri": uri, "auth": auth, "database": database}

    @property
    def exclude_events(self) -> set[str]:
        """Event exclusion patterns. Hook-specific, no coordinator fallback."""
        return set(self._config.get("exclude_events", []))

    @property
    def log_level(self) -> str:
        """Log level string. Hook-specific, no coordinator fallback."""
        return self._config.get("log_level", "WARNING")

    def session_dir(self, session_id: str) -> Path:
        """Construct full session directory path from resolved values."""
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"
```

### Step 4: Run ALL ConfigResolver tests to verify they pass

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_config_resolver.py -v
```

Expected: All tests PASS (21 prior + new ones from this task).

### Step 5: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py modules/hook-context-intelligence/tests/test_config_resolver.py && git commit -m "feat: complete ConfigResolver with all properties and session_dir()"
```

---

## Task 5: Wire ConfigResolver into `mount()` and `LoggingHandler`

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py`
- Modify: `modules/hook-context-intelligence/tests/test_logging_handler.py`
- Modify: `modules/hook-context-intelligence/tests/test_integration_mount.py`

### Step 1: Modify LoggingHandler to accept a resolver

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py`.

**Change the constructor** (currently lines 62-65). Replace this:

```python
    def __init__(self, base_path: str | Path, project_slug: str) -> None:
        self.base_path = Path(base_path).expanduser()
        self.project_slug = project_slug
        self.handled_events = set()
```

With this:

```python
    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver
        self.handled_events = set()
```

**Change the `_session_dir` method** (currently lines 67-68). Replace this:

```python
    def _session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"
```

With this:

```python
    def _session_dir(self, session_id: str) -> Path:
        return self._resolver.session_dir(session_id)
```

**Add the `Any` import** if not already present. The file already imports `from typing import Any` on line 12, so no change needed there.

### Step 2: Modify mount() to create and use ConfigResolver

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`.

**Add the import** — after line 13 (`from pathlib import Path`), add:

```python
from .config_resolver import ConfigResolver
```

**Replace lines 74-76** (the project_slug and base_path resolution). Replace this:

```python
    # -- Resolve project slug and base path --------------------------------
    project_slug = _resolve_project_slug(coordinator)
    base_path = config.get("base_path", "~/.amplifier/projects")
```

With this:

```python
    # -- Resolve config via ConfigResolver ---------------------------------
    resolver = ConfigResolver(config, coordinator)
```

**Replace line 88** (LoggingHandler construction). Replace this:

```python
    logging_handler = LoggingHandler(base_path, project_slug)
```

With this:

```python
    logging_handler = LoggingHandler(resolver)
```

**Do NOT remove `_resolve_project_slug`** yet — it's still tested in `test_mount_dispatcher.py::TestProjectSlugResolution`. We'll leave it as a dead helper for now; it can be cleaned up later.

### Step 3: Update LoggingHandler tests

Open `modules/hook-context-intelligence/tests/test_logging_handler.py`.

The tests currently construct `LoggingHandler(base_path=tmp_path, project_slug="proj")`. We need a tiny adapter that makes a resolver-like object from `(tmp_path, "proj")`.

**Add a helper** at the top of the file, after the existing imports (after line 12), add:

```python


class _FakeResolver:
    """Minimal resolver stand-in for LoggingHandler tests."""

    def __init__(self, base_path: Path, project_slug: str) -> None:
        self.base_path = base_path
        self.project_slug = project_slug

    def session_dir(self, session_id: str) -> Path:
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"
```

Now **find and replace all occurrences** of `LoggingHandler(base_path=tmp_path, project_slug="proj")` in this file and replace them with `LoggingHandler(_FakeResolver(tmp_path, "proj"))`.

There are exactly **17 occurrences** of `LoggingHandler(base_path=tmp_path, project_slug="proj")` in this file. Replace each one with `LoggingHandler(_FakeResolver(tmp_path, "proj"))`.

### Step 4: Update integration mount tests

Open `modules/hook-context-intelligence/tests/test_integration_mount.py`.

In `TestLoggingOnlyIntegration.test_session_lifecycle_writes_files` (line 68), the test constructs config with `base_path` and expects the LoggingHandler to use it. **This test should work as-is** because mount() now creates a ConfigResolver from config, and the resolver reads `base_path` from config. But verify the path expectations.

Currently line 118 expects:
```python
session_dir = tmp_path / "test-project" / "sessions" / session_id / "context-intelligence"
```

The old code used `_resolve_project_slug(coordinator)` which derived `"test-project"` from the working dir `/home/user/test-project`. The **new** code uses `resolver.project_slug` which checks `config.get("project_slug")` first, then `coordinator.config.get("project_slug")`, then defaults to `"default"`.

The mock coordinator has `coordinator.config = {}`, and the config dict only has `base_path`. So `resolver.project_slug` will resolve to `"default"`, **not** `"test-project"`.

**Fix the test config** at line 74. Change:

```python
        config = {"base_path": str(tmp_path)}
```

To:

```python
        config = {"base_path": str(tmp_path), "project_slug": "test-project"}
```

### Step 5: Run ALL tests to verify nothing is broken

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_logging_handler.py modules/hook-context-intelligence/tests/test_integration_mount.py modules/hook-context-intelligence/tests/test_mount_dispatcher.py modules/hook-context-intelligence/tests/test_config_resolver.py -v
```

Expected: All tests PASS.

If any LoggingHandler tests fail because of the constructor change, double-check that every `LoggingHandler(base_path=..., project_slug=...)` call has been converted to `LoggingHandler(_FakeResolver(...))`.

### Step 6: Run the FULL test suite to catch any other breakage

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/ -v --timeout=120
```

Expected: All tests PASS.

### Step 7: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add -A && git commit -m "refactor: wire ConfigResolver into mount() and LoggingHandler"
```

---

## Task 6: Wire ConfigResolver into GraphDataHook

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_data_hook.py`
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`
- Modify: `modules/hook-context-intelligence/tests/test_graph_data_hook.py`

### Step 1: Modify `_create_neo4j_store` to accept a resolver

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_data_hook.py`.

**Replace the `_create_neo4j_store` function** (lines 15-41). Replace the entire function with:

```python
def _create_neo4j_store(resolver: Any) -> Neo4jGraphStore:
    """Create a Neo4jGraphStore from the resolver's neo4j_config and forest_name.

    The resolver extracts connection details (uri, auth, database) from the
    graph_store config dict and resolves forest_name via its 4-step chain.
    """
    neo4j_cfg = resolver.neo4j_config
    if neo4j_cfg is None:
        msg = "neo4j_config is None — graph_store.config must be present"
        raise ValueError(msg)

    return Neo4jGraphStore(
        uri=neo4j_cfg["uri"],
        auth=neo4j_cfg["auth"],
        database=neo4j_cfg["database"],
        graph_forest_name=resolver.forest_name,
    )
```

**Add `Any` to the typing import** on line 7. Change:

```python
from typing import Any, Callable
```

This is already the case (line 7 already has `Any`), so no change needed.

### Step 2: Modify GraphDataHook to accept a resolver

In the same file, **replace the `__init__` method** (lines 51-54):

Replace this:

```python
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._store = _create_neo4j_store(config)
        self._flow = MountFlow(config=config, graph_store=self._store)
```

With this:

```python
    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver
        self._store = _create_neo4j_store(resolver)
        self._flow = MountFlow(config=resolver._config, graph_store=self._store)
```

Note: `MountFlow` still needs the raw config dict for `HookStateService` -> `HookConfig` (which reads `exclude_events`). We pass `resolver._config` as a bridge. Task 7 will clean this up when HookStateService also takes the resolver.

### Step 3: Update mount() to pass resolver to GraphDataHook

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py`.

**Replace line 109** (the GraphDataHook construction). Replace this:

```python
            graph_hook = GraphDataHook(config=config)
```

With this:

```python
            graph_hook = GraphDataHook(resolver)
```

Also update the conditional check on line 105. Replace this:

```python
    if config.get("enable_graph", False) and config.get("graph_store"):
```

With this:

```python
    if resolver.enable_graph and resolver.graph_store_config:
```

### Step 4: Update GraphDataHook tests

Open `modules/hook-context-intelligence/tests/test_graph_data_hook.py`.

The tests currently pass `_NEO4J_STORE_CONFIG` dict directly to `GraphDataHook(config)` and `_create_neo4j_store(config)`. We need to make them pass a resolver instead.

**Add a resolver helper** after the existing imports (after line 16), before `_NEO4J_STORE_CONFIG`:

```python
from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver


def _make_resolver(config: dict[str, Any] | None = None) -> ConfigResolver:
    """Build a ConfigResolver with the given config and a bare coordinator."""
    coordinator = MagicMock()
    coordinator.config = {}
    return ConfigResolver(config or {}, coordinator)
```

**Update `_NEO4J_STORE_CONFIG`** — keep the dict as-is (it's the raw config), but we'll wrap it in a resolver when passing to constructors.

**Update `TestCreateNeo4jStore`** — the `_create_neo4j_store` function now takes a resolver instead of a config dict.

Replace the entire `TestCreateNeo4jStore` class with:

```python
class TestCreateNeo4jStore:
    """_create_neo4j_store reads resolver's neo4j_config and forest_name."""

    def test_creates_neo4j_store_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        result = _create_neo4j_store(resolver)
        assert result is mock_store

    def test_passes_uri_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["uri"] == "neo4j://localhost:7687"

    def test_passes_auth_tuple_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["auth"] == ("neo4j", "test")

    def test_passes_database_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["database"] == "neo4j"

    def test_passes_forest_name_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["graph_forest_name"] == "default"

    def test_raises_when_no_neo4j_config(self):
        """ValueError when graph_store.config is absent."""
        resolver = _make_resolver({})
        with pytest.raises(ValueError):
            _create_neo4j_store(resolver)

    def test_auth_is_none_when_credentials_absent(self, mock_neo4j_store):
        """When username/password are absent, auth should be None."""
        config = {
            "graph_store": {
                "config": {
                    "uri": "bolt://localhost:7687",
                }
            }
        }
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(config)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["auth"] is None

    def test_database_defaults_to_neo4j_when_absent(self, mock_neo4j_store):
        """When database key is absent, it should default to 'neo4j'."""
        config = {
            "graph_store": {
                "config": {
                    "uri": "bolt://localhost:7687",
                    "username": "neo4j",
                    "password": "test",
                }
            }
        }
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(config)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["database"] == "neo4j"
```

**Update `TestGraphDataHookInit`** — replace `GraphDataHook(_NEO4J_STORE_CONFIG)` with `GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))`:

Replace the entire class with:

```python
class TestGraphDataHookInit:
    """GraphDataHook.__init__ creates Neo4jGraphStore via resolver."""

    def test_creates_neo4j_store(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        assert hook._store is mock_store

    def test_no_composite_store_attribute(self, mock_neo4j_store):
        """_composite_store attribute must NOT exist in the new implementation."""
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        assert not hasattr(hook, "_composite_store")

    def test_creates_mount_flow_with_store(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        assert hook._flow is not None
        assert hook._flow._graph_store is mock_store

    def test_neo4j_store_class_is_called(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        mock_cls.assert_called_once()
```

**Update `TestGraphDataHookMount`** — replace `GraphDataHook(_NEO4J_STORE_CONFIG)` with `GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))`:

Replace the entire class with:

```python
class TestGraphDataHookMount:
    """GraphDataHook.mount() runs MountFlow to READY and returns cleanup callable."""

    async def test_mount_returns_cleanup_callable(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        cleanup = await hook.mount(coordinator)
        assert callable(cleanup)

    async def test_mount_runs_mount_flow_to_ready(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        await hook.mount(coordinator)
        assert hook._flow.state == MountState.READY

    async def test_mount_registers_handlers(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        coordinator = _make_coordinator(
            contributed_events=[["session:start", "session:end", "tool:pre"]],
        )
        await hook.mount(coordinator)
        assert coordinator.hooks.register.call_count >= 3

    async def test_cleanup_calls_unregister(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        cleanup = await hook.mount(coordinator)
        cleanup()
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()

    async def test_cleanup_schedules_store_close(self, mock_neo4j_store):
        """Cleanup must schedule store.close() (fire-and-forget)."""
        mock_cls, mock_store = mock_neo4j_store
        hook = GraphDataHook(_make_resolver(_NEO4J_STORE_CONFIG))
        coordinator = _make_coordinator(
            contributed_events=[["session:start"]],
        )
        cleanup = await hook.mount(coordinator)
        cleanup()
        await asyncio.sleep(0)
        mock_store.close.assert_called_once()
```

**Keep `TestNoForbiddenImports` as-is** — it checks source code text, no constructor calls.

### Step 5: Run ALL affected tests

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_graph_data_hook.py modules/hook-context-intelligence/tests/test_integration_mount.py modules/hook-context-intelligence/tests/test_mount_dispatcher.py -v
```

Expected: All tests PASS.

### Step 6: Run the FULL test suite

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/ -v --timeout=120
```

Expected: All tests PASS.

### Step 7: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add -A && git commit -m "refactor: wire ConfigResolver into GraphDataHook and _create_neo4j_store"
```

---

## Task 7: Wire ConfigResolver into HookStateService

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py`
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py`
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_data_hook.py`
- Modify: `modules/hook-context-intelligence/tests/test_services.py`
- Modify: `modules/hook-context-intelligence/tests/test_mount_flow.py`
- Modify: `modules/hook-context-intelligence/tests/conftest.py`

> **Important:** HookStateService is used by MountFlow, which is used by GraphDataHook. The graph handlers (SessionHandler, StepHandler, etc.) all receive `HookStateService` via their constructor. We need to be careful — `HookConfig.is_excluded()` is used by `MountFlow.discover_events()`. The `HookConfig` class and its `is_excluded` method must continue to work.

### Step 1: Modify HookStateService constructor

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py`.

**Replace the `HookStateService.__init__` method** (lines 94-106). Replace this:

```python
    def __init__(
        self,
        raw_config: dict[str, Any],
        coordinator: Any = None,
        graph_store: Any = None,
    ) -> None:
        self.config = HookConfig(raw_config)
        self.coordinator = coordinator
        if graph_store is not None:
            self.graph = graph_store
        else:
            self.graph = GraphState()
        self._cursors: dict[str, SessionCursors] = {}
```

With this:

```python
    def __init__(
        self,
        raw_config: dict[str, Any] | None = None,
        coordinator: Any = None,
        graph_store: Any = None,
        *,
        resolver: Any = None,
    ) -> None:
        if resolver is not None:
            self.config = HookConfig(resolver._config)
            # No self.coordinator — resolver owns coordinator access
        else:
            # Legacy path: raw_config passed directly (backwards compat for tests)
            self.config = HookConfig(raw_config or {})
        if graph_store is not None:
            self.graph = graph_store
        else:
            self.graph = GraphState()
        self._cursors: dict[str, SessionCursors] = {}
```

This is a **backward-compatible** change: existing callers passing `raw_config={}` still work. New callers can pass `resolver=resolver` instead.

### Step 2: Update MountFlow to pass resolver when available

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py`.

**Modify the `__init__` method** (lines 41-50). Add an optional `resolver` parameter:

Replace this:

```python
    def __init__(self, config: dict[str, Any], graph_store: Any = None) -> None:
        self._config = config
        self._graph_store = graph_store
```

With this:

```python
    def __init__(self, config: dict[str, Any], graph_store: Any = None, resolver: Any = None) -> None:
        self._config = config
        self._graph_store = graph_store
        self._resolver = resolver
```

**Modify `create_services`** (lines 52-57). Replace this:

```python
    def create_services(self, coordinator: Any) -> None:
        """INIT -> STATE_CREATED: Instantiate HookStateService from config."""
        self.services = HookStateService(
            self._config, coordinator=coordinator, graph_store=self._graph_store
        )
        self.state = MountState.STATE_CREATED
```

With this:

```python
    def create_services(self, coordinator: Any) -> None:
        """INIT -> STATE_CREATED: Instantiate HookStateService from config."""
        if self._resolver is not None:
            self.services = HookStateService(
                resolver=self._resolver, graph_store=self._graph_store
            )
        else:
            self.services = HookStateService(
                self._config, coordinator=coordinator, graph_store=self._graph_store
            )
        self.state = MountState.STATE_CREATED
```

### Step 3: Update GraphDataHook to pass resolver to MountFlow

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_data_hook.py`.

In the `__init__` method, change:

```python
        self._flow = MountFlow(config=resolver._config, graph_store=self._store)
```

To:

```python
        self._flow = MountFlow(config=resolver._config, graph_store=self._store, resolver=resolver)
```

### Step 4: Update conftest.py services fixture

Open `modules/hook-context-intelligence/tests/conftest.py`.

The `services` fixture on line 161-163 currently creates `HookStateService(raw_config={})`. This still works because we kept the backward-compatible constructor. **No change needed.**

### Step 5: Run ALL affected tests

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/test_services.py modules/hook-context-intelligence/tests/test_mount_flow.py modules/hook-context-intelligence/tests/test_graph_data_hook.py modules/hook-context-intelligence/tests/test_integration_mount.py -v
```

Expected: All tests PASS.

### Step 6: Run the FULL test suite

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/ -v --timeout=120
```

Expected: All tests PASS.

### Step 7: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add -A && git commit -m "refactor: wire ConfigResolver into HookStateService and MountFlow"
```

---

## Task 8: Update Behavior YAML, README, and DOT Diagram

**Files:**
- Modify: `behaviors/context-intelligence.yaml`
- Modify: `README.md`
- Verify: `context/config-resolution.dot` (already current — no changes expected)

### Step 1: Update behaviors YAML

Open `behaviors/context-intelligence.yaml`.

Replace the `config:` block (lines 12-23) with:

```yaml
    config:
      # Path/identity values below are OPTIONAL — resolved lazily:
      #   explicit config -> coordinator.config -> sensible default
      # base_path: '~/.amplifier/projects'
      # project_slug: 'my-project'
      # project: 'my-project'           # sets forest_name if graph_forest_name absent
      exclude_events: []
      log_level: 'WARNING'
      enable_graph: false
      graph_store:
        type: 'neo4j'
        # graph_forest_name: 'default'   # resolved from project/project_slug if absent
        config:
          uri: '${NEO4J_URI:-bolt://localhost:7687}'
          username: '${NEO4J_USERNAME:-neo4j}'     # example — replace before enabling graph
          password: '${NEO4J_PASSWORD}'            # example — replace before enabling graph
          database: '${NEO4J_DATABASE:-neo4j}'
```

### Step 2: Update README config example

Open `README.md`.

Replace the Quick Start YAML block (lines 23-40) with:

```yaml
hooks:
  - module: hook-context-intelligence
    source: context-intelligence:modules/hook-context-intelligence
    config:
      # Path and identity values are optional — resolved via lazy fallback chain:
      #   explicit config -> coordinator.config -> sensible default
      # base_path: "~/.amplifier/projects"   # default: ~/.amplifier/projects
      # project_slug: "my-project"           # default: from coordinator or "default"
      # project: "my-project"                # sets graph forest_name
      exclude_events: []
      log_level: "WARNING"
      enable_graph: false                    # set true to activate graph generation
      graph_store:                           # configure when enable_graph: true
        type: "neo4j"
        # graph_forest_name: "default"       # resolved from project/project_slug if absent
        config:
          uri: "bolt://localhost:7687"
          username: "neo4j"
          password: "password"
          database: "neo4j"
```

### Step 3: Verify the DOT file is already current

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && cat context/config-resolution.dot | head -5
```

Expected: The DOT file already shows the ConfigResolver architecture with all three resolution chains. **No changes needed** — it was created during the design phase.

### Step 4: Lint the YAML

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -c "import yaml; yaml.safe_load(open('behaviors/context-intelligence.yaml'))" && echo "YAML OK"
```

Expected: `YAML OK` — no parse errors.

### Step 5: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add behaviors/context-intelligence.yaml README.md && git commit -m "docs: update config examples to show ConfigResolver lazy fallback chains"
```

---

## Task 9: Final Verification

### Step 1: Run linting

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m ruff check modules/hook-context-intelligence/ && python -m ruff format --check modules/hook-context-intelligence/
```

Expected: No errors. If there are formatting issues, fix them:

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m ruff format modules/hook-context-intelligence/ && python -m ruff check --fix modules/hook-context-intelligence/
```

### Step 2: Run the full module test suite

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest modules/hook-context-intelligence/tests/ -v --timeout=120 2>&1 | tail -30
```

Expected: All tests PASS, 0 failures.

### Step 3: Run bundle-level tests (if any exist)

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -m pytest tests/ -v --timeout=120 2>&1 | tail -20
```

Expected: All tests PASS (or `no tests ran` if there's no top-level tests directory — that's fine).

### Step 4: Verify the new config_resolver module is importable and complete

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && python -c "
from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver
r = ConfigResolver({}, type('C', (), {'config': {}})())
print('base_path:', r.base_path)
print('project_slug:', r.project_slug)
print('forest_name:', r.forest_name)
print('enable_graph:', r.enable_graph)
print('graph_store_config:', r.graph_store_config)
print('neo4j_config:', r.neo4j_config)
print('exclude_events:', r.exclude_events)
print('log_level:', r.log_level)
print('session_dir:', r.session_dir('test-123'))
print('ALL PROPERTIES VERIFIED')
"
```

Expected output:
```
base_path: /home/<user>/.amplifier/projects
project_slug: default
forest_name: default
enable_graph: False
graph_store_config: None
neo4j_config: None
exclude_events: set()
log_level: WARNING
session_dir: /home/<user>/.amplifier/projects/default/sessions/test-123/context-intelligence
ALL PROPERTIES VERIFIED
```

### Step 5: Commit any remaining lint fixes

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && git add -A && git diff --cached --stat
```

If there are changes:
```bash
git commit -m "chore: lint fixes from final verification"
```

If there are no changes, skip this step.

---

## Summary of Files Created/Modified

### Created (Tasks 1-4)
| File | Description |
|------|-------------|
| `modules/.../config_resolver.py` | ConfigResolver class — all properties + session_dir() |
| `modules/.../tests/test_config_resolver.py` | Full test coverage for ConfigResolver |

### Modified (Tasks 5-7)
| File | What Changed |
|------|-------------|
| `modules/.../__init__.py` | mount() creates ConfigResolver, passes to consumers |
| `modules/.../handlers/logging_handler.py` | Constructor takes resolver instead of (base_path, project_slug) |
| `modules/.../graph_data_hook.py` | _create_neo4j_store and GraphDataHook take resolver |
| `modules/.../services.py` | HookStateService accepts optional resolver kwarg |
| `modules/.../mount.py` | MountFlow accepts optional resolver, passes to services |
| `modules/.../tests/test_logging_handler.py` | _FakeResolver adapter for all handler tests |
| `modules/.../tests/test_graph_data_hook.py` | _make_resolver helper, all tests use resolver |
| `modules/.../tests/test_integration_mount.py` | config gains project_slug for path assertions |

### Modified (Task 8)
| File | What Changed |
|------|-------------|
| `behaviors/context-intelligence.yaml` | base_path/project_slug commented out, env vars added |
| `README.md` | Config example shows optional values with fallback docs |

### Not Modified (by design)
| File | Reason |
|------|--------|
| `neo4j_store.py` | Store constructor is NOT in scope |
| `graph_store.py` | Protocol is NOT in scope |
| `protocol.py` | EventHandler protocol unchanged |
| `handlers/*.py` (except logging_handler) | Graph handlers receive HookStateService, unchanged |
