"""Tests verifying behaviors/context-intelligence.yaml and README.md reflect
ConfigResolver lazy fallback chains.

These are documentation-state tests: they verify that the YAML config block and
README Quick Start accurately document the lazy resolution behaviour introduced
with ConfigResolver.
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
    """base_path and project_slug must be commented out (lazy-resolved)."""

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

    def test_base_path_comment_present_in_raw(self, behavior_raw):
        """Raw YAML file must contain commented-out base_path reference."""
        assert "# base_path" in behavior_raw, (
            "behaviors/context-intelligence.yaml must have a commented-out # base_path line"
        )

    def test_project_slug_comment_present_in_raw(self, behavior_raw):
        """Raw YAML file must contain commented-out project_slug reference."""
        assert "# project_slug" in behavior_raw, (
            "behaviors/context-intelligence.yaml must have a commented-out # project_slug line"
        )

    def test_resolution_order_comment_present(self, behavior_raw):
        """Raw YAML must contain a comment explaining the resolution chain."""
        assert "coordinator.config" in behavior_raw, (
            "YAML must document 'coordinator.config' in the resolution order comment"
        )
        assert "sensible default" in behavior_raw, (
            "YAML must document 'sensible default' in the resolution order comment"
        )


class TestBehaviorYamlRequiredKeys:
    """Required config keys must remain active."""

    def test_exclude_events_present(self, behavior_parsed):
        config = behavior_parsed["hooks"][0]["config"]
        assert "exclude_events" in config

    def test_log_level_present(self, behavior_parsed):
        config = behavior_parsed["hooks"][0]["config"]
        assert "log_level" in config

    def test_enable_graph_present(self, behavior_parsed):
        """enable_graph must be an active key so settings.yaml can override it during merge."""
        config = behavior_parsed["hooks"][0]["config"]
        assert "enable_graph" in config
        assert config["enable_graph"] is False, (
            "behavior default must be false; users override in settings.yaml"
        )

    def test_graph_store_present(self, behavior_parsed):
        config = behavior_parsed["hooks"][0]["config"]
        assert "graph_store" in config

    def test_graph_store_type_neo4j(self, behavior_parsed):
        config = behavior_parsed["hooks"][0]["config"]
        assert config["graph_store"]["type"] == "neo4j"


class TestBehaviorYamlEnvVarSyntax:
    """Neo4j config values must use env-var syntax."""

    def test_neo4j_uri_uses_env_var_syntax(self, behavior_raw):
        """uri must use ${NEO4J_URI:bolt://localhost:7687} syntax."""
        assert "${NEO4J_URI:bolt://localhost:7687}" in behavior_raw, (
            "Neo4j uri must use env var syntax: ${NEO4J_URI:bolt://localhost:7687}"
        )

    def test_neo4j_username_uses_env_var_syntax(self, behavior_raw):
        """username must use ${NEO4J_USERNAME:neo4j} syntax."""
        assert "${NEO4J_USERNAME:neo4j}" in behavior_raw, (
            "Neo4j username must use env var syntax: ${NEO4J_USERNAME:neo4j}"
        )

    def test_neo4j_password_uses_env_var_syntax(self, behavior_raw):
        """password must use ${NEO4J_PASSWORD} syntax (no default — must be set)."""
        assert "${NEO4J_PASSWORD}" in behavior_raw, (
            "Neo4j password must use env var syntax: ${NEO4J_PASSWORD}"
        )

    def test_neo4j_database_uses_env_var_syntax(self, behavior_raw):
        """database must use ${NEO4J_DATABASE:neo4j} syntax."""
        assert "${NEO4J_DATABASE:neo4j}" in behavior_raw, (
            "Neo4j database must use env var syntax: ${NEO4J_DATABASE:neo4j}"
        )

    def test_no_bash_style_default_syntax(self, behavior_raw):
        """Must NOT use bash-style ${VAR:-default} — Amplifier CLI uses ${VAR:default}."""
        assert ":-" not in behavior_raw, (
            "Found bash-style ':-' in env var syntax. "
            "Amplifier CLI uses single colon ':' for defaults, not ':-'. "
            "With ':-' the dash becomes part of the default value."
        )


class TestBehaviorYamlGraphForestName:
    """graph_forest_name comment must document its resolution chain."""

    def test_graph_forest_name_comment_present(self, behavior_raw):
        """Raw YAML must include a comment explaining graph_forest_name resolution."""
        assert "graph_forest_name" in behavior_raw
        # The comment must document the resolution chain including config.project
        assert "config.project" in behavior_raw, (
            "YAML must document 'config.project' in the graph_forest_name resolution comment"
        )


# ---------------------------------------------------------------------------
# README.md Quick Start tests
# ---------------------------------------------------------------------------


class TestReadmeQuickStart:
    """README Quick Start YAML block must show lazy fallback documentation."""

    def test_readme_has_quick_start_section(self, readme_raw):
        """README must contain a Quick Start section."""
        assert "## Quick Start" in readme_raw

    def test_readme_quick_start_base_path_commented_out(self, readme_raw):
        """README Quick Start must show base_path as a commented-out optional."""
        assert "# base_path:" in readme_raw, (
            "README Quick Start must show '# base_path:' as commented-out optional"
        )

    def test_readme_quick_start_project_slug_commented_out(self, readme_raw):
        """README Quick Start must show project_slug as a commented-out optional."""
        assert "# project_slug:" in readme_raw, (
            "README Quick Start must show '# project_slug:' as commented-out optional"
        )

    def test_readme_quick_start_enable_graph_with_comment(self, readme_raw):
        """README Quick Start must show enable_graph: false with a comment."""
        assert "enable_graph: false" in readme_raw

    def test_readme_quick_start_graph_store_present(self, readme_raw):
        """README Quick Start must show graph_store configuration."""
        assert "graph_store:" in readme_raw

    def test_readme_quick_start_graph_forest_name_present(self, readme_raw):
        """README Quick Start must show graph_forest_name."""
        assert "graph_forest_name" in readme_raw

    def test_readme_quick_start_env_var_syntax_uri(self, readme_raw):
        """README Quick Start must show env var syntax for Neo4j uri."""
        assert "${NEO4J_URI" in readme_raw, (
            "README Quick Start must show env var syntax for Neo4j URI"
        )

    def test_readme_quick_start_env_var_syntax_username(self, readme_raw):
        """README Quick Start must show env var syntax for Neo4j username."""
        assert "${NEO4J_USERNAME" in readme_raw, (
            "README Quick Start must show env var syntax for Neo4j username"
        )

    def test_readme_quick_start_env_var_syntax_password(self, readme_raw):
        """README Quick Start must show env var syntax for Neo4j password."""
        assert "${NEO4J_PASSWORD}" in readme_raw, (
            "README Quick Start must show env var syntax for Neo4j password"
        )

    def test_readme_quick_start_env_var_syntax_database(self, readme_raw):
        """README Quick Start must show env var syntax for Neo4j database."""
        assert "${NEO4J_DATABASE" in readme_raw, (
            "README Quick Start must show env var syntax for Neo4j database"
        )


# ---------------------------------------------------------------------------
# context/config-resolution.dot tests
# ---------------------------------------------------------------------------


class TestConfigResolutionDot:
    """config-resolution.dot must already contain ConfigResolver architecture."""

    def test_dot_file_exists(self):
        """config-resolution.dot must exist."""
        assert CONFIG_DOT.exists(), f"config-resolution.dot not found at {CONFIG_DOT}"

    def test_dot_has_config_resolver_node(self, config_dot_raw):
        """DOT must contain a ConfigResolver node."""
        assert "ConfigResolver" in config_dot_raw

    def test_dot_has_base_path_resolution_subgraph(self, config_dot_raw):
        """DOT must show base_path resolution chain."""
        assert "base_path" in config_dot_raw

    def test_dot_has_project_slug_resolution_subgraph(self, config_dot_raw):
        """DOT must show project_slug resolution chain."""
        assert "project_slug" in config_dot_raw

    def test_dot_has_forest_name_resolution_subgraph(self, config_dot_raw):
        """DOT must show forest_name resolution chain."""
        assert "forest_name" in config_dot_raw

    def test_dot_has_coordinator_inputs(self, config_dot_raw):
        """DOT must reference coordinator config as an input."""
        assert "coordinator" in config_dot_raw
