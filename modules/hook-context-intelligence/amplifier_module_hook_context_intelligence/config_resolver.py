"""ConfigResolver — lazy fallback chain for hook configuration values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_DEFAULT_BASE_PATH = "~/.amplifier/projects"
_DEFAULT_PROJECT_SLUG = "default"


class ConfigResolver:
    """Resolve configuration values with a lazy 3-step fallback chain.

    Resolution order for each property:
    1. Explicit hook config  (passed at construction time)
    2. coordinator.config   (safely accessed; missing attr handled)
    3. Sensible default

    Resolved values are cached after first access.
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._base_path: Path | None = None
        self._project_slug: str | None = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _coordinator_config_get(self, key: str) -> Any:
        """Safely read *key* from coordinator.config.

        Returns ``None`` if the coordinator has no ``.config`` attribute or
        if the key is absent from it.
        """
        coord_config = getattr(self._coordinator, "config", None)
        if not isinstance(coord_config, dict):
            return None
        return coord_config.get(key)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_slug(self) -> str:
        """Resolved project slug identifier.

        Chain: config['project_slug'] → coordinator.config['project_slug'] → 'default'.
        Result is cached after first access.
        """
        if self._project_slug is None:
            raw = (
                self._config.get("project_slug")
                or self._coordinator_config_get("project_slug")
                or _DEFAULT_PROJECT_SLUG
            )
            self._project_slug = str(raw)
        return self._project_slug

    @property
    def base_path(self) -> Path:
        """Resolved base path for project storage.

        Chain: config['base_path'] → coordinator.config['base_path'] → default.
        Tilde is expanded.  Result is cached after first access.
        """
        if self._base_path is None:
            raw = (
                self._config.get("base_path")
                or self._coordinator_config_get("base_path")
                or _DEFAULT_BASE_PATH
            )
            self._base_path = Path(raw).expanduser()
        return self._base_path
