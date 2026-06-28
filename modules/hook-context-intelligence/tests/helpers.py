"""Shared lifecycle test helpers for hook-context-intelligence tests.

These helpers eliminate duplication across test files that exercise the
two-phase mount() + on_session_ready() lifecycle, and provide composition-aware
helpers for validating bundle YAML structure across the layered behaviour split.

Usage::

    from tests.helpers import make_lifecycle_coordinator, mount_and_ready
    from tests.helpers import composed_behavior, ci_hook

The ``config_resolver``-focused tests in ``test_config_resolver.py`` use a
different coordinator shape and keep their own local helpers.

Known false positive (validator)
---------------------------------
The bundle-validator's ``unadvertised_but_referenced`` heuristic flags
``context-intelligence`` as an unadvertised mode because the string appears in
disk paths (``…/sessions/{id}/context-intelligence/``), @mention prefixes
(``@context-intelligence:context/…``), and skill names
(``context-intelligence-graph-query``).  All occurrences are paths / bundle
names / skill names — NOT slash-command invocations of the
``modes/context-intelligence.md`` design mode.  The mode is correctly
``advertised: false`` (it is an internal design mode).  Do NOT flip it to
``true`` and do NOT remove those path/skill references.  This is a validator
limitation, not a bundle defect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import yaml

# Repo root: modules/hook-context-intelligence/tests/helpers.py → 4 levels up
REPO_ROOT = Path(__file__).parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Composition-aware behaviour helpers
# ---------------------------------------------------------------------------


def composed_behavior(start: str = "context-intelligence") -> dict:
    """Resolve the umbrella behaviour's local includes chain and return a composed view.

    Starts at ``REPO_ROOT/behaviors/<start>.yaml`` and recursively resolves
    every ``includes:`` entry whose ``bundle:`` value starts with
    ``"context-intelligence:behaviors/"``, mapping it to
    ``REPO_ROOT/behaviors/<stem>.yaml`` (stem = part after the last ``/``).
    External refs (``git+https://…``) are silently skipped.
    Cycles are guarded via a visited-paths set.

    Returns a composed dict ``{"hooks": [...], "tools": [...]}`` that is the
    union of every composed layer's ``hooks`` and ``tools`` lists (including
    the umbrella's own, even if empty).
    """
    result: dict[str, list] = {"hooks": [], "tools": []}
    visited: set[Path] = set()

    def _resolve(stem: str) -> None:
        path = REPO_ROOT / "behaviors" / f"{stem}.yaml"
        if path in visited:
            return
        visited.add(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        result["hooks"].extend(data.get("hooks") or [])
        result["tools"].extend(data.get("tools") or [])
        for entry in data.get("includes") or []:
            if not isinstance(entry, dict):
                continue
            bundle_ref = entry.get("bundle", "")
            if bundle_ref.startswith("context-intelligence:behaviors/"):
                # e.g. "context-intelligence:behaviors/context-intelligence-logging"
                local_stem = bundle_ref.rsplit("/", 1)[-1]
                _resolve(local_stem)

    _resolve(start)
    return result


def ci_hook(composed: dict) -> dict:
    """Return the ``hook-context-intelligence`` spec from a composed behaviour view.

    Enforces exactly-one semantics — fails loud on zero *and* on >1 matches so
    that duplicate declarations are caught as early as bad wiring.

    Args:
        composed: The dict returned by :func:`composed_behavior`.

    Returns:
        The single hook entry whose ``module`` is ``"hook-context-intelligence"``.

    Raises:
        AssertionError: If zero or more than one match is found.
    """
    matches = [
        h for h in composed.get("hooks", []) if h.get("module") == "hook-context-intelligence"
    ]
    assert matches, (
        "no composed behaviour wires hook-context-intelligence (umbrella -> includes chain broken?)"
    )
    assert len(matches) == 1, (
        f"hook-context-intelligence declared in multiple composed layers "
        f"({len(matches)} occurrences)"
    )
    return matches[0]


def make_lifecycle_coordinator(
    contributed_events: list[list[str]] | None = None,
    capability_events: list[str] | None = None,
    working_dir: str | None = None,
) -> MagicMock:
    """Build a mock coordinator for lifecycle tests (mount + on_session_ready).

    Tracks all hooks.register() return values in ``coordinator._unregister_fns``
    so cleanup assertions can verify every registered handler is torn down.

    Args:
        contributed_events: Return value for ``collect_contributions("observability.events")``.
            Pass ``[[event1, event2], ...]`` to simulate module contributions.
        capability_events: Events exposed via the ``observability.events`` capability.
        working_dir: Value returned by ``get_capability("session.working_dir")``.
            Defaults to ``None`` (capability absent).
    """
    coordinator = MagicMock()
    coordinator.config = {}
    unregister_fns: list[MagicMock] = []
    capabilities: dict[str, Any] = {}

    def _register_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        unreg = MagicMock()
        unregister_fns.append(unreg)
        return unreg

    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(side_effect=_register_side_effect)
    coordinator._unregister_fns = unregister_fns

    if contributed_events is None:
        contributed_events = []
    coordinator.collect_contributions = AsyncMock(return_value=contributed_events)

    def _register_capability(name: str, value: Any) -> None:
        capabilities[name] = value

    coordinator.register_capability = MagicMock(side_effect=_register_capability)

    def _get_capability(name: str) -> Any:
        if name == "session.working_dir" and working_dir is not None:
            return working_dir
        if name == "observability.events" and capability_events is not None:
            return lambda: capability_events
        return capabilities.get(name)

    coordinator.get_capability = MagicMock(side_effect=_get_capability)

    return coordinator


async def mount_and_ready(coordinator: MagicMock, config: dict | None = None) -> Any:
    """Run mount() then on_session_ready() — the normal two-phase lifecycle.

    Returns the cleanup callable from mount().
    """
    from amplifier_module_hook_context_intelligence import mount, on_session_ready  # type: ignore[import-not-found]

    cleanup = await mount(coordinator, config=config or {})
    await on_session_ready(coordinator)
    return cleanup
