"""REAL end-to-end: connectable set (ii) + source provenance, driven through the
tools' execute() over real sockets.

Proof boundary (docs/multi-source-build-spec-v5.md §4-5, §6.1 scenarios e-j):

  - A caller can explicitly reach a hook DESTINATION by name, not just a tool
    source (the core of requirement (ii)).
  - Every result -- success or failure -- names the endpoint that answered /
    was attempted, and that name is provably CORRECT (matches the real server
    that responded), not just structurally present.
  - `list_sources: true` returns the whole connectable set (sources ∪
    destinations) without running a query; source shadows a same-named
    destination; no api_key is ever leaked in the listing.
  - The default (no-pointer) path is real-socket verified: 2+ SOURCES fails loud
    (the ONLY default-path fail-loud), while 0 sources + N destinations resolves
    to the FIRST destination in config order (destinations are the established
    read-fallback pool -- RATIFIED, no tightening; provenance names the pick).

Reuses the same stdlib ThreadingHTTPServer + BaseHTTPRequestHandler pattern as
test_phase0_fail_loud_e2e.py / modules/hook-context-intelligence/tests/test_dispatch_e2e.py
(zero new deps).

READ-SIDE / FAN-IN ONLY: this test never touches the hook's fan-out (fanout.py,
_DestinationDispatcher, logging_handler.py). The hook resolver is a minimal
stand-in exposing only `.destinations` (a real dict) and `.workspace` -- the
read path's only allowed contact with the hook.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Shared HTTP server utilities -- stdlib only, mirrors test_phase0_fail_loud_e2e.py
# ---------------------------------------------------------------------------


class _Behavior:
    """Mutable control block read by the handler on every request."""

    def __init__(self) -> None:
        self.status_code: int = 200
        self.sleep_s: float = 0.0
        self.body: bytes = b'{"results": []}'
        self.content_type: str = "application/json"
        self.hits: list[str] = []  # request paths this server actually received


def _find_free_port() -> int:
    """Return a free localhost TCP port by briefly binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler_class(behavior: _Behavior) -> type[BaseHTTPRequestHandler]:
    """Return a handler class serving POST /cypher and GET /blobs/{sid}/{key}
    according to *behavior*, recording every request path it receives."""

    class _Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            if behavior.sleep_s:
                time.sleep(behavior.sleep_s)
            self.send_response(behavior.status_code)
            self.send_header("Content-Type", behavior.content_type)
            self.end_headers()
            self.wfile.write(behavior.body)

        def do_POST(self) -> None:  # noqa: N802
            behavior.hits.append(self.path)
            if self.path != "/cypher":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)  # drain request body
            self._respond()

        def do_GET(self) -> None:  # noqa: N802
            behavior.hits.append(self.path)
            if not self.path.startswith("/blobs/"):
                self.send_error(404)
                return
            self._respond()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass  # silence stdlib server request logs

    return _Handler


class _SpawnedServer:
    """A running fake CI server: base_url + behavior control + shutdown handle."""

    def __init__(self, base_url: str, server: ThreadingHTTPServer, behavior: _Behavior) -> None:
        self.base_url = base_url
        self.server = server
        self.behavior = behavior

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _spawn_server(*, body: bytes = b'{"results": []}') -> _SpawnedServer:
    """Bind a ThreadingHTTPServer on an ephemeral port and serve it in a daemon
    thread. Returns a handle with the base_url and a mutable behavior block."""
    port = _find_free_port()
    behavior = _Behavior()
    behavior.body = body
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler_class(behavior))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    return _SpawnedServer(base_url, server, behavior)


# ---------------------------------------------------------------------------
# Coordinator / hook-resolver / tool-resolver builders
# ---------------------------------------------------------------------------


def _dest(name: str, url: str, api_key: str = "dest-key") -> SimpleNamespace:
    """Destination-like stand-in -- same attributes as the hook's real
    Destination NamedTuple (name/url/api_key/auth_mode/auth_resource)."""
    return SimpleNamespace(
        name=name, url=url, api_key=api_key, auth_mode="static", auth_resource=""
    )


def _make_hook(
    destinations: dict[str, SimpleNamespace], workspace: str = "test-workspace"
) -> SimpleNamespace:
    """Minimal hook-resolver stand-in exposing ONLY `.destinations` (read-only
    consumption of hook config -- see the module docstring's guardrail) and
    `.workspace` (read by the tool's workspace resolution, unrelated to fan-out)."""
    return SimpleNamespace(destinations=destinations, workspace=workspace)


