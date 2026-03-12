"""Shared services for all context-intelligence handlers."""

from __future__ import annotations

import dataclasses
import fnmatch
import logging
from typing import Any

logger = logging.getLogger(__name__)


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

    @graph_forest_name.setter
    def graph_forest_name(self, value: str) -> None:
        self._graph_forest_name = value

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

    def schedule_flush(self) -> None:
        """No-op for in-memory state — nothing to flush to external storage."""

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
        blob_store: Any = None,
    ) -> None:
        if resolver is not None:
            self.config = HookConfig(resolver._config)
        else:
            self.config = HookConfig(raw_config if raw_config is not None else {})
        self.coordinator = coordinator
        if graph_store is not None:
            self.graph = graph_store
        else:
            self.graph = GraphState()
        self._cursors: dict[str, SessionCursors] = {}
        self._seen_sessions: set[str] = set()
        self.blob_store = blob_store
        self._forest_resolved: bool = False

    def get_cursors(self, session_id: str) -> SessionCursors:
        """Return the SessionCursors for *session_id*, lazily creating one if needed."""
        if session_id not in self._cursors:
            self._cursors[session_id] = SessionCursors()
        return self._cursors[session_id]

    def _resolve_forest_name_from_coordinator(self) -> None:
        """Resolve graph_forest_name from coordinator runtime data.

        Called lazily on the first event — by this point the CLI has stamped
        ``project_slug`` into ``coordinator.config`` (which happens after
        session creation but before events fire).

        Resolution chain:
          1. coordinator.config["project_slug"] (CLI stamps this post-creation)
          2. session.working_dir capability (slugified to CLI format)
          3. leave as-is (store defaults to "default")
        """
        if self._forest_resolved:
            return
        self._forest_resolved = True

        if self.coordinator is None:
            return

        # Step 1: coordinator.config["project_slug"]
        coord_config = getattr(self.coordinator, "config", None)
        slug: str | None = None
        if isinstance(coord_config, dict):
            slug = coord_config.get("project_slug")

        # Step 2: session.working_dir capability → slugify
        if not slug:
            get_cap = getattr(self.coordinator, "get_capability", None)
            if get_cap is not None:
                wd = get_cap("session.working_dir")
                if isinstance(wd, str) and wd:
                    slug = wd.replace("/", "-").replace("\\", "-").replace(":", "")
                    if slug and not slug.startswith("-"):
                        slug = "-" + slug

        if slug:
            # Set on the graph store — it will use this for all subsequent flushes
            if hasattr(self.graph, "graph_forest_name"):
                self.graph.graph_forest_name = slug
            logger.debug("Resolved graph_forest_name from runtime: %s", slug)

    async def ensure_session_node(self, session_id: str, data: dict[str, Any]) -> None:
        """Ensure a Session node exists in the graph for this session_id.

        Called on the very first event for a session — creates a minimal
        Session node so child nodes (runs, steps, tools) are never orphaned.
        If session:start arrives later, SessionHandler enriches the node
        with full data (parent_id, metadata, etc.) via upsert.

        Also resolves graph_forest_name from runtime data on first call.

        This is idempotent — repeated calls for the same session_id are no-ops.
        """
        # Resolve forest name lazily from coordinator runtime data
        self._resolve_forest_name_from_coordinator()

        if session_id in self._seen_sessions:
            return
        self._seen_sessions.add(session_id)

        timestamp = data.get("timestamp", "")
        parent_id = (data.get("parent_id") or data.get("parent") or "").strip()
        labels: set[str] = {"Session", "Subsession"} if parent_id else {"Session", "Root"}
        properties: dict[str, Any] = {
            "started_at": timestamp,
            "status": "running",
        }
        await self.graph.upsert_node(session_id, labels, properties)

    def remove_cursors(self, session_id: str) -> None:
        """Remove cursor state for *session_id*. Safe to call for nonexistent sessions."""
        self._cursors.pop(session_id, None)
