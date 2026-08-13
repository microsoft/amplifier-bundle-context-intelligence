"""Tests for the keys.env loader.

The loader in keys_env.py must mirror app-cli's KeyManager._load_keys
exactly. The point of this module is that the standalone upload CLI resolves
the SAME values that the hook (running inside app-cli) sees. A "better" or
more lenient parser that disagrees with app-cli's own parsing rules is not
an improvement — it is a bug, because it would let the CLI and the hook
silently diverge on which API keys and tokens are in effect.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest  # noqa: F401 -- required by spec; clean_environ fixture is used via parameter only

from amplifier_module_tool_context_intelligence_upload.keys_env import (
    DEFAULT_KEYS_ENV_PATH,
    load_keys_env_into_environ,
)


def test_default_keys_env_path_is_beside_settings_yaml_in_amplifier_home() -> None:
    assert DEFAULT_KEYS_ENV_PATH == Path.home() / ".amplifier" / "keys.env"


def test_key_value_lines_are_loaded_into_the_process_environment(
    tmp_path: Path, clean_environ: None
) -> None:
    keys_env = tmp_path / "keys.env"
    keys_env.write_text("ANTHROPIC_API_KEY=sk-ant-123\nCI_TOKEN=tok-456\n", encoding="utf-8")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("CI_TOKEN", None)

    load_keys_env_into_environ(keys_env)

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-123"
    assert os.environ["CI_TOKEN"] == "tok-456"


def test_process_environment_wins_over_the_keys_env_file(
    tmp_path: Path, clean_environ: None
) -> None:
    """A value already exported in the shell must not be clobbered by the file."""
    keys_env = tmp_path / "keys.env"
    keys_env.write_text("CI_TOKEN=from-file\n", encoding="utf-8")
    os.environ["CI_TOKEN"] = "from-process"

    load_keys_env_into_environ(keys_env)

    assert os.environ["CI_TOKEN"] == "from-process"
