"""context_intelligence.bundle_analysis.inventory — local cache inventory.

Layer 2 declaration scan.  Reads ``~/.amplifier/cache/{bundle-slug-hash}/``
directories and enumerates what each bundle DECLARES:
  agents, modes, skills, recipes, context files.

Signals implemented
-------------------
LS-1  bundle.md present
LS-2  behaviors/ directory
LS-3  modes/ directory
LS-4  agents/ directory
LS-5  skills/ directory
LS-6  recipes/ directory
LS-7  freshness (mtime-based)
LS-8  registry/bundle enumeration
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Return the YAML frontmatter dict from *path*, or ``{}`` on any error.

    Only files whose text starts with ``"---"`` are considered to have
    frontmatter.  The section between the first and second ``"---"`` delimiters
    is parsed with :func:`yaml.safe_load`.
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


def _bundle_name_from_md(bundle_md: Path) -> str | None:
    """Return the bundle name declared in *bundle_md*, or ``None``."""
    fm = _read_frontmatter(bundle_md)
    try:
        return fm["bundle"]["name"]
    except (KeyError, TypeError):
        return None


def _enumerate_md_names(folder: Path) -> list[str]:
    """Return declared names for every ``.md`` file in *folder*.

    For each file the function tries, in order:
    * ``fm["bundle"]["name"]``
    * ``fm["meta"]["name"]``
    * ``fm["mode"]["name"]``
    * ``fm["agent"]["name"]``
    * file stem (fallback)

    Returns an empty list if *folder* does not exist.
    """
    if not folder.is_dir():
        return []
    names: list[str] = []
    for md_file in sorted(folder.glob("*.md")):
        fm = _read_frontmatter(md_file)
        name: str | None = None
        for section in ("bundle", "meta", "mode", "agent"):
            try:
                candidate = fm[section]["name"]
                if isinstance(candidate, str):
                    name = candidate
                    break
            except (KeyError, TypeError):
                continue
        if name is None:
            name = md_file.stem
        names.append(name)
    return names


def _enumerate_yaml_recipe_names(folder: Path) -> list[str]:
    """Return declared names for every ``.yaml``/``.yml`` file in *folder*.

    Tries ``data["recipe"]["name"]`` then ``data["name"]``, falls back to
    file stem.  Returns an empty list if *folder* does not exist.
    """
    if not folder.is_dir():
        return []
    names: list[str] = []
    all_yaml = sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml"))
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
        names.append(name)
    return names


def _enumerate_skill_names(skills_root: Path) -> list[str]:
    """Return skill names from *skills_root* (LS-5).

    Supports two conventions:

    * **Subdir layout** — ``skills/<skill>/`` (any directory under the root)
    * **Flat layout** — ``skills/<skill>.md`` (top-level ``.md`` files)

    Subdir names come first, then ``*.md`` stems.  Returns ``[]`` if
    *skills_root* does not exist.
    """
    if not skills_root.is_dir():
        return []
    names: list[str] = []
    # Subdir layout
    for child in sorted(skills_root.iterdir()):
        if child.is_dir():
            names.append(child.name)
    # Flat layout
    for md_file in sorted(skills_root.glob("*.md")):
        names.append(md_file.stem)
    return names


# ---------------------------------------------------------------------------
# Per-bundle scan
# ---------------------------------------------------------------------------


def _scan_one_bundle(bundle_dir: Path) -> tuple[str, dict[str, Any]] | None:
    """Scan a single bundle directory and return ``(name, info)`` or ``None``.

    Returns ``None`` if:
    * ``bundle.md`` is absent, or
    * the bundle name cannot be parsed from ``bundle.md``.
    """
    bundle_md = bundle_dir / "bundle.md"
    if not bundle_md.exists():
        return None
    name = _bundle_name_from_md(bundle_md)
    if name is None:
        return None

    declared: dict[str, list[str]] = {
        "agents": _enumerate_md_names(bundle_dir / "agents"),
        "modes": _enumerate_md_names(bundle_dir / "modes"),
        "skills": _enumerate_skill_names(bundle_dir / "skills"),
        "recipes": _enumerate_yaml_recipe_names(bundle_dir / "recipes"),
    }
    info: dict[str, Any] = {
        "scan_source": "cache",
        "declared": declared,
    }
    return name, info


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
        (from ``bundle.md``).  The special ``"_meta"`` key holds metadata
        about the scan itself.

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
