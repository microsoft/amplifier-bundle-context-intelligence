"""GraphDataHook — thin orchestrator wrapping MountFlow + Neo4jGraphStore."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .mount import MountFlow
from .neo4j_store import Neo4jGraphStore

logger = logging.getLogger(__name__)


def _create_neo4j_store(config: dict[str, Any]) -> Neo4jGraphStore:
    """Create a Neo4jGraphStore from the ``graph_store`` config dict.

    Expected config shape::

        {
            "type": "neo4j",
            "graph_forest_name": "default",
            "config": {
                "uri": "bolt://localhost:7687",
                "username": "neo4j",
                "password": "password",
                "database": "neo4j",
            },
        }
    """
    store_config = config["graph_store"]
    impl_config = store_config.get("config", {})
    forest_name = store_config.get("graph_forest_name", "default")

    uri = impl_config["uri"]
    username = impl_config.get("username")
    password = impl_config.get("password")
    auth = (username, password) if username and password else None
    database = impl_config.get("database", "neo4j")

    return Neo4jGraphStore(uri=uri, auth=auth, database=database, graph_forest_name=forest_name)


class GraphDataHook:
    """Thin orchestrator that creates a Neo4jGraphStore and delegates to MountFlow.

    Only meaningful when ``enable_graph: true`` AND ``graph_store`` is present
    in the hook configuration.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._store = _create_neo4j_store(config)
        self._flow = MountFlow(config=config, graph_store=self._store)

    async def mount(self, coordinator: Any) -> Callable:
        """Run the mount flow and return a cleanup callable.

        The cleanup callable tears down all registered hooks and schedules
        closing the graph store.
        """
        flow_cleanup = await self._flow.run(coordinator)
        store = self._store

        def cleanup() -> None:
            flow_cleanup()
            try:
                loop = asyncio.get_running_loop()
                # fire-and-forget; loop prevents GC until completion
                loop.create_task(store.close())
            except RuntimeError:
                # no running loop — synchronous shutdown context (e.g. test teardown)
                asyncio.run(store.close())

        return cleanup
