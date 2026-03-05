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

# Import from the mount submodule at package-init time so that
# sys.modules['...mount'] is populated *before* the ``mount`` function
# name is bound below.  The subsequent ``async def mount(...)`` then
# re-binds the ``mount`` attribute on this package to the function,
# while still leaving the submodule accessible via the fully-qualified
# dotted path used in test_mount_flow.py.
from .mount import MountFlow as _MountFlow  # noqa: F401  (re-exported via submodule)

__amplifier_module_type__ = "hook"

logger = logging.getLogger(__name__)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> Callable | None:
    """Mount the context-intelligence hook module.

    Executes the 6-state deterministic mount flow:
    INIT -> STATE_CREATED -> HANDLERS_INSTANTIATED -> EVENTS_DISCOVERED
    -> SPECIFIC_REGISTERED -> DEFAULT_REGISTERED (READY)
    """
    flow = _MountFlow(config=config or {})
    cleanup = await flow.run(coordinator)
    return cleanup
