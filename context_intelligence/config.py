"""Configuration resolution for context-intelligence.

Resolution chain: CLI args → environment variables → ``~/.amplifier/settings.yaml`` → defaults.

Usage::

    from context_intelligence.config import resolve_config

    server_url, api_key = resolve_config(
        server_url=args.server_url,
        api_key=args.api_key,
    )
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("context_intelligence.config")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_SCHEMA = {"name": "amplifier.log", "ver": "1.0.0"}
AMPLIFIER_DIR = Path.home() / ".amplifier"
SETTINGS_PATH = AMPLIFIER_DIR / "settings.yaml"

# The settings.yaml key the live hook is configured under. Both the hook and the
# standalone upload CLI read their config from this one block.
_HOOK_OVERRIDE_KEY = "hook-context-intelligence"

#: The ONE canonical reader-side default root for context-intelligence captures.
#: All readers fall back to this when AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH is unset.
DEFAULT_BASE_PATH = Path.home() / ".amplifier" / "projects"


# ---------------------------------------------------------------------------
# Shared env-var helpers (used by HookConfigResolver and ToolConfigResolver)
# ---------------------------------------------------------------------------

#: Environment variable prefix shared by all CI configuration.
#: ``AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE``  → workspace
#: ``AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL`` → context_intelligence_server_url
#: etc.
_ENV_PREFIX = "AMPLIFIER_CONTEXT_INTELLIGENCE_"


def _env(suffix: str) -> str | None:
    """Read ``AMPLIFIER_CONTEXT_INTELLIGENCE_<SUFFIX>`` from the environment.

    Returns the value as a string if set and non-empty, otherwise ``None``.
    """
    value = os.environ.get(_ENV_PREFIX + suffix)
    return value if value else None


def canonicalize_base_path(raw: str | Path | None) -> Path:
    """Canonicalise a raw base-path value to a **guaranteed absolute** :class:`Path`.

    Four rules applied in order (§D.2):

    1. Convert to string and strip whitespace.  ``None`` → empty string.
    2. Empty string → :data:`DEFAULT_BASE_PATH` (never anchored to CWD).
    3. Expand ``~`` via :meth:`~pathlib.Path.expanduser`.
    4. If the result is still relative → warn and fall back to
       :data:`DEFAULT_BASE_PATH`.  Relative paths are invalid: each OS process
       has its own CWD, so a relative root produces *different directories* for
       different processes even when the string is byte-identical.

    No ``os.path.normpath`` or CWD-anchoring — pathlib already drops trailing
    slashes; absoluteness, not normalisation, is the load-bearing property.

    .. important:: **Duplicated by design.** The fold gate forbids the hook's
       ``config_resolver.py`` from importing this package, so the SAME rules are
       inlined in ``HookConfigResolver.base_path``.  The two copies MUST stay
       byte-equivalent; ``tests/test_base_path_parity.py`` pins writer ≡ reader.
       If you edit one, edit the other and the parity test.

    Parameters
    ----------
    raw:
        A raw string, :class:`~pathlib.Path`, or ``None``.

    Returns
    -------
    Path
        An absolute :class:`~pathlib.Path`.  Never relative, never empty.
    """
    s = str(raw).strip() if raw is not None else ""
    if not s:
        return DEFAULT_BASE_PATH
    p = Path(s).expanduser()
    if not p.is_absolute():
        log.warning(
            "base_path %r is not absolute; using default %s",
            s,
            DEFAULT_BASE_PATH,
        )
        return DEFAULT_BASE_PATH
    return p


def context_intelligence_base_path() -> Path:
    """Reader-side root for context-intelligence captures.

    Reads ``AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH`` from the environment via
    :func:`_env` (which returns ``None`` for both unset and empty) and passes the
    result through :func:`canonicalize_base_path`, which guarantees an absolute
    path and falls back to :data:`DEFAULT_BASE_PATH` for empty or relative values.

    Mirrors the shell idiom::

        ${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:-~/.amplifier/projects}

    This helper is **gate-safe**: it lives in ``config.py``, which already imports
    ``os``.  It does **not** touch ``config_resolver.py`` (fold-discipline gate).
    """
    return canonicalize_base_path(_env("BASE_PATH"))


def reader_writer_roots_disagree(
    env_raw: str | None,
    writer_base_path: str | Path,
) -> tuple[bool, Path, Path]:
    """Compare the reader root against the writer root (§C.3 consistency check).

    Pure, side-effect-free core of the startup consistency check in the hook's
    ``on_session_ready``.  Extracted here (rather than left inline) so the
    divergence condition is **unit-testable** without importing the hook package
    (which needs ``amplifier_core`` at import time).

    Both operands pass through the SAME :func:`canonicalize_base_path`, so the
    comparison is symmetric: a relocated *writer* whose root the env-only readers
    cannot see produces ``disagree=True``.

    Parameters
    ----------
    env_raw:
        The raw ``AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH`` value (``None`` when
        unset/empty) — exactly what every reader resolves from.
    writer_base_path:
        The writer's resolved ``base_path`` (e.g. ``resolver.base_path``).

    Returns
    -------
    tuple[bool, Path, Path]
        ``(disagree, reader_root, writer_root)``.  ``disagree`` is ``True`` when
        the canonicalized roots differ — the caller should then warn LOUD.
    """
    reader_root = canonicalize_base_path(env_raw)
    writer_root = canonicalize_base_path(str(writer_base_path))
    return reader_root != writer_root, reader_root, writer_root


# ---------------------------------------------------------------------------
# Shared capture-path helpers (canonical capture definition — §D.1)
# ---------------------------------------------------------------------------

#: Fixed-shape glob (relative to a ``sessions/`` directory) that matches
#: exactly the files the writer produces.  One capture =
#: ``<sessions_dir>/<session_id>/context-intelligence/events.jsonl``.
#:
#: The ``events.jsonl`` **file** is the discriminator — a bare
#: ``context-intelligence/`` directory without the file is not a recoverable
#: capture and must not be counted.  Amplifier core's
#: ``sessions/<id>/metadata.json`` has no ``context-intelligence/`` segment and
#: is excluded by construction.
CAPTURE_GLOB = "*/context-intelligence/events.jsonl"


def capture_paths_under_sessions_dir(sessions_dir: Path) -> list[Path]:
    """Return all capture paths under a project ``sessions/`` directory.

    Uses the fixed-shape :data:`CAPTURE_GLOB` — **not** a recursive ``**``
    glob — so only the writer's real output layout is matched.

    Parameters
    ----------
    sessions_dir:
        The ``<base>/<project_slug>/sessions`` directory to scan.

    Returns
    -------
    list[Path]
        Sorted list of ``events.jsonl`` file paths, one per qualifying session
        (including subsessions, which are flat siblings under ``sessions/``).
        ``session_id`` for any path ``p`` is ``p.parent.parent.name``.
    """
    return sorted(sessions_dir.glob(CAPTURE_GLOB))


# ---------------------------------------------------------------------------
# Shell-style placeholder expander (used by ToolConfigResolver)
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\$\{([^}:]+)(?::([^}]*))?}")


def _expand_env_placeholders(value: str) -> str:
    """Expand shell-style ``${VAR}``, ``${VAR:}``, ``${VAR:default}`` placeholders.

    - ``${VAR}`` — replaced with ``os.environ[VAR]`` if set, else ``""``.
    - ``${VAR:}`` — same as ``${VAR}`` (empty default when var is unset).
    - ``${VAR:default}`` — replaced with ``os.environ[VAR]`` if set, else ``"default"``.
    - Non-placeholder strings pass through unchanged.

    Note: ``os.path.expandvars`` does **not** support the ``${VAR:default}``
    colon syntax used by the agent behavior YAML files shipped with this bundle,
    hence this small regex-based helper.
    """

    def _replace(m: re.Match[str]) -> str:
        var_name = m.group(1)
        default = m.group(2) if m.group(2) is not None else ""
        return os.environ.get(var_name, default)

    return _PLACEHOLDER_RE.sub(_replace, value)


# ---------------------------------------------------------------------------
# Settings.yaml parser
# ---------------------------------------------------------------------------


def read_hook_config_block(path: Path) -> dict[str, Any]:
    """Return the ``overrides.hook-context-intelligence.config`` block from *path*.

    This is the single settings.yaml reader for the bundle. Returns ``{}`` when
    the file is missing, unparseable, or has no such block -- a malformed
    settings.yaml must never crash a caller.

    PyYAML is optional here on purpose: this package declares only
    ``azure-identity`` as a runtime dependency, so ``context_intelligence`` can
    be installed without PyYAML. The line-based fallback covers the flat scalar
    keys we need in that case.
    """
    if not path.is_file():
        return {}

    try:
        import yaml
    except ImportError:
        return _crude_hook_config_block(path)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a malformed settings.yaml must never crash a caller
        log.debug("Could not parse %s: %s", path, exc)
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


def _crude_hook_config_block(path: Path) -> dict[str, Any]:
    """Line-based fallback for when PyYAML is unavailable.

    Collects flat ``key: value`` pairs nested under the
    ``hook-context-intelligence`` section. Good enough for the scalar
    connection keys; nested structures are not represented.
    """
    result: dict[str, Any] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result

    in_ci_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _HOOK_OVERRIDE_KEY in stripped:
            in_ci_section = True
            continue
        if not in_ci_section:
            continue
        # A non-indented line ends the section.
        if not line.startswith((" ", "\t")):
            in_ci_section = False
            continue
        if stripped.endswith(":"):
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        result[key.strip()] = value.strip().strip("'\"")
    return result


def _parse_settings_yaml(path: Path) -> dict:
    """Projection of :func:`read_hook_config_block` onto the connection keys.

    Kept as a distinct function because ``resolve_config`` and ``tool_resolver``
    want the flattened ``{"server_url", "api_key"}`` shape, not the raw block.
    """
    config = read_hook_config_block(path)
    result: dict[str, str] = {}
    if "context_intelligence_server_url" in config:
        result["server_url"] = config["context_intelligence_server_url"]
    if "context_intelligence_api_key" in config:
        result["api_key"] = config["context_intelligence_api_key"]
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_config(
    *,
    server_url: str | None = None,
    api_key: str | None = None,
    auth_mode: str = "static",
) -> tuple[str, str]:
    """Resolve server URL and API key.

    Resolution order:
    1. Explicit arguments (from CLI flags)
    2. Environment variables (``AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL``,
       ``AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY``)
    3. ``~/.amplifier/settings.yaml``

    Parameters
    ----------
    server_url:
        Base URL of the CI server (overrides env var / settings.yaml).
    api_key:
        API key for static-mode auth (overrides env var / settings.yaml).
    auth_mode:
        ``"static"`` (default) — api_key is required.
        ``"entra"`` — api_key is optional; token is acquired via azure-identity.

    Returns:
        ``(server_url, api_key)`` tuple.  In entra mode ``api_key`` may be an
        empty string — callers must use an ``AuthStrategy`` to build the header.

    Raises:
        SystemExit: if server_url cannot be resolved, or if api_key is missing
        in static mode.
    """
    resolved_url = server_url or os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL")
    resolved_key = api_key or os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY")

    # Fallback to settings.yaml
    if not resolved_url or (not resolved_key and auth_mode == "static"):
        settings = _parse_settings_yaml(SETTINGS_PATH)
        if not resolved_url:
            resolved_url = settings.get("server_url", "")
        if not resolved_key and auth_mode == "static":
            resolved_key = settings.get("api_key", "")

    if not resolved_url:
        raise SystemExit(
            "No CI server URL found. Set AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL "
            "or use --server-url, or configure in ~/.amplifier/settings.yaml"
        )
    if auth_mode == "static" and not resolved_key:
        raise SystemExit(
            "No CI API key found. Set AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY "
            "or use --api-key, or configure in ~/.amplifier/settings.yaml"
        )

    return resolved_url, resolved_key or ""
