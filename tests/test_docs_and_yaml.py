"""Tests verifying behaviors/context-intelligence.yaml and README.md reflect
the thin-forwarder architecture introduced with the server-dispatch redesign.

These are documentation-state tests: they verify that the YAML config block and
README accurately document the thin forwarder pattern where session events are
written to local JSONL and optionally forwarded to a Context Intelligence server.
"""

from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).parent.parent
BEHAVIOR_YAML = BUNDLE_DIR / "behaviors" / "context-intelligence.yaml"
README = BUNDLE_DIR / "README.md"
CONFIG_DOT = BUNDLE_DIR / "context" / "config-resolution.dot"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def behavior_raw() -> str:
    return BEHAVIOR_YAML.read_text()


@pytest.fixture(scope="session")
def behavior_parsed(behavior_raw) -> dict:
    return yaml.safe_load(behavior_raw)


@pytest.fixture(scope="session")
def readme_raw() -> str:
    return README.read_text()


@pytest.fixture(scope="session")
def config_dot_raw() -> str:
    return CONFIG_DOT.read_text()


# ---------------------------------------------------------------------------
# behaviors/context-intelligence.yaml tests
# ---------------------------------------------------------------------------


class TestBehaviorYamlValid:
    def test_yaml_parses_without_error(self, behavior_parsed):
        """YAML file must parse without any errors."""
        assert behavior_parsed is not None
        assert isinstance(behavior_parsed, dict)

    def test_hooks_section_present(self, behavior_parsed):
        """Top-level hooks key must exist."""
        assert "hooks" in behavior_parsed

    def test_hook_config_key_exists(self, behavior_parsed):
        """hooks[0].config must be present."""
        config = behavior_parsed["hooks"][0]["config"]
        assert config is not None


class TestBehaviorYamlCommentedOptionals:
    """Thin-forwarder optionals must be commented out (not active YAML keys)."""

    def test_base_path_commented_out_not_active_key(self, behavior_parsed):
        """base_path must NOT be an active key in config (it should be commented out)."""
        config = behavior_parsed["hooks"][0]["config"]
        assert "base_path" not in config, (
            "base_path should be commented out (lazy fallback), not an active YAML key"
        )

    def test_project_slug_commented_out_not_active_key(self, behavior_parsed):
        """project_slug must NOT be an active key in config (it should be commented out)."""
        config = behavior_parsed["hooks"][0]["config"]
        assert "project_slug" not in config, (
            "project_slug should be commented out (lazy fallback), not an active YAML key"
        )

    def test_exclude_events_comment_present_in_raw(self, behavior_raw):
        """Raw YAML file must contain commented-out exclude_events reference."""
        assert "# exclude_events" in behavior_raw, (
            "behaviors/context-intelligence.yaml must have a commented-out # exclude_events line"
        )

    def test_resolution_order_comment_present(self, behavior_raw):
        """Raw YAML must contain a comment explaining workspace auto-resolution."""
        assert "coordinator project_slug" in behavior_raw, (
            "YAML must document workspace auto-resolution from 'coordinator project_slug'"
        )


class TestBehaviorYamlRequiredKeys:
    """Required config keys for the thin-forwarder architecture must remain active."""

    def test_exclude_events_not_active_in_config(self, behavior_parsed):
        """exclude_events must be commented out, not an active config key."""
        config = behavior_parsed["hooks"][0]["config"]
        assert "exclude_events" not in config, (
            "exclude_events should be commented out (optional), not an active YAML key"
        )

    def test_log_level_present(self, behavior_parsed):
        """log_level must be an active key controlling hook verbosity."""
        config = behavior_parsed["hooks"][0]["config"]
        assert "log_level" in config

    def test_context_intelligence_server_url_present(self, behavior_parsed):
        """context_intelligence_server_url must be active — it controls server dispatch."""
        config = behavior_parsed["hooks"][0]["config"]
        assert "context_intelligence_server_url" in config

    def test_workspace_present(self, behavior_parsed):
        """workspace must be active — it scopes session data on the server."""
        config = behavior_parsed["hooks"][0]["config"]
        assert "workspace" in config


class TestBehaviorYamlEnvVarSyntax:
    """Config values must use AMPLIFIER_CONTEXT_INTELLIGENCE_ prefixed env vars."""

    def test_server_url_uses_env_var_syntax(self, behavior_raw):
        """context_intelligence_server_url must use ${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL:} syntax."""
        assert "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL:}" in behavior_raw, (
            "context_intelligence_server_url must use env var syntax: "
            "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL:}"
        )

    def test_workspace_uses_env_var_syntax(self, behavior_raw):
        """workspace must use ${AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE:} syntax."""
        assert "${AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE:}" in behavior_raw, (
            "workspace must use env var syntax: ${AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE:}"
        )

    def test_log_level_uses_env_var_syntax(self, behavior_raw):
        """log_level must use ${AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL:INFO} syntax with INFO as default."""
        assert "${AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL:INFO}" in behavior_raw, (
            "log_level must use env var syntax: ${AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL:INFO}"
        )

    def test_no_bash_style_default_syntax(self, behavior_raw):
        """Must NOT use bash-style ${VAR:-default} — Amplifier CLI uses ${VAR:default}."""
        assert ":-" not in behavior_raw, (
            "Found bash-style ':-' in env var syntax. "
            "Amplifier CLI uses single colon ':' for defaults, not ':-'. "
            "With ':-' the dash becomes part of the default value."
        )

    def test_no_short_ci_prefix(self, behavior_raw):
        """Must NOT use short CI_ prefix — use AMPLIFIER_CONTEXT_INTELLIGENCE_ prefix."""
        assert "${CI_" not in behavior_raw, (
            "Found short CI_ env var prefix. "
            "Use AMPLIFIER_CONTEXT_INTELLIGENCE_ prefix for clarity."
        )


