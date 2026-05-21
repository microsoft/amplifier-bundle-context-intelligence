"""context_intelligence.bundle_analysis.inventory — local cache inventory.

Three-tier declaration scan.  Reads ``~/.amplifier/cache/{bundle-slug-hash}/``
directories and enumerates what each bundle DECLARES across three tiers:

always_active
    Agents, context, skills, and recipes that are loaded unconditionally.
    Sources: ``behaviors/*.yaml`` (agents.include, context.include, recipes
    block keys) and filesystem layout (skills/ subdirs, recipes/*.yaml).

agent_level
    Per-agent declared tools, context, and skills.
    Source: ``agents/*.md`` frontmatter.

mode_gated
    Per-mode contributed agents, context, and skills.
    Source: ``modes/*.md`` frontmatter (mode.contributes).

Disk cache
-----------
After a fresh scan each bundle directory receives a ``.bundle-scan.json``
file.  Subsequent scans load this file and skip re-parsing when the
``cache_key`` (bundle directory name) still matches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_FILE = ".bundle-scan.json"
_ALWAYS_ACTIVE_SET_KEYS = ("agents", "context", "skills", "recipes")
_MODE_GATED_SET_KEYS = ("agents", "context", "skills")
_AGENT_LEVEL_SET_KEYS = ("tools", "context", "skills")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Return the YAML frontmatter dict from *path*, or ``{}`` on any error.

    Only files whose text starts with ``"---"`` are considered to have
    frontmatter.  The section between the first and second ``"---"``
    delimiters is parsed with :func:`yaml.safe_load`.
    """
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}
        parts = text.split("---")
        if len(parts) < 3:  # noqa: PLR2004
            return {}
        fm = yaml.safe_load(parts[1])
        return fm if isinstance(fm, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _read_bundle_manifest(bundle_dir: Path) -> dict[str, Any]:
    """Return the bundle manifest dict from bundle.md (first) or bundle.yaml."""
    bundle_md = bundle_dir / "bundle.md"
    if bundle_md.exists():
        return _read_frontmatter(bundle_md)
    bundle_yaml = bundle_dir / "bundle.yaml"
    if bundle_yaml.exists():
        try:
            data = yaml.safe_load(bundle_yaml.read_text(encoding="utf-8")) or {}
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _strip_bundle_prefix(name: str) -> str:
    """Strip a leading ``bundle:`` prefix from an agent name."""
    prefix = "bundle:"
    if name.startswith(prefix):
        return name[len(prefix) :]
    return name


def _scan_behaviors(
    bundle_dir: Path,
) -> tuple[set[str], set[str], set[str]]:
    """Iterate ``behaviors/*.yaml`` and return ``(agents, context, recipes)`` sets.

    * **agents** — from ``agents.include`` list (prefixes stripped).
    * **context** — from ``context.include`` list.
    * **recipes** — from top-level ``recipes`` block keys.
    """
    agents: set[str] = set()
    context: set[str] = set()
    recipes: set[str] = set()

    behaviors_dir = bundle_dir / "behaviors"
    if not behaviors_dir.is_dir():
        return agents, context, recipes

    for yaml_file in sorted(behaviors_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            data = {}

        if not isinstance(data, dict):
            continue

        # agents.include
        try:
            includes = data["agents"]["include"]
            if isinstance(includes, list):
                for item in includes:
                    if isinstance(item, str):
                        agents.add(_strip_bundle_prefix(item))
        except (KeyError, TypeError):
            pass

        # context.include
        try:
            ctx_includes = data["context"]["include"]
            if isinstance(ctx_includes, list):
                for item in ctx_includes:
                    if isinstance(item, str):
                        context.add(item)
        except (KeyError, TypeError):
            pass

        # recipes block keys
        try:
            recipes_block = data["recipes"]
            if isinstance(recipes_block, dict):
                for key in recipes_block:
                    if isinstance(key, str):
                        recipes.add(key)
        except (KeyError, TypeError):
            pass

    return agents, context, recipes


def _enumerate_skill_names(skills_root: Path) -> set[str]:
    """Return subdir names + flat ``*.md`` stems under *skills_root*.

    Returns an empty set if *skills_root* does not exist.
    """
    if not skills_root.is_dir():
        return set()
    names: set[str] = set()
    for child in sorted(skills_root.iterdir()):
        if child.is_dir():
            names.add(child.name)
    for md_file in sorted(skills_root.glob("*.md")):
        names.add(md_file.stem)
    return names


def _enumerate_yaml_recipe_names(recipes_root: Path) -> set[str]:
    """Return recipe names from ``recipes/*.yaml`` and ``*.yml``.

    Tries ``data["recipe"]["name"]`` then ``data["name"]``, falls back to
    file stem.  Returns an empty set if *recipes_root* does not exist.
    """
    if not recipes_root.is_dir():
        return set()
    names: set[str] = set()
    all_yaml = sorted(recipes_root.glob("*.yaml")) + sorted(recipes_root.glob("*.yml"))
    for yaml_file in all_yaml:
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            data = {}
        name: str | None = None
        try:
            candidate = data["recipe"]["name"]
            if isinstance(candidate, str):
                name = candidate
        except (KeyError, TypeError):
            pass
        if name is None:
            try:
                candidate = data["name"]
                if isinstance(candidate, str):
                    name = candidate
            except (KeyError, TypeError):
                pass
        if name is None:
            name = yaml_file.stem
        names.add(name)
    return names


def _scan_mode_contributes(
    bundle_dir: Path,
) -> tuple[set[str], dict[str, dict[str, set[str]]]]:
    """Iterate ``modes/*.md`` and return ``(mode_names, mode_gated_dict)``.

    *mode_names* is the set of all declared mode names.
    *mode_gated_dict* maps mode name → ``{agents, context, skills}`` sets
    for modes that have a ``mode.contributes`` block.  Agents may be
    declared as a dict (dict-keys are agent names) or a list.
    """
    mode_names: set[str] = set()
    mode_gated: dict[str, dict[str, set[str]]] = {}

    modes_dir = bundle_dir / "modes"
    if not modes_dir.is_dir():
        return mode_names, mode_gated

    for md_file in sorted(modes_dir.glob("*.md")):
        fm = _read_frontmatter(md_file)
        try:
            mode_name = fm["mode"]["name"]
        except (KeyError, TypeError):
            mode_name = md_file.stem

        if not isinstance(mode_name, str):
            continue

        mode_names.add(mode_name)

        try:
            contributes = fm["mode"]["contributes"]
        except (KeyError, TypeError):
            continue

        if not isinstance(contributes, dict):
            continue

        mode_entry: dict[str, set[str]] = {
            "agents": set(),
            "context": set(),
            "skills": set(),
        }

        # agents: dict-keys or list
        try:
            ag = contributes["agents"]
            if isinstance(ag, dict):
                for key in ag:
                    if isinstance(key, str):
                        mode_entry["agents"].add(key)
            elif isinstance(ag, list):
                for item in ag:
                    if isinstance(item, str):
                        mode_entry["agents"].add(item)
        except (KeyError, TypeError):
            pass

        # context: list
        try:
            ctx = contributes["context"]
            if isinstance(ctx, list):
                for item in ctx:
                    if isinstance(item, str):
                        mode_entry["context"].add(item)
        except (KeyError, TypeError):
            pass

        # skills: list
        try:
            sk = contributes["skills"]
            if isinstance(sk, list):
                for item in sk:
                    if isinstance(item, str):
                        mode_entry["skills"].add(item)
        except (KeyError, TypeError):
            pass

        mode_gated[mode_name] = mode_entry

    return mode_names, mode_gated


def _scan_agent_level(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    """Iterate ``agents/*.md`` and return per-agent tool/context/skill declarations.

    Agent name is taken from ``meta.name``, falling back to ``agent.name``,
    then the file stem.  Only agents with at least one non-empty declaration
    are included in the result.
    """
    agent_level: dict[str, dict[str, Any]] = {}

    agents_dir = bundle_dir / "agents"
    if not agents_dir.is_dir():
        return agent_level

    for md_file in sorted(agents_dir.glob("*.md")):
        fm = _read_frontmatter(md_file)

        # Determine agent name
        name: str | None = None
        try:
            candidate = fm["meta"]["name"]
            if isinstance(candidate, str):
                name = candidate
        except (KeyError, TypeError):
            pass
        if name is None:
            try:
                candidate = fm["agent"]["name"]
                if isinstance(candidate, str):
                    name = candidate
            except (KeyError, TypeError):
                pass
        if name is None:
            name = md_file.stem

        # tools: list of {module: name} dicts or raw strings
        tools: list[Any] = []
        try:
            t = fm["tools"]
            if isinstance(t, list):
                tools = t
        except (KeyError, TypeError):
            pass

        # context: list of strings
        context: set[str] = set()
        try:
            ctx = fm["context"]
            if isinstance(ctx, list):
                for item in ctx:
                    if isinstance(item, str):
                        context.add(item)
        except (KeyError, TypeError):
            pass

        # skills: list of strings
        skills: set[str] = set()
        try:
            sk = fm["skills"]
            if isinstance(sk, list):
                for item in sk:
                    if isinstance(item, str):
                        skills.add(item)
        except (KeyError, TypeError):
            pass

        # Only include agents with non-empty declarations
        if not tools and not context and not skills:
            continue

        agent_level[name] = {
            "tools": tools,
            "context": context,
            "skills": skills,
        }

    return agent_level


# ---------------------------------------------------------------------------
# Disk cache helpers
# ---------------------------------------------------------------------------


def _sets_to_lists(data: Any) -> Any:
    """Recursively convert sets to sorted lists for JSON serialisation."""
    if isinstance(data, set):
        return sorted(str(item) for item in data)
    if isinstance(data, dict):
        return {k: _sets_to_lists(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_sets_to_lists(item) for item in data]
    return data


def _save_cache(bundle_dir: Path, data: dict[str, Any]) -> None:
    """Write ``.bundle-scan.json`` inside *bundle_dir*.  Silently ignores OSError."""
    try:
        cache_file = bundle_dir / _CACHE_FILE
        serialisable = _sets_to_lists(data)
        cache_file.write_text(json.dumps(serialisable), encoding="utf-8")
    except OSError:
        pass


def _hydrate_sets(data: dict[str, Any]) -> dict[str, Any]:
    """Re-inflate string lists into sets after JSON deserialisation.

    Applies the ``_*_SET_KEYS`` constants to determine which fields should
    be sets.  Tools within agent_level are left as lists because they may
    contain unhashable dict entries.
    """
    result = dict(data)

    # always_active
    if isinstance(result.get("always_active"), dict):
        aa = dict(result["always_active"])
        for key in _ALWAYS_ACTIVE_SET_KEYS:
            v = aa.get(key)
            if isinstance(v, list) and all(isinstance(i, str) for i in v):
                aa[key] = set(v)
        result["always_active"] = aa

    # mode_gated
    if isinstance(result.get("mode_gated"), dict):
        mg: dict[str, Any] = {}
        for mode_name, mode_data in result["mode_gated"].items():
            if isinstance(mode_data, dict):
                md = dict(mode_data)
                for key in _MODE_GATED_SET_KEYS:
                    v = md.get(key)
                    if isinstance(v, list) and all(isinstance(i, str) for i in v):
                        md[key] = set(v)
                mg[mode_name] = md
            else:
                mg[mode_name] = mode_data
        result["mode_gated"] = mg

    # agent_level — tools stays as list; context and skills become sets
    if isinstance(result.get("agent_level"), dict):
        al: dict[str, Any] = {}
        for agent_name, agent_data in result["agent_level"].items():
            if isinstance(agent_data, dict):
                ad = dict(agent_data)
                for key in ("context", "skills"):
                    v = ad.get(key)
                    if isinstance(v, list) and all(isinstance(i, str) for i in v):
                        ad[key] = set(v)
                al[agent_name] = ad
            else:
                al[agent_name] = agent_data
        result["agent_level"] = al

    # modes
    v = result.get("modes")
    if isinstance(v, list) and all(isinstance(i, str) for i in v):
        result["modes"] = set(v)

    return result


def _load_cache(bundle_dir: Path, cache_key: str) -> dict[str, Any] | None:
    """Load ``.bundle-scan.json`` from *bundle_dir*.

    Returns ``None`` when:
    * the file is missing,
    * the file cannot be decoded as JSON,
    * the parsed value is not a dict, or
    * the stored ``cache_key`` does not match *cache_key*.
    """
    cache_file = bundle_dir / _CACHE_FILE
    if not cache_file.exists():
        return None
    try:
        raw = cache_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    if data.get("cache_key") != cache_key:
        return None

    return _hydrate_sets(data)


# ---------------------------------------------------------------------------
# Per-bundle scan
# ---------------------------------------------------------------------------


def _scan_one_bundle(bundle_dir: Path) -> tuple[str, dict[str, Any]] | None:
    """Scan a single bundle directory and return ``(name, info)`` or ``None``.

    Tries the on-disk cache first.  On a cache hit the ``_bundle_name``
    field is popped from the cached data and returned as the first element.
    On a cache miss a full scan is performed and the result is persisted.

    Returns ``None`` if the bundle manifest is absent or the bundle name
    cannot be read as a string.
    """
    cache_key = bundle_dir.name

    # --- cache hit path ---
    cached = _load_cache(bundle_dir, cache_key)
    if cached is not None:
        bundle_name = cached.pop("_bundle_name", None)
        if isinstance(bundle_name, str):
            return bundle_name, cached

    # --- fresh scan path ---
    manifest = _read_bundle_manifest(bundle_dir)
    try:
        bundle_name = manifest["bundle"]["name"]
    except (KeyError, TypeError):
        return None

    if not isinstance(bundle_name, str):
        return None

    # Run all four scanners
    b_agents, b_context, b_recipes = _scan_behaviors(bundle_dir)
    b_skills = _enumerate_skill_names(bundle_dir / "skills")
    b_recipes |= _enumerate_yaml_recipe_names(bundle_dir / "recipes")
    mode_names, mode_gated = _scan_mode_contributes(bundle_dir)
    agent_level = _scan_agent_level(bundle_dir)

    info: dict[str, Any] = {
        "always_active": {
            "agents": b_agents,
            "context": b_context,
            "skills": b_skills,
            "recipes": b_recipes,
        },
        "agent_level": agent_level,
        "mode_gated": mode_gated,
        "modes": mode_names,
        "scan_source": "present",
        "cache_key": cache_key,
    }

    # Persist with _bundle_name folded in so cache hit can recover it
    to_save = dict(info)
    to_save["_bundle_name"] = bundle_name
    _save_cache(bundle_dir, to_save)

    return bundle_name, info


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_cache(*, cache_root: Path) -> dict[str, Any]:
    """Scan the local Amplifier bundle cache and return a structured inventory.

    Parameters
    ----------
    cache_root:
        Root directory of the local Amplifier bundle cache
        (e.g. ``~/.amplifier/cache``).

    Returns
    -------
    dict
        Structured inventory of installed bundles.  Keys are bundle names
        (from ``bundle.md`` or ``bundle.yaml``).  The special ``\"_meta\"``
        key holds metadata about the scan itself.

        If *cache_root* does not exist or is not a directory, returns::

            {"_meta": {"scan_source": "absent", "cache_root": str(cache_root)}}
    """
    if not cache_root.exists() or not cache_root.is_dir():
        return {"_meta": {"scan_source": "absent", "cache_root": str(cache_root)}}

    result: dict[str, Any] = {
        "_meta": {"scan_source": "cache", "cache_root": str(cache_root)},
    }
    for child in sorted(cache_root.iterdir()):
        if not child.is_dir():
            continue
        scanned = _scan_one_bundle(child)
        if scanned is None:
            continue
        bundle_name, bundle_info = scanned
        result[bundle_name] = bundle_info

    return result
