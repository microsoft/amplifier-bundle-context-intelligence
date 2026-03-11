"""Shared services for all context-intelligence handlers."""

from __future__ import annotations

import dataclasses
import fnmatch
from typing import Any


@dataclasses.dataclass
class SessionCursors:
    """Per-session cursor state for tracking active run/step/tool positions."""

    current_run_id: str | None = None
    current_step_id: str | None = None
    run_counter: int = 0
    step_counter: int = 0
    prompt_preview: str = ""
    parallel_groups: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    tool_call_map: dict[str, str] = dataclasses.field(default_factory=dict)


class HookConfig:
    """Configuration for the context-intelligence hook."""

    def __init__(self, raw_config: dict[str, Any]) -> None:
        self._raw = raw_config
        self._exclude_patterns: set[str] = set(raw_config.get("exclude_events", []))

    @property
    def exclude_events(self) -> set[str]:
        return self._exclude_patterns

    def is_excluded(self, event: str) -> bool:
        for pattern in self._exclude_patterns:
            if fnmatch.fnmatch(event, pattern):
                return True
        return False


class GraphState:
    """In-memory property graph state conforming to the GraphStore protocol."""

    def __init__(self, graph_forest_name: str = "default") -> None:
        self._graph_forest_name = graph_forest_name
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    @property
    def graph_forest_name(self) -> str:
        return self._graph_forest_name

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self._nodes.get(node_id)

    async def upsert_node(self, node_id: str, labels: set[str], properties: dict[str, Any]) -> None:
        existing = self._nodes.get(node_id)
        if existing is not None:
            existing["labels"] |= labels
            existing["properties"].update(properties)
            return
        node = {"id": node_id, "labels": set(labels), "properties": dict(properties)}
        self._nodes[node_id] = node

    async def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        return self._edges.get((source, target, edge_type))

    async def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> None:
        key = (source, target, edge_type)
        existing = self._edges.get(key)
        if existing is not None:
            existing["properties"].update(properties)
            return
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
            "properties": dict(properties),
        }
        self._edges[key] = edge

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        await self.flush()


class HookStateService:
    """Top-level service container shared across all handlers."""

    def __init__(
        self,
        raw_config: dict[str, Any] | None = None,
        coordinator: Any = None,
        graph_store: Any = None,
        *,
        resolver: Any = None,
    ) -> None:
        if resolver is not None:
            self.config = HookConfig(resolver._config)
            self.coordinator = None
        else:
            self.config = HookConfig(raw_config if raw_config is not None else {})
            self.coordinator = coordinator
        if graph_store is not None:
            self.graph = graph_store
        else:
            self.graph = GraphState()
        self._cursors: dict[str, SessionCursors] = {}

    def get_cursors(self, session_id: str) -> SessionCursors:
        """Return the SessionCursors for *session_id*, lazily creating one if needed."""
        if session_id not in self._cursors:
            self._cursors[session_id] = SessionCursors()
        return self._cursors[session_id]

    def remove_cursors(self, session_id: str) -> None:
        """Remove cursor state for *session_id*. Safe to call for nonexistent sessions."""
        self._cursors.pop(session_id, None)
