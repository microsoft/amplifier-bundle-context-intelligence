"""Tests for ConfigResolver resolution chains."""

from pathlib import Path
from unittest.mock import MagicMock

from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(config: dict | None = None) -> MagicMock:
    """Build a MagicMock coordinator with a .config dict attribute."""
    coordinator = MagicMock()
    coordinator.config = config if config is not None else {}
    return coordinator


def _make_bare_coordinator() -> object:
    """Return a plain object without a .config attribute."""
    return object()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBasePathResolution:
    def test_config_value_wins(self) -> None:
        """Explicit hook config base_path wins over coordinator config."""
        coordinator = _make_coordinator(config={"base_path": "/coordinator/path"})
        resolver = ConfigResolver(config={"base_path": "/explicit/path"}, coordinator=coordinator)

        assert resolver.base_path == Path("/explicit/path")

    def test_coordinator_fallback_when_config_absent(self) -> None:
        """When config has no base_path, falls back to coordinator.config."""
        coordinator = _make_coordinator(config={"base_path": "/coordinator/path"})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.base_path == Path("/coordinator/path")

    def test_default_when_both_absent(self) -> None:
        """When both config and coordinator lack base_path, uses default."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.base_path == Path("~/.amplifier/projects").expanduser()

    def test_tilde_expanded(self) -> None:
        """Tilde in base_path is expanded (no '~' in string result)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"base_path": "~/custom/path"}, coordinator=coordinator)

        assert "~" not in str(resolver.base_path)

    def test_cached_after_first_access(self) -> None:
        """base_path returns the same object on repeated access (cached)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        first = resolver.base_path
        second = resolver.base_path

        assert first is second

    def test_coordinator_without_config_attr_falls_back_to_default(self) -> None:
        """Coordinator without .config attribute safely falls back to default."""
        bare = _make_bare_coordinator()
        resolver = ConfigResolver(config={}, coordinator=bare)

        assert resolver.base_path == Path("~/.amplifier/projects").expanduser()

    def test_returns_path_type(self) -> None:
        """base_path always returns a Path instance."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"base_path": "/some/path"}, coordinator=coordinator)

        assert isinstance(resolver.base_path, Path)


class TestProjectSlugResolution:
    def test_config_value_wins(self) -> None:
        """Explicit hook config project_slug wins over coordinator config."""
        coordinator = _make_coordinator(config={"project_slug": "from-coordinator"})
        resolver = ConfigResolver(config={"project_slug": "from-config"}, coordinator=coordinator)

        assert resolver.project_slug == "from-config"

    def test_coordinator_fallback_when_config_absent(self) -> None:
        """When config has no project_slug, falls back to coordinator.config."""
        coordinator = _make_coordinator(config={"project_slug": "from-coordinator"})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.project_slug == "from-coordinator"

    def test_default_when_both_absent(self) -> None:
        """When both config and coordinator lack project_slug, uses 'default'."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.project_slug == "default"

    def test_cached_after_first_access(self) -> None:
        """project_slug returns the same object on repeated access (cached)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        first = resolver.project_slug
        second = resolver.project_slug

        assert first is second

    def test_coordinator_without_config_attr_falls_back_to_default(self) -> None:
        """Coordinator without .config attribute safely falls back to 'default'."""
        bare = _make_bare_coordinator()
        resolver = ConfigResolver(config={}, coordinator=bare)

        assert resolver.project_slug == "default"

    def test_returns_str_type(self) -> None:
        """project_slug always returns a str instance."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"project_slug": "my-project"}, coordinator=coordinator)

        assert isinstance(resolver.project_slug, str)


class TestForestNameResolution:
    def test_graph_store_config_wins(self) -> None:
        """graph_store.graph_forest_name wins over all other options."""
        coordinator = _make_coordinator(config={"project_slug": "from-coordinator"})
        resolver = ConfigResolver(
            config={
                "graph_store": {"graph_forest_name": "from-graph-store"},
                "project": "from-project",
            },
            coordinator=coordinator,
        )

        assert resolver.forest_name == "from-graph-store"

    def test_config_project_fallback(self) -> None:
        """config['project'] used when no graph_forest_name."""
        coordinator = _make_coordinator(config={"project_slug": "from-coordinator"})
        resolver = ConfigResolver(
            config={"project": "from-config-project"},
            coordinator=coordinator,
        )

        assert resolver.forest_name == "from-config-project"

    def test_coordinator_project_slug_fallback(self) -> None:
        """coordinator.config.project_slug used when no config.project."""
        coordinator = _make_coordinator(config={"project_slug": "from-coordinator-slug"})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.forest_name == "from-coordinator-slug"

    def test_default_when_all_absent(self) -> None:
        """Resolves to 'default' when all sources absent."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.forest_name == "default"

    def test_graph_store_not_a_dict_skips_gracefully(self) -> None:
        """Non-dict graph_store is skipped gracefully, falls through to config.project."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"graph_store": "not-a-dict", "project": "fallback-project"},
            coordinator=coordinator,
        )

        assert resolver.forest_name == "fallback-project"

    def test_graph_store_missing_forest_key_falls_through(self) -> None:
        """Dict graph_store without graph_forest_name falls through to config.project."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"graph_store": {"type": "neo4j"}, "project": "from-project-key"},
            coordinator=coordinator,
        )

        assert resolver.forest_name == "from-project-key"

    def test_cached_after_first_access(self) -> None:
        """forest_name returns the same object on repeated access (cached)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        first = resolver.forest_name
        second = resolver.forest_name

        assert first is second

    def test_coordinator_without_config_attr_falls_back_to_default(self) -> None:
        """Coordinator without .config attribute safely falls back to 'default'."""
        bare = _make_bare_coordinator()
        resolver = ConfigResolver(config={}, coordinator=bare)

        assert resolver.forest_name == "default"

    def test_returns_str_type(self) -> None:
        """forest_name always returns a str instance."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"graph_store": {"graph_forest_name": "my-forest"}},
            coordinator=coordinator,
        )

        assert isinstance(resolver.forest_name, str)


