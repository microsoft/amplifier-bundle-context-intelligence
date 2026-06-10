"""Vendored symbols from ``context_intelligence`` for standalone installability.

The hook module's runtime needs four symbols from the parent bundle's
``context_intelligence`` package. Copying them here lets the hook install
and mount standalone — without requiring the parent bundle on PYTHONPATH
or in the install tree.

Provenance (last synced from upstream):
  - AMPLIFIER_DIR, SETTINGS_PATH, _parse_settings_yaml
      → context_intelligence/config.py
  - workspace_slug
      → context_intelligence/reconstruct/discover.py

If you change behaviour in either of those upstream files, mirror the change
here. The two copies are aligned by convention, not by shared import — the
hook MUST NOT import from ``context_intelligence`` at runtime, or it stops
being standalone-installable in environments that strip ``[tool.uv.sources]``
(notably amplifier-agent's ``--no-sources`` activator policy).

This module is internal to the hook package. External callers should not
import from here; if you need the same logic in your own code, copy it.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("amplifier_module_hook_context_intelligence._vendored")

# ---------------------------------------------------------------------------
# From context_intelligence/config.py
# ---------------------------------------------------------------------------

AMPLIFIER_DIR = Path.home() / ".amplifier"
SETTINGS_PATH = AMPLIFIER_DIR / "settings.yaml"


def _parse_settings_yaml(path: Path) -> dict:
    """Minimal YAML parser for settings.yaml -- good enough for the flat keys
    we need without requiring PyYAML.

    Returns a dict with CI server config keys (``server_url``, ``api_key``) if found.
    """
    result: dict[str, str] = {}
    if not path.is_file():
        return result

    try:
        # Try PyYAML first
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            ci_cfg = (
                data.get("overrides", {}).get("hook-context-intelligence", {}).get("config", {})
            )
            if isinstance(ci_cfg, dict):
                if "context_intelligence_server_url" in ci_cfg:
                    result["server_url"] = ci_cfg["context_intelligence_server_url"]
                if "context_intelligence_api_key" in ci_cfg:
                    result["api_key"] = ci_cfg["context_intelligence_api_key"]
    except ImportError:
        # Fallback: crude line-based extraction
        try:
            text = path.read_text()
            in_ci_section = False
            for line in text.splitlines():
                stripped = line.strip()
                if "hook-context-intelligence" in stripped:
                    in_ci_section = True
                    continue
                if in_ci_section:
                    if stripped.startswith("context_intelligence_server_url:"):
                        val = stripped.split(":", 1)[1].strip().strip("'\"")
                        result["server_url"] = val
                    elif stripped.startswith("context_intelligence_api_key:"):
                        val = stripped.split(":", 1)[1].strip().strip("'\"")
                        result["api_key"] = val
                    # If we hit a non-indented line, we've left the section
                    if not line.startswith(" ") and not line.startswith("\t") and stripped:
                        if "context_intelligence" not in stripped:
                            in_ci_section = False
        except OSError:
            pass
    except Exception as exc:
        log.debug("Could not parse %s: %s", path, exc)
    return result


# ---------------------------------------------------------------------------
# From context_intelligence/reconstruct/discover.py
# ---------------------------------------------------------------------------


def workspace_slug(project_dir: str) -> str:
    """Derive the workspace slug from an absolute project directory path.

    Converts the absolute path to a slug by replacing every ``/`` with ``-``.

    Examples::

        workspace_slug("/home/bkrabach/dev/attractor-dev-machine")
        # -> "-home-bkrabach-dev-attractor-dev-machine"

    Parameters
    ----------
    project_dir:
        Absolute path to the project directory.

    Returns
    -------
    str
        Slug derived from the absolute path.
    """
    import os

    return os.path.abspath(project_dir).replace("/", "-")
