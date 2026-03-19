"""Test that context_intelligence_api_key is wired in the behavior config.

Without this key, the AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY env var never
reaches the hook's ConfigResolver, silently breaking auth.
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml

BUNDLE_DIR = Path(__file__).parent.parent
BEHAVIOR_YAML = BUNDLE_DIR / "behaviors" / "context-intelligence.yaml"

_API_KEY_VAR = "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY"

ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?}")


def _replace_env_match(match: re.Match[str]) -> str:
    """Expand a single ${VAR:default} match using the current environment."""
    var_name = match.group(1)
    default = match.group(2)
    return os.environ.get(var_name, default if default is not None else "")


def expand_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """Replicate Amplifier CLI expand_env_vars logic."""

    def replace_value(value: Any) -> Any:
        if isinstance(value, str):
            return ENV_PATTERN.sub(_replace_env_match, value)
        if isinstance(value, dict):
            return {k: replace_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace_value(item) for item in value]
        return value

    return replace_value(config)


def _hook_config() -> dict:
    data = yaml.safe_load(BEHAVIOR_YAML.read_text())
    return data["hooks"][0]["config"]


class TestApiKeyConfig:
    """context_intelligence_api_key must be present in the hook config block."""

    def test_api_key_present_in_config(self):
        """The hook config must contain the context_intelligence_api_key key."""
        config = _hook_config()
        assert "context_intelligence_api_key" in config, (
            "context_intelligence_api_key is missing from the hook config — "
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY env var will never reach "
            "the ConfigResolver, silently breaking auth"
        )

    def test_api_key_uses_correct_env_var_syntax(self):
        """The api_key value must use the ${VAR:} pattern (single colon, no dash)."""
        config = _hook_config()
        raw_value = config.get("context_intelligence_api_key", "")
        assert raw_value == "${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY:}", (
            f"Expected '${{AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY:}}' "
            f"but got '{raw_value}'"
        )

    def test_api_key_defaults_to_empty_when_unset(self, monkeypatch):
        """When the env var is not set, the key expands to empty string."""
        monkeypatch.delenv(_API_KEY_VAR, raising=False)
        config = _hook_config()
        expanded = expand_env_vars({"key": config["context_intelligence_api_key"]})
        assert expanded["key"] == ""

    def test_api_key_expands_from_env(self, monkeypatch):
        """When the env var is set, the key expands to its value."""
        monkeypatch.setenv(_API_KEY_VAR, "secret-token-abc123")
        config = _hook_config()
        expanded = expand_env_vars({"key": config["context_intelligence_api_key"]})
        assert expanded["key"] == "secret-token-abc123"

    def test_api_key_positioned_after_server_url(self):
        """api_key must appear immediately after context_intelligence_server_url."""
        content = BEHAVIOR_YAML.read_text()
        lines = content.splitlines()
        server_url_idx = next(
            (
                i
                for i, line in enumerate(lines)
                if "context_intelligence_server_url" in line
            ),
            None,
        )
        api_key_idx = next(
            (
                i
                for i, line in enumerate(lines)
                if "context_intelligence_api_key" in line
            ),
            None,
        )
        assert server_url_idx is not None, "context_intelligence_server_url not found"
        assert api_key_idx is not None, "context_intelligence_api_key not found"
        assert api_key_idx == server_url_idx + 1, (
            f"api_key (line {api_key_idx + 1}) must be immediately after "
            f"server_url (line {server_url_idx + 1})"
        )

    def test_yaml_remains_valid(self):
        """The behavior YAML must remain parseable after the change."""
        try:
            data = yaml.safe_load(BEHAVIOR_YAML.read_text())
        except yaml.YAMLError as e:
            raise AssertionError(
                f"behaviors/context-intelligence.yaml is invalid YAML: {e}"
            ) from e
        assert data is not None
