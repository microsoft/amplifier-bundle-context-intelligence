"""ConfigResolver — lazy fallback chain for hook configuration values."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_DEFAULT_BASE_PATH = "~/.amplifier/projects"
_DEFAULT_PROJECT_SLUG = "default"


def _slugify_path(path_str: str) -> str:
    """Convert an absolute path to the CLI's project slug format.

    Matches ``amplifier_app_cli.project_utils.get_project_slug()``:
    full path with separators replaced by hyphens, prefixed with ``-``.

    Examples:
        ``/workspace``            → ``-workspace``
        ``/home/user/repos/app``  → ``-home-user-repos-app``
    """
    slug = path_str.replace("/", "-").replace("\\", "-").replace(":", "")
    if slug and not slug.startswith("-"):
        slug = "-" + slug
    return slug or _DEFAULT_PROJECT_SLUG


class ConfigResolver:
    """Resolve configuration values with lazy fallback chains.

    Resolution order per property:

    - project_slug: config → coordinator.config → session.working_dir capability → 'default'
    - base_path:    config → coordinator.config → default
    - forest_name:  config[graph_store][graph_forest_name] → config[project]
                    → project_slug (full chain above) → 'default'

    Resolved values are cached after first access.

    Note: Empty strings in config are treated as absent and fall through to the
    next source in the chain (standard ``or``-chain falsy semantics).
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._base_path: Path | None = None
        self._project_slug: str | None = None
        self._forest_name: str | None = None
        self._exclude_events: frozenset[str] | None = None

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

    def _slug_from_working_dir(self) -> str | None:
        """Derive a project slug from the coordinator's session.working_dir capability.

        The Amplifier CLI stamps ``project_slug`` into ``coordinator.config``
        *after* session creation, but hooks mount *during* creation — so
        ``coordinator.config["project_slug"]`` is not yet available.  The
        ``session.working_dir`` capability IS registered by the foundation's
        ``bundle.py`` before hooks mount, so we can derive the slug from it.

        Returns ``None`` if the capability is not available.
        """
        get_cap = getattr(self._coordinator, "get_capability", None)
        if get_cap is None:
            return None
        working_dir = get_cap("session.working_dir")
        if not isinstance(working_dir, str) or not working_dir:
            return None
        return _slugify_path(str(Path(working_dir).resolve()))

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_slug(self) -> str:
        """Resolved project slug identifier.

        Chain: config['project_slug']
               → coordinator.config['project_slug']
               → session.working_dir capability (slugified)
               → 'default'.

        Result is cached after first access.
        """
        if self._project_slug is None:
            raw = (
                self._config.get("project_slug")
                or self._coordinator_config_get("project_slug")
                or self._slug_from_working_dir()
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

    @property
    def forest_name(self) -> str:
        """Resolved forest name for graph storage.

        Chain: config['graph_store']['graph_forest_name']
               → config['project']
               → project_slug (includes working_dir capability fallback)

        Non-dict graph_store values are skipped gracefully.
        Result is cached after first access.
        """
        if self._forest_name is None:
            value: Any = None

            # Step 1: graph_store.graph_forest_name (explicit override)
            graph_store = self._config.get("graph_store")
            if isinstance(graph_store, dict):
                value = graph_store.get("graph_forest_name")

            # Step 2: config['project']
            if not value:
                value = self._config.get("project")

            # Step 3: delegate to project_slug (has its own full chain
            #         including working_dir capability fallback)
            if not value:
                value = self.project_slug

            self._forest_name = str(value) if value else _DEFAULT_PROJECT_SLUG

        return self._forest_name

    @property
    def enable_graph(self) -> bool:
        """Whether graph storage is enabled.

        Resolution chain (first truthy wins):
          1. Environment variable ``CI_ENABLE_GRAPH`` (e.g. ``CI_ENABLE_GRAPH=true``)
          2. config['enable_graph']

        The env-var override exists because the Amplifier CLI merges behavior
        YAML config ON TOP of settings.yaml hook config, so a behavior default
        of ``enable_graph: false`` silently wins over the user's
        ``enable_graph: true`` in settings.yaml.  The env var gives users a
        reliable override path unaffected by YAML merge ordering.
        """
        env = os.environ.get("CI_ENABLE_GRAPH", "").strip().lower()
        if env in ("1", "true", "yes"):
            return True
        return bool(self._config.get("enable_graph", False))

    @property
    def graph_store_config(self) -> dict[str, Any] | None:
        """Full graph_store configuration dict, or None if absent.

        Reads directly from config['graph_store']. No coordinator fallback.
        """
        value = self._config.get("graph_store")
        return value if isinstance(value, dict) else None

    @property
    def neo4j_config(self) -> dict[str, Any] | None:
        """Extracted Neo4j connection parameters, or None if unavailable.

        Extracts uri, auth (as a (username, password) tuple or None),
        and database (defaulting to 'neo4j') from graph_store['config'].

        Returns None if graph_store is absent or has no 'config' key.
        """
        store = self.graph_store_config
        if store is None:
            return None

        inner = store.get("config")
        if not isinstance(inner, dict):
            return None

        uri = inner.get("uri")
        username = inner.get("username")
        password = inner.get("password")
        auth = (username, password) if username is not None and password is not None else None
        database = inner.get("database", "neo4j")

        return {"uri": uri, "auth": auth, "database": database}

    @property
    def exclude_events(self) -> frozenset[str]:
        """Frozen set of event names to exclude from processing.

        Reads directly from config['exclude_events'], defaults to empty frozenset.
        No coordinator fallback.  Result is cached after first access.
        """
        if self._exclude_events is None:
            self._exclude_events = frozenset(self._config.get("exclude_events", []))
        return self._exclude_events

    @property
    def log_level(self) -> str:
        """Log level string for this module.

        Reads directly from config['log_level'], defaults to 'WARNING'.
        No coordinator fallback.
        """
        return str(self._config.get("log_level", "WARNING"))

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def session_dir(self, session_id: str) -> Path:
        """Compose the session-scoped context-intelligence directory path.

        Returns: base_path / project_slug / 'sessions' / session_id / 'context-intelligence'
        """
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"
