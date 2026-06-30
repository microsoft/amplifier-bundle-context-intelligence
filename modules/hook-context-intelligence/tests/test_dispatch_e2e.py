"""END-TO-END: real local HTTP server — kill mid-stream, restart, assert recovery.

Proof boundary (state in this docstring):
  This test proves two things working together against a real stdlib HTTP server,
  using no external HTTP-mock dependencies:

  1. Client-side retry: _DestinationDispatcher retries on ConnectError (classified
     as _TRANSIENT) with capped exponential backoff until the server comes back up.
     No mocks — real httpx AsyncClient, real TCP connections on localhost.

  2. Server-side dedup by idempotency_key: when the same server instance (same
     _EventStore) is restarted on the same port, an event that was in-flight when
     the server died is retried by the client but rejected as a duplicate by the
     server's idempotency_key check.  Every original event arrives exactly once,
     in original enqueue order, with no duplicates.

  A remote (Tailscale-style) endpoint run is a documented follow-up, not covered here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DestinationDispatcher,
)

_LOGGER_NAME = "amplifier_module_hook_context_intelligence.handlers.logging_handler"


# ---------------------------------------------------------------------------
# Shared HTTP server utilities — stdlib only, zero new dependencies
# ---------------------------------------------------------------------------


class _EventStore:
    """Thread-safe ordered store that deduplicates events by idempotency_key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen_keys: set[str] = set()
        self._events: list[dict[str, Any]] = []

    def receive(self, payload: dict[str, Any]) -> bool:
        """Idempotently record payload.  Returns True if accepted, False if duplicate."""
        key = payload.get("idempotency_key", "")
        with self._lock:
            if key in self._seen_keys:
                return False
            self._seen_keys.add(key)
            self._events.append(payload)
            return True

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)


def _make_handler_class(store: _EventStore) -> type[BaseHTTPRequestHandler]:
    """Return a handler class that POSTs /events into the given store."""

    class EventHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/events":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
                store.receive(payload)
            except Exception:
                self.send_error(400)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format: str, *args: Any) -> None:
            pass  # silence stdlib server request logs

    return EventHandler


def _find_free_port() -> int:
    """Return a free localhost TCP port by briefly binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, store: _EventStore) -> ThreadingHTTPServer:
    """Bind a ThreadingHTTPServer on *port* and serve it in a daemon thread."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler_class(store))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---------------------------------------------------------------------------
# Dispatcher factory
# ---------------------------------------------------------------------------


def _make_dispatcher(url: str) -> _DestinationDispatcher:
    """Build a dispatcher with fast backoff so the E2E test completes quickly."""
    return _DestinationDispatcher(
        name="e2e-dest",
        url=url,
        api_key="test-key",
        workspace="ws",
        dispatch_timeout=2.0,
        failure_threshold=3,
        queue_capacity=256,
        close_drain_timeout=20.0,
        backoff_initial=0.05,  # 50 ms first retry
        backoff_max=0.20,  # cap at 200 ms
        backoff_jitter=False,
    )


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


