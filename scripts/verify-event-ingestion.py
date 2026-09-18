#!/usr/bin/env python3
"""Score the public ingestion seam against an explicitly supplied, isolated server.

Writes one synthetic event and reads only its unique session. Does not provision a
server, inspect user configuration, or print credentials/response error bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from context_intelligence import AsyncCIClient
from context_intelligence.client import CIClientError
from context_intelligence.upload import build_event_payload


def server_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError(
            "use an HTTP(S) URL without credentials, query or fragment"
        )
    return value.rstrip("/")


async def score(args: argparse.Namespace) -> dict:
    token = args.token_file.read_text().strip()
    if not token:
        raise ValueError("token file is empty")
    sid = "public-ingest-" + uuid4().hex
    payload = build_event_payload(
        "host:diagnostic",
        "ci-public-api-fixture",
        {
            "session_id": sid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": uuid4().hex,
            "message": "synthetic public ingestion seam",
        },
    )
    client = AsyncCIClient(args.server_url, token, timeout=10)
    receipts = [await client.ingest(payload), await client.ingest(payload)]
    query = (
        "MATCH (n:Event) WHERE n.session_id = $sid "
        "RETURN n.event_name AS event_name, count(n) AS count"
    )
    rows = []
    for _ in range(80):
        rows = await client.cypher(query, workspace=payload["workspace"], params={"sid": sid})
        if rows:
            break
        await asyncio.sleep(0.25)
    failures = {}
    for name, failing in (
        ("bad_auth", AsyncCIClient(args.server_url, "synthetic-invalid-token", timeout=2)),
        ("connection_refused", AsyncCIClient(args.unavailable_url, token, timeout=2)),
    ):
        try:
            await failing.ingest(payload)
        except CIClientError as exc:
            failures[name] = {"error_type": exc.error_type, "status_code": exc.status_code}
        else:
            failures[name] = {"error_type": "unexpected_success"}
    source = Path(__file__).resolve().parents[1]
    checks = {
        "first_acceptance": receipts[0] == {"status": "queued", "session_id": sid},
        "duplicate_receipt": receipts[1] == {"status": "duplicate", "session_id": sid},
        "one_graph_event": rows == [{"event_name": payload["event"], "count": 1}],
        "auth_fails_loudly": failures["bad_auth"]
        == {"error_type": "http_status", "status_code": 401},
        "network_fails_loudly": failures["connection_refused"]
        == {"error_type": "connection_error", "status_code": None},
    }
    return {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "server_url": args.server_url,
        "server_revision": args.server_revision,
        "client_source_sha256": {
            str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                source / "context_intelligence/client.py",
                source / "context_intelligence/upload/__init__.py",
            )
        },
        "request": payload,
        "receipts": receipts,
        "graph_query": query,
        "graph_rows": rows,
        "failures": failures,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", type=server_url, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--server-revision", required=True, help="operator-verified server commit")
    parser.add_argument("--unavailable-url", type=server_url, default="http://127.0.0.1:1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(score(args))
    except CIClientError as exc:
        print(json.dumps({"passed": False, "error_type": exc.error_type}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    args.output.chmod(0o600)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
