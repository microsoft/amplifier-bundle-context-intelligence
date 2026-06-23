"""ToolConfigResolver — lazy config resolver for CI tools in analytics-only mode.

Used by tool-graph-query and tool-blob-read when the hook-context-intelligence
module is NOT mounted.  Constructed **eagerly** inside the tool's ``__init__``
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

from context_intelligence.config import SETTINGS_PATH, _env, _parse_settings_yaml

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class ReadEndpoint(NamedTuple):
    """A single read-config entry (mirrors Destination shape, minus upload fields).

    url and api_key may be empty strings (→ falsy → that field falls through
    in the per-field resolution chain).
    """

    name: str
    url: str
    api_key: str


# ---------------------------------------------------------------------------
# Resolution helpers (free functions — shared by both tools)
# ---------------------------------------------------------------------------


def _first_entry(mapping: Any) -> Any | None:
    """First value of an insertion-ordered ``dict``, or None.

    Defensive: returns None when *mapping* is not a non-empty dict (e.g. a test
    double, or an unset attribute). Used for BOTH read_destinations and the hook's
    destinations so the 'first' rule is identical on both sides.
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


def resolve_query_endpoint(
    hook_resolver: Any | None,
    tool_resolver: "ToolConfigResolver",
) -> tuple[str | None, str | None]:
    """Resolve (server_url, api_key) for the query path. Per-field independent.

    Explicit-first order (each field, first non-empty wins):
      1. first entry of tool_resolver.read_destinations (.url / .api_key)
      2. first upload destination on the hook resolver (.url / .api_key)
      3. AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL / AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
    Returns (None, None)-able per field; each is None only if all three miss.

    Emits one DEBUG line naming which tier supplied each field.
    """
    read = _first_entry(tool_resolver.read_destinations)
    dest = _first_destination(hook_resolver)

    url, url_src = _pick(
        ((read.url if read else None), f"read_destinations:{read.name}" if read else None),
        ((dest.url if dest else None), f"destination:{dest.name}" if dest else None),
        (_env("SERVER_URL"), "env:SERVER_URL"),
    )
    api_key, key_src = _pick(
        ((read.api_key if read else None), f"read_destinations:{read.name}" if read else None),
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
    ``read_destinations`` mapping property for the explicit read-config model.
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._workspace: str | None = None
        self._read_destinations: dict[str, ReadEndpoint] | None = None

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
    # Scalar properties (used by legacy synthesis in read_destinations)
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
    def read_destinations(self) -> dict[str, ReadEndpoint]:
        """Parsed ``read_destinations`` mapping, or legacy-synthesized ``{"default": ...}``.

        Parsing rules:
        - If ``config["read_destinations"]`` key is **present**: parse it
          (may be empty dict {}). Each value must be a dict; ``url``/``api_key``
          are ``str(...).strip()``; non-dict entries are skipped.
        - If key is **absent**: apply legacy synthesis. If BOTH explicit
          ``context_intelligence_server_url`` and ``context_intelligence_api_key``
          are non-empty (from ``config`` dict or coordinator.config **only** —
          env and settings.yaml are excluded so env cannot enter tier 1), synthesize
          ``{"default": ReadEndpoint(...)}``. Otherwise return ``{}``.
          (Mirrors the hook's destinations D10 synthesis.)

        Cached after first access.
        """
        if self._read_destinations is not None:
            return self._read_destinations

        _sentinel = object()
        raw = self._config.get("read_destinations", _sentinel)
        key_present = raw is not _sentinel

        if key_present:
            result: dict[str, ReadEndpoint] = {}
            if isinstance(raw, dict):
                for name, spec in raw.items():
                    if not isinstance(spec, dict):
                        continue
                    url = str(spec.get("url", "") or "").strip()
                    api_key = str(spec.get("api_key", "") or "").strip()
                    result[name] = ReadEndpoint(name=name, url=url, api_key=api_key)
            self._read_destinations = result
            return self._read_destinations

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
            self._read_destinations = {
                "default": ReadEndpoint(name="default", url=legacy_url, api_key=legacy_key)
            }
        else:
            self._read_destinations = {}
        return self._read_destinations
