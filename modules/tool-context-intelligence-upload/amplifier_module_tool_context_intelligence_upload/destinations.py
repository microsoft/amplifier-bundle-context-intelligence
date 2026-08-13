"""Read fan-out destinations from settings.yaml and select among them.

This module has two responsibilities, both thin glue over existing machinery:

1. ``read_destinations`` reads the ``overrides.hook-context-intelligence.config``
   block the LIVE HOOK is configured under, expands ``${VAR}`` using an
   environment backed by ``~/.amplifier/keys.env``, and hands the block to the
   hook's own ``HookConfigResolver`` so destination parsing (including legacy
   single-server synthesis) is the hook's code, not a lookalike.

2. ``select_destination`` replicates the query tool's "1 -> auto-select,
   2+ -> disambiguate" semantics against a ``dict[str, Destination]``.

Scope note (design non-goal): ONLY the Amplifier home is read; project-local
``./.amplifier/settings.yaml`` is deliberately not consulted in v1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amplifier_module_hook_context_intelligence.config_resolver import (
    Destination,
    HookConfigResolver,
)
from context_intelligence.config import SETTINGS_PATH, _expand_env_placeholders

from .keys_env import load_keys_env_into_environ

_HOOK_OVERRIDE_KEY = "hook-context-intelligence"
_EXPANDABLE_DESTINATION_FIELDS = ("url", "api_key", "auth_resource")
_EXPANDABLE_LEGACY_KEYS = ("context_intelligence_server_url", "context_intelligence_api_key")


class DestinationSelectionError(Exception):
    """Raised when a destination cannot be resolved to exactly one entry."""


def _read_hook_config_block(settings_path: Path) -> dict[str, Any]:
    """Return ``overrides.hook-context-intelligence.config`` from *settings_path*.

    Returns ``{}`` when the file is missing, unparseable, or has no such
    block -- a malformed settings.yaml must never crash an upload run.
    """
    if not settings_path.is_file():
        return {}

    try:
        import yaml

        data = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}
    overrides = data.get("overrides")
    if not isinstance(overrides, dict):
        return {}
    hook_override = overrides.get(_HOOK_OVERRIDE_KEY)
    if not isinstance(hook_override, dict):
        return {}
    config = hook_override.get("config")
    return config if isinstance(config, dict) else {}


def _expand_hook_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *config* with ``${VAR}`` expanded in connection-bearing fields.

    In hook mode, app-cli performs this expansion before ``mount()``; the
    standalone CLI has no app-cli, so it does the identical step here.
    """
    expanded = dict(config)

    for key in _EXPANDABLE_LEGACY_KEYS:
        value = expanded.get(key)
        if isinstance(value, str):
            expanded[key] = _expand_env_placeholders(value)

    destinations = expanded.get("destinations")
    if isinstance(destinations, dict):
        rebuilt: dict[str, Any] = {}
        for name, spec in destinations.items():
            if not isinstance(spec, dict):
                # Preserve non-dict specs VERBATIM -- the hook resolver skips
                # them too, isolating a broken UNUSED destination.
                rebuilt[name] = spec
                continue
            new_spec = dict(spec)
            for field in _EXPANDABLE_DESTINATION_FIELDS:
                value = new_spec.get(field)
                if isinstance(value, str):
                    new_spec[field] = _expand_env_placeholders(value)
            rebuilt[name] = new_spec
        expanded["destinations"] = rebuilt

    return expanded


def read_destinations(settings_path: Path = SETTINGS_PATH) -> dict[str, Destination]:
    """Read and resolve fan-out destinations from *settings_path*.

    Steps:
    1. Load ``~/.amplifier/keys.env`` into the process environment (process
       env always wins) so ``${VAR}`` has something to expand against.
    2. Read the hook config block.
    3. Expand ``${VAR}`` in url/api_key/auth_resource and the two legacy
       scalars.
    4. Hand the expanded block to the hook's own ``HookConfigResolver``,
       which parses the destinations map AND performs legacy single-server
       synthesis (``{"default": Destination(..., include=("**",))}``)
       exactly as the hook does.

    Returns ``{}`` when nothing is configured; never raises for a missing or
    malformed settings.yaml.
    """
    load_keys_env_into_environ()
    config = _read_hook_config_block(settings_path)
    if not config:
        return {}
    # coordinator=None is safe because HookConfigResolver only reaches the
    # coordinator through getattr(..., default=None) lookups
    # (config_resolver.py:167-195).
    return HookConfigResolver(_expand_hook_config(config), None).destinations


def select_destination(
    destinations: dict[str, Destination], requested: str | None = None
) -> Destination:
    """Select exactly one destination from *destinations*.

    Semantics (matching the query tool):
    - *requested* given -> look it up by name; unknown name raises.
    - *requested* is None and exactly one destination configured -> auto-select it.
    - *requested* is None and 0 or 2+ destinations configured -> raise, naming
      the available destinations so the caller can disambiguate.
    """
    if requested is not None:
        try:
            return destinations[requested]
        except KeyError:
            raise DestinationSelectionError(
                f"Unknown destination {requested!r}. Available: {sorted(destinations)}"
            ) from None

    if len(destinations) == 1:
        return next(iter(destinations.values()))

    if not destinations:
        raise DestinationSelectionError("No destinations configured.")

    raise DestinationSelectionError(
        f"Multiple destinations configured ({sorted(destinations)}); specify one explicitly."
    )
