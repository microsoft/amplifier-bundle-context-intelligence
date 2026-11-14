"""Actual server readiness; no credentials, response bodies or exception detail printed."""

import json
from urllib.request import urlopen

if not __debug__:
    raise RuntimeError("readiness assertions require non-optimized Python")

with urlopen("http://127.0.0.1:8000/status", timeout=5) as response:
    status = json.load(response)
assert status["status"] == "ok"
assert status["neo4j_connected"] is True
assert status["neo4j_query_connected"] is True
assert status["identity_ready"] is True
assert status["recovery_complete"] is True
assert status["recovery_error"] is None
assert status["auth"]["mode"] == "static"
