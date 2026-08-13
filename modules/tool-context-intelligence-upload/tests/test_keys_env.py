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

import pytest

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


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("CI_TOKEN=bare", "bare"),
        ('CI_TOKEN="double-quoted"', "double-quoted"),
        ("CI_TOKEN='single-quoted'", "single-quoted"),
        ("CI_TOKEN=   surrounded-by-spaces   ", "surrounded-by-spaces"),
        ('CI_TOKEN=  "  inner spaces kept  "  ', "  inner spaces kept  "),
    ],
)
def test_value_whitespace_then_quotes_are_stripped_in_that_order(
    tmp_path: Path, clean_environ: None, line: str, expected: str
) -> None:
    keys_env = tmp_path / "keys.env"
    keys_env.write_text(line + "\n", encoding="utf-8")
    os.environ.pop("CI_TOKEN", None)

    load_keys_env_into_environ(keys_env)

    assert os.environ["CI_TOKEN"] == expected


def test_whitespace_around_the_key_is_stripped(tmp_path: Path, clean_environ: None) -> None:
    keys_env = tmp_path / "keys.env"
    keys_env.write_text("  CI_TOKEN  =padded-key\n", encoding="utf-8")
    os.environ.pop("CI_TOKEN", None)

    load_keys_env_into_environ(keys_env)

    assert os.environ["CI_TOKEN"] == "padded-key"


def test_blank_comment_and_malformed_lines_are_skipped(tmp_path: Path, clean_environ: None) -> None:
    keys_env = tmp_path / "keys.env"
    keys_env.write_text(
        "\n# a leading comment\n   \nNOT_A_PAIR\n#COMMENTED_OUT=nope\nGOOD=value\n",
        encoding="utf-8",
    )
    for key in ("NOT_A_PAIR", "COMMENTED_OUT", "GOOD"):
        os.environ.pop(key, None)

    load_keys_env_into_environ(keys_env)

    assert os.environ["GOOD"] == "value"
    assert "NOT_A_PAIR" not in os.environ
    assert "COMMENTED_OUT" not in os.environ


def test_a_value_containing_equals_splits_on_the_first_equals_only(
    tmp_path: Path, clean_environ: None
) -> None:
    keys_env = tmp_path / "keys.env"
    keys_env.write_text("CI_TOKEN=abc=def=ghi\n", encoding="utf-8")
    os.environ.pop("CI_TOKEN", None)

    load_keys_env_into_environ(keys_env)

    assert os.environ["CI_TOKEN"] == "abc=def=ghi"


def test_the_export_prefix_is_not_supported(tmp_path: Path, clean_environ: None) -> None:
    """app-cli has no `export ` handling, so neither do we — parity over convenience."""
    keys_env = tmp_path / "keys.env"
    keys_env.write_text("export CI_TOKEN=v\n", encoding="utf-8")
    os.environ.pop("CI_TOKEN", None)

    load_keys_env_into_environ(keys_env)

    assert "CI_TOKEN" not in os.environ


def test_a_missing_keys_env_file_is_a_silent_no_op(tmp_path: Path, clean_environ: None) -> None:
    missing = tmp_path / "no-such-dir" / "keys.env"
    before = dict(os.environ)

    load_keys_env_into_environ(missing)  # must not raise

    assert dict(os.environ) == before


def test_an_unreadable_keys_env_path_fails_silently(tmp_path: Path, clean_environ: None) -> None:
    """A directory where a file was expected: read_text raises, we swallow it.

    Uses a directory rather than chmod 0o000 so the test is deterministic even
    when the suite runs as root (root can read a 000-mode file).
    """
    keys_env = tmp_path / "keys.env"
    keys_env.mkdir()
    before = dict(os.environ)

    load_keys_env_into_environ(keys_env)  # must not raise

    assert dict(os.environ) == before
