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