class TestKillAndRestart:
    """Kill server mid-stream → restart it → assert full recovery with no duplicates."""

    async def test_kill_mid_stream_restart_all_arrive_in_order(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        Proof boundary (see module docstring for full statement):
          Against a real local stdlib HTTP server, after killing mid-stream and
          restarting, every streamed event is received in original enqueue order,
          with no duplicates (dedup by idempotency_key), and the RECOVERY notice
          ('Reconnected to ... resuming delivery') fired.
        """
        N = 12  # total events: 6 enqueued pre-kill, 6 enqueued post-kill
        port = _find_free_port()
        store = _EventStore()
        server2: ThreadingHTTPServer | None = None

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            server1 = _start_server(port, store)
            d = _make_dispatcher(f"http://127.0.0.1:{port}")

            try:
                # ------------------------------------------------------------------ #
                # Phase 1: enqueue first batch and wait for at least 1 delivery      #
                # ------------------------------------------------------------------ #
                for i in range(N // 2):
                    d.enqueue(f"evt:{i}", {"session_id": "s1", "idx": i})

                loop = asyncio.get_event_loop()
                t0 = loop.time()
                while store.count < 1:
                    if loop.time() - t0 > 5.0:
                        pytest.fail(
                            "No events reached the server within 5 s — "
                            "check port binding or dispatcher URL"
                        )
                    await asyncio.sleep(0.02)

                # ------------------------------------------------------------------ #
                # Phase 2: kill the server mid-stream                                #
                # ------------------------------------------------------------------ #
                # shutdown() signals serve_forever to stop (blocks ≤ poll_interval   #
                # = 0.5 s), then server_close() releases the socket so new           #
                # connection attempts get ECONNREFUSED → httpx.ConnectError →        #
                # _TRANSIENT → dispatcher enters retry/backoff loop.                  #
                # ------------------------------------------------------------------ #
                server1.shutdown()
                server1.server_close()

                # Enqueue second batch while the server is down
                for i in range(N // 2, N):
                    d.enqueue(f"evt:{i}", {"session_id": "s1", "idx": i})

                # Wait until the dispatcher enters degraded state (proves retry active)
                t1 = loop.time()
                while not d._degraded_warned:
                    if loop.time() - t1 > 5.0:
                        pytest.fail(
                            "Dispatcher never became degraded after server kill. "
                            f"_consecutive_failures={d._consecutive_failures}"
                        )
                    await asyncio.sleep(0.05)

                # ------------------------------------------------------------------ #
                # Phase 3: restart the server on the same port                       #
                # ThreadingHTTPServer sets allow_reuse_address=True (HTTPServer      #
                # default) so rebinding to the same port succeeds immediately.       #
                # ------------------------------------------------------------------ #
                server2 = _start_server(port, store)

                # Wait for all N unique events to arrive (generous timeout)
                t2 = loop.time()
                while store.count < N:
                    if loop.time() - t2 > 20.0:
                        pytest.fail(
                            f"Only {store.count}/{N} events arrived within 20 s. "
                            f"Indices received: "
                            f"{[e['data']['idx'] for e in store.snapshot()]}"
                        )
                    await asyncio.sleep(0.1)

                # ------------------------------------------------------------------ #
                # Assertions                                                          #
                # ------------------------------------------------------------------ #
                received = store.snapshot()

                # (1) All N unique events arrived (no events lost)
                assert len(received) == N, (
                    f"Expected {N} unique events, got {len(received)}. "
                    f"Indices: {[e['data']['idx'] for e in received]}"
                )

                # (2) Events arrived in original enqueue order.
                # Single-worker FIFO + deterministic idempotency_key guarantees this:
                # even if event K was retried (server received it but ACK was lost),
                # the dedup rejects the duplicate and the event appears exactly once,
                # in its original position.
                indices = [e["data"]["idx"] for e in received]
                assert indices == list(range(N)), (
                    f"Events not in original enqueue order.\n"
                    f"Expected: {list(range(N))}\n"
                    f"Got:      {indices}"
                )

                # (3) No duplicate idempotency keys in the deduplicated store
                keys = [e["idempotency_key"] for e in received]
                assert len(keys) == len(set(keys)), (
                    f"Duplicate idempotency_keys found: "
                    f"{[k for k in set(keys) if keys.count(k) > 1]}"
                )

                # (4) Recovery notice fired after the dispatcher reconnected
                recovery_msgs = [
                    r.getMessage()
                    for r in caplog.records
                    if "Reconnected to" in r.getMessage() and "resuming delivery" in r.getMessage()
                ]
                assert len(recovery_msgs) >= 1, (
                    "Expected RECOVERY notice ('Reconnected to ... resuming delivery') "
                    "but none was logged. "
                    f"All INFO+ records from dispatcher: "
                    f"{[r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]}"
                )

            finally:
                if server2 is not None:
                    server2.shutdown()
                    server2.server_close()
                await d.close()
