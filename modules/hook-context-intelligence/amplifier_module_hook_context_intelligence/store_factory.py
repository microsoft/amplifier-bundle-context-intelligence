"""Factory for graph store backends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .graph_store import GraphStore

_DEFAULT_FILE_ROOT = str(Path("~/.amplifier/graphs"))
_DEFAULT_FOREST_NAME = "default"


def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    """Create a graph store from configuration.

    Parameters
    ----------
    store_config:
        Dictionary with optional ``type`` (default ``"file"``), optional
        ``graph_forest_name`` (default ``"default"``), and optional
        ``config`` dict containing backend-specific kwargs.

        ``graph_forest_name`` is read at the top level of *store_config*
        (not inside ``config``) and passed to every backend constructor.

        Example::

            {"type": "duckdb", "graph_forest_name": "my-project",
             "config": {"connection": ":memory:"}}
    """
    store_type = store_config.get("type", "file")
    impl_config = store_config.get("config", {})
    forest_name = store_config.get("graph_forest_name", _DEFAULT_FOREST_NAME)

    if store_type == "file":
        from .file_store import FileGraphStore

        root = impl_config.get("graph_store_root", _DEFAULT_FILE_ROOT)
        return FileGraphStore(graph_store_root=root, graph_forest_name=forest_name)

    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore

        connection = impl_config.get("connection", ":memory:")
        return DuckDBGraphStore(connection=connection, graph_forest_name=forest_name)

    raise ValueError(f"Unknown graph_store type: {store_type}")
