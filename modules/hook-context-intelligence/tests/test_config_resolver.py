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


class TestWorkspaceResolution:
    """workspace property — coordinator.config > config > project_slug."""

    def test_coordinator_config_wins_over_hook_config(self) -> None:
        """coordinator.config['workspace'] has highest priority."""
        coordinator = _make_coordinator(config={"workspace": "from-coordinator"})
        resolver = ConfigResolver(config={"workspace": "from-hook"}, coordinator=coordinator)

        assert resolver.workspace == "from-coordinator"

    def test_hook_config_wins_over_project_slug(self) -> None:
        """config['workspace'] wins when coordinator has no workspace."""
        coordinator = _make_coordinator(config={"project_slug": "proj-slug"})
        resolver = ConfigResolver(config={"workspace": "from-hook"}, coordinator=coordinator)

        assert resolver.workspace == "from-hook"

    def test_falls_back_to_project_slug(self) -> None:
        """When both coordinator.config and config lack workspace, falls back to project_slug."""
        coordinator = _make_coordinator(config={"project_slug": "slug-fallback"})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.workspace == "slug-fallback"

    def test_defaults_to_default_when_all_absent(self) -> None:
        """When all workspace sources are absent, resolves to 'default'."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.workspace == "default"

    def test_returns_str_type(self) -> None:
        """workspace always returns a str."""
        coordinator = _make_coordinator(config={"workspace": "my-ws"})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert isinstance(resolver.workspace, str)

    def test_coordinator_none_falls_back_to_config(self) -> None:
        """When coordinator is None, falls back to config['workspace']."""
        resolver = ConfigResolver(config={"workspace": "from-config"}, coordinator=None)

        assert resolver.workspace == "from-config"


class TestContextIntelligenceServerUrl:
    """context_intelligence_server_url property."""

    def test_returns_none_when_absent(self) -> None:
        """Returns None when context_intelligence_server_url not in config."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.context_intelligence_server_url is None

    def test_returns_string_when_set(self) -> None:
        """Returns the URL string when configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"context_intelligence_server_url": "http://localhost:8000"},
            coordinator=coordinator,
        )

        assert resolver.context_intelligence_server_url == "http://localhost:8000"

    def test_returns_none_for_empty_string(self) -> None:
        """Returns None when value is an empty string (falsy)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"context_intelligence_server_url": ""},
            coordinator=coordinator,
        )

        assert resolver.context_intelligence_server_url is None


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

    def test_returns_frozenset_type(self) -> None:
        """exclude_events always returns a frozenset instance (cached, immutable)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"exclude_events": ["event_a"]},
            coordinator=coordinator,
        )

        assert isinstance(resolver.exclude_events, frozenset)

    def test_cached_after_first_access(self) -> None:
        """exclude_events returns the same object on repeated access (cached)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"exclude_events": ["event_a", "event_b"]},
            coordinator=coordinator,
        )

        first = resolver.exclude_events
        second = resolver.exclude_events

        assert first is second


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


class TestBlobStoreRoot:
    """blob_store_root property resolves to the project-level context-intelligence directory."""

    def test_blob_store_root_returns_path(self) -> None:
        """blob_store_root is base_path / project_slug / 'sessions'."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"base_path": "/tmp/test-projects", "project_slug": "my-project"},
            coordinator=coordinator,
        )
        result = resolver.blob_store_root
        assert result == Path("/tmp/test-projects") / "my-project" / "sessions"

    def test_blob_store_root_uses_default_base_path(self) -> None:
        """blob_store_root works with default base_path."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"project_slug": "default"}, coordinator=coordinator)
        result = resolver.blob_store_root
        expected = Path("~/.amplifier/projects").expanduser() / "default" / "sessions"
        assert result == expected


class TestDispatchTimeout:
    def test_defaults_to_30(self) -> None:
        """dispatch_timeout returns 30.0 when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.dispatch_timeout == 30.0

    def test_reads_from_config(self) -> None:
        """dispatch_timeout returns the configured value as a float."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"dispatch_timeout": 10}, coordinator=coordinator)

        assert resolver.dispatch_timeout == 10.0

    def test_returns_float_type(self) -> None:
        """dispatch_timeout always returns a float even when config value is a string."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"dispatch_timeout": "45"}, coordinator=coordinator)

        assert isinstance(resolver.dispatch_timeout, float)
