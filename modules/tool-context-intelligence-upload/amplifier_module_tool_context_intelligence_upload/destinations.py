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

import sys
from pathlib import Path
from typing import Any

from amplifier_module_hook_context_intelligence.config_resolver import (
    Destination,
    HookConfigResolver,
)
from context_intelligence.config import (
    SETTINGS_PATH,
    _expand_env_placeholders,
    read_hook_config_block,
)

from .keys_env import load_keys_env_into_environ

_EXPANDABLE_DESTINATION_FIELDS = ("url", "api_key", "auth_resource")
_EXPANDABLE_LEGACY_KEYS = ("context_intelligence_server_url", "context_intelligence_api_key")


class DestinationSelectionError(Exception):
    """Raised when a destination cannot be resolved to exactly one entry."""


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
    4. Hand the expanded block to the hook's own ``HookConfigResolver`` and
       call ``validate_destinations()``, which parses the destinations map,
       performs legacy single-server synthesis
       (``{"default": Destination(..., include=("**",))}``) exactly as the
       hook does, AND drops any destination whose connection config is
       unusable.

    Using ``validate_destinations()`` rather than the raw ``destinations``
    property is what keeps this CLI honest with the live hook: ``mount()``
    validates, so an upload run must not accept a destination the hook
    itself would refuse. Since #85 this method degrades per-destination
    (logs and drops) instead of raising, so a single malformed entry costs
    only that entry -- preserving the "never raises for a malformed
    settings.yaml" contract below.

    Returns ``{}`` when nothing is configured; never raises for a missing or
    malformed settings.yaml.
    """
    load_keys_env_into_environ()
    config = read_hook_config_block(settings_path)
    if not config:
        return {}
    # coordinator=None is safe because HookConfigResolver only reaches the
    # coordinator through getattr(..., default=None) lookups
    # (config_resolver.py:167-195).
    return HookConfigResolver(_expand_hook_config(config), None).validate_destinations()


def _format_valid_names(destinations: dict[str, Destination]) -> str:
    return ", ".join(sorted(destinations)) if destinations else "(none configured)"


def select_destination(
    destinations: dict[str, Destination],
    requested_name: str | None,
    interactive: bool,
) -> Destination:
    """Select exactly one destination from *destinations*.

    Semantics:
    - 0 destinations configured -> raise ``DestinationSelectionError``; never
      silently proceed with nothing to upload to.
    - *requested_name* given -> return that entry, or raise
      ``DestinationSelectionError`` listing the valid names. An explicit
      request is NEVER silently redirected to a different destination.
    - exactly 1 destination configured -> auto-selected with NO prompt,
      regardless of *interactive*, because there is nothing to disambiguate.
    - 2+ destinations and *interactive* -> a numbered prompt is written to
      stderr and answered via ``input()``. stderr keeps the menu out of the
      machine-readable result JSON that this CLI writes to stdout (cli.py
      module docstring: "All human-facing progress output goes to stderr").
    - 2+ destinations and not *interactive* -> raise
      ``DestinationSelectionError`` listing the valid names; never block on
      stdin in a non-interactive context.

    Note this function handles destination DISAMBIGUATION only -- the
    separate upload safety confirmation ("Proceed? [y/N]") is a distinct
    concern and still applies even to a silently auto-selected single
    destination.
    """
    if not destinations:
        raise DestinationSelectionError(
            "No context-intelligence destinations are configured. Add one under "
            "overrides.hook-context-intelligence.config.destinations in "
            "~/.amplifier/settings.yaml, or pass --server-url/--api-key."
        )

    if requested_name is not None:
        if requested_name not in destinations:
            raise DestinationSelectionError(
                f"Unknown destination {requested_name!r}. "
                f"Configured destinations: {_format_valid_names(destinations)}."
            )
        return destinations[requested_name]

    if len(destinations) == 1:
        return next(iter(destinations.values()))

    if not interactive:
        raise DestinationSelectionError(
            f"{len(destinations)} destinations are configured and none was requested, "
            "but this is not an interactive terminal. Pass --destination <name>. "
            f"Configured destinations: {_format_valid_names(destinations)}."
        )

    return _prompt_for_destination(destinations)


def _prompt_for_destination(destinations: dict[str, Destination]) -> Destination:
    """Print a numbered menu of *destinations* and read one choice from stdin.

    Accepts either the 1-based menu number or the destination name. Names are listed
    in sorted order so the numbering is stable across runs (dict insertion order
    follows settings.yaml, which a user may reorder at any time).
    """
    names = sorted(destinations)
    print("Multiple context-intelligence destinations are configured:", file=sys.stderr)
    for index, name in enumerate(names, start=1):
        print(f"  {index}. {name}  ({destinations[name].url})", file=sys.stderr)
    sys.stderr.write(f"Select a destination [1-{len(names)}]: ")
    sys.stderr.flush()
    answer = input().strip()

    if answer.isdigit():
        choice = int(answer)
        if 1 <= choice <= len(names):
            return destinations[names[choice - 1]]
    if answer in destinations:
        return destinations[answer]

    raise DestinationSelectionError(
        f"Invalid selection {answer!r}. "
        f"Configured destinations: {_format_valid_names(destinations)}."
    )
