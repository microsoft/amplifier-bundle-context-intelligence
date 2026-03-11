"""Amplifier module: context-intelligence hook.

Single entry point dispatching to two internal paths:
  [ALWAYS]       LoggingHandler  (flat JSONL)
  [CONDITIONAL]  GraphDataHook   (wraps existing 7 graph handlers)
"""

from __future__ import annotations

import fnmatch
import logging
import re
from pathlib import Path
from typing import Any, Callable

# Import from the mount submodule at package-init time so that
# sys.modules['...mount'] is populated *before* the ``mount`` function
# name is bound below.
from .mount import MountFlow as _MountFlow  # noqa: F401

__amplifier_module_type__ = "hook"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_project_slug(coordinator: Any) -> str:
    """Derive a filesystem-safe project slug from the coordinator's working dir."""
    cap = coordinator.get_capability("session.working_dir")
    if isinstance(cap, str):
        working_dir = cap
    else:
        working_dir = str(Path.cwd())

    raw_name = Path(working_dir).name
    slug = raw_name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "default"


async def _discover_events(coordinator: Any, exclude_patterns: set[str]) -> set[str]:
    """Discover events from both contribution and legacy capability channels."""
    discovered: set[str] = set()

    # Contributions channel — async, returns list[list[str]]
    contributions = await coordinator.collect_contributions("observability.events")
    for event_list in contributions:
        discovered.update(event_list)

    # Legacy capability channel — sync, returns callable or None
    capability_fn = coordinator.get_capability("observability.events")
    if capability_fn is not None:
        discovered.update(capability_fn())

    # Apply exclusion filter using fnmatch
    return {e for e in discovered if not any(fnmatch.fnmatch(e, pat) for pat in exclude_patterns)}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> Callable | None:
    """Mount the context-intelligence hook module.

    1. [ALWAYS]       Create LoggingHandler for flat JSONL logging
    2. [CONDITIONAL]  Create GraphDataHook when enable_graph + graph_store
    """
    config = config or {}
    cleanup_fns: list[Callable] = []

    # -- Resolve project slug and base path --------------------------------
    project_slug = _resolve_project_slug(coordinator)
    base_path = config.get("base_path", "~/.amplifier/projects")

    # -- Discover events ---------------------------------------------------
    exclude_patterns = set(config.get("exclude_events", []))
    events = await _discover_events(coordinator, exclude_patterns)

    if not events:
        return None

    # -- [ALWAYS] LoggingHandler -------------------------------------------
    from .logging_handler import LoggingHandler

    logging_handler = LoggingHandler(base_path, project_slug)
    logging_handler.handled_events = set(events)

    logging_unreg_fns: list[Callable] = []
    for event in events:
        unreg = coordinator.hooks.register(
            event, logging_handler, priority=100, name="LoggingHandler"
        )
        logging_unreg_fns.append(unreg)

    def _logging_cleanup() -> None:
        for unreg in logging_unreg_fns:
            unreg()

    cleanup_fns.append(_logging_cleanup)

    # -- [CONDITIONAL] GraphDataHook ---------------------------------------
    if config.get("enable_graph", False) and config.get("graph_store"):
        try:
            from .graph_data_hook import GraphDataHook

            graph_hook = GraphDataHook(config=config)
            graph_cleanup = await graph_hook.mount(coordinator)
            cleanup_fns.append(graph_cleanup)
        except Exception:
            logger.exception("Failed to mount GraphDataHook; logging path continues")

    # -- Unified cleanup ---------------------------------------------------
    def cleanup() -> None:
        for fn in reversed(cleanup_fns):
            fn()

    return cleanup
