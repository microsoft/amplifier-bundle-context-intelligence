"""context_intelligence.bundle_analysis.signals — graph-based usage signals.

Layer 1 detection: executes pre-written Cypher query files (S-1..S-18) via an
injected AsyncCIClient and aggregates rows into per-bundle invocation counts by
component type (agents, skills, modes, recipes, tools).

The library receives a ready-to-use client — it knows nothing about config,
env vars, or coordinator capabilities.
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

    query_map = _SESSION_QUERY_MAP if session_id is not None else _WORKSPACE_QUERY_MAP
    bundles: dict[str, Any] = {}
    queries_dir = _queries_dir()

    try:
        for component, (file_stem, bundle_key, count_key) in query_map.items():
            query_file = queries_dir / f"{file_stem}.cypher"
            if not query_file.exists():
                logger.warning("Query file not found, skipping: %s", query_file)
                continue

            query = query_file.read_text(encoding="utf-8").strip()

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
