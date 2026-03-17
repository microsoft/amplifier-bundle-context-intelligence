"""ConfigResolver — lazy fallback chain for hook configuration values."""

from __future__ import annotations

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
    - workspace:    coordinator.config['workspace'] → config['workspace'] → project_slug

    Resolved values are cached after first access.

    Note: Empty strings in config are treated as absent and fall through to the
    next source in the chain (standard ``or``-chain falsy semantics).
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._base_path: Path | None = None
        self._project_slug: str | None = None
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
        ``coordinator.config[\"project_slug\"]`` is not yet available.  The
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
    def workspace(self) -> str:
        """Workspace identifier for this session.

        Priority (first truthy value wins):
        1. coordinator.config[\"workspace\"]  — set by integrations at coordinator level
        2. config[\"workspace\"]              — explicit hook config
        3. project_slug / project chain     — from CLI or working dir
        """
        # 1. Coordinator-level workspace (highest — set by integrations)
        if self._coordinator is not None:
            coord_config = getattr(self._coordinator, "config", {}) or {}
            ws = coord_config.get("workspace")
            if ws:
                return str(ws)

        # 2. Explicit workspace in hook config
        ws = self._config.get("workspace")
        if ws:
            return str(ws)

        # 3. Fall through project_slug / project / working_dir / 'default'
        return self.project_slug

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

    @property
    def dispatch_timeout(self) -> float:
        """Write timeout in seconds for dispatching context-intelligence requests.

        Reads directly from config['dispatch_timeout'], defaults to 10.0.
        This budget applies to the HTTP write phase; connect/read/pool
        timeouts are fixed in the handler. No coordinator fallback.
        Always returns a float.
        """
        return float(self._config.get("dispatch_timeout", 10.0))

    @property
    def dispatch_failure_threshold(self) -> int:
        """Number of consecutive dispatch failures before the circuit opens.

        Reads directly from config['dispatch_failure_threshold'], defaults to 3.
        No coordinator fallback.  Always returns an int.
        """
        return int(self._config.get("dispatch_failure_threshold", 3))

    @property
    def dispatch_queue_capacity(self) -> int:
        """Maximum queued HTTP dispatches before dispatch is disabled.

        Reads directly from config['dispatch_queue_capacity'], defaults to 256.
        No coordinator fallback. Always returns an int.
        """
        return int(self._config.get("dispatch_queue_capacity", 256))

    @property
    def close_drain_timeout(self) -> float:
        """Max seconds to wait for queued HTTP dispatches during cleanup.

        Reads directly from config['close_drain_timeout'], defaults to 0.5.
        No coordinator fallback. Always returns a float.
        """
        return float(self._config.get("close_drain_timeout", 0.5))

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def session_dir(self, session_id: str) -> Path:
        """Compose the session-scoped context-intelligence directory path.

        Returns: base_path / project_slug / 'sessions' / session_id / 'context-intelligence'
        """
        return self.base_path / self.project_slug / "sessions" / session_id / "context-intelligence"

    @property
    def blob_store_root(self) -> Path:
        """Root directory for blob storage.

        Returns: base_path / project_slug / 'sessions'

        DiskBlobStore uses this as its root, storing blobs in:
            <blob_store_root> / <session_id> / blobs / <key>.json
        which places them alongside the session's context-intelligence directory.
        """
        return self.base_path / self.project_slug / "sessions"

    @property
    def context_intelligence_server_url(self) -> str | None:
        """URL of the context-intelligence server, or None if not configured.

        Reads directly from config['context_intelligence_server_url'].
        No coordinator fallback.
        """
        value = self._config.get("context_intelligence_server_url")
        return str(value) if value else None

    @property
    def neo4j_config(self) -> dict[str, Any] | None:
        """Extracted Neo4j connection parameters, or None if unavailable.

        Retained for backward compatibility — returns None since graph_store
        configuration has been removed from the thin-forwarder bundle.
        """
        return None
