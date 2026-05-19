"""context_intelligence.bundle_analysis.signals — graph-based usage signals.

Layer 1 detection: executes pre-written Cypher query files (S-1..S-18) via an
injected AsyncCIClient and aggregates rows into per-bundle invocation counts by
component type (agents, skills, modes, recipes, tools).

The library receives a ready-to-use client — it knows nothing about config,
env vars, or coordinator capabilities.

When every Cypher query raises an exception (server unreachable), the function
falls back to :func:`.jsonl_signals.run_signals_from_jsonl` for best-effort
signal extraction from local events.jsonl files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from context_intelligence.client import AsyncCIClient

logger = logging.getLogger("context_intelligence.bundle_analysis.signals")

# ---------------------------------------------------------------------------
# Query maps: component_type -> (cypher_file_stem, row_bundle_key, row_count_key)
#
# Each entry names one .cypher file under bundle_analysis/queries/.
# Session map is used when session_id is provided; workspace map otherwise.
# ---------------------------------------------------------------------------

_SESSION_QUERY_MAP: dict[str, tuple[str, str, str]] = {
    "agents": ("s01_agents_in_session", "bundle", "invocations"),
    "skills": ("s04_skill_load_in_session", "bundle", "skill_invocations"),
    "modes": ("s05_mode_set_in_session", "bundle", "mode_invocations"),
    "recipes": ("s03_recipe_execute_in_session", "bundle", "invocations"),
    "tools": ("s15_bundle_contributed_tools_in_session", "bundle", "invocations"),
}

_WORKSPACE_QUERY_MAP: dict[str, tuple[str, str, str]] = {
    "agents": ("s01_agents_cross_session", "bundle", "total_invocations"),
    "skills": ("s04_skill_load_candidates", "bundle", "skill_invocations"),
    "modes": ("s05_mode_set_candidates", "bundle", "mode_invocations"),
    "recipes": ("s03_recipe_execute_candidates", "bundle", "invocations"),
    "tools": ("s15_bundle_contributed_tools", "bundle", "invocations"),
}


def _queries_dir() -> Path:
    """Return the path to the packaged queries directory."""
    return Path(__file__).parent / "queries"


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
    base_path: Path | None = None,
) -> dict[str, Any]:
    """Query the CI graph for bundle usage signals, with JSONL fallback.

    Falls back to :func:`.jsonl_signals.run_signals_from_jsonl` when the CI
    graph server is unreachable (all Cypher queries fail with exceptions).  If
    the server responds — even with empty results — the graph result is
    authoritative and no fallback occurs.

    Parameters
    ----------
    client:
        Async CI client. When the client has no server URL configured, signals
        are read directly from JSONL without attempting the graph.
    workspace:
        Workspace to scope queries.
    session_id:
        Optional session ID to narrow scope.
    base_path:
        Root of the Amplifier projects directory for the JSONL fallback.
        Defaults to ``~/.amplifier/projects``.
    """
    from context_intelligence.bundle_analysis.jsonl_signals import run_signals_from_jsonl  # noqa: PLC0415

    params: dict[str, Any] = {}
    if session_id is not None:
        params["session_id"] = session_id

    query_map = _SESSION_QUERY_MAP if session_id is not None else _WORKSPACE_QUERY_MAP

    bundles: dict[str, Any] = {}
    queries_dir = _queries_dir()

    # Track how many queries ran vs. how many failed with exceptions.
    # If ALL fail → server unreachable → fall back to JSONL.
    query_count = 0
    exception_count = 0

    for component, (file_stem, bundle_key, count_key) in query_map.items():
        query_file = queries_dir / f"{file_stem}.cypher"
        if not query_file.exists():
            logger.warning("Query file not found, skipping: %s", query_file)
            continue

        query_count += 1
        try:
            rows = await client.cypher(query_file.read_text(encoding="utf-8"), workspace, params=params)
        except Exception as exc:
            exception_count += 1
            logger.warning("Cypher query failed for %s (component=%s): %s", query_file.name, component, exc)
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

    # If every query threw an exception, the server is unreachable.
    # Fall back to JSONL extraction.
    if query_count > 0 and exception_count == query_count:
        logger.info(
            "CI graph server unreachable (%d/%d queries failed) — "
            "falling back to JSONL signal extraction",
            exception_count,
            query_count,
        )
        return run_signals_from_jsonl(
            workspace=workspace,
            session_id=session_id,
            base_path=base_path,
        )

    return bundles