class TestEnableGraph:
    def test_defaults_to_false(self) -> None:
        """enable_graph returns False when not set in config."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.enable_graph is False

    def test_explicit_true_works(self) -> None:
        """enable_graph returns True when explicitly set to True."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"enable_graph": True}, coordinator=coordinator)

        assert resolver.enable_graph is True

    def test_returns_bool_type(self) -> None:
        """enable_graph always returns a bool instance."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"enable_graph": 1}, coordinator=coordinator)

        assert isinstance(resolver.enable_graph, bool)


class TestGraphStoreConfig:
    def test_returns_none_when_absent(self) -> None:
        """graph_store_config returns None when graph_store not in config."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.graph_store_config is None

    def test_returns_dict_when_present(self) -> None:
        """graph_store_config returns the full dict when graph_store is set."""
        store = {"type": "neo4j", "uri": "bolt://localhost:7687"}
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"graph_store": store}, coordinator=coordinator)

        assert resolver.graph_store_config == store


class TestNeo4jConfig:
    def test_returns_none_when_no_graph_store(self) -> None:
        """neo4j_config returns None when graph_store is absent."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.neo4j_config is None

    def test_extracts_full_config(self) -> None:
        """neo4j_config extracts uri, auth tuple, and database from graph_store.config."""
        store = {
            "config": {
                "uri": "bolt://localhost:7687",
                "username": "neo4j",
                "password": "secret",
                "database": "mydb",
            }
        }
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"graph_store": store}, coordinator=coordinator)

        result = resolver.neo4j_config
        assert result is not None
        assert result["uri"] == "bolt://localhost:7687"
        assert result["auth"] == ("neo4j", "secret")
        assert result["database"] == "mydb"

    def test_auth_none_when_credentials_absent(self) -> None:
        """neo4j_config returns auth=None when username/password are not present."""
        store = {
            "config": {
                "uri": "bolt://localhost:7687",
            }
        }
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"graph_store": store}, coordinator=coordinator)

        result = resolver.neo4j_config
        assert result is not None
        assert result["auth"] is None

    def test_database_defaults_to_neo4j(self) -> None:
        """neo4j_config database defaults to 'neo4j' when not explicitly set."""
        store = {
            "config": {
                "uri": "bolt://localhost:7687",
                "username": "neo4j",
                "password": "secret",
            }
        }
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"graph_store": store}, coordinator=coordinator)

        result = resolver.neo4j_config
        assert result is not None
        assert result["database"] == "neo4j"

    def test_returns_none_when_no_config_key(self) -> None:
        """neo4j_config returns None when graph_store has no 'config' key."""
        store = {"type": "neo4j"}
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"graph_store": store}, coordinator=coordinator)

        assert resolver.neo4j_config is None


class TestExcludeEvents:
    def test_defaults_to_empty_set(self) -> None:
        """exclude_events returns an empty set when not set in config."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.exclude_events == set()

    def test_returns_set_from_list(self) -> None:
        """exclude_events converts a list from config to a set."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"exclude_events": ["event_a", "event_b"]},
            coordinator=coordinator,
        )

        assert resolver.exclude_events == {"event_a", "event_b"}

    def test_returns_set_type(self) -> None:
        """exclude_events always returns a set instance."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"exclude_events": ["event_a"]},
            coordinator=coordinator,
        )

        assert isinstance(resolver.exclude_events, set)


class TestLogLevel:
    def test_defaults_to_warning(self) -> None:
        """log_level returns 'WARNING' when not set in config."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.log_level == "WARNING"

    def test_explicit_value_works(self) -> None:
        """log_level returns the explicitly configured value."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"log_level": "DEBUG"}, coordinator=coordinator)

        assert resolver.log_level == "DEBUG"


class TestSessionDir:
    def test_composes_correct_path_from_explicit_values(self) -> None:
        """session_dir composes base_path / project_slug / sessions / session_id / context-intelligence."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"base_path": "/my/base", "project_slug": "my-project"},
            coordinator=coordinator,
        )

        result = resolver.session_dir("abc123")

        assert result == Path("/my/base/my-project/sessions/abc123/context-intelligence")

    def test_uses_resolved_defaults(self) -> None:
        """session_dir uses default base_path and project_slug when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        result = resolver.session_dir("xyz")
        expected = (
            Path("~/.amplifier/projects").expanduser()
            / "default"
            / "sessions"
            / "xyz"
            / "context-intelligence"
        )

        assert result == expected

    def test_returns_path_type(self) -> None:
        """session_dir returns a Path instance."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"base_path": "/base", "project_slug": "proj"},
            coordinator=coordinator,
        )

        assert isinstance(resolver.session_dir("sess-1"), Path)

    def test_uses_coordinator_values_in_path_composition(self) -> None:
        """session_dir uses coordinator-resolved base_path and project_slug."""
        coordinator = _make_coordinator(
            config={"base_path": "/coord/base", "project_slug": "coord-project"}
        )
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        result = resolver.session_dir("sess-42")

        assert result == Path("/coord/base/coord-project/sessions/sess-42/context-intelligence")
