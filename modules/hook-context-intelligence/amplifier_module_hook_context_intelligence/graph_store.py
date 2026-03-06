"""GraphStore protocol — the async interface for graph storage backends.

Non-negotiable guarantees
-------------------------
1. upsert_node / upsert_edge MUST return immediately (buffer, no I/O).
2. get_node / get_edge MUST reflect buffered state (buffer-first reads).
3. flush() persists buffered writes (called by lifecycle triggers, not handlers).
4. close() MUST call flush() before releasing resources.
5. Flush failure MUST NOT propagate to handlers.

QueryableStore extension
------------------------
6. supported_dialects advertises the set of query languages the backend speaks.
7. execute_query runs a query in the specified (or default) dialect.
8. ValueError is raised when the requested dialect is not in supported_dialects.
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

        MUST return immediately — buffer only, no I/O.
        """
        ...

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        """Insert or update an edge.

        Identity is (source, target, edge_type).  Same merge semantics as nodes.

        MUST return immediately — buffer only, no I/O.
        """
        ...

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node by ID.  MUST reflect buffered state."""
        ...

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        """Retrieve an edge by composite key.  MUST reflect buffered state."""
        ...

    async def flush(self) -> None:
        """Persist buffered writes.

        Called by lifecycle triggers, not handlers.  Flush failure MUST NOT
        propagate to handlers.
        """
        ...

    async def close(self) -> None:
        """Shut down the store.  MUST call flush() before releasing resources."""
        ...


@runtime_checkable
class QueryableStore(GraphStore, Protocol):
    """Extension of GraphStore that supports ad-hoc queries.

    Backends that speak one or more query languages (SQL, Cypher, PGQ, …)
    implement this protocol to expose ``execute_query``.
    """

    @property
    def supported_dialects(self) -> frozenset[str]:
        """The set of query dialects this backend can execute."""
        ...

    async def execute_query(
        self, query: str, params: dict[str, Any] | None = None, dialect: str | None = None
    ) -> list[dict[str, Any]]:
        """Execute a query in the given dialect.

        Parameters
        ----------
        query:
            The query string.
        params:
            Optional bind parameters.
        dialect:
            Which query language to use.  ``None`` means the backend's default.
            Raises ``ValueError`` if *dialect* is not in ``supported_dialects``.
        """
        ...