class TestBehaviorYamlWorkspace:
    """workspace must document its auto-resolution chain."""

    def test_workspace_auto_resolution_documented(self, behavior_raw):
        """Raw YAML must include a comment explaining workspace auto-resolution."""
        assert "workspace" in behavior_raw
        # The comment must document auto-resolution from coordinator project_slug
        assert "coordinator project_slug" in behavior_raw, (
            "YAML must document workspace auto-resolution from coordinator project_slug"
        )


# ---------------------------------------------------------------------------
# README.md tests
# ---------------------------------------------------------------------------


class TestReadmeInstallation:
    """README must show correct installation as --app."""

    def test_readme_has_quick_start_section(self, readme_raw):
        """README must contain a Quick Start section."""
        assert "## Quick Start" in readme_raw

    def test_readme_shows_app_flag(self, readme_raw):
        """README must show --app flag for bundle installation."""
        assert "--app" in readme_raw, "README must show --app flag for bundle installation"

    def test_readme_shows_bundle_add(self, readme_raw):
        """README must show amplifier bundle add command."""
        assert "amplifier bundle add" in readme_raw

    def test_readme_shows_bundle_use(self, readme_raw):
        """README must show amplifier bundle use command."""
        assert "amplifier bundle use" in readme_raw


class TestReadmeConfiguration:
    """README must document configuration with AMPLIFIER_CONTEXT_INTELLIGENCE_ env vars."""

    def test_readme_shows_server_url_config(self, readme_raw):
        """README must show context_intelligence_server_url."""
        assert "context_intelligence_server_url" in readme_raw

    def test_readme_shows_workspace_config(self, readme_raw):
        """README must show workspace configuration."""
        assert "workspace" in readme_raw

    def test_readme_shows_server_url_env_var(self, readme_raw):
        """README must show AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL env var."""
        assert "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL" in readme_raw, (
            "README must show AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL env var"
        )

    def test_readme_shows_workspace_env_var(self, readme_raw):
        """README must show AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE env var."""
        assert "AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE" in readme_raw, (
            "README must show AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE env var"
        )

    def test_readme_shows_log_level_env_var(self, readme_raw):
        """README must show AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL env var."""
        assert "AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL" in readme_raw, (
            "README must show AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL env var"
        )

    def test_readme_no_short_ci_prefix_in_env_vars(self, readme_raw):
        """README must not use short CI_ prefix for env vars."""
        # Allow "CI server" as prose but not CI_SERVER_URL as an env var name
        lines = readme_raw.splitlines()
        for line in lines:
            if "export CI_" in line or "`CI_" in line:
                pytest.fail(
                    f"Found short CI_ env var prefix in README: {line.strip()}\n"
                    "Use AMPLIFIER_CONTEXT_INTELLIGENCE_ prefix instead."
                )


# ---------------------------------------------------------------------------
# context/config-resolution.dot tests
# ---------------------------------------------------------------------------


class TestConfigResolutionDot:
    """config-resolution.dot must document the ConfigResolver architecture."""

    def test_dot_file_exists(self):
        """config-resolution.dot must exist."""
        assert CONFIG_DOT.exists(), f"config-resolution.dot not found at {CONFIG_DOT}"

    def test_dot_has_config_resolver_node(self, config_dot_raw):
        """DOT must contain a ConfigResolver node."""
        assert "ConfigResolver" in config_dot_raw

    def test_dot_has_base_path_resolution(self, config_dot_raw):
        """DOT must show base_path resolution chain."""
        assert "base_path" in config_dot_raw

    def test_dot_has_project_slug_resolution(self, config_dot_raw):
        """DOT must show project_slug resolution chain."""
        assert "project_slug" in config_dot_raw

    def test_dot_has_workspace_resolution(self, config_dot_raw):
        """DOT must show workspace resolution chain."""
        assert "workspace" in config_dot_raw

    def test_dot_has_coordinator_inputs(self, config_dot_raw):
        """DOT must reference coordinator config as an input."""
        assert "coordinator" in config_dot_raw

    def test_dot_has_session_dir_derivation(self, config_dot_raw):
        """DOT must show session_dir derived path."""
        assert "session_dir" in config_dot_raw

    def test_dot_has_env_var_reference(self, config_dot_raw):
        """DOT must reference AMPLIFIER_CONTEXT_INTELLIGENCE_ env vars."""
        assert "AMPLIFIER_CONTEXT_INTELLIGENCE" in config_dot_raw
