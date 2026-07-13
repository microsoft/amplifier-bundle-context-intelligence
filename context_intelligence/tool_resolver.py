"""ToolConfigResolver — lazy config resolver for CI tools in analytics-only mode.

Used by the tool-context-intelligence-query module (graph_query + blob_read tools)
when the hook-context-intelligence module is NOT mounted.  Constructed **eagerly** inside the tool's ``__init__``
(both tools always create a ``ToolConfigResolver`` at construction time).  Its
properties mirror HookConfigResolver for the shared config keys.

When ``hook-context-intelligence`` IS mounted it registers a
``HookConfigResolver`` as ``context_intelligence.hook_config_resolver``; the
tools then use ``resolve_query_connection(hook_resolver, tool_resolver)`` which
resolves a SINGLE endpoint from the connectable pool (``sources`` ∪ hook
``destinations``), preferring the explicit read-config over the hook's upload
destinations.

Resolution priority for every scalar property (mirrors HookConfigResolver for
the shared config keys):
    1. ``config`` dict (mount config / settings.yaml overrides)
    2. ``coordinator.config`` dict (coordinator-level overrides)
    3. ``AMPLIFIER_CONTEXT_INTELLIGENCE_*`` environment variable
    4. ``~/.amplifier/settings.yaml`` (``overrides.hook-context-intelligence…``)

workspace resolution differs from HookConfigResolver by design:
    HookConfigResolver.workspace falls back to ``project_slug`` which is
    auto-derived from session.working_dir (a hook-only capability).
    ToolConfigResolver.workspace falls back to the env var then ``"default"``
    because there is no session.working_dir in analytics-only mode.

See resolve_query_connection() for the full connectable-pool resolution +
provenance contract used by the tools (docs/multi-source-build-spec-v5.md §4-5).
"""

from __future__ import annotations

import logging
import math
from typing import Any, NamedTuple

from context_intelligence.config import (
    SETTINGS_PATH,
    _env,
    _expand_env_placeholders,
    _parse_settings_yaml,
)

log = logging.getLogger(__name__)

#: Case-insensitive string tokens accepted for boolean config knobs.
_TRUE_TOKENS = frozenset({"true", "1", "yes", "on"})
_FALSE_TOKENS = frozenset({"false", "0", "no", "off"})

#: Default per-request HTTP timeout (seconds) for the read/query path -- matches
#: the sync helpers' existing ``timeout=30`` (client.py _http_post/_http_get).
_DEFAULT_REQUEST_TIMEOUT = 30.0


def _coerce_positive_float(value: Any, *, default: float, minimum: float) -> float:
    """Coerce *value* to a float >= *minimum*, tolerating bad operator input.

    Resolution:
    - None or unparseable strings ('', 'abc') -> *default* (never raises)
    - valid numbers / numeric strings -> float(value)
    - non-finite (inf/-inf/nan) -> *default* (an infinite/NaN timeout is a footgun:
      it would let a slow/hung server stall a query forever, exactly the failure
      mode Phase 0 exists to prevent)
    - result is then clamped to max(minimum, parsed)

    Deliberately duplicated from the hook's private
    ``amplifier_module_hook_context_intelligence.config_resolver._coerce_positive_float``
    (same discipline, e.g. ``dispatch_timeout``) rather than imported -- this
    package must not depend on the hook module (read-side / fan-in only; see
    docs/multi-source-build-spec-v5.md §1 guardrail 1).
    """
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(minimum, parsed)


def _expand(value: Any) -> Any:
    """Expand shell-style ``${VAR}`` placeholders in *value* if it is a string.

    Returns the expanded string, or *value* unchanged when it is not a string.
    An unexpanded placeholder like ``${VAR:}`` with *VAR* unset expands to ``""``
    (falsy), letting the caller's ``or``-chain continue to the next source.
    """
    return _expand_env_placeholders(value) if isinstance(value, str) else value


