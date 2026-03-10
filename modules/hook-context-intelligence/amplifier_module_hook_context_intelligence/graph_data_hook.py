"""GraphDataHook — thin orchestrator wrapping MountFlow + CompositeGraphStore."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .mount import MountFlow
from .store_factory import create_composite_store

logger = logging.getLogger(__name__)


class GraphDataHook:
    """Thin orchestrator that creates a CompositeGraphStore and delegates to MountFlow.

    Only meaningful when ``enable_graph: true`` AND ``graph_stores[]`` is present
    in the hook configuration.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._composite_store = create_composite_store(config["graph_stores"])
        self._flow = MountFlow(config=config, graph_store=self._composite_store)

    async def mount(self, coordinator: Any) -> Callable:
        """Run the mount flow and return a cleanup callable.

        The cleanup callable tears down all registered hooks and schedules
        closing the composite store.
        """
        flow_cleanup = await self._flow.run(coordinator)
        composite_store = self._composite_store

        def cleanup() -> None:
            flow_cleanup()
            try:
                loop = asyncio.get_running_loop()
                # fire-and-forget; loop prevents GC until completion
                loop.create_task(composite_store.close())
            except RuntimeError:
                asyncio.run(composite_store.close())

        return cleanup
