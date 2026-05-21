"""context_intelligence.bundle_analysis.processor — raw-event attribution.

Transforms a flat list of RawSignalEvent objects into a per-bundle usage dict
where each bundle entry contains six named *sets* of strings.

Supported event kinds and their attribution:

  agent_spawned    — agent field carries ``bundle:component``; splits on ':'
                     and adds ``component`` to ``agents`` set for the bundle prefix.
                     Falls back to inventory agent_to_bundle lookup when no ':'.
  skill_loaded     — skill_source carries the skill file path; bundle name
                     is extracted via _bundle_from_source_path (fallback:
                     skill_to_bundle inventory lookup).  Skill name is
                     Path(source).parent.name and is added to ``skills`` set.
  recipe_execute   — recipe_path carries the recipe path argument; bundle
                     name is extracted via _bundle_from_recipe_path.
                     The path segment after ':' (leading '/' stripped) is
                     added to the ``recipes`` set.
  mentions_resolved — resolutions list; each entry is one of:
                      * new format: dict with 'bundle' and 'resolved_path' keys
                        → resolved_path added to ``context`` set for bundle.
                      * legacy format: dict with 'is_new', 'source_type',
                        'mention'/'resolved_path' keys (backward compat).

Output schema per bundle::

    {
        "agents":  set[str],  # component names of spawned agents
        "skills":  set[str],  # skill folder names loaded from this bundle
        "recipes": set[str],  # recipe paths executed from this bundle
        "context": set[str],  # resolved_paths of context files from this bundle
        "modes":   set[str],  # mode_activated events attributed via inventory mode_to_bundle lookup
        "tools":   set[str],  # tool_call events attributed via inventory tool_to_bundle lookup
    }
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .fetchers import RawSignalEvent

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Ordered tuple of the six named-set keys every bundle entry contains.
_SIGNAL_KEYS: tuple[str, ...] = (
    "agents",
    "skills",
    "recipes",
    "context",
    "tools",
    "modes",
)

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
    """Ensure *bundles* has a set-initialised entry for *name*."""
    if name not in bundles:
        bundles[name] = {key: set() for key in _SIGNAL_KEYS}


def _build_reverse_lookups(
    inventory: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Build reverse lookup maps from the three-tier inventory schema.

    Returns ``(agent_to_bundle, skill_to_bundle, tool_to_bundle, mode_to_bundle)``.
    Uses ``setdefault`` for first-writer-wins on duplicate keys.
    """
    agent_to_bundle: dict[str, str] = {}
    skill_to_bundle: dict[str, str] = {}
    tool_to_bundle: dict[str, str] = {}
    mode_to_bundle: dict[str, str] = {}

    for bundle_name, bundle_info in inventory.items():
        if bundle_name.startswith("_"):
            continue
        if not isinstance(bundle_info, dict):
            continue
        aa = bundle_info.get("always_active", {})
        if not isinstance(aa, dict):
            continue

        for agent in aa.get("agents", set()) or set():
            if isinstance(agent, str):
                agent_to_bundle.setdefault(agent, bundle_name)
        for skill in aa.get("skills", set()) or set():
            if isinstance(skill, str):
                skill_to_bundle.setdefault(skill, bundle_name)
        for mode in bundle_info.get("modes", set()) or set():
            if isinstance(mode, str):
                mode_to_bundle.setdefault(mode, bundle_name)
        # tools: collected from agent_level agent entries
        agent_level = bundle_info.get("agent_level", {})
        if isinstance(agent_level, dict):
            for _agent_name, agent_info in agent_level.items():
                if not isinstance(agent_info, dict):
                    continue
                for tool in agent_info.get("tools", set()) or set():
                    if isinstance(tool, str):
                        # Plain string (synthetic/test inventory) — used as-is.
                        tool_to_bundle.setdefault(tool, bundle_name)
                    elif isinstance(tool, dict):
                        # Real inventory format: {module: "tool-<name>", source: "..."}
                        # Normalize to event tool name: strip "tool-" prefix, replace "-" with "_".
                        # e.g. "tool-graph-query" -> "graph_query"
                        module = tool.get("module")
                        if isinstance(module, str) and module.startswith("tool-"):
                            normalized = module[5:].replace("-", "_")
                            if normalized:
                                tool_to_bundle.setdefault(normalized, bundle_name)

    return agent_to_bundle, skill_to_bundle, tool_to_bundle, mode_to_bundle


# ---------------------------------------------------------------------------
# Main aggregation function
# ---------------------------------------------------------------------------


