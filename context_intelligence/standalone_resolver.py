"""StandaloneConfigResolver — lightweight config resolver for analytics-only mode.

Used by tool-graph-query and tool-blob-read when the hook-context-intelligence
module is NOT mounted (i.e. the context-intelligence-analytics behavior is used
without the full context-intelligence behavior).

Reads server_url, api_key, workspace, and base_path from environment variables
and ~/.amplifier/settings.yaml — the same sources ConfigResolver uses, but
without needing a coordinator or a registered capability.

Resolution order (mirrors ConfigResolver):
  server_url:  AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL  → settings.yaml
  api_key:     AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY     → settings.yaml
  workspace:   AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE   → 'default'
  base_path:   AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH   → ~/.amplifier/projects
"""

from __future__ import annotations

import os
from pathlib import Path

from context_intelligence.config import SETTINGS_PATH, _parse_settings_yaml

_ENV_PREFIX = "AMPLIFIER_CONTEXT_INTELLIGENCE_"
_DEFAULT_BASE_PATH = "~/.amplifier/projects"
_DEFAULT_WORKSPACE = "default"


def _env(suffix: str) -> str | None:
    """Read ``AMPLIFIER_CONTEXT_INTELLIGENCE_<SUFFIX>`` from the environment."""
    value = os.environ.get(_ENV_PREFIX + suffix)
    return value if value else None


class StandaloneConfigResolver:
    """Config resolver for analytics-only mode (no hook mounted).

    Provides the same interface as ConfigResolver but reads exclusively from
    environment variables and ~/.amplifier/settings.yaml.  Instantiated lazily
    by tool-graph-query and tool-blob-read when the
    ``context_intelligence.config_resolver`` coordinator capability is absent.
    """

    def __init__(self) -> None:
        self._settings: dict | None = None

    def _get_settings(self) -> dict:
        if self._settings is None:
            self._settings = _parse_settings_yaml(SETTINGS_PATH)
        return self._settings

    @property
    def context_intelligence_server_url(self) -> str | None:
        """Server URL: env var → settings.yaml → None."""
        value = _env("SERVER_URL") or self._get_settings().get("server_url")
        return str(value) if value else None

    @property
    def context_intelligence_api_key(self) -> str | None:
        """API key: env var → settings.yaml → None."""
        value = _env("API_KEY") or self._get_settings().get("api_key")
        return str(value) if value else None

    @property
    def workspace(self) -> str:
        """Workspace: AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE → 'default'."""
        return _env("WORKSPACE") or _DEFAULT_WORKSPACE

    @property
    def base_path(self) -> Path:
        """Base path: AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH → ~/.amplifier/projects."""
        raw = _env("BASE_PATH") or _DEFAULT_BASE_PATH
        return Path(raw).expanduser()
