"""context_intelligence.bundle_analysis.processor — raw-event attribution.

Transforms a flat list of RawSignalEvent objects into a per-bundle usage dict.

Supported event kinds and their attribution:

  agent_spawned    — agent field carries ``bundle:component``; splits on ':'
                     and increments ``agents`` for the bundle prefix.
  skill_loaded     — skill_source carries the skill file path; bundle name
                     is extracted via _bundle_from_source_path.
  recipe_execute   — recipe_path carries the recipe path argument; bundle
                     name is extracted via _bundle_from_recipe_path.
  mentions_resolved — resolutions list; each entry with is_new=True and a
                      non-empty bundle string increments ``context``.

Output schema per bundle::

    {
        "agents":  int,  # agent_spawned events attributed to this bundle
        "skills":  int,  # skill_loaded events attributed to this bundle
        "recipes": int,  # recipe_execute events attributed to this bundle
        "context": int,  # mentions_resolved new-file events attributed here
        "modes":   int,  # always 0 — not attributable from these events
        "tools":   int,  # always 0 — not attributable from these events
    }
"""

from __future__ import annotations

import re
from typing import Any

from .fetchers import RawSignalEvent

# ---------------------------------------------------------------------------
# Internal regex + constants
# ---------------------------------------------------------------------------

# Matches exactly 16 lowercase hex characters preceded by a hyphen at end-of-string.
# Examples:
#   "superpowers-a6aca0133cf890bf"  → match (strips to "superpowers")
#   "foundation"                    → no match (returned as-is)
#   "mything-abc123"               → no match (only 6 hex chars, not 16)
_HASH_SUFFIX_RE = re.compile(r"-[0-9a-f]{16}$")

# Ordered: /cache/skills/ is checked before /cache/ so the more specific
# marker always wins when both are present in the path.
_CACHE_MARKERS = ("/cache/skills/", "/cache/")


# ---------------------------------------------------------------------------
# Path parsers
# ---------------------------------------------------------------------------


def _bundle_from_source_path(source: str) -> str | None:
    """Extract a bundle name from a skill source file path.

    Searches for the first of ``/cache/skills/`` or ``/cache/`` in *source*,
    takes the path segment immediately after the marker as the slug, then
    strips any trailing 16-hex-char hash suffix (e.g. ``-a6aca0133cf890bf``).

    Returns ``None`` when no cache marker is found or when *source* is empty.
    """
    if not source:
        return None

    for marker in _CACHE_MARKERS:
        idx = source.find(marker)
        if idx != -1:
            after = source[idx + len(marker) :]
            slug = after.split("/")[0]
            if not slug:
                return None
            return _HASH_SUFFIX_RE.sub("", slug)

    return None


def _bundle_from_recipe_path(path: str) -> str | None:
    """Extract a bundle name from a recipe path string.

    Requires the ``@`` prefix and a ``:`` separator.  Returns the bundle
    portion before the colon, or ``None`` when either requirement is missing.

    Examples::

        "@recipes:examples/code-review.yaml" → "recipes"
        "@foundation:"                        → "foundation"
        "recipes:path"                        → None  (no @ prefix)
        "@no-colon"                           → None  (no : separator)
    """
    if not path or not path.startswith("@"):
        return None
    colon_idx = path.find(":")
    if colon_idx == -1:
        return None
    bundle = path[1:colon_idx]  # strip leading '@', take up to ':'
    return bundle if bundle else None


# ---------------------------------------------------------------------------
# Bundle-dict helpers
# ---------------------------------------------------------------------------


def _ensure(bundles: dict[str, Any], name: str) -> None:
    """Ensure *bundles* has a zero-initialised entry for *name*."""
    if name not in bundles:
        bundles[name] = {
            "agents": 0,
            "skills": 0,
            "recipes": 0,
            "context": 0,
            "modes": 0,
            "tools": 0,
        }


# ---------------------------------------------------------------------------
# Main aggregation function
# ---------------------------------------------------------------------------


def process_events(events: list[RawSignalEvent]) -> dict[str, Any]:
    """Aggregate attribution signals from *events* into per-bundle counts.

    Parameters
    ----------
    events:
        A list of :class:`~context_intelligence.bundle_analysis.fetchers.RawSignalEvent`
        objects, typically produced by
        :class:`~context_intelligence.bundle_analysis.fetchers.GraphFetcher` or
        :class:`~context_intelligence.bundle_analysis.fetchers.JSONLFetcher`.

    Returns
    -------
    dict
        Mapping from bundle name to a usage-count dict with six keys:
        ``agents``, ``skills``, ``recipes``, ``context``, ``modes``, ``tools``.
        ``modes`` and ``tools`` are always 0 — these event kinds carry no
        reliable bundle attribution.
        Returns ``{}`` when *events* is empty or no event yields attribution.
    """
    bundles: dict[str, Any] = {}

    for event in events:
        if event.kind == "agent_spawned":
            agent = event.agent
            if not isinstance(agent, str) or ":" not in agent:
                continue
            bundle_name = agent.split(":", 1)[0]
            if not bundle_name:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["agents"] += 1

        elif event.kind == "skill_loaded":
            source = event.skill_source
            if not isinstance(source, str):
                continue
            bundle_name = _bundle_from_source_path(source)
            if not bundle_name:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["skills"] += 1

        elif event.kind == "recipe_execute":
            recipe_path = event.recipe_path
            if not isinstance(recipe_path, str):
                continue
            bundle_name = _bundle_from_recipe_path(recipe_path)
            if not bundle_name:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["recipes"] += 1

        elif event.kind == "mentions_resolved":
            resolutions = event.resolutions
            if not isinstance(resolutions, list):
                continue
            for resolution in resolutions:
                if not isinstance(resolution, dict):
                    continue
                if not resolution.get("is_new"):
                    continue
                bundle_name = resolution.get("bundle")
                if not isinstance(bundle_name, str) or not bundle_name:
                    continue
                _ensure(bundles, bundle_name)
                bundles[bundle_name]["context"] += 1

    return bundles


__all__ = ["process_events"]
