#!/usr/bin/env python3
"""Latency-injecting HTTP proxy — the piece that makes the bug reproducible.

The delivery shortfall this profile validates only exists when the destination
is SLOWER than the producer. A localhost POST is sub-millisecond, so the
single-in-flight dispatcher keeps up trivially and NOTHING reproduces. Sitting
this in front of a real Context-Intelligence server reproduces the measured
Azure/APIM round-trip (~250-300 ms) without needing Azure.

PER-REQUEST latency, not per-connection. httpx reuses keep-alive connections, so
a connection-level sleep would delay only the first request on each connection
and the queue would never build. That is why this speaks HTTP/1.1 rather than
piping bytes between sockets.

Stdlib only (http.server + urllib): the DTU container has python3 but is not
guaranteed to have httpx before the bundle install runs, and this proxy must be
able to start first.

    python3 latency_proxy.py --listen 8100 --upstream http://10.0.0.1:38000 \
        --latency-ms 250 --stats /root/proxy-stats.json
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATS = {"requests": 0, "bytes_in": 0, "errors": 0, "started_at": time.time()}
STATS_LOCK = threading.Lock()

UPSTREAM = ""
LATENCY_S = 0.0
STATS_PATH = ""


def _record(key: str, amount: int = 1) -> None:
    with STATS_LOCK:
        STATS[key] += amount
        if STATS_PATH:
            try:
                with open(STATS_PATH, "w") as fh:
                    json.dump(STATS, fh)
            except OSError:
                pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # silence access logs
        return

    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        _record("requests")
        _record("bytes_in", len(body))

        # THE POINT OF THIS FILE.
        if LATENCY_S > 0:
            time.sleep(LATENCY_S)

        request = urllib.request.Request(
            f"{UPSTREAM.rstrip('/')}{self.path}", data=body or None, method=method
        )
        for key, value in self.headers.items():
            if key.lower() in ("host", "content-length", "connection", "transfer-encoding"):
                continue
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                status = response.status
                ctype = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            payload, status = exc.read(), exc.code
            ctype = exc.headers.get("Content-Type", "application/json")
        except Exception as exc:  # upstream down -> an honest 502, never a hang
            _record("errors")
            payload = f"proxy upstream error: {exc}".encode()
            status, ctype = 502, "text/plain"

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # BaseHTTPRequestHandler API
        self._proxy("POST")

    def do_GET(self) -> None:  # BaseHTTPRequestHandler API
        self._proxy("GET")

    def do_DELETE(self) -> None:  # BaseHTTPRequestHandler API
        self._proxy("DELETE")


def main() -> None:
    global UPSTREAM, LATENCY_S, STATS_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=8100)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--latency-ms", type=float, default=250.0)
    parser.add_argument("--stats", default="")
    args = parser.parse_args()

    UPSTREAM = args.upstream
    LATENCY_S = args.latency_ms / 1000.0
    STATS_PATH = args.stats

    server = ThreadingHTTPServer(("127.0.0.1", args.listen), Handler)
    print(
        f"latency-proxy :{args.listen} -> {UPSTREAM} (+{args.latency_ms}ms/request)",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
