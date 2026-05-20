"""ConfigResolver — lazy fallback chain for hook configuration values."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from context_intelligence.config import SETTINGS_PATH, _parse_settings_yaml
from context_intelligence.reconstruct.discover import workspace_slug

_DEFAULT_BASE_PATH = "~/.amplifier/projects"
_DEFAULT_PROJECT_SLUG = "default"

# Environment variable prefix for all hook configuration.
# AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE  → workspace
# AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL → context_intelligence_server_url
# etc.
_ENV_PREFIX = "AMPLIFIER_CONTEXT_INTELLIGENCE_"


def _env(suffix: str) -> str | None:
    """Read ``AMPLIFIER_CONTEXT_INTELLIGENCE_<SUFFIX>`` from the environment.

    Returns the value as a string if set and non-empty, otherwise ``None``.
    """
    value = os.environ.get(_ENV_PREFIX + suffix)
    return value if value else None


def _slugify_path(path_str: str) -> str:
    """Convert an absolute path to the CLI's project slug format.

    Matches ``amplifier_app_cli.project_utils.get_project_slug()``:
    full path with separators replaced by hyphens, prefixed with ``-``.

    Examples:
        ``/workspace``            → ``-workspace``
        ``/home/user/repos/app``  → ``-home-user-repos-app``
    """
    if not path_str:
        return _DEFAULT_PROJECT_SLUG
    slug = workspace_slug(path_str)
    # Windows normalisation: replace backslashes and strip drive-letter colons.
    slug = slug.replace("\\", "-").replace(":", "")
    if slug and not slug.startswith("-"):
        slug = "-" + slug
    return slug or _DEFAULT_PROJECT_SLUG


class ConfigResolver:
    """Resolve configuration values with lazy fallback chains.

    Resolution order per property:

    - project_slug: config → coordinator.config → session.working_dir capability → 'default'
    - base_path:    config → coordinator.config → default
    - workspace:    config['workspace'] → coordinator.config['workspace'] → project_slug

    Resolved values are cached after first access.

    Note: Empty strings in config are treated as absent and fall through to the
    next source in the chain (standard ``or``-chain falsy semantics).
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._base_path: Path | None = None
        self._project_slug: str | None = None
        self._workspace: str | None = None
        self._exclude_events: frozenset[str] | None = None
        self._additional_events: frozenset[str] | None = None

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
        1. config[\"workspace\"]              — explicit hook config / settings.yaml / env var (highest)
        2. coordinator.config[\"workspace\"]  — set by the application at coordinator level
        3. project_slug / project chain     — auto-resolved from CLI or working dir

        Follows the same config → coordinator → default pattern as all other properties.
        """
        if self._workspace is None:
            raw = (
                self._config.get("workspace")
                or self._coordinator_config_get("workspace")
                or self.project_slug
            )
            self._workspace = str(raw)

        return self._workspace

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
    def additional_events(self) -> frozenset[str]:
        """Events to register for unconditionally, regardless of capability discovery.

        Resolves mount-order race: modules that contribute observability.events after
        the hook mounts will still be covered if listed here.
        Reads from config['additional_events'], defaults to empty frozenset.
        """
        if self._additional_events is None:
            self._additional_events = frozenset(self._config.get("additional_events", []))
        return self._additional_events

    @property
    def allow_workspaces(self) -> list[str]:
        """Workspace patterns permitted to dispatch to the server.

        When any entry is present, only workspaces matching one of these
        patterns will dispatch to the remote server.  When empty (the
        default), nothing dispatches — deny-all is the default posture.

        Reads config['allow_workspaces'], defaults to [].
        No coordinator fallback.  Not cached (cheap list copy per call).
        """
        return list(self._config.get("allow_workspaces", []))

    @property
    def deny_workspaces(self) -> list[str]:
        """Workspace patterns blocked from server dispatch.

        Trims matching workspaces from what allow_workspaces already
        opened.  Has no effect when allow_workspaces is empty — there is
        nothing to trim from.  Deny always beats allow when both match.

        Reads config['deny_workspaces'], defaults to [].
        No coordinator fallback.  Not cached (cheap list copy per call).
        """
        return list(self._config.get("deny_workspaces", []))

    def _evaluate_forwarding(self) -> bool:
        """Evaluate the five-step forwarding resolution chain.

        Resolution order (first match wins):

        1. config['forwarding_enabled'] is False
           → False  (host path-rule hard override; short-circuits pattern eval)
        2. allow_workspaces is empty
           → False  (deny-all default: nothing dispatches without explicit opt-in)
        3. workspace matches none of allow_workspaces
           → False  (workspace not opted in)
        4. workspace matches any deny_workspaces pattern
           → False  (trimmed from what allow opened; deny beats allow)
        5. default
           → True   (opted in and not trimmed)
        """
        # Step 1: host hard override (only False short-circuits; None/True are ignored)
        explicit = self._config.get("forwarding_enabled")
        if explicit is False:
            return False

        allow = self.allow_workspaces
        deny = self.deny_workspaces
        workspace = self.workspace

        # Step 2: deny-all default — nothing dispatches without an explicit allow entry
        if not allow:
            return False

        # Step 3: workspace not opted in
        if not any(fnmatch.fnmatch(workspace, p) for p in allow):
            return False

        # Step 4: workspace trimmed by deny list
        if any(fnmatch.fnmatch(workspace, p) for p in deny):
            return False

        # Step 5: permitted
        return True

    @property
    def forwarding_enabled(self) -> bool:
        """Whether this session should dispatch events to the remote server.

        Recomputed on every access (no caching) so dynamic config updates
        are immediately reflected without remounting.

        See _evaluate_forwarding() for the full resolution chain.
        See ConfigResolver class docstring 'Workspace forwarding semantics'
        for the user-facing explanation.
        """
        return self._evaluate_forwarding()

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

    @property
    def parent_id(self) -> str:
        """Parent session ID supplied by a resolver via SessionFactory.create_phase_session.

        Empty string means absent / root session (preserves existing semantics).
        No coordinator fallback, no env fallback — this is a per-session hook-config value
        stamped by the resolver for each spawned phase session (CR-1).
        """
        return str(self._config.get("parent_id", "") or "")

    @property
    def resolve_instance_id(self) -> str:
        """Resolver instance ID supplied via SessionFactory.create_phase_session.

        Empty string if absent. No coordinator fallback, no env fallback.
        """
        return str(self._config.get("resolve_instance_id", "") or "")

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

        Resolution order (first truthy value wins):
        1. config['context_intelligence_server_url']  — bundle config / settings.yaml overrides
        2. coordinator.config['context_intelligence_server_url']  — coordinator-level config
        3. AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL env var
        4. ~/.amplifier/settings.yaml  — lowest-priority fallback
        """
        value = (
            self._config.get("context_intelligence_server_url")
            or self._coordinator_config_get("context_intelligence_server_url")
            or _env("SERVER_URL")
            or _parse_settings_yaml(SETTINGS_PATH).get("server_url")
        )
        return str(value) if value else None

    @property
    def context_intelligence_api_key(self) -> str | None:
        """API key for the context-intelligence server, or None if not configured.

        Resolution order (first truthy value wins):
        1. config['context_intelligence_api_key']  — bundle config / settings.yaml overrides
        2. coordinator.config['context_intelligence_api_key']  — coordinator-level config
        3. AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY env var
        4. ~/.amplifier/settings.yaml  — lowest-priority fallback
        """
        value = (
            self._config.get("context_intelligence_api_key")
            or self._coordinator_config_get("context_intelligence_api_key")
            or _env("API_KEY")
            or _parse_settings_yaml(SETTINGS_PATH).get("api_key")
        )
        return str(value) if value else None

    @property
    def neo4j_config(self) -> dict[str, Any] | None:
        """Extracted Neo4j connection parameters, or None if unavailable.

        Retained for backward compatibility — returns None since graph_store
        configuration has been removed from the thin-forwarder bundle.
        """
        return None
