"""Shared services for all context-intelligence handlers."""

from __future__ import annotations

import fnmatch
from typing import Any


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
    """In-memory property graph state. Storage-agnostic."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.current_session: str | None = None
        self.current_run: str | None = None
        self.current_step: str | None = None
        self.step_counter: int = 0
        self.pending_delegate_tool_call_id: str | None = None

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self._nodes.get(node_id)

    def upsert_node(
        self, node_id: str, labels: set[str], properties: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self._nodes.get(node_id)
        if existing is not None:
            existing["labels"] |= labels
            existing["properties"].update(properties)
            return existing
        node = {"id": node_id, "labels": set(labels), "properties": dict(properties)}
        self._nodes[node_id] = node
        return node

    def get_edge(self, source: str, target: str, edge_type: str) -> dict[str, Any] | None:
        return self._edges.get((source, target, edge_type))

    def upsert_edge(
        self, source: str, target: str, edge_type: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        key = (source, target, edge_type)
        existing = self._edges.get(key)
        if existing is not None:
            existing["properties"].update(properties)
            return existing
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
            "properties": dict(properties),
        }
        self._edges[key] = edge
        return edge


class HookStateService:
    """Top-level service container shared across all handlers."""

    def __init__(self, raw_config: dict[str, Any]) -> None:
        self.config = HookConfig(raw_config)
        self.graph = GraphState()
