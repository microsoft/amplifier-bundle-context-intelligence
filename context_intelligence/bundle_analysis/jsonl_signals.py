"""context_intelligence.bundle_analysis.jsonl_signals — JSONL-based signal extraction.

Fallback for run_signals when the CI graph server is unavailable.
Extracts bundle usage signals from local events.jsonl files by parsing
raw session events. Coverage compared to graph signals:

  agents   FULL   — delegate:agent_spawned always carries bundle:component
  skills   BEST-EFFORT — source path carries bundle slug; name stripped of hash
  modes    NONE   — mode names lack bundle namespace in event data
  recipes  NONE   — recipe paths not reliably bundled in event data
  tools    NONE   — tool names are bare (todo, delegate) without bundle attribution

The result dict has the same shape as run_signals so it is a drop-in fallback.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("context_intelligence.bundle_analysis.jsonl_signals")

# Matches the hash suffix appended to Amplifier bundle cache directory slugs.
# Examples: superpowers-a6aca0133cf890bf  → superpowers
#           amplifier-bundle-context-intelligence-ecd41f3e6fa67bd2 → amplifier-bundle-context-intelligence
_SLUG_HASH_RE = re.compile(r"-[0-9a-f]{16}$")


def _bundle_name_from_slug(slug: str) -> str:
    """Strip the 16-hex-char hash suffix from a bundle cache directory slug."""
    return _SLUG_HASH_RE.sub("", slug)


def _bundle_name_from_source_path(source: str) -> str | None:
    """Extract bundle name from a skill source path.

    Example source: /home/user/.amplifier/cache/skills/superpowers-a6aca0133cf890bf/skills
    Returns: 'superpowers'
    """
    # Look for the cache/skills/<slug>/ pattern
    for marker in ("/cache/skills/", "/cache/"):
        idx = source.find(marker)
        if idx != -1:
            after = source[idx + len(marker):]
            slug = after.split("/")[0]
            if slug:
                return _bundle_name_from_slug(slug)
    return None


def _ensure_bundle_entry(bundles: dict[str, Any], name: str) -> None:
    if name not in bundles:
        bundles[name] = {"agents": 0, "skills": 0, "modes": 0, "recipes": 0, "tools": 0}


def _process_event(record: dict[str, Any], bundles: dict[str, Any]) -> None:
    event_name = record.get("event", "")
    data = record.get("data") or {}

    if event_name in ("delegate:agent_spawned", "delegate:agent_resumed"):
        agent = data.get("agent", "")
        if isinstance(agent, str) and ":" in agent:
            bundle_name = agent.split(":", 1)[0]
            if bundle_name:
                _ensure_bundle_entry(bundles, bundle_name)
                bundles[bundle_name]["agents"] += 1

    elif event_name == "skill:loaded":
        source = data.get("source", "") or data.get("skill_directory", "")
        if isinstance(source, str):
            bundle_name = _bundle_name_from_source_path(source)
            if bundle_name:
                _ensure_bundle_entry(bundles, bundle_name)
                bundles[bundle_name]["skills"] += 1


def _parse_jsonl_file(path: Path, bundles: dict[str, Any]) -> None:
    """Parse a single events.jsonl file, accumulating bundle usage counts."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _process_event(record, bundles)
    except OSError as exc:
        logger.debug("Could not read %s: %s", path, exc)


def run_signals_from_jsonl(
    *,
    workspace: str,
    session_id: str | None = None,
    base_path: Path | None = None,
) -> dict[str, Any]:
    """Extract bundle usage signals from local events.jsonl files.

    Parameters
    ----------
    workspace:
        Workspace slug (e.g. '-home-user-my-project').
    session_id:
        If provided, scope to a single session. Otherwise scan all sessions
        in the workspace.
    base_path:
        Root of the Amplifier projects directory. Defaults to
        ``~/.amplifier/projects``.

    Returns
    -------
    dict
        Same shape as :func:`run_signals`:
        ``{bundle: {agents, skills, modes, recipes, tools}}``.
        Modes, recipes, and tools are always 0 (not extractable from JSONL).
    """
    base_path = base_path or Path.home() / ".amplifier" / "projects"
    sessions_dir = base_path / workspace / "sessions"

    if not sessions_dir.exists():
        logger.debug("Workspace sessions dir not found: %s", sessions_dir)
        return {}

    if session_id:
        jsonl_paths = [sessions_dir / session_id / "context-intelligence" / "events.jsonl"]
    else:
        jsonl_paths = list(sessions_dir.glob("*/context-intelligence/events.jsonl"))

    if not jsonl_paths:
        logger.debug("No events.jsonl files found in workspace %s", workspace)
        return {}

    bundles: dict[str, Any] = {}
    for path in jsonl_paths:
        if path.exists():
            _parse_jsonl_file(path, bundles)

    if bundles:
        logger.info(
            "JSONL signals extracted %d bundles from %d file(s) in workspace %s",
            len(bundles),
            sum(1 for p in jsonl_paths if p.exists()),
            workspace,
        )
    return bundles
