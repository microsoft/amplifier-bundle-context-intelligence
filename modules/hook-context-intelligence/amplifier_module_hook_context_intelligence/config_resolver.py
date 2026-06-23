"""ConfigResolver — lazy fallback chain for hook configuration values."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_intelligence.reconstruct.discover import workspace_slug

log = logging.getLogger(__name__)

_DEFAULT_BASE_PATH = "~/.amplifier/projects"
_DEFAULT_PROJECT_SLUG = "default"

# Default event-name patterns (fnmatch) excluded from local JSONL logging and graph dispatch.
#
# The pattern ``llm:stream_*delta`` expresses the transient-streaming-delta *category*: it
# matches every per-token delta event (currently ``llm:stream_block_delta``) while sparing
# the structural streaming events (block_start, block_end, stream_aborted).  The glob comes
# directly from the "Event dispositions" convention in the provider streaming contract
# (provider-streaming-contract.md) and is intentionally IDENTICAL to the default used by
# amplifier-module-hooks-logging — aligned by that convention, NOT by shared code.
#
# The two hooks are deliberately decoupled; they must NOT share a module, constant, or import.
# Keep them in sync via the contract, never by extracting a shared module.  If you change
# this default, mirror the change in amplifier-module-hooks-logging independently.
#
# Set exclude_events: [] in config to opt back in to all events including the deltas.
_DEFAULT_EXCLUDE_EVENTS: list[str] = ["llm:stream_*delta"]


@dataclass(frozen=True)
class Destination:
    """A single context-intelligence fan-out destination.

    name:    dict key in config['destinations']; identifier + merge key.
    url:     base URL (app already expanded ${VAR}). POSTs go to f"{url}/events".
    api_key: bearer token (app already expanded ${VAR}).
    include: pathspec (gitwildmatch) patterns. No default — a destination without
             an explicit include has an empty pattern set and matches NOTHING.
             Declare include explicitly to receive any sessions.
    exclude: pathspec patterns; exclude-wins, per-destination (S3). Default [].
    """

    name: str
    url: str
    api_key: str
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


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


class HookConfigResolver:
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
        self._destinations: dict[str, Destination] | None = None

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
    def working_dir(self) -> str:
        """Absolute session working directory from the ``session.working_dir`` capability.

        Read live (not cached) so it reflects mid-session working-directory changes.
        Returns "" when the capability is unavailable.
        """
        get_cap = getattr(self._coordinator, "get_capability", None)
        if get_cap is None:
            return ""
        wd = get_cap("session.working_dir")
        if not isinstance(wd, str) or not wd:
            return ""
        return wd

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
        """Frozen set of event-name patterns (fnmatch) to suppress from logging and dispatch.

        Defaults to ``_DEFAULT_EXCLUDE_EVENTS`` (["llm:stream_*delta"]) — matching the
        transient per-token streaming delta category (fnmatch) while sparing the structural
        streaming events (block_start, block_end, stream_aborted).

        Set ``exclude_events: []`` in config to disable the filter and log/dispatch every event.
        No coordinator fallback.  Result is cached after first access.
        """
        if self._exclude_events is None:
            self._exclude_events = frozenset(
                self._config.get("exclude_events", _DEFAULT_EXCLUDE_EVENTS)
            )
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

    @property
    def context_intelligence_server_url(self) -> str | None:
        """URL of the context-intelligence server, or None if not configured.

        Resolution order (first truthy value wins):
        1. config['context_intelligence_server_url']  — bundle config / settings.yaml overrides
        2. coordinator.config['context_intelligence_server_url']  — coordinator-level config

        Note: env var and ~/.amplifier/settings.yaml reads have been removed (D1 contract fix).
        The app layer (app-cli) is responsible for reading and expanding those sources before
        passing config to mount().
        """
        value = self._config.get("context_intelligence_server_url") or self._coordinator_config_get(
            "context_intelligence_server_url"
        )
        return str(value) if value else None

    @property
    def context_intelligence_api_key(self) -> str | None:
        """API key for the context-intelligence server, or None if not configured.

        Resolution order (first truthy value wins):
        1. config['context_intelligence_api_key']  — bundle config / settings.yaml overrides
        2. coordinator.config['context_intelligence_api_key']  — coordinator-level config

        Note: env var and ~/.amplifier/settings.yaml reads have been removed (D1 contract fix).
        The app layer (app-cli) is responsible for reading and expanding those sources before
        passing config to mount().
        """
        value = self._config.get("context_intelligence_api_key") or self._coordinator_config_get(
            "context_intelligence_api_key"
        )
        return str(value) if value else None

    @property
    def destinations(self) -> dict[str, Destination]:
        """Resolved fan-out destinations, keyed by name.

        Source: config['destinations'] (a dict). Each value is a dict with
        keys url, api_key, include?, exclude?. Missing/empty include -> () → matches nothing;
        missing exclude -> []. ${VAR} is already expanded by the app.

        Back-compat (D10): if config['destinations'] is absent/empty BUT the
        legacy scalar context_intelligence_server_url is present, synthesize
        {"default": Destination(url=..., api_key=..., include=("**",))}.

        Returns {} when neither destinations nor a legacy url is configured
        (-> local-JSONL only, S4).
        """
        if self._destinations is not None:
            return self._destinations

        _sentinel = object()
        raw = self._config.get("destinations", _sentinel)
        destinations_key_present = raw is not _sentinel

        if destinations_key_present:
            # Key is explicitly set — parse the dict (may be empty or non-empty).
            # An explicit empty dict {} is valid (local-only) — no legacy synthesis.
            result: dict[str, Destination] = {}
            if isinstance(raw, dict):
                for name, spec in raw.items():
                    if not isinstance(spec, dict):
                        continue
                    url = str(spec.get("url", "") or "").strip()
                    api_key = str(spec.get("api_key", "") or "").strip()
                    include = tuple(spec.get("include") or [])
                    exclude = tuple(spec.get("exclude") or [])
                    result[name] = Destination(
                        name=name,
                        url=url,
                        api_key=api_key,
                        include=include,
                        exclude=exclude,
                    )
            self._destinations = result
            return self._destinations

        # Key is absent: back-compat synthesis from the legacy scalar.
        #
        # Synthesize the "default" destination ONLY when BOTH url and api_key are
        # present. A url with no api_key must NOT raise at mount: the pre-fan-out
        # behavior for that config was "dispatch disabled, local JSONL continues",
        # and synthesizing Destination(api_key="") here would make
        # validate_destinations() raise -> mount() fail, regressing existing
        # single-server setups. Degrade to local-only with a discoverable WARNING.
        legacy_url = self.context_intelligence_server_url
        legacy_key = self.context_intelligence_api_key
        if legacy_url and legacy_key:
            self._destinations = {
                "default": Destination(
                    name="default",
                    url=legacy_url,
                    api_key=legacy_key,
                    include=("**",),
                    exclude=(),
                )
            }
            return self._destinations
        if legacy_url and not legacy_key:
            log.warning(
                "context-intelligence: context_intelligence_server_url is set but "
                "context_intelligence_api_key is empty after expansion; dispatch "
                "disabled, local JSONL only. Set context_intelligence_api_key "
                "(or its expanded ${VAR}) to enable dispatch."
            )

        self._destinations = {}
        return self._destinations

    @property
    def neo4j_config(self) -> dict[str, Any] | None:
        """Extracted Neo4j connection parameters, or None if unavailable.

        Retained for backward compatibility — returns None since graph_store
        configuration has been removed from the thin-forwarder bundle.
        """
        return None

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

    def validate_destinations(self) -> dict[str, Destination]:
        """Validate and return all configured destinations. Fail-fast (C3).

        After the app's ${VAR} expansion, a destination with an empty/missing
        url OR api_key is a configuration ERROR, not a silent per-event drop.

        Raises:
            ValueError: naming the offending destination(s) and the empty field(s).
        Returns:
            The validated destinations dict (possibly empty -> local-only, OK).
        """
        dests = self.destinations
        problems: list[str] = []
        for name, dest in dests.items():
            if not dest.url:
                problems.append(f"{name}: missing url")
            if not dest.api_key:
                problems.append(f"{name}: missing api_key")
        if problems:
            raise ValueError(
                f"context-intelligence destinations misconfigured: {', '.join(problems)}. "
                f"Set url and api_key (or the expanded ${{VAR}}) under "
                f"overrides.hook-context-intelligence.config.destinations.<name>."
            )
        return dests


# ---------------------------------------------------------------------------
# Backward-compat alias — import either name (HookConfigResolver is canonical)
# ---------------------------------------------------------------------------
ConfigResolver = HookConfigResolver
