# Dual-Path Library Template

Every tool produced by the context-intelligence mode follows this shape.
The library is the dispatcher; agent tools, CLIs, and embedded hosts are thin
wrappers that call into it.

Vendor this file into the consuming project. The consuming project owns
maintenance — when the context intelligence schema changes, the consuming
project's owner updates the local copy. Probes fail loudly on version mismatch
(see `_assert_jsonl_compatible`) so a stale copy cannot silently return wrong
answers.

---

## Complete Python Template

```python
"""
Dual-path library template — context intelligence-aware tooling.

Every tool produced by the context-intelligence mode follows this
shape. The library is the dispatcher; agent tools, CLIs, and embedded
hosts are thin wrappers that call into it.

Vendor this file into the consuming project. The consuming project owns
maintenance — when the context intelligence schema changes, the consuming
project's owner updates the local copy. Probes fail loudly on version
mismatch (see _assert_jsonl_compatible) so a stale copy cannot silently
return wrong answers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Literal

# ---------------------------------------------------------------------------
# Schema compatibility — fail loud on mismatch.

_SUPPORTED_JSONL_VERSION = "1.0.0"


class JsonlSchemaMismatch(RuntimeError):
    """Raised when local JSONL files are not the version this code expects."""


def _assert_jsonl_compatible(session_dir: Path) -> None:
    """Read metadata.json and verify the format/version."""
    meta_path = session_dir / "metadata.json"
    meta = json.loads(meta_path.read_text())
    fmt = meta.get("format")
    ver = meta.get("version")
    if fmt != "context-intelligence":
        raise JsonlSchemaMismatch(
            f"{meta_path}: expected format=context-intelligence, got {fmt!r}"
        )
    if ver != _SUPPORTED_JSONL_VERSION:
        raise JsonlSchemaMismatch(
            f"{meta_path}: expected version={_SUPPORTED_JSONL_VERSION}, got {ver!r}"
        )


# ---------------------------------------------------------------------------
# Server reachability probe — resolves the [] ambiguity.

_PROBE_TTL_SECONDS = 60
_PROBE_TIMEOUT_SECONDS = 2
_probe_cache: dict[str, tuple[float, bool]] = {}


def _probe_server_reachable(server_url: str | None, api_key: str | None) -> bool:
    """
    Run `RETURN 1 AS ok` with a short timeout. Cache the result for 60s
    keyed on server URL. AsyncCIClient.cypher() returns [] on any error,
    so we treat [{"ok": 1}] as the only positive signal.
    """
    if not server_url:
        return False
    now = time.monotonic()
    cached = _probe_cache.get(server_url)
    if cached and now - cached[0] < _PROBE_TTL_SECONDS:
        return cached[1]

    try:
        from context_intelligence.client import AsyncCIClient  # in-process
        client = AsyncCIClient(server_url, api_key, timeout=_PROBE_TIMEOUT_SECONDS)
        rows = client.cypher_sync("RETURN 1 AS ok")
        ok = bool(rows) and rows[0].get("ok") == 1
    except Exception:
        ok = False

    _probe_cache[server_url] = (now, ok)
    return ok


# ---------------------------------------------------------------------------
# Public entry point — replace get_X() with your tool's question.

Mode = Literal["auto", "graph", "jsonl"]


def get_X(
    *args: Any,
    mode: Mode = "auto",
    server_url: str | None = None,
    api_key: str | None = None,
    session_dir: Path | None = None,
) -> list[dict]:
    """
    Answer the question this library exists to answer.

    mode='auto'  — probe server, route accordingly (production default)
    mode='graph' — force graph path; raise if unreachable
    mode='jsonl' — force JSONL path
    """
    if mode == "graph":
        if not _probe_server_reachable(server_url, api_key):
            raise RuntimeError("graph mode requested but server is unreachable")
        return _via_graph(*args, server_url=server_url, api_key=api_key)

    if mode == "jsonl":
        assert session_dir is not None, "jsonl mode requires session_dir"
        return _via_jsonl(*args, session_dir=session_dir)

    # auto
    if _probe_server_reachable(server_url, api_key):
        return _via_graph(*args, server_url=server_url, api_key=api_key)
    assert session_dir is not None, "auto fallback requires session_dir"
    return _via_jsonl(*args, session_dir=session_dir)


# ---------------------------------------------------------------------------
# Graph path — Data Layer 2 / Foundation Layer via Cypher.

def _via_graph(*args: Any, server_url: str, api_key: str | None) -> list[dict]:
    from context_intelligence.client import AsyncCIClient
    client = AsyncCIClient(server_url, api_key)
    cypher = """
        // Replace with the verified Cypher this tool was crystallised around.
        MATCH (s:Session) RETURN s LIMIT 10
    """
    return client.cypher_sync(cypher, params={})


# ---------------------------------------------------------------------------
# JSONL path — Data Layer 1 baseline, always available.

def _via_jsonl(*args: Any, session_dir: Path) -> list[dict]:
    _assert_jsonl_compatible(session_dir)
    events_path = session_dir / "events.jsonl"
    out: list[dict] = []
    with events_path.open() as fh:
        for line in fh:
            event = json.loads(line)
            # Replace with the event filter this tool was crystallised around.
            if event.get("event") == "tool:post":
                out.append(event)
    return out


# ---------------------------------------------------------------------------
# Wrapper examples (each is a thin shell over get_X):
#
# Agent tool (in-session):
#   register a tool whose handler reads HookConfigResolver for server_url and
#   session_dir, then calls get_X(..., mode="auto", ...).
#
# CLI (out-of-session):
#   click/argparse wrapper. Use resolve_config() from
#   context_intelligence.config to populate server_url, api_key. Accept
#   --session-dir for the JSONL path. Then call get_X(..., mode="auto", ...).
#
# Embedded host (e.g. resolver, app-cli, custom application):
#   import get_X directly. The host already knows where its sessions live
#   and has its own config — pass them in.
#
# ---------------------------------------------------------------------------
# Vendoring obligations:
#
# 1. Bump _SUPPORTED_JSONL_VERSION when context-intelligence ships a new
#    JSONL schema and you have updated _via_jsonl() to handle it.
# 2. Re-verify the Cypher in _via_graph against the latest schema.
# 3. The probe is correct as-is — do not modify unless the bundle changes
#    the contract.
# 4. Schema mismatch raises JsonlSchemaMismatch — surface it clearly to
#    the user. Silent acceptance produces wrong answers.
```

---

## Key Points

- **`_probe_server_reachable`**: Runs `RETURN 1 AS ok` with a 2-second timeout. Caches the result for 60 seconds keyed on server URL. This resolves the `[]` ambiguity — `AsyncCIClient.cypher()` returns `[]` for both "no rows" and "server unreachable".
- **`_assert_jsonl_compatible`**: Must be called before reading any JSONL event lines. Fails loudly on format/version mismatch.
- **`get_X`**: The public dispatcher. Replace with your tool's actual question. Use `mode="auto"` in production.
- **`_via_graph`**: Replace the placeholder Cypher with the verified query crystallised during design-mode investigation.
- **`_via_jsonl`**: Replace the placeholder filter with the event filter and field extraction pattern verified during design-mode exploration.
- **Wrapper shells**: Agent tools, CLIs, and embedded hosts are thin wrappers — they resolve configuration and call `get_X`, nothing more.
