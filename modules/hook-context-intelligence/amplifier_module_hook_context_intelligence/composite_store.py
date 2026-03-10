"""CompositeGraphStore — fan-out write store with failure isolation."""

from __future__ import annotations

import logging
from typing import Any

from .graph_store import GraphStore

logger = logging.getLogger(__name__)


class CompositeGraphStore:
    """Fan-out write store that wraps N backing GraphStore instances.

    Writes are broadcast to every backing store.  Reads use a
    first-responder strategy: the first store that returns a non-None
    result wins.  Failures in individual stores are logged but never
    propagated to the caller.

    Does NOT implement QueryableStore.
    """

    def __init__(
        self,
        stores: list[GraphStore],
        graph_forest_name: str | None = None,
    ) -> None:
        if not stores:
            raise ValueError("CompositeGraphStore requires at least one backing store")
        self._stores = list(stores)
        self._graph_forest_name = graph_forest_name or stores[0].graph_forest_name

    @property
    def graph_forest_name(self) -> str:
        """The forest this composite store writes to."""
        return self._graph_forest_name

    # ------------------------------------------------------------------
    # Write path (fan-out to all stores)
    # ------------------------------------------------------------------

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        for store in self._stores:
            try:
                await store.upsert_node(node_id, labels, properties)
            except Exception:
                logger.exception("upsert_node failed on %r", store)

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        for store in self._stores:
            try:
                await store.upsert_edge(source, target, edge_type, properties)
            except Exception:
                logger.exception("upsert_edge failed on %r", store)

    async def flush(self) -> None:
        for store in self._stores:
            try:
                await store.flush()
            except Exception:
                logger.exception("flush failed on %r", store)

    async def close(self) -> None:
        for store in self._stores:
            try:
                await store.close()
            except Exception:
                logger.exception("close failed on %r", store)

    # ------------------------------------------------------------------
    # Read path (first-responder)
    # ------------------------------------------------------------------

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        for store in self._stores:
            try:
                result = await store.get_node(node_id)
                if result is not None:
                    return result
            except Exception:
                logger.exception("get_node failed on %r", store)
        return None

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        for store in self._stores:
            try:
                result = await store.get_edge(source, target, edge_type)
                if result is not None:
                    return result
            except Exception:
                logger.exception("get_edge failed on %r", store)
        return None
