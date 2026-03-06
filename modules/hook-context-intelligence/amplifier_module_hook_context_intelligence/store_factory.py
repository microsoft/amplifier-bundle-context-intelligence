"""Factory for graph store backends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .graph_store import GraphStore

_DEFAULT_FILE_LOCATION = str(Path("~/.amplifier/graph"))


def create_graph_store(store_config: dict[str, Any]) -> GraphStore:
    """Create a graph store from configuration.

    Parameters
    ----------
    store_config:
        Dictionary with optional ``type`` (default ``"file"``) and optional
        ``config`` dict containing backend-specific kwargs.

        When ``type`` is ``"file"`` and no ``config`` key is present, a
        default location of ``~/.amplifier/graph`` is used.  Pass an
        explicit ``config`` dict to override (or omit ``location`` to get
        a ``TypeError``).

        Example::

            {"type": "duckdb", "config": {"connection": ":memory:"}}
    """
    store_type = store_config.get("type", "file")
    impl_config = store_config.get("config", {})

    if store_type == "file":
        from .file_store import FileGraphStore

        if "config" not in store_config:
            impl_config.setdefault("location", _DEFAULT_FILE_LOCATION)
        return FileGraphStore(**impl_config)

    if store_type == "duckdb":
        from .duckdb_store import DuckDBGraphStore

        return DuckDBGraphStore(**impl_config)

    raise ValueError(f"Unknown graph_store type: {store_type}")
