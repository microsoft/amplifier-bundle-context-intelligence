"""Context Intelligence hook — thin event forwarder.

Writes session events to local JSONL and dispatches them to the
Context Intelligence server when ``context_intelligence_server_url``
is configured.

Configuration keys
------------------
context_intelligence_server_url : str, optional
    Base URL of the Context Intelligence server, e.g.
    ``http://localhost:8000``.  When set, every event is POSTed
    to ``{url}/events`` and ``blob_list``/``blob_dump``/``graph_query``
    agent tools are registered.
workspace : str, optional
    Workspace identifier used to scope graph data on the server.
    Resolved automatically from the coordinator when not set
    (see ConfigResolver.workspace).
log_level : str, optional
    Logging level.  Default ``"INFO"``.
base_path : str, optional
    Root directory for JSONL output.  Defaults to the coordinator
    working directory.
exclude_events : list[str], optional
    Event name patterns (fnmatch) to suppress from logging and dispatch.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

log = logging.getLogger(__name__)

__amplifier_module_type__ = "hook"


async def _discover_events(coordinator: Any) -> set[str]:
    """Union of ALL_EVENTS + module contributions + legacy capability."""
    from amplifier_core.events import ALL_EVENTS  # type: ignore[import-not-found]

    discovered: set[str] = set(ALL_EVENTS)

    contributions = await coordinator.collect_contributions("observability.events")
    for event_list in contributions:
        discovered.update(event_list)

    capability = coordinator.get_capability("observability.events")
    if capability is not None:
        raw = capability() if callable(capability) else capability
        if isinstance(raw, (list, set, frozenset, tuple)):
            discovered.update(raw)

    return discovered


async def mount(coordinator: Any, config: dict[str, Any]):  # noqa: ANN202
    """Mount the context-intelligence hook.

    Always mounts:
    - LoggingHandler  — writes events.jsonl + dispatches to CI server

    When ``context_intelligence_server_url`` is configured:
    - BlobTool        — registers blob_list / blob_dump as agent tools
    - GraphQueryTool  — registers graph_query as an agent tool
    """
    from .config_resolver import ConfigResolver
    from .handlers.logging_handler import LoggingHandler

    resolver = ConfigResolver(config, coordinator)
    events = await _discover_events(coordinator)

    exclude = resolver.exclude_events
    active_events = {e for e in events if not any(fnmatch.fnmatch(e, p) for p in exclude)}

    logging_handler = LoggingHandler(resolver)
    unregister_fns: list[Any] = []
    for event in active_events:
        unreg = coordinator.hooks.register(
            event, logging_handler, priority=100, name="LoggingHandler"
        )
        unregister_fns.append(unreg)

    if resolver.context_intelligence_server_url:
        try:
            from .blob_tool import BlobTool

            blob_tool = BlobTool(server_url=resolver.context_intelligence_server_url)
            tools = getattr(coordinator, "tools", None)
            if tools is not None and hasattr(tools, "register"):
                tools.register(
                    "blob_list",
                    blob_tool.blob_list,
                    description="List all blob URIs stored for a session on the CI server.",
                )
                tools.register(
                    "blob_dump",
                    blob_tool.blob_dump,
                    description="Retrieve a blob from the CI server by URI and write to a local file.",
                )
        except Exception:
            log.exception("Failed to register BlobTool — continuing without blob tools")

        try:
            from .graph_query_tool import GraphQueryTool

            graph_tool = GraphQueryTool(
                server_url=resolver.context_intelligence_server_url,
                workspace=resolver.workspace,
            )
            await coordinator.mount("tools", graph_tool, name=graph_tool.name)
        except Exception:
            log.exception("Failed to register GraphQueryTool — continuing without graph tools")

    def cleanup() -> None:
        for unreg in unregister_fns:
            try:
                unreg()
            except Exception:
                pass

    return cleanup