def _make_coordinator(hook_resolver: Any | None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability = MagicMock(return_value=hook_resolver)
    return coordinator


def _make_tool_resolver(sources: dict[str, dict[str, str]], coordinator: Any) -> Any:
    from context_intelligence.tool_resolver import ToolConfigResolver

    return ToolConfigResolver({"sources": sources}, coordinator)


# ---------------------------------------------------------------------------
# (e) Explicit-select a DESTINATION by name -> reaches that server
# ---------------------------------------------------------------------------


class TestExplicitDestinationSelection:
    async def test_e_explicit_select_destination_by_name_reaches_it(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        source_server = _spawn_server(body=b'{"results": [{"n": "source-row"}]}')
        dest_server = _spawn_server(body=b'{"results": [{"n": "dest-row"}]}')
        try:
            hook = _make_hook({"warehouse": _dest("warehouse", dest_server.base_url)})
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver(
                {"primary": {"url": source_server.base_url, "api_key": "src-key"}}, coordinator
            )
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute(
                {"query": "MATCH (n) RETURN n LIMIT 1", "source": "warehouse"}
            )

            assert result.success is True
            assert result.output is not None
            assert result.output["source"]["origin"] == "destination"
            assert result.output["source"]["name"] == "warehouse"
            assert result.output["rows"] == [{"n": "dest-row"}]
            # The request actually reached the destination server, not the source.
            assert "/cypher" in dest_server.behavior.hits
            assert dest_server.behavior.hits.count("/cypher") == 1
            assert source_server.behavior.hits == []
        finally:
            source_server.shutdown()
            dest_server.shutdown()


# ---------------------------------------------------------------------------
# (f) No sources + one destination -> resolves to destination automatically
# ---------------------------------------------------------------------------


class TestNoSourcesOneDestination:
    async def test_f_no_sources_one_destination_resolves_to_it(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        dest_server = _spawn_server(body=b'{"results": []}')
        try:
            hook = _make_hook({"only_dest": _dest("only_dest", dest_server.base_url)})
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver({}, coordinator)  # 0 sources configured
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})

            assert result.success is True
            assert result.output is not None
            assert result.output["source"]["origin"] == "destination"
            assert result.output["source"]["name"] == "only_dest"
            assert dest_server.behavior.hits == ["/cypher"]
        finally:
            dest_server.shutdown()


# ---------------------------------------------------------------------------
# (g) Provenance correctness -- for BOTH a source and a destination selection
# ---------------------------------------------------------------------------


class TestProvenanceCorrectness:
    async def test_g_provenance_names_actual_source_server(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        server = _spawn_server(body=b'{"results": []}')
        try:
            coordinator = _make_coordinator(None)  # no hook mounted
            resolver = _make_tool_resolver(
                {"primary": {"url": server.base_url, "api_key": "k"}}, coordinator
            )
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})

            assert result.success is True
            assert result.output is not None
            assert result.output["source"]["url"] == server.base_url
            assert result.output["source"]["name"] == "primary"
            assert result.output["source"]["origin"] == "source"
        finally:
            server.shutdown()

    async def test_g_provenance_names_actual_destination_server(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        server = _spawn_server(body=b'{"results": []}')
        try:
            hook = _make_hook({"only_dest": _dest("only_dest", server.base_url)})
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver({}, coordinator)
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})

            assert result.success is True
            assert result.output is not None
            assert result.output["source"]["url"] == server.base_url
            assert result.output["source"]["name"] == "only_dest"
            assert result.output["source"]["origin"] == "destination"
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# (h) Default (no pointer) resolution:
#     - 2+ SOURCES still fails loud (unchanged from #67 -- the ONLY default-path
#       ambiguity that fails loud).
#     - 0 sources + N destinations resolves to the FIRST destination (RATIFIED
#       RULE, user override of spec §4.4 -- destinations are the established read
#       fallback pool; provenance makes the pick visible, so no fail-loud).
# ---------------------------------------------------------------------------


