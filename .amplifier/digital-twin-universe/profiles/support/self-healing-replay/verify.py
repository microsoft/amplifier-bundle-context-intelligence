#!/usr/bin/env python3
"""Assertions for the self-healing replay profile. Stdlib only.

Three lessons from the spike are encoded here rather than left to the caller,
because each one produced a WRONG result first:

1. **Wait for the drain.** ``POST /events`` returns 202 after a durable append,
   BEFORE the async flush to Neo4j. Counting immediately reads a number that is
   still climbing, so a duplicate check passes or fails for the wrong reason.

2. **Never assert ``:Event count == record count``.** Not every record becomes an
   ``:Event`` node -- some become ``ContentBlock`` / ``SST_EVENT`` nodes. The
   obvious assertion fails at 399 vs 400 on a perfectly healthy system.
   Convergence is proven as a FIXED POINT: one more full replay changes nothing,
   which demonstrates complete delivery AND idempotence in one move.

3. **Namespace the workspace per run.** The graph persists between runs. Reusing
   a workspace name makes run 2 read run 1's nodes and report "bug did not
   reproduce" -- a passing test that asserts nothing.

Usage:
    verify.py nodes      --server URL --token T --workspace WS [--wait]
    verify.py watermark  --session-dir DIR --destination NAME
    verify.py lines      --session-dir DIR
    verify.py reproduced --session-dir DIR --server URL --token T --workspace WS
    verify.py healed     --session-dir DIR --server URL --token T --workspace WS
    verify.py stalled    --session-dir DIR --destination NAME
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def ok(message: str) -> None:
    print(f"PASS: {message}")


def cypher(server: str, token: str, query: str, params: dict) -> list[dict]:
    body = json.dumps({"query": query, "params": params}).encode()
    request = urllib.request.Request(
        f"{server.rstrip('/')}/cypher",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read()).get("results", [])


def count_nodes(server: str, token: str, workspace: str) -> int:
    rows = cypher(
        server, token, "MATCH (n) WHERE n.workspace = $ws RETURN count(n) AS c", {"ws": workspace}
    )
    return int(rows[0]["c"]) if rows else 0


def count_events(server: str, token: str, workspace: str) -> int:
    rows = cypher(
        server,
        token,
        "MATCH (n:Event) WHERE n.workspace = $ws RETURN count(n) AS c",
        {"ws": workspace},
    )
    return int(rows[0]["c"]) if rows else 0


def wait_drained(server: str, token: str, workspace: str, timeout: float = 240.0) -> int:
    """Poll until the node count stops moving (lesson 1)."""
    deadline = time.monotonic() + timeout
    last, stable_at = -1, 0.0
    while time.monotonic() < deadline:
        current = count_nodes(server, token, workspace)
        if current == last and current > 0:
            if stable_at and (time.monotonic() - stable_at) >= 3.0:
                return current
            stable_at = stable_at or time.monotonic()
        else:
            last, stable_at = current, 0.0
        time.sleep(1.0)
    return count_nodes(server, token, workspace)


def count_lines(session_dir: Path) -> int:
    path = session_dir / "events.jsonl"
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for line in fh if line.strip())


def load_watermark(session_dir: Path, destination: str) -> dict:
    path = session_dir / "delivery" / f"{destination}.json"
    if not path.exists():
        fail(f"no watermark at {path} -- Increment 0 did not write one")
    return json.loads(path.read_text())


def newest_session(root: Path) -> Path:
    candidates = sorted(
        root.glob("*/sessions/*/context-intelligence"),
        key=lambda p: (p / "events.jsonl").stat().st_mtime if (p / "events.jsonl").exists() else 0,
    )
    if not candidates:
        fail(f"no session directory under {root}")
    return candidates[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--server", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--session-dir", default="")
    parser.add_argument("--destination", default="main")
    parser.add_argument("--expect-nodes", type=int, default=-1)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()

    session_dir = Path(args.session_dir) if args.session_dir else None

    if args.command == "nodes":
        total = (
            wait_drained(args.server, args.token, args.workspace)
            if args.wait
            else count_nodes(args.server, args.token, args.workspace)
        )
        events = count_events(args.server, args.token, args.workspace)
        print(json.dumps({"nodes": total, "events": events, "workspace": args.workspace}))
        return

    if args.command == "lines":
        assert session_dir
        print(json.dumps({"lines": count_lines(session_dir)}))
        return

    if args.command == "watermark":
        assert session_dir
        print(json.dumps(load_watermark(session_dir, args.destination)))
        return

    if args.command == "reproduced":
        # S1: the live path must have FAILED to deliver everything.
        assert session_dir
        lines = count_lines(session_dir)
        delivered = wait_drained(args.server, args.token, args.workspace, timeout=60)
        events = count_events(args.server, args.token, args.workspace)
        watermark = load_watermark(session_dir, args.destination)
        size = (session_dir / "events.jsonl").stat().st_size
        print(
            json.dumps(
                {"lines": lines, "nodes": delivered, "events": events, "watermark": watermark}
            )
        )
        if events >= lines:
            fail(
                f"bug did NOT reproduce: server holds {events} :Event for {lines} records. "
                "Raise the proxy latency or lower dispatch_queue_capacity."
            )
        if watermark["offset"] >= size:
            fail("watermark claims EOF but the server is behind -- the cursor is lying")
        ok(
            f"reproduced: {events} :Event on the server for {lines} local records; "
            f"watermark at {watermark['offset']}/{size}"
        )
        return

    if args.command == "healed":
        # S2/S3: converge, watermark at EOF, and a further replay is a no-op.
        assert session_dir
        size = (session_dir / "events.jsonl").stat().st_size
        total = wait_drained(args.server, args.token, args.workspace)
        watermark = load_watermark(session_dir, args.destination)
        if watermark["offset"] != size:
            fail(f"watermark {watermark['offset']} != EOF {size} -- backlog not fully swept")
        if args.expect_nodes >= 0 and total != args.expect_nodes:
            fail(f"NOT a fixed point: {args.expect_nodes} -> {total} nodes on replay")
        print(json.dumps({"nodes": total, "watermark": watermark}))
        ok(f"healed: watermark at EOF ({size}), {total} nodes for {args.workspace}")
        return

    if args.command == "stalled":
        # S4: a real outage must leave the cursor where it was, loudly.
        assert session_dir
        watermark = load_watermark(session_dir, args.destination)
        print(json.dumps(watermark))
        if watermark["offset"] != 0:
            fail(f"watermark advanced to {watermark['offset']} against a broken destination")
        if watermark.get("last_outcome") != "no_progress":
            fail(f"expected last_outcome=no_progress, got {watermark.get('last_outcome')!r}")
        if not watermark.get("last_error"):
            fail("no last_error recorded -- the failure would be invisible")
        if not watermark.get("consecutive_sweeps_without_progress"):
            fail("no-progress counter did not increment")
        ok(f"outage stayed loud: offset 0, last_error={watermark['last_error'][:60]!r}")
        return

    fail(f"unknown command {args.command!r}")


if __name__ == "__main__":
    main()
