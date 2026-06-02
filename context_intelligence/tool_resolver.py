"""ToolConfigResolver — lazy config resolver for CI tools in analytics-only mode.

Used by tool-graph-query and tool-blob-read when the hook-context-intelligence
module is NOT mounted.  Constructed **eagerly** inside the tool's ``__init__``
(both tools always create a ``ToolConfigResolver`` at construction time).  Its
properties are evaluated lazily on each access.  The hook resolver
(``context_intelligence.hook_config_resolver`` coordinator capability), when
present, takes priority over ``ToolConfigResolver`` at call time.

Resolution priority for every property (mirrors HookConfigResolver for the
shared keys):

  1. mount() config dict                   — highest, from agent frontmatter
  2. coordinator.config                    — app-level programmatic override
  3. AMPLIFIER_CONTEXT_INTELLIGENCE_* env var
  4. ~/.amplifier/settings.yaml            — lowest-priority fallback
  5. default                               — built-in last resort

workspace resolution differs from HookConfigResolver by design:

  HookConfigResolver.workspace falls back to ``project_slug`` which is
  auto-derived from ``session.working_dir`` — a coordinator capability
  that only exists in an active capture session managed by the hook.

  ToolConfigResolver.workspace falls back to the env var then ``"default"``
  because in analytics-only mode there is no live capture session to derive
  a project slug from.  Set ``AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE``
  explicitly, or pass ``workspace`` in the tool's mount() config dict.

Properties: ``context_intelligence_server_url``, ``context_intelligence_api_key``,
``workspace``.  ``workspace`` is cached after first access; ``server_url`` and
``api_key`` are recomputed on each call (mirrors HookConfigResolver behaviour).
"""

from __future__ import annotations

from typing import Any

from context_intelligence.config import (  # type: ignore[attr-defined]
    SETTINGS_PATH,
    _env,
    _expand_env_placeholders,
    _parse_settings_yaml,
)

_DEFAULT_WORKSPACE = "default"


def _expand(value: Any) -> Any:
    """Expand shell-style ``${VAR}`` placeholders in *value* if it is a string.

    Returns the expanded string, or *value* unchanged when it is not a string.
    An unexpanded placeholder like ``${VAR:}`` with *VAR* unset expands to ``""``
    (falsy), letting the caller's ``or``-chain continue to the next source.
    """
    return _expand_env_placeholders(value) if isinstance(value, str) else value


class ToolConfigResolver:
    """Config resolver for CI tools — analytics-only mode (no hook mounted).

    Constructed eagerly in the tool's ``__init__`` (both tools always create a
    ``ToolConfigResolver`` at construction time, alongside ``_hook_resolver = None``).
    Its properties are evaluated lazily on each access.  At call time, the hook
    resolver (when present via the coordinator capability) takes priority; this
    class is only consulted when the hook capability is absent.  Reads
    ``server_url``, ``api_key``, and ``workspace`` using the same four-level
    priority chain as ``HookConfigResolver`` for those keys.
    See module docstring for the workspace asymmetry rationale.
    """

    def __init__(self, config: dict[str, Any], coordinator: Any) -> None:
        self._config = config
        self._coordinator = coordinator
        self._workspace: str | None = None  # cached after first access

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
    # Properties
    # ------------------------------------------------------------------

    @property
    def context_intelligence_server_url(self) -> str | None:
        """Server URL.

        Resolution order (first truthy value wins):
        1. config['context_intelligence_server_url']  — mount() config dict
        2. coordinator.config['context_intelligence_server_url']
        3. AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL env var
        4. ~/.amplifier/settings.yaml

        Shell-style placeholders (``${VAR:}``) in steps 1–2 are expanded
        against the environment before the truthiness check so that an
        unexpanded placeholder does not short-circuit the chain.
        """
        value = (
            _expand(self._config.get("context_intelligence_server_url"))
            or _expand(self._coordinator_config_get("context_intelligence_server_url"))
            or _env("SERVER_URL")
            or _parse_settings_yaml(SETTINGS_PATH).get("server_url")
        )
        return str(value) if value else None

    @property
    def context_intelligence_api_key(self) -> str | None:
        """API key.

        Resolution order (first truthy value wins):
        1. config['context_intelligence_api_key']  — mount() config dict
        2. coordinator.config['context_intelligence_api_key']
        3. AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY env var
        4. ~/.amplifier/settings.yaml

        Shell-style placeholders (``${VAR:}``) in steps 1–2 are expanded
        against the environment before the truthiness check so that an
        unexpanded placeholder does not short-circuit the chain.
        """
        value = (
            _expand(self._config.get("context_intelligence_api_key"))
            or _expand(self._coordinator_config_get("context_intelligence_api_key"))
            or _env("API_KEY")
            or _parse_settings_yaml(SETTINGS_PATH).get("api_key")
        )
        return str(value) if value else None

    @property
    def workspace(self) -> str:
        """Workspace identifier for scoping queries.

        Resolution order (first truthy value wins):
        1. config['workspace']              — mount() config dict
        2. coordinator.config['workspace']
        3. AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE env var
        4. 'default'                        — built-in fallback

        Note: does NOT fall back to project_slug / session.working_dir.
        That auto-derivation belongs to HookConfigResolver, which runs inside
        an active capture session.  In analytics-only mode set the env var or
        pass workspace explicitly via the tool's config.

        Shell-style placeholders (``${VAR:}``) in steps 1–2 are expanded
        against the environment before the truthiness check.

        Cached after first access.
        """
        if self._workspace is None:
            self._workspace = str(
                _expand(self._config.get("workspace"))
                or _expand(self._coordinator_config_get("workspace"))
                or _env("WORKSPACE")
                or _DEFAULT_WORKSPACE
            )
        return self._workspace
