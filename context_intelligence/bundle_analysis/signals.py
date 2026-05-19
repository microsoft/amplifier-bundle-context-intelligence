"""context_intelligence.bundle_analysis.signals — graph-based usage signals.

Layer 1 detection: executes pre-written Cypher query files (S-1..S-18) via an
injected AsyncCIClient and aggregates rows into per-bundle invocation counts by
component type (agents, skills, modes, recipes, tools).

The library receives a ready-to-use client — it knows nothing about config,
env vars, or coordinator capabilities.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from context_intelligence.client import AsyncCIClient

logger = logging.getLogger("context_intelligence.bundle_analysis.signals")

# ---------------------------------------------------------------------------
# Query map: component_type -> list of (cypher_file_stem, row_bundle_key, row_count_key)
# ---------------------------------------------------------------------------

_QUERY_MAP: dict[str, list[tuple[str, str, str]]] = {
    "agents": [("s01-s02-agents", "bundle", "invocations")],
    "skills": [("s04-s05-s09-s12-s13-skills-modes", "bundle", "skill_invocations")],
    "modes": [("s04-s05-s09-s12-s13-skills-modes", "bundle", "mode_invocations")],
    "recipes": [("s03-s10-s11-recipes", "bundle", "invocations")],
    "tools": [("s08-s15-coverage-tools", "bundle", "invocations")],
}


def _queries_dir() -> Path:
    """Return the path to the packaged _queries directory."""
    return Path(__file__).resolve().parent.parent / "_queries"


def _extract_cypher(body: str) -> str:
    """Extract pure Cypher statement from a query-section body.

    Skips leading blank lines and ``//`` comment-only lines (the doc block
    preceding each query), then captures lines from the first Cypher keyword
    up to and including the line that contains the statement-terminating
    ``;``.  Inline ``//`` comments embedded inside the statement are kept.
    """
    cypher_lines: list[str] = []
    started = False

    for line in body.splitlines():
        stripped = line.strip()
        if not started:
            # Skip blank lines and comment-only lines at the top of the block
            if stripped == "" or stripped.startswith("//"):
                continue
            started = True
        cypher_lines.append(line)
        # Stop at the statement terminator; skip if the line is itself a comment
        if ";" in stripped and not stripped.startswith("//"):
            break

    return "\n".join(cypher_lines).strip()


def _select_query(file_text: str, *, session_id: str | None) -> str | None:
    """Select the appropriate single Cypher query from a multi-query file.

    Each ``.cypher`` file contains multiple named queries separated by
    ``// QUERY: <name>`` marker lines.  The correct query is chosen by scope:

    - ``session_id`` provided → first query whose name contains ``_in_session``
    - workspace-only           → first query whose name contains ``_cross_session``

    Returns ``None`` if no matching query is found for the requested scope
    (a warning is logged instead of raising).
    """
    # Split into [header, name1, body1, name2, body2, ...]
    parts = re.split(r"^// QUERY:\s*(\S+)\s*$", file_text, flags=re.MULTILINE)

    queries: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        cypher = _extract_cypher(body)
        if cypher:
            queries[name] = cypher

    suffix = "_in_session" if session_id is not None else "_cross_session"
    for name, cypher in queries.items():
        if suffix in name:
            return cypher

    logger.warning(
        "No query with suffix %r found in file (available: %s)",
        suffix,
        ", ".join(queries.keys()) or "<none>",
    )
    return None


def _ensure_bundle_entry(bundles: dict[str, Any], name: str) -> None:
    """Ensure *bundles* has a zero-initialised entry for *name*."""
    if name not in bundles:
        bundles[name] = {
            "agents": 0,
            "skills": 0,
            "modes": 0,
            "recipes": 0,
            "tools": 0,
        }


async def run_signals(
    *,
    client: AsyncCIClient,
    workspace: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Query the context-intelligence graph and return bundle usage signals.

    Parameters
    ----------
    client:
        Async CI client for Cypher queries.
    workspace:
        Workspace to scope queries.
    session_id:
        Optional session ID to narrow the query scope.

    Returns
    -------
    dict
        Mapping of bundle name → ``{agents, skills, modes, recipes, tools}``
        invocation counts.  Returns an empty dict on any unexpected error.
    """
    params: dict[str, Any] = {}
    if session_id is not None:
        params["session_id"] = session_id

    bundles: dict[str, Any] = {}
    queries_dir = _queries_dir()

    try:
        for component, query_specs in _QUERY_MAP.items():
            for file_stem, bundle_key, count_key in query_specs:
                query_file = queries_dir / f"{file_stem}.cypher"
                if not query_file.exists():
                    logger.warning("Query file not found, skipping: %s", query_file)
                    continue

                file_text = query_file.read_text(encoding="utf-8")
                query = _select_query(file_text, session_id=session_id)
                if query is None:
                    logger.warning(
                        "No suitable query found in %s for scope (session_id=%r), skipping",
                        query_file.name,
                        session_id,
                    )
                    continue

                try:
                    rows = await client.cypher(query, workspace, params=params)
                except Exception as exc:
                    logger.warning(
                        "Cypher query failed for %s (component=%s): %s",
                        query_file.name,
                        component,
                        exc,
                    )
                    continue

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    bundle_name = row.get(bundle_key)
                    if not isinstance(bundle_name, str):
                        continue
                    count = row.get(count_key, 1)
                    if not isinstance(count, (int, float)):
                        continue
                    _ensure_bundle_entry(bundles, bundle_name)
                    bundles[bundle_name][component] += int(count)

    except Exception as exc:
        logger.warning("run_signals encountered an unexpected error: %s", exc)
        return {}

    return bundles
