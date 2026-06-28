"""ToolConfigResolver — lazy config resolver for CI tools in analytics-only mode.

Used by the tool-context-intelligence-query module (graph_query + blob_read tools)
when the hook-context-intelligence module is NOT mounted.  Constructed **eagerly** inside the tool's ``__init__``
(both tools always create a ``ToolConfigResolver`` at construction time).  Its
properties mirror HookConfigResolver for the shared config keys.

When ``hook-context-intelligence`` IS mounted it registers a
``HookConfigResolver`` as ``context_intelligence.hook_config_resolver``; the
tools then use ``resolve_query_endpoint(hook_resolver, tool_resolver)`` which
prefers the explicit read-config over the hook's upload destinations.

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

See resolve_query_endpoint() for the full three-tier fallback used by the tools.
"""

from __future__ import annotations

import logging
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


# ---------------------------------------------------------------------------
# Resolution helpers (free functions — shared by both tools)
# ---------------------------------------------------------------------------


def _first_entry(mapping: Any) -> Any | None:
    """First value of an insertion-ordered ``dict``, or None.

    Defensive: returns None when *mapping* is not a non-empty dict (e.g. a test
    double, or an unset attribute). Used for BOTH the tool's own ``sources``
    mapping and the hook's destinations so the 'first' rule is identical on
    both sides.
    """
    if not isinstance(mapping, dict) or not mapping:
        return None
    return next(iter(mapping.values()), None)


def _first_destination(hook_resolver: Any | None) -> Any | None:
    """First upload Destination on the hook resolver, or None."""
    if hook_resolver is None:
        return None
    return _first_entry(getattr(hook_resolver, "destinations", None))


def _pick(*candidates: tuple[str | None, str | None]) -> tuple[str | None, str | None]:
    """Return (value, source-label) for the first non-empty candidate, else (None, None)."""
    for value, source in candidates:
        if value:
            return value, source
    return None, None


def resolve_query_auth_strategy(
    hook_resolver: Any | None,
    tool_resolver: "ToolConfigResolver",
    api_key: str = "",
) -> Any:
    """Build an AuthStrategy for query tool requests.

    Lookup priority (mirrors resolve_query_endpoint field-by-field):
      1. first entry of tool_resolver.sources  (auth_mode / auth_resource)
      2. first upload destination on the hook resolver (auth_mode / auth_resource)
      3. env AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_MODE / _AUTH_RESOURCE (tier-3 fallback)

    The returned strategy always calls ``headers()`` per-request, so Entra tokens
    are refreshed by the azure-identity SDK when they near expiry.

    Parameters
    ----------
    hook_resolver:
        The hook's HookConfigResolver (may be None).
    tool_resolver:
        The tool's ToolConfigResolver.
    api_key:
        Resolved API key (from resolve_query_endpoint). Used for static mode.

    Returns
    -------
    AuthStrategy
        A built auth strategy (``ApiKeyAuth`` or ``EntraTokenAuth``).
    """
    from context_intelligence.auth import ApiKeyAuth, build_auth_strategy  # noqa: PLC0415

    read = _first_entry(tool_resolver.sources)
    dest = _first_destination(hook_resolver)

    # auth_mode / auth_resource: first non-empty source wins
    auth_mode: str = (
        (read.auth_mode if read else "")
        or (getattr(dest, "auth_mode", "") if dest else "")
        or _env("AUTH_MODE")
        or "static"
    )
    auth_resource: str = (
        (read.auth_resource if read else "")
        or (getattr(dest, "auth_resource", "") if dest else "")
        or _env("AUTH_RESOURCE")
        or ""
    )

    if auth_mode == "static":
        # Return an ApiKeyAuth even for empty key — same graceful-degrade behaviour as before.
        return ApiKeyAuth(api_key)

    # For entra (and any future mode), delegate to build_auth_strategy which raises loudly.
    return build_auth_strategy(
        auth_mode=auth_mode,
        api_key=api_key,
        auth_resource=auth_resource,
    )


