"""ConfigResolver — lazy fallback chain for hook configuration values."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_intelligence.reconstruct.discover import workspace_slug

log = logging.getLogger(__name__)

_DEFAULT_BASE_PATH = "~/.amplifier/projects"
_DEFAULT_PROJECT_SLUG = "default"

# String values that a human operator would write to mean "False".
# Used by _coerce_bool to prevent the bool('false') == True footgun
# that occurs when environment variables are wired as strings.
_FALSEY: frozenset[str] = frozenset({"0", "false", "no", "off", ""})


def _coerce_bool(value: Any, *, default: bool) -> bool:
    """Coerce *value* to bool, handling string env-var representations correctly.

    Resolution:
    - ``None``  → *default*
    - ``bool``  → as-is (no conversion)
    - ``str``   → trimmed and lowercased; in ``_FALSEY`` → ``False``, else ``True``
    - anything else → ``bool(value)``

    This is the safe alternative to ``bool(value)`` when the value may come
    from a YAML/env-var string expansion where ``'false'`` must mean ``False``
    (``bool('false') == True`` is the footgun this function prevents).
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY
    return bool(value)


def _coerce_positive_float(value: Any, *, default: float, minimum: float) -> float:
    """Coerce *value* to a float >= *minimum*, tolerating bad operator input.

    Resolution:
    - None or unparseable strings ('', 'abc') -> *default* (never raises)
    - valid numbers / numeric strings -> float(value)
    - result is then clamped to max(minimum, parsed)

    This is the safe alternative to ``float(value)`` when the value may come
    from a YAML/env-var string expansion where malformed or zero/negative values
    must not crash session startup or produce unusable timeout budgets.
    """
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    # Reject non-finite values (inf, -inf, nan). float("inf")/"nan"/"1e400"
    # parse cleanly, but a non-finite timeout is a footgun the low-side
    # ``minimum`` floor does not catch: an infinite read timeout makes a POST
    # await forever on a slow/hung server, re-opening the exact
    # "healthy-but-slow server permanently stalls dispatch" failure this guard
    # exists to prevent. Treat non-finite input as garbage -> fall back to default.
    if not math.isfinite(parsed):
        return default
    return max(minimum, parsed)


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

    name:          dict key in config['destinations']; identifier + merge key.
    url:           base URL (app already expanded ${VAR}). POSTs go to f"{url}/events".
    api_key:       bearer token (app already expanded ${VAR}). Required for auth_mode="static".
    include:       pathspec (gitwildmatch) patterns. No default — a destination without
                   an explicit include has an empty pattern set and matches NOTHING.
                   Declare include explicitly to receive any sessions.
    exclude:       pathspec patterns; exclude-wins, per-destination (S3). Default [].
    auth_mode:     ``"static"`` (default) — use api_key as bearer token.
                   ``"entra"``            — acquire a delegated Entra token via azure-identity.
    auth_resource: Entra resource URI (e.g. ``api://<client_id>``). Required for auth_mode="entra".
                   App already expanded any ${VAR} before mount.
    """

    name: str
    url: str
    api_key: str
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    auth_mode: str = "static"
    auth_resource: str = ""


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
        Result is cached after first access.

        Canonicalisation rules (§D.2 — identical to reader-side
        ``canonicalize_base_path`` in ``context_intelligence.config``; inlined
        here to keep zero hook→reader-package coupling and the fold gate green):

        0. **Unexpanded-placeholder guard.** A value that still looks like a
           shell placeholder (``${...}``) means the host app did NOT expand the
           ``base_path: "${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:}"`` binding
           (the hook relies on the app layer to expand ``${VAR}`` before mount,
           exactly as it does for ``url`` / ``api_key`` / ``exclude_events``).
           Rather than treat the literal ``${...}`` as a bogus relative path and
           warn on every session, fall back to the default **silently**.  The
           env-reading readers still relocate via the variable directly, and the
           §C.3 startup consistency check in ``on_session_ready`` fires LOUD if
           the variable was actually set (i.e. real relocation was intended but
           the binding did not expand).
        1. Strip whitespace from the raw string.
        2. Empty string → ``_DEFAULT_BASE_PATH`` (never anchored to CWD).
        3. Expand ``~`` via :meth:`~pathlib.Path.expanduser`.
        4. If the result is still relative → warn, fall back to default.

        Both the writer (this property) and readers call the same rules so
        canonicalized paths are byte-identical regardless of which side resolves
        them.  The §D.2 contract test drives the real property and asserts
        writer ≡ reader and always absolute.
        """
        if self._base_path is None:
            raw = (
                self._config.get("base_path")
                or self._coordinator_config_get("base_path")
                or _DEFAULT_BASE_PATH
            )
            # §D.2 canonicalizer (inline — no os, no import os, fold-gate safe).
            # DUPLICATED BY DESIGN: the byte-equivalent reader copy is
            # `canonicalize_base_path` in `context_intelligence.config`. The fold
            # gate forbids importing that package here, so the two MUST be kept in
            # sync by hand; `tests/test_base_path_parity.py` pins writer ≡ reader.
            # Edit one → edit the other and the parity test.
            s = str(raw).strip()
            if not s or s.startswith("${"):
                # Empty, OR an unexpanded ${VAR} placeholder (host app did not
                # expand the binding). Either way → default, no noise. §D.2 rule 0.
                self._base_path = Path(_DEFAULT_BASE_PATH).expanduser()
            else:
                p = Path(s).expanduser()
                if not p.is_absolute():
                    log.warning(
                        "base_path %r is not absolute; using default %s",
                        s,
                        _DEFAULT_BASE_PATH,
                    )
                    self._base_path = Path(_DEFAULT_BASE_PATH).expanduser()
                else:
                    self._base_path = p
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
        Bad/unparseable input falls back to the default; values are clamped
        to a 0.1 s floor (see _coerce_positive_float).
        """
        return _coerce_positive_float(
            self._config.get("dispatch_timeout"), default=10.0, minimum=0.1
        )

    @property
    def dispatch_read_timeout(self) -> float:
        """Read timeout in seconds for the HTTP read phase of dispatch.

        Reads directly from config['dispatch_read_timeout'], defaults to 10.0.
        This budget applies to the HTTP read phase only; connect/write/pool
        timeouts are unchanged. No coordinator fallback.
        Bad/unparseable input falls back to the default; values are clamped
        to a 0.1 s floor (see _coerce_positive_float).
        """
        return _coerce_positive_float(
            self._config.get("dispatch_read_timeout"), default=10.0, minimum=0.1
        )

    @property
    def dispatch_connect_timeout(self) -> float:
        """Connect timeout in seconds for the HTTP connect phase of dispatch.

        Reads directly from config['dispatch_connect_timeout'], defaults to 3.0.
        This budget applies to the TCP/TLS connect phase only; write/read/pool
        timeouts are unchanged. No coordinator fallback. A too-tight connect
        budget manufactures spurious httpx.TimeoutException -> transient failures
        (the "delivery degraded, retrying with backoff" warning) against a healthy
        server, so the default is deliberately generous for cross-region,
        Entra-authenticated calls over VPN/proxy. Bad/unparseable input falls
        back to the default; values are clamped to a 0.1 s floor
        (see _coerce_positive_float).
        """
        return _coerce_positive_float(
            self._config.get("dispatch_connect_timeout"), default=3.0, minimum=0.1
        )

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
        No coordinator fallback. Always returns an int >= 1.

        Values < 1 are clamped to 1. asyncio.Queue(maxsize=0) is UNBOUNDED, so
        a zero or negative value would silently remove the memory guard (TB-03).
        """
        return max(1, int(self._config.get("dispatch_queue_capacity", 256)))

    @property
    def close_drain_timeout(self) -> float:
        """Max seconds to wait for queued HTTP dispatches during cleanup.

        Reads directly from config['close_drain_timeout'], defaults to 10.0.
        No coordinator fallback. Bad/unparseable input falls back to the
        default; values are clamped to a 0.1 s floor (see _coerce_positive_float)
        — matching the other dispatch_* timeout knobs.

        The default is 10.0 s so that remote deployments (e.g. Azure behind APIM
        with a per-request Entra token) — whose round-trip is far longer than a
        sub-second budget allows — drain their queued tail events cleanly at
        shutdown out of the box. Undelivered events are always durable in
        events.jsonl (recoverable via context-intelligence-upload) regardless.
        Local/low-latency setups may lower this — e.g. ``close_drain_timeout: 0.5``
        — for a snappier shutdown.
        """
        return _coerce_positive_float(
            self._config.get("close_drain_timeout"), default=10.0, minimum=0.1
        )

    @property
    def dispatch_backoff_initial(self) -> float:
        """Initial retry backoff interval in seconds after a dispatch failure.

        Reads directly from config['dispatch_backoff_initial'], defaults to 1.0.
        No coordinator fallback. Always returns a float.
        """
        return float(self._config.get("dispatch_backoff_initial", 1.0))

    @property
    def dispatch_backoff_max(self) -> float:
        """Maximum retry backoff interval in seconds (cap for exponential back-off).

        Reads directly from config['dispatch_backoff_max'], defaults to 30.0.
        No coordinator fallback. Always returns a float.
        """
        return float(self._config.get("dispatch_backoff_max", 30.0))

    @property
    def dispatch_backoff_jitter(self) -> bool:
        """Whether to add random jitter to backoff intervals.

        Reads directly from config['dispatch_backoff_jitter'], defaults to True.
        No coordinator fallback. Always returns a bool.

        String values are coerced via _coerce_bool so that an operator setting
        an env var to 'false' correctly disables jitter (``bool('false') == True``
        is the footgun this avoids).
        """
        return _coerce_bool(self._config.get("dispatch_backoff_jitter"), default=True)

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
                    auth_mode = str(spec.get("auth_mode", "static") or "static").strip()
                    auth_resource = str(spec.get("auth_resource", "") or "").strip()
                    result[name] = Destination(
                        name=name,
                        url=url,
                        api_key=api_key,
                        include=include,
                        exclude=exclude,
                        auth_mode=auth_mode,
                        auth_resource=auth_resource,
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

        Per-target XOR auth validation:
        - auth_mode="static" (default): api_key must be non-empty.
        - auth_mode="entra":           auth_resource must be non-empty; api_key is not required.
        - unknown auth_mode:           always raises.
        - url must always be non-empty.

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
            if dest.auth_mode == "static":
                if not dest.api_key:
                    problems.append(f"{name}: missing api_key")
            elif dest.auth_mode == "entra":
                if not dest.auth_resource:
                    problems.append(f"{name}: missing auth_resource (required for auth_mode=entra)")
            else:
                problems.append(
                    f"{name}: unknown auth_mode {dest.auth_mode!r} (valid: 'static', 'entra')"
                )
        if problems:
            raise ValueError(
                f"context-intelligence destinations misconfigured: {', '.join(problems)}. "
                f"Set url and api_key (static) or auth_resource (entra) under "
                f"overrides.hook-context-intelligence.config.destinations.<name>."
            )
        return dests


# ---------------------------------------------------------------------------
# Backward-compat alias — import either name (HookConfigResolver is canonical)
# ---------------------------------------------------------------------------
ConfigResolver = HookConfigResolver
