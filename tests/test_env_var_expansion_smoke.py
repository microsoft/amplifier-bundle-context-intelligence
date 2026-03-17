"""Smoke tests verifying env var syntax in behavior YAML expands correctly.

These tests replicate the Amplifier CLI's expand_env_vars logic
(amplifier_app_cli/runtime/config.py) to verify that the env var tokens in
behaviors/context-intelligence.yaml produce correct values when expanded.

The Amplifier CLI uses ${VAR:default} (single colon), NOT ${VAR:-default}
(bash-style).  With the bash-style syntax the leading dash becomes part of
the default value, which silently breaks connection strings.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

BUNDLE_DIR = Path(__file__).parent.parent
BEHAVIOR_YAML = BUNDLE_DIR / "behaviors" / "context-intelligence.yaml"

# Env var names used in the behavior YAML
_SERVER_URL_VAR = "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL"
_WORKSPACE_VAR = "AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE"
_LOG_LEVEL_VAR = "AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL"

# ---------------------------------------------------------------------------
# Replicate the Amplifier CLI expand_env_vars logic exactly
# Source: amplifier_app_cli/runtime/config.py lines 599-619
# ---------------------------------------------------------------------------

ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?}")


def expand_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """Expand ${VAR} and ${VAR:default} references within config values."""

    def replace_value(value: Any) -> Any:
        if isinstance(value, str):
            return ENV_PATTERN.sub(_replace_match, value)
        if isinstance(value, dict):
            return {k: replace_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace_value(item) for item in value]
        return value

    def _replace_match(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")

    return replace_value(config)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def behavior_parsed() -> dict:
    return yaml.safe_load(BEHAVIOR_YAML.read_text())


@pytest.fixture(scope="session")
def hook_raw_config(behavior_parsed) -> dict:
    """The raw (unexpanded) hook config from the behavior YAML."""
    return behavior_parsed["hooks"][0]["config"]


# ---------------------------------------------------------------------------
# Smoke: defaults expand to valid values when env vars are NOT set
# ---------------------------------------------------------------------------


class TestEnvVarDefaultExpansion:
    """When env vars are unset, defaults must produce usable values."""

    def test_server_url_defaults_to_empty(self, hook_raw_config, monkeypatch):
        """AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL defaults to empty string (no server by default)."""
        monkeypatch.delenv(_SERVER_URL_VAR, raising=False)
        expanded = expand_env_vars(
            {"url": hook_raw_config["context_intelligence_server_url"]}
        )
        assert expanded["url"] == ""

    def test_workspace_defaults_to_empty(self, hook_raw_config, monkeypatch):
        """AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE defaults to empty string (auto-resolved from coordinator)."""
        monkeypatch.delenv(_WORKSPACE_VAR, raising=False)
        expanded = expand_env_vars({"workspace": hook_raw_config["workspace"]})
        assert expanded["workspace"] == ""

    def test_log_level_defaults_to_info(self, hook_raw_config, monkeypatch):
        """AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL defaults to INFO."""
        monkeypatch.delenv(_LOG_LEVEL_VAR, raising=False)
        expanded = expand_env_vars({"log_level": hook_raw_config["log_level"]})
        assert expanded["log_level"] == "INFO"


# ---------------------------------------------------------------------------
# Smoke: env vars override defaults when set
# ---------------------------------------------------------------------------


class TestEnvVarOverride:
    """When env vars are set, they must take precedence over defaults."""

    def test_server_url_overridden_by_env(self, hook_raw_config, monkeypatch):
        """AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL env var overrides the empty default."""
        monkeypatch.setenv(_SERVER_URL_VAR, "http://ci-server:8080")
        expanded = expand_env_vars(
            {"url": hook_raw_config["context_intelligence_server_url"]}
        )
        assert expanded["url"] == "http://ci-server:8080"

    def test_workspace_overridden_by_env(self, hook_raw_config, monkeypatch):
        """AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE env var overrides the empty default."""
        monkeypatch.setenv(_WORKSPACE_VAR, "my-project")
        expanded = expand_env_vars({"workspace": hook_raw_config["workspace"]})
        assert expanded["workspace"] == "my-project"

    def test_log_level_overridden_by_env(self, hook_raw_config, monkeypatch):
        """AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL env var overrides the INFO default."""
        monkeypatch.setenv(_LOG_LEVEL_VAR, "DEBUG")
        expanded = expand_env_vars({"log_level": hook_raw_config["log_level"]})
        assert expanded["log_level"] == "DEBUG"


# ---------------------------------------------------------------------------
# Guard: bash-style :- would silently produce wrong defaults
# ---------------------------------------------------------------------------


class TestBashStyleSyntaxCaught:
    """Prove that ${VAR:-default} is wrong — the dash leaks into the value."""

    def test_bash_style_uri_produces_wrong_default(self):
        """If someone uses :- the default gets a leading dash."""
        bad = {"uri": "${NEO4J_URI:-bolt://localhost:7687}"}
        expanded = expand_env_vars(bad)
        assert expanded["uri"] == "-bolt://localhost:7687", (
            "Expected leading dash proving :- is wrong for Amplifier CLI"
        )

    def test_correct_syntax_produces_clean_default(self):
        """Single colon gives the intended default."""
        good = {"uri": "${NEO4J_URI:bolt://localhost:7687}"}
        expanded = expand_env_vars(good)
        assert expanded["uri"] == "bolt://localhost:7687"

    def test_behavior_yaml_has_no_dash_defaults(self, hook_raw_config, monkeypatch):
        """Full expansion of actual YAML must produce no leading dashes."""
        for var in (_SERVER_URL_VAR, _WORKSPACE_VAR, _LOG_LEVEL_VAR):
            monkeypatch.delenv(var, raising=False)
        expanded = expand_env_vars(hook_raw_config)
        for key, val in expanded.items():
            assert not str(val).startswith("-"), (
                f"config.{key} expanded to '{val}' — "
                f"leading dash means bash-style :- syntax was used"
            )
