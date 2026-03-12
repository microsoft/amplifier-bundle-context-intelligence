"""GraphDataHook — thin orchestrator wrapping MountFlow + Neo4jGraphStore."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .mount import MountFlow
from .neo4j_store import Neo4jGraphStore

logger = logging.getLogger(__name__)


def _create_neo4j_store(resolver: Any) -> Neo4jGraphStore:
    """Create a Neo4jGraphStore from the resolver's neo4j_config.

    The ``graph_forest_name`` is NOT set here — it is resolved lazily from
    coordinator runtime data at event time by ``HookStateService``.

    Raises ValueError if resolver.neo4j_config is None (no graph_store config present).
    """
    neo4j_cfg = resolver.neo4j_config
    if neo4j_cfg is None:
        raise ValueError(
            "neo4j_config is not available in the resolver; "
            "ensure 'graph_store.config' is present in hook configuration"
        )

    return Neo4jGraphStore(
        uri=neo4j_cfg["uri"],
        auth=neo4j_cfg["auth"],
        database=neo4j_cfg["database"],
    )


class GraphDataHook:
    """Thin orchestrator that creates a Neo4jGraphStore and delegates to MountFlow.

    Only meaningful when ``enable_graph: true`` AND ``graph_store`` is present
    in the hook configuration.
    """

    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver
        self._store = _create_neo4j_store(resolver)
        self._flow = MountFlow(config=resolver._config, graph_store=self._store, resolver=resolver)

    async def mount(self, coordinator: Any, events: set[str] | None = None) -> Callable:
        """Run the mount flow and return a cleanup callable.

        Args:
            coordinator: The Amplifier coordinator instance.
            events: Pre-resolved event set. If None, uses empty set.

        The cleanup callable tears down all registered hooks and schedules
        closing the graph store.
        """
        flow_cleanup = await self._flow.run(coordinator, events or set())
        store = self._store

        async def _async_cleanup() -> None:
            flow_cleanup()
            await store.close()  # awaits pending flush + final flush + driver close

        def cleanup() -> None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_async_cleanup())
            except RuntimeError:
                # no running loop — synchronous shutdown (e.g. test teardown)
                flow_cleanup()
                asyncio.run(store.close())

        return cleanup
