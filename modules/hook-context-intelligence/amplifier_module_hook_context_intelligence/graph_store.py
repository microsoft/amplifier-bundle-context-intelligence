"""GraphStore protocol — the async interface for graph storage backends.

Non-negotiable guarantees
-------------------------
1. upsert_node / upsert_edge MUST return immediately (buffer, no I/O).
2. get_node / get_edge MUST reflect buffered state (buffer-first reads).
3. flush() persists buffered writes (called by lifecycle triggers, not handlers).
4. close() MUST call flush() before releasing resources.
5. Flush failure MUST NOT propagate to handlers.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphStore(Protocol):
    """Async protocol for graph storage backends.

    Implementations buffer writes in memory and expose buffer-first reads so
    that handlers always see a consistent, up-to-date view without waiting for
    I/O.  Persistence is driven by lifecycle triggers via ``flush()``.
    """

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        """Insert or update a node.

        Merge semantics: new properties merge with existing.  New keys added,
        existing overwritten, unmentioned preserved.  Labels unioned.
        """
        ...

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        """Insert or update an edge.

        Identity is (source, target, edge_type).  Same merge semantics as nodes.
        """
        ...

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node by ID.  Must reflect buffered state."""
        ...

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        """Retrieve an edge by composite key.  Must reflect buffered state."""
        ...

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a backend-specific query."""
        ...

    async def flush(self) -> None:
        """Persist buffered writes."""
        ...

    async def close(self) -> None:
        """Shut down the store.  Must call flush() before releasing resources."""
        ...