class TestDefaultResolutionAmbiguity:
    async def test_h_two_plus_sources_no_pointer_fails_loud(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        server_a = _spawn_server()
        server_b = _spawn_server()
        try:
            coordinator = _make_coordinator(None)
            resolver = _make_tool_resolver(
                {
                    "a": {"url": server_a.base_url, "api_key": "k"},
                    "b": {"url": server_b.base_url, "api_key": "k"},
                },
                coordinator,
            )
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})

            assert result.success is False
            assert result.error is not None
            assert result.error["type"] == "ambiguous_source_selection"
            assert result.error["valid_sources"] == ["a", "b"]
            # No server was ever contacted -- fails loud BEFORE any HTTP call.
            assert server_a.behavior.hits == []
            assert server_b.behavior.hits == []
        finally:
            server_a.shutdown()
            server_b.shutdown()

    async def test_h_zero_sources_two_plus_destinations_resolves_to_first(self) -> None:
        """RATIFIED RULE (user override of spec §4.4): 0 sources + N destinations +
        no pointer -> resolve to the FIRST destination in config order (destinations
        are the established read-fallback pool). Does NOT fail loud. Provenance
        names that first destination, making the pick visible to the user."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        dest_a = _spawn_server(body=b'{"results": [{"n": "from-d1"}]}')
        dest_b = _spawn_server(body=b'{"results": [{"n": "from-d2"}]}')
        try:
            hook = _make_hook(
                {
                    "d1": _dest("d1", dest_a.base_url),
                    "d2": _dest("d2", dest_b.base_url),
                }
            )
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver({}, coordinator)  # 0 sources
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})

            assert result.success is True
            assert result.output is not None
            # First destination in config order wins -- and provenance names it.
            assert result.output["source"]["origin"] == "destination"
            assert result.output["source"]["name"] == "d1"
            assert result.output["source"]["url"] == dest_a.base_url
            assert result.output["rows"] == [{"n": "from-d1"}]
            # Only the first destination was actually queried (single-hit).
            assert dest_a.behavior.hits == ["/cypher"]
            assert dest_b.behavior.hits == []
        finally:
            dest_a.shutdown()
            dest_b.shutdown()


# ---------------------------------------------------------------------------
# (i) list_sources -- returns the correct merged pool
# ---------------------------------------------------------------------------


class TestListSources:
    async def test_i_list_sources_returns_merged_pool_no_query_run(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        source_server = _spawn_server()
        dest_server = _spawn_server()
        try:
            hook = _make_hook(
                {"warehouse": _dest("warehouse", dest_server.base_url, api_key="secret-dk")}
            )
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver(
                {"primary": {"url": source_server.base_url, "api_key": "secret-sk"}}, coordinator
            )
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute({"list_sources": True})

            assert result.success is True
            assert result.output is not None
            names = {e["name"]: e for e in result.output["connectable_set"]}
            assert set(names) == {"primary", "warehouse"}
            assert names["primary"]["origin"] == "source"
            assert names["primary"]["url"] == source_server.base_url
            assert names["warehouse"]["origin"] == "destination"
            assert names["warehouse"]["url"] == dest_server.base_url
            # No api_key leaked anywhere in the listing.
            serialized = json.dumps(result.output)
            assert "secret-sk" not in serialized
            assert "secret-dk" not in serialized
            # No query was actually run against either server.
            assert source_server.behavior.hits == []
            assert dest_server.behavior.hits == []
        finally:
            source_server.shutdown()
            dest_server.shutdown()

    async def test_i_source_shadows_same_named_destination_in_listing(self) -> None:
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        source_server = _spawn_server()
        dest_server = _spawn_server()
        try:
            hook = _make_hook({"default": _dest("default", dest_server.base_url)})
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver(
                {"default": {"url": source_server.base_url, "api_key": "k"}}, coordinator
            )
            tool = GraphQueryTool(coordinator, resolver)

            result = await tool.execute({"list_sources": True})

            assert result.success is True
            assert result.output is not None
            entries = result.output["connectable_set"]
            assert len(entries) == 1
            assert entries[0]["name"] == "default"
            assert entries[0]["origin"] == "source"
            assert entries[0]["url"] == source_server.base_url
        finally:
            source_server.shutdown()
            dest_server.shutdown()


# ---------------------------------------------------------------------------
# (j) blob_read E2E -- explicit destination select + a down destination
# ---------------------------------------------------------------------------


class TestBlobReadMultiSource:
    async def test_j_blob_read_explicit_destination_select_succeeds(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        blob_body = json.dumps({"hello": "warehouse"}).encode("utf-8")
        dest_server = _spawn_server(body=blob_body)
        try:
            hook = _make_hook({"warehouse": _dest("warehouse", dest_server.base_url)})
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver({}, coordinator)
            tool = BlobReadTool(coordinator, resolver)

            result = await tool.execute({"uri": "ci-blob://session1/mykey", "source": "warehouse"})

            assert result.success is True
            assert result.output is not None
            assert result.output["source"]["name"] == "warehouse"
            assert result.output["source"]["origin"] == "destination"
            assert result.output["source"]["url"] == dest_server.base_url
            assert "path" in result.output
            assert dest_server.behavior.hits == ["/blobs/session1/mykey"]
        finally:
            dest_server.shutdown()

    async def test_j_blob_read_down_destination_classified_failure_with_source(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        port = _find_free_port()  # bound-then-released -> guaranteed nothing listening
        down_url = f"http://127.0.0.1:{port}"
        hook = _make_hook({"warehouse": _dest("warehouse", down_url)})
        coordinator = _make_coordinator(hook)
        resolver = _make_tool_resolver({}, coordinator)
        tool = BlobReadTool(coordinator, resolver)

        result = await tool.execute({"uri": "ci-blob://session1/mykey", "source": "warehouse"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "connection_error"
        assert result.error["source"]["name"] == "warehouse"
        assert result.error["source"]["origin"] == "destination"

    async def test_j_blob_read_list_sources(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        dest_server = _spawn_server()
        try:
            hook = _make_hook({"warehouse": _dest("warehouse", dest_server.base_url)})
            coordinator = _make_coordinator(hook)
            resolver = _make_tool_resolver({}, coordinator)
            tool = BlobReadTool(coordinator, resolver)

            result = await tool.execute({"list_sources": True})

            assert result.success is True
            assert result.output is not None
            names = {e["name"] for e in result.output["connectable_set"]}
            assert names == {"warehouse"}
            assert dest_server.behavior.hits == []
        finally:
            dest_server.shutdown()