def resolve_query_endpoint(
    hook_resolver: Any | None,
    tool_resolver: "ToolConfigResolver",
) -> tuple[str | None, str | None]:
    """Resolve (server_url, api_key) for the query path. Per-field independent.

    Explicit-first order (each field, first non-empty wins):
      1. first entry of tool_resolver.sources (.url / .api_key)
      2. first upload destination on the hook resolver (.url / .api_key)
      3. AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL / AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
    Returns (None, None)-able per field; each is None only if all three miss.

    Emits one DEBUG line naming which tier supplied each field.
    """
    read = _first_entry(tool_resolver.sources)
    dest = _first_destination(hook_resolver)

    url, url_src = _pick(
        ((read.url if read else None), f"source:{read.name}" if read else None),
        ((dest.url if dest else None), f"destination:{dest.name}" if dest else None),
        (_env("SERVER_URL"), "env:SERVER_URL"),
    )
    api_key, key_src = _pick(
        ((read.api_key if read else None), f"source:{read.name}" if read else None),
        ((dest.api_key if dest else None), f"destination:{dest.name}" if dest else None),
        (_env("API_KEY"), "env:API_KEY"),
    )

    log.debug(
        "CI query endpoint resolved: url<-%s api_key<-%s",
        url_src or "none",
        key_src or "none",
    )
    return (url or None, api_key or None)


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

        Note: the query path (resolve_query_endpoint) does NOT use this property
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

        Note: the query path (resolve_query_endpoint) does NOT use this property
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
        # at tier 3 in resolve_query_endpoint() so it never outranks the hook
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

    def validate_sources(self) -> dict[str, Source]:
        """Validate and return all configured sources. Fail-fast (mirrors validate_destinations).

        Per-source XOR auth validation:
        - auth_mode="static" (default): api_key must be non-empty.
        - auth_mode="entra":           auth_resource must be non-empty; api_key is not required.
        - unknown auth_mode:           always raises.
        - url must always be non-empty for explicitly configured sources.

        Empty sources dict is valid (no explicit read-config; fallback to hook destinations / env).

        Raises:
            ValueError: naming the offending source(s) and the empty field(s).
        Returns:
            The validated sources dict (possibly empty -> fallback to hook resolver / env, OK).
        """
        srcs = self.sources
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
        if problems:
            raise ValueError(
                f"context-intelligence sources misconfigured: {', '.join(problems)}. "
                f"Set url and api_key (static) or auth_resource (entra) under "
                f"overrides.tool-context-intelligence-query.config.sources.<name>."
            )
        return srcs

    @property
    def skill_sync_enabled(self) -> bool:
        """Whether the analytics path syncs watched skills on session start.

        Default ``False`` — opt-in; headless / single-command-series workflows
        pay zero skill traffic per turn unless explicitly enabled.  Set to
        ``true`` to restore the full per-session sync (``GET /version`` ping +
        conditional skill fetch + ``skill:unloaded`` reload handler).

        Resolution order (first *definite* value wins; empty / placeholder /
        unrecognized values are treated as *absent* and fall through):
        1. config['skill_sync_enabled']                       — mount() config dict
        2. coordinator.config['skill_sync_enabled']           — app-level override
        3. AMPLIFIER_CONTEXT_INTELLIGENCE_SKILL_SYNC_ENABLED   — env var
        4. False                                              — default (opt-in)

        Accepted string forms (case-insensitive): true/1/yes/on and
        false/0/no/off.  An unexpanded YAML placeholder that resolves to an
        empty string resolves to the default (``False``), never ``True`` — it
        cannot silently enable sync for everyone.
        """
        for raw in (
            _expand(self._config.get("skill_sync_enabled")),
            _expand(self._coordinator_config_get("skill_sync_enabled")),
            _env("SKILL_SYNC_ENABLED"),
        ):
            resolved = _coerce_bool(raw)
            if resolved is not None:
                return resolved
        return False
