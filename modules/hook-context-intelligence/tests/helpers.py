"""Shared lifecycle test helpers for hook-context-intelligence tests.

These helpers eliminate duplication across test files that exercise the
two-phase mount() + on_session_ready() lifecycle.

Usage::

    from tests.helpers import make_lifecycle_coordinator, mount_and_ready

The ``config_resolver``-focused tests in ``test_config_resolver.py`` use a
different coordinator shape and should keep their own local helpers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


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