def _coerce_bool(value: Any) -> bool | None:
    """Three-state boolean coercion for config knobs.

    Returns ``True`` / ``False`` only when *value* is a definite, recognized
    boolean; returns ``None`` (meaning "absent — fall through to the next
    source / the default") for every other case.

    Critically, an **empty / whitespace-only string** resolves to ``None``,
    **never** ``False``.  This is what makes an unexpanded YAML placeholder
    (``"${AMPLIFIER_CONTEXT_INTELLIGENCE_SKILL_SYNC_ENABLED:}"`` with the env
    var unset, which expands to ``""``) behave as *absent* rather than silently
    disabling the knob for every user.  An unrecognized string is likewise
    treated as absent (safe fall-through) rather than guessed.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None  # empty / placeholder / whitespace → absent
    if text in _TRUE_TOKENS:
        return True
    if text in _FALSE_TOKENS:
        return False
    return None  # unrecognized → absent (fall through to default)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class Source(NamedTuple):
    """A single read-config entry (mirrors Destination shape, minus upload fields).

    url and api_key may be empty strings (→ falsy → that field falls through
    in the per-field resolution chain).

    auth_mode:     ``"static"`` (default) — use api_key as bearer token.
                   ``"entra"``            — acquire a delegated Entra token via azure-identity.
    auth_resource: Entra resource URI (e.g. ``api://<client_id>``). Required for auth_mode="entra".
    """

    name: str
    url: str
    api_key: str
    auth_mode: str = "static"
    auth_resource: str = ""


class SourceSelectionError(ValueError):
    """Raised when a caller-supplied (or absent) `source` selector cannot be resolved
    unambiguously against the configured `sources` map.

    Always a ValueError subclass so existing `except ValueError` call sites (if any)
    still catch it, but carries structured data so tool execute() methods can build a
    precise ToolResult error without re-parsing the message string.
    """

    def __init__(self, message: str, *, error_type: str, valid_names: list[str]) -> None:
        super().__init__(message)
        #: "unknown_source" | "ambiguous_source_selection" -- mirrors ToolResult.error["type"].
        self.error_type = error_type
        #: Sorted list of configured source names, for the caller to echo back verbatim.
        self.valid_names = valid_names


class PoolEntry(NamedTuple):
    """One entry in the connectable pool (a tool ``source`` or a hook ``destination``).

    See ``_connectable_pool()`` -- the pool is ``sources`` union hook ``destinations``,
    used ONLY for explicit selection (``source=<name>``) and listing
    (``list_sources: true``). It never widens the DEFAULT (no-pointer) resolution
    path -- see ``resolve_query_connection()``.
    """

    name: str
    url: str
    api_key: str
    auth_mode: str
    auth_resource: str
    kind: str  # "source" | "destination"


class EndpointOrigin(NamedTuple):
    """Provenance of a resolved connection -- surfaced to the user.

    ``name``: the entry name ("prod" / "default" / "" when resolved purely from env).
    ``url``:  the resolved server URL (matches the endpoint that actually answers).
    ``kind``: "source" | "destination" | "env".
    """

    name: str
    url: str
    kind: str


class ResolvedConnection(NamedTuple):
    """Result of ``resolve_query_connection()`` -- a SINGLE resolved endpoint,
    its auth strategy, and its provenance."""

    url: str | None
    api_key: str | None
    auth_strategy: Any
    origin: EndpointOrigin | None  # None only when url is None


# ---------------------------------------------------------------------------
# Resolution helpers (free functions — shared by both tools)
# ---------------------------------------------------------------------------


def _select_source(
    sources: dict[str, Source],
    requested_name: str | None,
) -> Source | None:
    """Select which configured ``sources`` entry a caller wants (criteria 1-3).

    Parameters
    ----------
    sources:
        ``tool_resolver.sources`` -- the parsed, name-keyed map.
    requested_name:
        The caller's explicit ``source`` input (``input.get("source")``), or ``None``
        if the caller didn't pass one.

    Returns
    -------
    Source | None
        - ``None`` only when ``sources`` is empty AND ``requested_name`` is ``None``
          -- unchanged legacy behavior: caller falls through to tier 2 (hook
          destination) / tier 3 (env).
        - The matching ``Source`` in every other case.

    Raises
    ------
    SourceSelectionError
        - ``error_type="unknown_source"``: ``requested_name`` is not ``None`` and is
          not a key in ``sources`` (whether ``sources`` is empty or non-empty). An
          explicit request is NEVER silently redirected to a different endpoint
          (criterion 2) -- not even the hook's upload destination.
        - ``error_type="ambiguous_source_selection"``: ``requested_name`` is ``None``
          and 2+ sources are configured (criterion 3). This is unconditional -- there
          is no implicit "use the first entry" fallback for any caller.
    """
    if requested_name is not None:
        if requested_name not in sources:
            raise SourceSelectionError(
                f"context-intelligence: unknown source {requested_name!r}. "
                f"Configured sources: "
                f"{', '.join(sorted(sources)) if sources else '(none configured)'}.",
                error_type="unknown_source",
                valid_names=sorted(sources),
            )
        return sources[requested_name]

    if not sources:
        return None  # unchanged: tier 1 empty -> fall through to tier 2 / tier 3

    if len(sources) == 1:
        return next(iter(sources.values()))  # single entry -- no ambiguity, no selector needed

    raise SourceSelectionError(
        f"context-intelligence: {len(sources)} sources are configured "
        f"({', '.join(sorted(sources))}) but no `source` was specified. "
        f"Pass source=<name> to select one.",
        error_type="ambiguous_source_selection",
        valid_names=sorted(sources),
    )


def _origin_dict(origin: EndpointOrigin | None) -> dict[str, str] | None:
    """Render an ``EndpointOrigin`` as the JSON-safe ``source`` field callers put on
    every ``ToolResult`` (success AND failure) so the endpoint that answered / was
    attempted is always visible to the user -- see spec §5."""
    if origin is None:
        return None
    return {"name": origin.name, "url": origin.url, "origin": origin.kind}


def _connectable_pool(
    tool_resolver: "ToolConfigResolver",
    hook_resolver: Any | None,
) -> dict[str, PoolEntry]:
    """Ordered pool: tool ``sources`` (config order) THEN hook ``destinations``
    (config order). SOURCE WINS on name collision (both the tool's legacy
    synthesis and the hook's D10 synthesis mint an entry literally named
    ``"default"`` -- the source-side one shadows the destination-side one,
    consistent with the source-outranks-destination precedence below).

    Tolerates ``hook_resolver is None`` (pre-hook-mount) -> sources only.

    This is the connectable SET used for explicit selection (``source=<name>``,
    which can now name a destination) and listing (``list_sources: true``). It
    is NEVER used to widen the default (no-pointer) resolution path -- see
    ``resolve_query_connection()``.
    """
    pool: dict[str, PoolEntry] = {}
    for s in tool_resolver.sources.values():  # sources first
        pool[s.name] = PoolEntry(s.name, s.url, s.api_key, s.auth_mode, s.auth_resource, "source")
    dests = getattr(hook_resolver, "destinations", None) if hook_resolver is not None else None
    if isinstance(dests, dict):
        for d in dests.values():
            if d.name in pool:  # SOURCE WINS -- do not overwrite
                continue
            pool[d.name] = PoolEntry(
                d.name,
                d.url,
                d.api_key,
                getattr(d, "auth_mode", "") or "static",
                getattr(d, "auth_resource", "") or "",
                "destination",
            )
    return pool


def _select_from_pool(
    pool: dict[str, PoolEntry],
    requested_name: str | None,
) -> PoolEntry | None:
    """Explicit name -> resolve against the WHOLE pool (source OR destination).

    No name -> returns ``None``; the caller applies default (no-pointer) semantics,
    which never widen to the pool (see ``resolve_query_connection()``).

    Raises
    ------
    SourceSelectionError
        ``error_type="unknown_source"`` -- *requested_name* is not ``None`` and is
        not a key in *pool*. Lists the WHOLE pool (source + destination names),
        not just the tool's own sources, since a caller can now name either.
    """
    if requested_name is not None:
        if requested_name not in pool:
            raise SourceSelectionError(
                f"context-intelligence: unknown source {requested_name!r}. "
                f"Connectable set: {', '.join(sorted(pool)) if pool else '(none configured)'}.",
                error_type="unknown_source",
                valid_names=sorted(pool),
            )
        return pool[requested_name]
    return None


def resolve_query_connection(
    hook_resolver: Any | None,
    tool_resolver: "ToolConfigResolver",
    *,
    source_name: str | None = None,
) -> ResolvedConnection:
    """Select ONE endpoint, build its auth strategy, and report its provenance.

    SINGLE-HIT -- never queries more than one endpoint. Replaces the pair
    ``resolve_query_endpoint`` / ``resolve_query_auth_strategy`` (each of which
    re-ran selection independently and discarded the origin); this consolidates
    selection into one pass and returns the origin the caller needs to surface
    provenance to the user (docs/multi-source-build-spec-v5.md §4-5).

    Resolution:
      1. ``source_name`` given -> resolve against the WHOLE connectable pool
         (``sources`` union hook ``destinations`` -- §4.1/4.2). Unknown name fails
         loud, listing the whole pool.
      2. No name -> DEFAULT semantics, UNCHANGED from #67 (no tightening):
         - 1 source configured -> use it.
         - 2+ sources configured -> fail loud (``ambiguous_source_selection``,
           names sources). This is the ONLY default-path ambiguity that fails
           loud (Brian's #67 rule).
         - 0 sources, N destinations -> use the FIRST destination in config order
           (the established read-fallback pool; destinations are the read pool
           when no sources are configured). This does NOT fail loud regardless of
           how many destinations exist -- source provenance (origin="destination")
           makes the pick visible to the user, so there is no silent-selection
           concern. Read-only: only reads ``hook_resolver.destinations``.
         - 0 sources, 0 destinations -> fall through to env (tier 3).

    RATIFIED RULE (user override of spec §4.4): the earlier "tightening" that
    failed loud on 0 sources + 2+ destinations was reverted -- destinations-as-
    fallback keeps first-destination-wins; only 2+ SOURCES fails loud.
      3. Selected ``source``-kind entry is validated via
         ``tool_resolver.validate_source()`` (criterion 4, per-entry fail-loud).
         Selected ``destination``-kind entry is consumed as-is (the hook owns
         destination validation on its own write path; we do not call it).
      4. url/api_key/auth_mode/auth_resource each fall back to env
         (``AMPLIFIER_CONTEXT_INTELLIGENCE_*``) only when the selected entry's own
         field is empty (tier-3 preserved per-field).

    Raises
    ------
    SourceSelectionError
        Selection is ambiguous or names an unconfigured/unknown entry.
    ValueError
        The *selected* source itself fails per-field validation (criterion 4) --
        message names ONLY that one source, never the whole map.
    """
    from context_intelligence.auth import ApiKeyAuth, build_auth_strategy  # noqa: PLC0415

    pool = _connectable_pool(tool_resolver, hook_resolver)
    entry = _select_from_pool(pool, source_name)

    if entry is None and source_name is None:
        selected_source = _select_source(tool_resolver.sources, None)
        if selected_source is not None:
            entry = pool.get(selected_source.name)
        else:
            # 0 sources configured -- destinations are the established read-fallback
            # pool: pick the FIRST destination in config order (RATIFIED RULE, user
            # override of spec §4.4). This does NOT fail loud regardless of how many
            # destinations exist -- provenance (origin="destination") makes the pick
            # visible. Read-only: only reads hook_resolver.destinations via the pool.
            entry = next(
                (e for e in pool.values() if e.kind == "destination"),
                None,  # 0 destinations too -> entry stays None -> pure env tier below.
            )

    if entry is not None:
        if entry.kind == "source":
            tool_resolver.validate_source(entry.name)  # raises ValueError naming only this entry
        # destination-kind: consumed as-is -- the hook owns destination validation
        # on its own write path (validate_destinations); we never call it (read-only).
        url = entry.url or _env("SERVER_URL")
        api_key = entry.api_key or _env("API_KEY")
        auth_mode = entry.auth_mode or _env("AUTH_MODE") or "static"
        auth_resource = entry.auth_resource or _env("AUTH_RESOURCE") or ""
        origin = EndpointOrigin(entry.name, url, entry.kind) if url else None
    else:
        url = _env("SERVER_URL")
        api_key = _env("API_KEY")
        auth_mode = _env("AUTH_MODE") or "static"
        auth_resource = _env("AUTH_RESOURCE") or ""
        origin = EndpointOrigin("", url, "env") if url else None

    if auth_mode == "static":
        # Return an ApiKeyAuth even for empty key — same graceful-degrade behaviour as before.
        auth_strategy: Any = ApiKeyAuth(api_key or "")
    else:
        # For entra (and any future mode), delegate to build_auth_strategy which raises loudly.
        auth_strategy = build_auth_strategy(
            auth_mode=auth_mode,
            api_key=api_key or "",
            auth_resource=auth_resource,
        )

    return ResolvedConnection(
        url=url or None,
        api_key=api_key or None,
        auth_strategy=auth_strategy,
        origin=origin,
    )


# ---------------------------------------------------------------------------
# ToolConfigResolver
# ---------------------------------------------------------------------------


class ToolConfigResolver:
    """Lazy config resolver for CI query tools (graph-query, blob-read).

    Created eagerly at tool construction time alongside ``_hook_resolver = None``.
    Provides scalar config properties that mirror HookConfigResolver and a new
    ``sources`` mapping property for the explicit read-config model.
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._workspace: str | None = None
        self._sources: dict[str, Source] | None = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _coordinator_config_get(self, key: str) -> Any:
        """Safely read *key* from coordinator.config.

        Returns ``None`` if the coordinator has no ``.config`` attribute or
        if the key is absent from it.  Mirrors HookConfigResolver._coordinator_config_get.
        """
        coord_config = getattr(self._coordinator, "config", None)
        if not isinstance(coord_config, dict):
            return None
        return coord_config.get(key)

    # ------------------------------------------------------------------
    # Scalar properties (used by legacy synthesis in sources)
    # ------------------------------------------------------------------

    @property
    def context_intelligence_server_url(self) -> str | None:
        """Server URL: config → coordinator.config → env → settings.yaml.

        Note: the query path (resolve_query_connection) does NOT use this property
        for env resolution. Env is consulted only at tier 3 via _env("SERVER_URL").
        Kept for PR #27 API parity.
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
        """API key: config → coordinator.config → env → settings.yaml.

        Note: the query path (resolve_query_connection) does NOT use this property
        for env resolution. Env is consulted only at tier 3 via _env("API_KEY").
        Kept for PR #27 API parity.
        """
        value = (
            self._config.get("context_intelligence_api_key")
            or self._coordinator_config_get("context_intelligence_api_key")
            or _env("API_KEY")
            or _parse_settings_yaml(SETTINGS_PATH).get("api_key")
        )
        return str(value) if value else None

    @property
    def workspace(self) -> str:
        """Workspace: config → coordinator.config → env → "default".

        Deliberately does NOT fall back to project_slug (which requires
        session.working_dir, a hook-only capability).
        """
        if self._workspace is None:
            raw = (
                self._config.get("workspace")
                or self._coordinator_config_get("workspace")
                or _env("WORKSPACE")
                or "default"
            )
            self._workspace = str(raw)
        return self._workspace

    @property
    def request_timeout(self) -> float:
        """Per-request HTTP timeout (seconds) for the read/query path.

        Resolution: config['request_timeout'] -> coordinator.config -> env
        (``AMPLIFIER_CONTEXT_INTELLIGENCE_QUERY_TIMEOUT``) -> default 30.0.

        Coercion (never raises -- see ``_coerce_positive_float``), and note the
        two distinct fallbacks, which are deliberate:
        - Missing / unparseable / non-finite (None, "", "abc", inf, nan) -> the
          30.0s DEFAULT.
        - A parseable but non-positive value (0, negative) -> CLAMPED UP to the
          0.1s floor (``minimum=0.1``), NOT the 30.0s default. So configuring
          ``request_timeout: 0`` yields 0.1s, not 30s -- a deliberate, documented
          behavior (a positive number the operator wrote is honored as "as small
          as allowed" rather than silently reset to the default), and the 0.1s
          floor still prevents a zero/negative timeout from disabling the guard.

        Not cached: cheap to compute and mirrors the other scalar properties
        reading fresh each access except workspace (which caches by design).
        """
        raw = (
            self._config.get("request_timeout")
            if self._config.get("request_timeout") is not None
            else self._coordinator_config_get("request_timeout")
        )
        if raw is None:
            raw = _env("QUERY_TIMEOUT")
        return _coerce_positive_float(raw, default=_DEFAULT_REQUEST_TIMEOUT, minimum=0.1)

    # ------------------------------------------------------------------
    # Read-config mapping (the new explicit read-config model)
    # ------------------------------------------------------------------

    @property
    def sources(self) -> dict[str, Source]:
        """Parsed ``sources`` mapping, or legacy-synthesized ``{"default": ...}``.

        Parsing rules:
        - If ``config["sources"]`` key is **present**: parse it
          (may be empty dict {}). Each value must be a dict; ``url``/``api_key``
          are ``str(...).strip()``; non-dict entries are skipped.
        - If key is **absent**: apply legacy synthesis. If BOTH explicit
          ``context_intelligence_server_url`` and ``context_intelligence_api_key``
          are non-empty (from ``config`` dict or coordinator.config **only** —
          env and settings.yaml are excluded so env cannot enter tier 1), synthesize
          ``{"default": Source(...)}``. Otherwise return ``{}``.
          (Mirrors the hook's destinations D10 synthesis.)

        Cached after first access.
        """
        if self._sources is not None:
            return self._sources

        _sentinel = object()
        raw = self._config.get("sources", _sentinel)
        key_present = raw is not _sentinel

        if key_present:
            result: dict[str, Source] = {}
            if isinstance(raw, dict):
                for name, spec in raw.items():
                    if not isinstance(spec, dict):
                        continue
                    url = str(_expand(spec.get("url", "") or "")).strip()
                    api_key = str(_expand(spec.get("api_key", "") or "")).strip()
                    auth_mode = str(_expand(spec.get("auth_mode", "static") or "static")).strip()
                    auth_resource = str(_expand(spec.get("auth_resource", "") or "")).strip()
                    result[name] = Source(
                        name=name,
                        url=url,
                        api_key=api_key,
                        auth_mode=auth_mode,
                        auth_resource=auth_resource,
                    )
            self._sources = result
            return self._sources

        # Key absent: legacy synthesis from EXPLICIT config only.
        # env and settings.yaml are intentionally excluded — env is consulted only
        # at tier 3 in resolve_query_connection() so it never outranks the hook
        # destination (tier 2).
        legacy_url = self._config.get(
            "context_intelligence_server_url"
        ) or self._coordinator_config_get("context_intelligence_server_url")
        legacy_key = self._config.get(
            "context_intelligence_api_key"
        ) or self._coordinator_config_get("context_intelligence_api_key")
        if legacy_url and legacy_key:
            self._sources = {"default": Source(name="default", url=legacy_url, api_key=legacy_key)}
        else:
            self._sources = {}
        return self._sources

    def validate_sources(self) -> list[str]:
        """Best-effort validation pass over ALL configured sources -- WARN, never raise.

        BREAKING CHANGE (criterion 4, docs/designs/workstream-1-multi-source-query-tools.md):
        previously this method raised ValueError naming EVERY problem across the WHOLE
        sources map, and was called unconditionally at mount() time -- one bad entry
        blocked ALL queries, including ones the caller never intended to touch. It is now
        a non-fatal diagnostic pass: still runs at mount() (so operators still see typos
        immediately in logs), but only WARNS. Hard, fail-loud validation of the specific
        source a query actually targets now happens per-query via validate_source(name)
        (below), called from resolve_query_connection() only for the ONE selected entry.

        Per-source XOR auth rules (unchanged from before):
        - auth_mode="static" (default): api_key must be non-empty.
        - auth_mode="entra":           auth_resource must be non-empty; api_key is not required.
        - unknown auth_mode:           always a problem.
        - url must always be non-empty for explicitly configured sources.

        Empty sources dict is valid (no explicit read-config; fallback to hook destinations / env).

        Returns:
            The list of problem strings found (possibly empty). Never raises.
        """
        problems = self._collect_source_problems(self.sources)
        if problems:
            log.warning(
                "context-intelligence sources misconfigured (mount-time diagnostic only "
                "-- queries against OTHER, correctly configured sources are unaffected; "
                "hard validation is now per-source at query time): %s. "
                "Set url and api_key (static) or auth_resource (entra) under "
                "overrides.tool-context-intelligence-query.config.sources.<name>.",
                ", ".join(problems),
            )
        return problems

    def validate_source(self, name: str) -> Source:
        """Validate and return exactly ONE named source. Fail-fast for JUST this entry.

        This is the hard, query-time gate (criterion 4): a misconfigured entry only ever
        blocks queries that target IT, never its siblings.

        Raises
        ------
        KeyError
            `name` is not in ``self.sources``. (Callers should always pass a name that
            already came from ``_select_source`` / ``self.sources``, so this should be
            unreachable in practice -- it is not the caller-facing "unknown source"
            error, which is ``SourceSelectionError`` and is raised earlier, in
            ``_select_source``, before this method is ever called.)
        ValueError
            The named source fails per-field validation. Message names ONLY `name`.
        """
        src = self.sources[name]
        problems = self._collect_source_problems({name: src})
        if problems:
            raise ValueError(
                f"context-intelligence source {name!r} misconfigured: {', '.join(problems)}. "
                f"Set url and api_key (static) or auth_resource (entra) under "
                f"overrides.tool-context-intelligence-query.config.sources.{name}."
            )
        return src

    @staticmethod
    def _collect_source_problems(srcs: dict[str, Source]) -> list[str]:
        """Shared per-field XOR check, extracted so validate_sources()/validate_source()
        apply IDENTICAL rules to the whole map vs. a single entry (criterion 4 requires
        they diverge only in *scope* -- whole-map vs one-entry -- never in *rule*)."""
        problems: list[str] = []
        for name, src in srcs.items():
            if not src.url:
                problems.append(f"{name}: missing url")
            if src.auth_mode == "static":
                if not src.api_key:
                    problems.append(f"{name}: missing api_key")
            elif src.auth_mode == "entra":
                if not src.auth_resource:
                    problems.append(f"{name}: missing auth_resource (required for auth_mode=entra)")
            else:
                problems.append(
                    f"{name}: unknown auth_mode {src.auth_mode!r} (valid: 'static', 'entra')"
                )
        return problems