def process_events(events: list[RawSignalEvent], inventory: dict[str, Any]) -> dict[str, Any]:
    """Aggregate attribution signals from *events* into per-bundle named sets.

    Parameters
    ----------
    events:
        A list of :class:`~context_intelligence.bundle_analysis.fetchers.RawSignalEvent`
        objects, typically produced by
        :class:`~context_intelligence.bundle_analysis.fetchers.JSONLFetcher`.
    inventory:
        Three-tier bundle inventory produced by
        :func:`~context_intelligence.bundle_analysis.inventory.scan_cache`.
        Used to build reverse lookups (agent→bundle, skill→bundle, etc.)
        for events that don't carry an explicit bundle prefix.

    Returns
    -------
    dict
        Mapping from bundle name to a usage dict with six named-set keys:
        ``agents``, ``skills``, ``recipes``, ``context``, ``modes``, ``tools``
        (all ``set[str]``).
        ``modes`` is populated via ``mode:activated`` / ``mode:changed`` events
        using an inventory reverse-lookup; ``tools`` is populated via
        ``tool:pre`` events using an inventory reverse-lookup.
        Returns ``{}`` when *events* is empty or no event yields attribution.
    """
    bundles: dict[str, Any] = {}

    agent_to_bundle, skill_to_bundle, tool_to_bundle, mode_to_bundle = _build_reverse_lookups(
        inventory
    )

    for event in events:
        if event.kind == "agent_spawned":
            agent = event.agent
            if not isinstance(agent, str):
                continue
            if ":" in agent:
                bundle_name, _, component = agent.partition(":")
            else:
                component = agent
                bundle_name = agent_to_bundle.get(agent, "")
            if not bundle_name:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["agents"].add(component)

        elif event.kind == "skill_loaded":
            source = event.skill_source
            if not isinstance(source, str):
                continue
            skill_name = Path(source).parent.name
            bundle_name = _bundle_from_source_path(source)
            if not bundle_name:
                # Fallback: look up skill name in inventory
                bundle_name = skill_to_bundle.get(skill_name, "")
            if not bundle_name or not skill_name:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["skills"].add(skill_name)

        elif event.kind == "recipe_execute":
            recipe_path = event.recipe_path
            if not isinstance(recipe_path, str):
                continue
            bundle_name = _bundle_from_recipe_path(recipe_path)
            if not bundle_name:
                continue
            colon_idx = recipe_path.find(":")
            recipe_item = recipe_path[colon_idx + 1 :].lstrip("/") if colon_idx != -1 else ""
            if not recipe_item:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["recipes"].add(recipe_item)

        elif event.kind == "tool_call":
            tool_name = event.tool_name
            if not isinstance(tool_name, str) or not tool_name:
                continue
            bundle_name = tool_to_bundle.get(tool_name)
            if not bundle_name:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["tools"].add(tool_name)

        elif event.kind == "mode_activated":
            mode_name = event.mode_name
            if not isinstance(mode_name, str) or not mode_name:
                continue
            bundle_name = mode_to_bundle.get(mode_name)
            if not bundle_name:
                continue
            _ensure(bundles, bundle_name)
            bundles[bundle_name]["modes"].add(mode_name)

        elif event.kind == "mentions_resolved":
            resolutions = event.resolutions
            if not isinstance(resolutions, list):
                continue
            for resolution in resolutions:
                if not isinstance(resolution, dict):
                    continue

                resolved_path = resolution.get("resolved_path") or ""

                # ---- New simplified format: 'bundle' key provided directly ----
                if "bundle" in resolution:
                    bundle_name = resolution.get("bundle") or None
                    if not bundle_name or not resolved_path:
                        continue
                    _ensure(bundles, bundle_name)
                    bundles[bundle_name]["context"].add(resolved_path)
                    continue

                # ---- Legacy format: derive bundle from source_type (is_new gate) ----
                if not resolution.get("is_new"):
                    continue

                source_type = resolution.get("source_type", "")
                bundle_name = None

                if source_type == "bundle_namespace":
                    # mention = "foundation:context/bundle-awareness.md" — bundle is left of ":"
                    mention = resolution.get("mention") or ""
                    bundle_name = mention.split(":", 1)[0] if ":" in mention else None

                elif source_type == "bundle_context_decl":
                    # no @mention string — attribute via cache path slug
                    bundle_name = _bundle_from_source_path(resolved_path)

                else:
                    # user_shortcut, home_shortcut, relative_path, project_shortcut — no bundle
                    bundle_name = None

                if not bundle_name or not resolved_path:
                    continue
                _ensure(bundles, bundle_name)
                bundles[bundle_name]["context"].add(resolved_path)

    return bundles


__all__ = ["process_events"]
