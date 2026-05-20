"""Configuration resolution for context-intelligence.

Resolution chain: CLI args → environment variables → ``~/.amplifier/settings.yaml`` → defaults.

Usage::

    from context_intelligence.config import resolve_config

    server_url, api_key = resolve_config(
        server_url=args.server_url,
        api_key=args.api_key,
    )
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("context_intelligence.config")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_SCHEMA = {"name": "amplifier.log", "ver": "1.0.0"}
AMPLIFIER_DIR = Path.home() / ".amplifier"
SETTINGS_PATH = AMPLIFIER_DIR / "settings.yaml"


# ---------------------------------------------------------------------------
# Settings.yaml parser
# ---------------------------------------------------------------------------


def _parse_settings_yaml(path: Path) -> dict:
    """Minimal YAML parser for settings.yaml — reads the nested context_intelligence_server block without requiring PyYAML.

    Returns a dict with keys ``server_url`` and ``api_key`` if found under
    ``overrides.hook-context-intelligence.config.context_intelligence_server``.
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
                server_cfg = ci_cfg.get("context_intelligence_server", {})
                if isinstance(server_cfg, dict):
                    if server_cfg.get("url"):
                        result["server_url"] = server_cfg["url"]
                    if server_cfg.get("api_key"):
                        result["api_key"] = server_cfg["api_key"]
    except ImportError:
        # Fallback: crude line-based extraction for environments without PyYAML.
        # Handles the nested context_intelligence_server: block.
        try:
            text = path.read_text()
            in_ci_section = False
            in_server_block = False
            for line in text.splitlines():
                stripped = line.strip()
                if "hook-context-intelligence" in stripped:
                    in_ci_section = True
                    in_server_block = False
                    continue
                if in_ci_section:
                    if not in_server_block and stripped == "context_intelligence_server:":
                        in_server_block = True
                        continue
                    if in_server_block:
                        if stripped.startswith("url:"):
                            val = stripped.split(":", 1)[1].strip().strip("'\"")
                            if val:
                                result["server_url"] = val
                        elif stripped.startswith("api_key:"):
                            val = stripped.split(":", 1)[1].strip().strip("'\"")
                            if val:
                                result["api_key"] = val
                        elif stripped and not stripped.startswith("#"):
                            if not line.startswith("        "):
                                in_server_block = False
                    if not line.startswith(" ") and not line.startswith("\t") and stripped:
                        if (
                            "context_intelligence" not in stripped
                            and "hook-context-intelligence" not in stripped
                        ):
                            in_ci_section = False
                            in_server_block = False
        except OSError:
            pass
    except Exception as exc:
        log.debug("Could not parse %s: %s", path, exc)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_config(
    *,
    server_url: str | None = None,
    api_key: str | None = None,
) -> tuple[str, str]:
    """Resolve server URL and API key.

    Resolution order:
    1. Explicit arguments (from CLI flags)
    2. Environment variables (``AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL``,
       ``AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY``)
    3. ``~/.amplifier/settings.yaml``

    Returns:
        ``(server_url, api_key)`` tuple.

    Raises:
        SystemExit: if either value cannot be resolved.
    """
    resolved_url = server_url or os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL")
    resolved_key = api_key or os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY")

    # Fallback to settings.yaml
    if not resolved_url or not resolved_key:
        settings = _parse_settings_yaml(SETTINGS_PATH)
        if not resolved_url:
            resolved_url = settings.get("server_url", "")
        if not resolved_key:
            resolved_key = settings.get("api_key", "")

    if not resolved_url:
        raise SystemExit(
            "No CI server URL found. Set AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL "
            "or use --server-url, or configure in ~/.amplifier/settings.yaml"
        )
    if not resolved_key:
        raise SystemExit(
            "No CI API key found. Set AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY "
            "or use --api-key, or configure in ~/.amplifier/settings.yaml"
        )

    return resolved_url, resolved_key
