"""END-TO-END: Phase 0 fail-loud + real timeout, driven through the tools' execute().

Proof boundary (see docs/multi-source-build-spec-v5.md §3 for the full Phase 0 spec):

  A SELECTED context-intelligence source that is down, hangs, or rejects the request
  can never masquerade as an empty ``success=True`` result. ``AsyncCIClient.cypher()``
  / ``fetch_blob()`` classify-and-raise ``CIClientError`` instead of swallowing to
  ``[]`` / ``None``, the async client honors a real per-request timeout, and both
  ``GraphQueryTool`` and ``BlobReadTool`` catch ``CIClientError`` and surface a
  classified ``success=False`` ``ToolResult``. The one deliberate exception: a
  genuine 200 response with no rows is NOT an error -- it is an empty success.

Real transport only for scenarios (a)-(c) and their blob_read counterparts --
per §6 of the spec, no mock stands in for connection-refused / timeout / HTTP-status
classification. Uses the same stdlib ``ThreadingHTTPServer`` pattern as
``modules/hook-context-intelligence/tests/test_dispatch_e2e.py`` (zero new deps).

READ-SIDE / FAN-IN ONLY: this test never touches the hook's fan-out (fanout.py,
_DestinationDispatcher, logging_handler.py). The hook resolver is stubbed to return
None (no hook mounted) -- the tools resolve entirely from their own ``sources`` config.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import MagicMock

import pytest

from context_intelligence.tool_resolver import ToolConfigResolver

# ---------------------------------------------------------------------------
# Shared HTTP server utilities -- stdlib only, mirrors test_dispatch_e2e.py
# ---------------------------------------------------------------------------


class _Behavior:
    """Mutable control block read by the handler on every request."""

    def __init__(self) -> None:
        self.status_code: int = 200
        self.sleep_s: float = 0.0
        self.body: bytes = b'{"results": []}'
        self.content_type: str = "application/json"
        self.accepted = threading.Condition()
        self.post_bodies: list[bytes] = []
        self.handler_threads: list[threading.Thread] = []
        self.finished_posts = 0
        self.serving_thread: threading.Thread | None = None


def _find_free_port() -> int:
    """Return a free localhost TCP port by briefly binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler_class(behavior: _Behavior) -> type[BaseHTTPRequestHandler]:
    """Return a handler class serving POST /cypher and GET /blobs/{sid}/{key}
    according to *behavior*."""

    class _Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            if behavior.sleep_s:
                time.sleep(behavior.sleep_s)
            try:
                self.send_response(behavior.status_code)
                self.send_header("Content-Type", behavior.content_type)
                self.end_headers()
                self.wfile.write(behavior.body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when the real client times out and closes its socket.

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/cypher":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            with behavior.accepted:
                behavior.post_bodies.append(body)
                behavior.handler_threads.append(threading.current_thread())
                behavior.accepted.notify_all()
            try:
                self._respond()
            finally:
                with behavior.accepted:
                    behavior.finished_posts += 1
                    behavior.accepted.notify_all()

        def do_GET(self) -> None:  # noqa: N802
            if not self.path.startswith("/blobs/"):
                self.send_error(404)
                return
            self._respond()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass  # silence stdlib server request logs

    return _Handler


def _start_server(port: int, behavior: _Behavior) -> ThreadingHTTPServer:
    """Bind a ThreadingHTTPServer on *port* and serve it in a daemon thread."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler_class(behavior))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    behavior.serving_thread = t
    t.start()
    return server


def _make_coordinator() -> MagicMock:
    """Coordinator stub with no hook mounted (get_capability -> None) -- forces
    the tools to resolve entirely from their own `sources` config (read-side only,
    zero contact with the hook's fan-out)."""
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability = MagicMock(return_value=None)
    return coordinator


def _make_tool_resolver(
    base_url: str, *, request_timeout: float | None = None
) -> ToolConfigResolver:
    """Real ToolConfigResolver with a single configured source pointing at *base_url*.

    Single source -> no ambiguity, no `source=` selector needed on the tool call.
    """
    config: dict[str, Any] = {
        "sources": {
            "testsrc": {"url": base_url, "api_key": "test-key"},
        },
    }
    if request_timeout is not None:
        config["request_timeout"] = request_timeout
    return ToolConfigResolver(config, _make_coordinator())


# ---------------------------------------------------------------------------
# Scenario (a)-(d): GraphQueryTool.execute()
# ---------------------------------------------------------------------------


class TestGraphQueryPhase0:
    """Real-socket scenarios a-d from build-spec-v5 §6.1, driven through
    GraphQueryTool.execute()."""

    async def test_request_timeout_is_invocation_local_over_real_tcp(self) -> None:
        """Long/short overrides change only this call; four calls yield four POSTs."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import GraphQueryTool

        behavior = _Behavior()
        behavior.sleep_s = 1.0
        server = _start_server(0, behavior)
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        results = []
        elapsed = []
        expected_body = {
            "query": "synthetic query",
            "params": {"request_timeout": 999, "nested": {"value": 7}},
            "workspace": "*",
        }
        try:
            tool = GraphQueryTool(
                _make_coordinator(), _make_tool_resolver(base_url, request_timeout=0.2)
            )
            for index, override in enumerate(
                [{}, {"request_timeout": 3}, {"request_timeout": 0.1}, {}], start=1
            ):
                # Never share nested params with the independent wire oracle.
                invocation = deepcopy(expected_body)
                invocation.update(override)
                t0 = time.monotonic()
                results.append(await tool.execute(invocation))
                elapsed.append(time.monotonic() - t0)
                assert invocation == {**expected_body, **override}
                with behavior.accepted:
                    assert behavior.accepted.wait_for(
                        lambda expected_count=index: len(behavior.post_bodies) >= expected_count,
                        timeout=3.0,
                    ), "tool returned without an accepted POST"
            with behavior.accepted:
                assert behavior.accepted.wait_for(
                    lambda: behavior.finished_posts == 4, timeout=5.0
                ), "not all POST handlers finished"
        finally:
            server.shutdown()
            server.server_close()  # Daemon request handlers are explicitly joined below.
            assert behavior.serving_thread is not None
            behavior.serving_thread.join(timeout=5.0)
            assert not behavior.serving_thread.is_alive()
            for thread in behavior.handler_threads:
                thread.join(timeout=5.0)
                assert not thread.is_alive()
            assert server.socket.fileno() == -1
            with socket.socket() as probe:
                probe.settimeout(1.0)
                assert probe.connect_ex(("127.0.0.1", port)) != 0

        assert [result.success for result in results] == [False, True, False, False]
        assert len(behavior.post_bodies) == behavior.finished_posts == 4
        assert [json.loads(body) for body in behavior.post_bodies] == [expected_body] * 4
        assert all(elapsed[index] < 0.9 for index in (0, 2, 3)), elapsed
        assert 1.0 <= elapsed[1] < 3.0, elapsed
        hint = (
            "The tool does not retry automatically. Inspect query scope, projections, and "
            "pagination first. If an intentionally complex query still needs more time and "
            "retrying is safe, retry with a larger top-level request_timeout in seconds. "
            "A client timeout is not a database deadline; prior work may still be running."
        )
        for index, effective in [(0, 0.2), (2, 0.1), (3, 0.2)]:
            result = results[index]
            assert result.error is not None
            assert result.error["type"] == "timeout"
            assert result.error["request_timeout"] == effective
            assert result.error["retry_hint"] == hint
            assert result.error["source"] == {
                "name": "testsrc",
                "url": base_url,
                "origin": "source",
            }
            assert (
                f"Effective HTTP request_timeout={effective:g} seconds." in result.error["message"]
            )
            assert result.output == result.error["message"]
            assert hint in result.get_serialized_output()
        assert results[1].error is None
        assert results[1].output == {
            "source": {"name": "testsrc", "url": base_url, "origin": "source"},
            "rows": [],
        }

    async def test_a_connection_refused(self) -> None:
        """(a) Down/refused source -> success False, error.type == connection_error."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import (
            GraphQueryTool,
        )

        port = _find_free_port()  # bound-then-released -> guaranteed nothing listening
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url)
        tool = GraphQueryTool(_make_coordinator(), resolver)

        result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "connection_error"

    async def test_b_hang_triggers_timeout(self) -> None:
        """(b) Hung source -> per-source timeout fires -> success False, type timeout.

        Server sleeps 2s; configured request_timeout is 0.3s -- proves the timeout
        actually fires (test completes in ~0.3s, not 2s+).
        """
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import (
            GraphQueryTool,
        )

        port = _find_free_port()
        behavior = _Behavior()
        behavior.sleep_s = 2.0
        server = _start_server(port, behavior)
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url, request_timeout=0.3)
        tool = GraphQueryTool(_make_coordinator(), resolver)

        try:
            t0 = time.monotonic()
            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})
            elapsed = time.monotonic() - t0
        finally:
            server.shutdown()
            server.server_close()

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "timeout"
        assert elapsed < 1.5, f"Expected timeout to fire near 0.3s, took {elapsed:.2f}s"

    @pytest.mark.parametrize("status_code", [500, 401])
    async def test_c_http_status_error(self, status_code: int) -> None:
        """(c) 500 / 401 -> classified http_status failure, NOT empty-success."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import (
            GraphQueryTool,
        )

        port = _find_free_port()
        behavior = _Behavior()
        behavior.status_code = status_code
        behavior.body = b'{"detail": "error"}'
        server = _start_server(port, behavior)
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url)
        tool = GraphQueryTool(_make_coordinator(), resolver)

        try:
            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})
        finally:
            server.shutdown()
            server.server_close()

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_status"
        assert result.error["status_code"] == status_code

    @pytest.mark.parametrize("body", [b'{"results": []}', b"[]"])
    async def test_d_genuine_empty_200_is_success(self, body: bytes) -> None:
        """(d) Genuine empty 200 -> success True, empty output. We must NOT over-raise."""
        from amplifier_module_tool_context_intelligence_query.graph_query_tool import (
            GraphQueryTool,
        )

        port = _find_free_port()
        behavior = _Behavior()
        behavior.body = body
        server = _start_server(port, behavior)
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url)
        tool = GraphQueryTool(_make_coordinator(), resolver)

        try:
            result = await tool.execute({"query": "MATCH (n) RETURN n LIMIT 1"})
        finally:
            server.shutdown()
            server.server_close()

        assert result.success is True
        assert result.output is not None
        assert result.output["rows"] == []


# ---------------------------------------------------------------------------
# blob_read_tool.py counterparts -- same CIClientError catch, same client
# ---------------------------------------------------------------------------


class TestBlobReadPhase0:
    """Real-socket scenarios for BlobReadTool.execute() -- same Phase 0 contract
    (fetch_blob raises CIClientError instead of swallowing to None)."""

    async def test_connection_refused(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import (
            BlobReadTool,
        )

        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url)
        tool = BlobReadTool(_make_coordinator(), resolver)

        result = await tool.execute({"uri": "ci-blob://session1/mykey"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "connection_error"

    async def test_hang_triggers_timeout(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import (
            BlobReadTool,
        )

        port = _find_free_port()
        behavior = _Behavior()
        behavior.sleep_s = 2.0
        server = _start_server(port, behavior)
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url, request_timeout=0.3)
        tool = BlobReadTool(_make_coordinator(), resolver)

        try:
            t0 = time.monotonic()
            result = await tool.execute({"uri": "ci-blob://session1/mykey"})
            elapsed = time.monotonic() - t0
        finally:
            server.shutdown()
            server.server_close()

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "timeout"
        assert elapsed < 1.5, f"Expected timeout to fire near 0.3s, took {elapsed:.2f}s"

    @pytest.mark.parametrize("status_code", [500, 401])
    async def test_http_status_error(self, status_code: int) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import (
            BlobReadTool,
        )

        port = _find_free_port()
        behavior = _Behavior()
        behavior.status_code = status_code
        behavior.body = b'{"detail": "error"}'
        server = _start_server(port, behavior)
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url)
        tool = BlobReadTool(_make_coordinator(), resolver)

        try:
            result = await tool.execute({"uri": "ci-blob://session1/mykey"})
        finally:
            server.shutdown()
            server.server_close()

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_status"
        assert result.error["status_code"] == status_code

    async def test_genuine_blob_200_is_success_and_carries_provenance(self) -> None:
        """Real 200 with a valid JSON blob body -> success True, file written, AND
        the success output carries the `source` provenance block {name,url,origin}
        naming the REAL server that answered (Risk 1, addressable part).

        NOTE (Risk 1 scope): the *production* blob round-trip -- a ci-blob:// URI
        minted by the hook's WRITE-side fan-out and later resolved -- cannot be
        exercised here: blobs are minted write-side, which is out of scope for this
        read-side work (guardrail: never touch the hook fan-out). This real-socket
        test IS the proof for the blob-read SUCCESS + provenance path: a live HTTP
        server serves GET /blobs/<session>/<key>, BlobReadTool.execute() drives a
        real success, and we assert the provenance block names that server.
        """
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import (
            BlobReadTool,
        )

        port = _find_free_port()
        behavior = _Behavior()
        behavior.body = json.dumps({"hello": "world"}).encode("utf-8")
        server = _start_server(port, behavior)
        base_url = f"http://127.0.0.1:{port}"
        resolver = _make_tool_resolver(base_url)
        tool = BlobReadTool(_make_coordinator(), resolver)

        try:
            result = await tool.execute({"uri": "ci-blob://session1/mykey"})
        finally:
            server.shutdown()
            server.server_close()

        assert result.success is True
        assert result.output is not None
        assert "path" in result.output
        # Provenance block present, well-formed, and naming the REAL server that answered.
        assert result.output["source"] == {
            "name": "testsrc",
            "url": base_url,
            "origin": "source",
        }
