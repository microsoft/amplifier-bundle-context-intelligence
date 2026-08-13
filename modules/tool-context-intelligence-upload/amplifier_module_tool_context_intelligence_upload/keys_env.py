"""Loader for ~/.amplifier/keys.env, mirroring app-cli's KeyManager._load_keys.

The standalone upload CLI runs OUTSIDE app-cli, so nothing performs the
keys.env -> environ step that app-cli normally does before resolving
``${VAR}`` placeholders in settings.yaml. This module exists to perform that
same step for the upload CLI, so that both the CLI and the in-process hook
resolve the exact same values from the exact same file.

Deviating from app-cli's parser -- even to be "more correct" or more
lenient -- would make the two disagree about which keys and tokens are in
effect. That disagreement is precisely the bug this module exists to
prevent, so the parsing rules below are copied faithfully from app-cli
(amplifier_app_cli/key_manager.py:24-41, KeyManager._load_keys) rather than
reimplemented from first principles.
"""

from __future__ import annotations

import os
from pathlib import Path

# keys.env lives beside settings.yaml in the Amplifier home directory
# (~/.amplifier/). Project-local ./.amplifier/keys.env is deliberately NOT
# consulted here -- supporting it is a design non-goal for v1.
DEFAULT_KEYS_ENV_PATH: Path = Path.home() / ".amplifier" / "keys.env"


def load_keys_env_into_environ(path: Path = DEFAULT_KEYS_ENV_PATH) -> None:
    """Parse ``path`` as a simple KEY=VALUE file and load it into os.environ.

    Parsing rules (mirrored exactly from app-cli's KeyManager._load_keys):

    - Each line is stripped of leading/trailing whitespace first.
    - Blank lines are skipped.
    - Lines starting with ``#`` are treated as comments and skipped.
    - Lines with no ``=`` character are skipped.
    - The line is split on the FIRST ``=`` only (``str.split("=", 1)``), so
      values are allowed to contain additional ``=`` characters.
    - The key is stripped of surrounding whitespace.
    - The value is only set into os.environ when the key is NOT already
      present there -- the real process environment always wins over
      whatever is in keys.env. This means keys.env only fills gaps; it never
      overrides a value the user (or their shell) already exported.
    - The value has trailing/leading whitespace stripped, then a single
      layer of double quotes stripped, then a single layer of single quotes
      stripped, in that exact order: ``.strip().strip('"').strip("'")``.
      This order is intentional and is not symmetric: a value written as
      ``'"x"'`` becomes ``x``, but a double-quoted string that has single
      quotes nested *inside* it keeps those inner double quotes, because by
      the time the outer single quotes are stripped, the double-quote strip
      has already happened.
    - There is NO handling of a leading ``export `` prefix on a line -- a
      line like ``export FOO=bar`` is not specially recognized; the key
      would literally be ``export FOO``.
    - Any error reading or decoding the file (file missing, permission
      denied, undecodable bytes, etc.) is swallowed silently and the
      function simply returns, loading nothing. This matches app-cli's
      behavior of failing silently rather than raising.

    Note: this function only loads raw values from keys.env into
    os.environ. It does NOT perform ``${VAR}``-style placeholder expansion
    in settings.yaml -- that is a separate concern handled by
    ``context_intelligence.config._expand_env_placeholders``.
    """
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
    except Exception:
        # Parity with app-cli: any file error fails silently.
        return
