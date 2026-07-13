"""BlobReadTool — fetches blob content from the context-intelligence server.

Configuration and provenance are resolved via ``resolve_query_connection``
(same as GraphQueryTool — parity guaranteed by the shared helper), a
SINGLE-HIT selection over the connectable pool (tool ``sources`` ∪ hook
``destinations``). See ``resolve_query_connection``'s docstring in
context_intelligence/tool_resolver.py for the authoritative selection rule
(in brief: explicit ``source=<name>`` reaches any pool entry; with no name,
1 source -> it, 2+ sources -> fail loud, 0 sources -> the FIRST destination
in config order for any N, else env).

Every result (success or failure) carries a ``source`` field naming the
endpoint that answered / was attempted (docs/multi-source-build-spec-v5.md §5).
Callers can also pass ``list_sources: true`` to discover the connectable set
without fetching a blob.

The ``ToolConfigResolver`` is injected at construction time by ``mount()``
(one shared instance for both CI read tools — single config namespace).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from amplifier_core import ToolResult

from context_intelligence.client import AsyncCIClient, CIClientError
from context_intelligence.tool_resolver import (
    ToolConfigResolver,
    _connectable_pool,
    _origin_dict,
    resolve_query_connection,
)

_URI_SCHEME = "ci-blob://"
_BLOB_DIR = Path("/tmp/ci-blobs")


def _sanitize_path_component(s: str) -> str:
    """Replace any char not in [a-zA-Z0-9._-] with underscore."""
    return re.sub(r"[^a-zA-Z0-9._\-]", "_", s)


class BlobReadTool:
    """Tool that fetches a ci-blob:// URI from the server and writes it to disk."""

    def __init__(self, coordinator: Any, resolver: ToolConfigResolver | None = None) -> None:
        self._coordinator = coordinator
        self._tool_resolver = resolver or ToolConfigResolver({}, coordinator)
        self._hook_resolver: Any | None = None

    @property
    def name(self) -> str:
        return "blob_read"

    @property
    def description(self) -> str:
        return (
            "Fetch a ci-blob:// URI from the server and write it to disk. "
            "Returns the file path plus the `source` (name/url/origin) that answered -- "
            "state it in your answer. Use bash+jq to inspect the file as the content "
            "would be likely large."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": (
                        "A ci-blob:// URI to fetch (e.g. ci-blob://session_id/key). "
                        "Required unless list_sources=true."
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Optional name of a specific connectable endpoint to fetch the blob "
                        "from -- either a configured read source OR a hook upload destination "
                        "(the full connectable set; call with list_sources=true to see the "
                        "names). Omitting `source` uses the default endpoint: the single "
                        "configured source, or -- if no sources are configured -- the first "
                        "destination in config order (destinations are the read fallback pool). "
                        "The only case where omitting it errors is when 2+ SOURCES are "
                        "configured (then you must pass source=<name>, and the error lists the "
                        "valid names); any number of destinations is fine to leave implicit. "
                        "Pass source=<name> to target a specific source or destination."
                    ),
                },
                "list_sources": {
                    "type": "boolean",
                    "description": (
                        "When true, do NOT fetch a blob. Return the connectable set -- every "
                        "source and destination this tool can reach, with name, url, and origin "
                        "(source/destination). Use this to discover valid `source` values before "
                        "selecting one."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
        from context_intelligence.tool_resolver import SourceSelectionError  # noqa: PLC0415

        # (1) Lazy hook resolver resolution -- retried every call while None
        # (hook may mount after this tool).
        if self._hook_resolver is None:
            self._hook_resolver = self._coordinator.get_capability(
                "context_intelligence.hook_config_resolver"
            )

        if input.get("list_sources"):
            pool = _connectable_pool(self._tool_resolver, self._hook_resolver)
            return ToolResult(
                success=True,
                output={
                    "connectable_set": [
                        {"name": e.name, "url": e.url, "origin": e.kind} for e in pool.values()
                    ]
                },
            )

        # (2) Resolve the connection (url + api_key + auth strategy + provenance)
        source_name = input.get("source")
        try:
            conn = resolve_query_connection(
                self._hook_resolver, self._tool_resolver, source_name=source_name
            )
        except SourceSelectionError as exc:
            return ToolResult(
                success=False,
                error={
                    "message": str(exc),
                    "type": exc.error_type,
                    "valid_sources": exc.valid_names,
                },
            )
        except ValueError as exc:
            return ToolResult(
                success=False,
                error={"message": str(exc), "type": "source_misconfigured"},
            )

        if not conn.url:
            return ToolResult(
                success=False,
                error={
                    "message": "context-intelligence server URL not configured",
                    "type": "configuration_error",
                },
            )
        server_url = conn.url.rstrip("/")

        if "uri" not in input:
            # Endpoint already resolved above -> attach provenance (spec §5.1):
            # `source` is present on failures that occur AFTER an endpoint is chosen.
            return ToolResult(
                success=False,
                error={
                    "message": "uri is required unless list_sources=true",
                    "type": "validation_error",
                    "source": _origin_dict(conn.origin),
                },
            )

        # (3) Parse URI
        uri: str = input["uri"]
        if not uri.startswith(_URI_SCHEME):
            # Endpoint already resolved above -> attach provenance (spec §5.1).
            return ToolResult(
                success=False,
                error={
                    "message": f"URI must start with {_URI_SCHEME}",
                    "type": "uri_error",
                    "source": _origin_dict(conn.origin),
                },
            )
        rest = uri[len(_URI_SCHEME) :]
        if "/" not in rest:
            # Endpoint already resolved above -> attach provenance (spec §5.1).
            return ToolResult(
                success=False,
                error={
                    "message": "URI must be in format ci-blob://session_id/key",
                    "type": "uri_error",
                    "source": _origin_dict(conn.origin),
                },
            )
        slash_idx = rest.index("/")
        session_id = rest[:slash_idx]
        key = rest[slash_idx + 1 :]

        # (4) Sanitize both components for use in file path
        safe_session_id = _sanitize_path_component(session_id)
        safe_key = _sanitize_path_component(key)

        # (5) Construct AsyncCIClient with auth strategy
        async_client = AsyncCIClient(
            server_url=server_url,
            api_key=conn.api_key or "",
            auth_strategy=conn.auth_strategy,
            timeout=self._tool_resolver.request_timeout,
        )

        # (6) Fetch blob using original unsanitized values for the server request
        try:
            data = await async_client.fetch_blob(session_id, key)
        except CIClientError as exc:
            # success=False + output unset is safe: ToolResult.model_post_init
            # back-fills output from error["message"] when output is None. Do NOT
            # also set output= here or that back-fill is suppressed.
            origin_name = conn.origin.name if conn.origin and conn.origin.name else server_url
            return ToolResult(
                success=False,
                error={
                    "message": f"blob fetch failed against {origin_name}: {exc}",
                    "type": exc.error_type,  # connection_error|timeout|http_status|decode_error
                    "source": _origin_dict(conn.origin),
                    **({"status_code": exc.status_code} if exc.status_code is not None else {}),
                },
            )

        # (7) Return http_error if data is None (genuine JSON null body -- not a
        # transport failure; transport failures now raise CIClientError above)
        if data is None:
            return ToolResult(
                success=False,
                error={
                    "message": "HTTP error fetching blob",
                    "type": "http_error",
                    "source": _origin_dict(conn.origin),
                },
            )

        # (8) Write to disk: json.dumps for dict/list, raw string otherwise
        dest = _BLOB_DIR / safe_session_id / f"{safe_key}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (dict, list)):
            dest.write_text(json.dumps(data))
        else:
            dest.write_text(data)

        # (9) Return success with path + provenance
        return ToolResult(
            success=True,
            output={"path": str(dest), "source": _origin_dict(conn.origin)},
        )
