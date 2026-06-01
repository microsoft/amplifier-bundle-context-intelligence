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
import re
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
    """Minimal YAML parser for settings.yaml — good enough for the flat keys
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
# Shared env-var helpers (used by HookConfigResolver and ToolConfigResolver)
# ---------------------------------------------------------------------------

#: Environment variable prefix shared by all CI configuration.
#: ``AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE``  → workspace
#: ``AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL`` → context_intelligence_server_url
#: etc.
_ENV_PREFIX = "AMPLIFIER_CONTEXT_INTELLIGENCE_"


def _env(suffix: str) -> str | None:
    """Read ``AMPLIFIER_CONTEXT_INTELLIGENCE_<SUFFIX>`` from the environment.

    Returns the value as a string if set and non-empty, otherwise ``None``.
    """
    value = os.environ.get(_ENV_PREFIX + suffix)
    return value if value else None


# ---------------------------------------------------------------------------
# Shell-style placeholder expander (used by ToolConfigResolver)
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def _expand_env_placeholders(value: str) -> str:
    """Expand shell-style ``${VAR}``, ``${VAR:}``, ``${VAR:default}`` placeholders.

    - ``${VAR}`` — replaced with ``os.environ[VAR]`` if set, else ``""``.
    - ``${VAR:}`` — same as ``${VAR}`` (empty default when var is unset).
    - ``${VAR:default}`` — replaced with ``os.environ[VAR]`` if set, else ``"default"``.
    - Non-placeholder strings pass through unchanged.

    Note: ``os.path.expandvars`` does **not** support the ``${VAR:default}``
    colon syntax used by the agent behavior YAML files shipped with this bundle,
    hence this small regex-based helper.
    """

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        var_name = m.group(1)
        default = m.group(2) if m.group(2) is not None else ""
        return os.environ.get(var_name, default)

    return _PLACEHOLDER_RE.sub(_replace, value)


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
