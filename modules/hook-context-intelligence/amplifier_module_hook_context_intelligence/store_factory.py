"""Factory for graph store backends."""

from __future__ import annotations

from typing import Any

from .graph_store import GraphStore


def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    """Create a graph store from configuration.

    Parameters
    ----------
    store_config:
        Dictionary with optional ``type`` (default ``"duckdb"``) and
        backend-specific keys such as ``connection``.
    """
    store_type = store_config.get("type", "duckdb")

    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore

        connection = store_config.get("connection", ":memory:")
        return DuckDBGraphStore(connection=connection)

    raise ValueError(f"Unknown graph_store type: {store_type}")
