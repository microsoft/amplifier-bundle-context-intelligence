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
def neo4j_raw_config(behavior_parsed) -> dict:
    """The raw (unexpanded) graph_store.config from the behavior YAML."""
    return behavior_parsed["hooks"][0]["config"]["graph_store"]["config"]


# ---------------------------------------------------------------------------
# Smoke: defaults expand to valid values when env vars are NOT set
# ---------------------------------------------------------------------------


class TestEnvVarDefaultExpansion:
    """When env vars are unset, defaults must produce usable values."""

    def test_uri_defaults_to_bolt_localhost(self, neo4j_raw_config, monkeypatch):
        monkeypatch.delenv("NEO4J_URI", raising=False)
        expanded = expand_env_vars({"uri": neo4j_raw_config["uri"]})
        assert expanded["uri"] == "bolt://localhost:7687"

    def test_username_defaults_to_neo4j(self, neo4j_raw_config, monkeypatch):
        monkeypatch.delenv("NEO4J_USERNAME", raising=False)
        expanded = expand_env_vars({"username": neo4j_raw_config["username"]})
        assert expanded["username"] == "neo4j"

    def test_password_defaults_to_empty(self, neo4j_raw_config, monkeypatch):
        monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
        expanded = expand_env_vars({"password": neo4j_raw_config["password"]})
        assert expanded["password"] == ""

    def test_database_defaults_to_neo4j(self, neo4j_raw_config, monkeypatch):
        monkeypatch.delenv("NEO4J_DATABASE", raising=False)
        expanded = expand_env_vars({"database": neo4j_raw_config["database"]})
        assert expanded["database"] == "neo4j"


# ---------------------------------------------------------------------------
# Smoke: env vars override defaults when set
# ---------------------------------------------------------------------------


class TestEnvVarOverride:
    """When env vars are set, they must take precedence over defaults."""

    def test_uri_overridden_by_env(self, neo4j_raw_config, monkeypatch):
        monkeypatch.setenv("NEO4J_URI", "bolt://prod:7687")
        expanded = expand_env_vars({"uri": neo4j_raw_config["uri"]})
        assert expanded["uri"] == "bolt://prod:7687"

    def test_username_overridden_by_env(self, neo4j_raw_config, monkeypatch):
        monkeypatch.setenv("NEO4J_USERNAME", "admin")
        expanded = expand_env_vars({"username": neo4j_raw_config["username"]})
        assert expanded["username"] == "admin"

    def test_password_overridden_by_env(self, neo4j_raw_config, monkeypatch):
        monkeypatch.setenv("NEO4J_PASSWORD", "s3cret")
        expanded = expand_env_vars({"password": neo4j_raw_config["password"]})
        assert expanded["password"] == "s3cret"

    def test_database_overridden_by_env(self, neo4j_raw_config, monkeypatch):
        monkeypatch.setenv("NEO4J_DATABASE", "production")
        expanded = expand_env_vars({"database": neo4j_raw_config["database"]})
        assert expanded["database"] == "production"


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

    def test_behavior_yaml_has_no_dash_defaults(self, neo4j_raw_config, monkeypatch):
        """Full expansion of actual YAML must produce no leading dashes."""
        for var in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
            monkeypatch.delenv(var, raising=False)
        expanded = expand_env_vars(neo4j_raw_config)
        for key, val in expanded.items():
            assert not str(val).startswith("-"), (
                f"config.{key} expanded to '{val}' — "
                f"leading dash means bash-style :- syntax was used"
            )