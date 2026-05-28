"""Tests for ConfigResolver resolution chains."""

from pathlib import Path
from unittest.mock import MagicMock

from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver
from amplifier_module_hook_context_intelligence.config_resolver import _slugify_path


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
    """workspace property — config > coordinator.config > project_slug.

    Follows the same pattern as base_path and project_slug: explicit hook
    config wins, coordinator.config is the middle fallback, project_slug
    (auto-derived) is the last resort.
    """

    def test_hook_config_wins_over_coordinator_config(self) -> None:
        """config['workspace'] has highest priority — overrides coordinator.config."""
        coordinator = _make_coordinator(config={"workspace": "from-coordinator"})
        resolver = ConfigResolver(config={"workspace": "from-hook"}, coordinator=coordinator)

        assert resolver.workspace == "from-hook"

    def test_coordinator_config_fallback_when_hook_config_absent(self) -> None:
        """coordinator.config['workspace'] is used when config has no workspace."""
        coordinator = _make_coordinator(config={"workspace": "from-coordinator"})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

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

    def test_returns_none_when_absent(self, monkeypatch, tmp_path) -> None:
        """Returns None when context_intelligence_server_url not in config."""
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.context_intelligence_server_url is None

    def test_returns_string_when_set(self, monkeypatch, tmp_path) -> None:
        """Returns the URL string when configured."""
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"server": {"url": "http://localhost:8000"}},
            coordinator=coordinator,
        )

        assert resolver.context_intelligence_server_url == "http://localhost:8000"

    def test_returns_none_for_empty_string(self, monkeypatch, tmp_path) -> None:
        """Returns None when value is an empty string (falsy)."""
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"server": {"url": ""}},
            coordinator=coordinator,
        )

        assert resolver.context_intelligence_server_url is None

    def test_coordinator_config_url_used_when_hook_config_absent(
        self, monkeypatch, tmp_path
    ) -> None:
        """URL from coordinator.config['server']['url'] is used when hook config has no server."""
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        coordinator = _make_coordinator(config={"server": {"url": "http://coordinator-url:8000"}})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.context_intelligence_server_url == "http://coordinator-url:8000"


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


class TestContextIntelligenceApiKey:
    """context_intelligence_api_key property."""

    def test_returns_none_when_not_configured(self, monkeypatch, tmp_path) -> None:
        """Returns None when context_intelligence_api_key not in config."""
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))

        assert resolver.context_intelligence_api_key is None

    def test_returns_string_when_configured(self) -> None:
        """Returns the API key string when configured."""
        resolver = ConfigResolver(
            config={"server": {"api_key": "my-secret-key"}},
            coordinator=_make_coordinator(config={}),
        )

        assert resolver.context_intelligence_api_key == "my-secret-key"

    def test_returns_none_for_empty_string(self, monkeypatch, tmp_path) -> None:
        """Returns None when value is an empty string (falsy)."""
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        resolver = ConfigResolver(
            config={"server": {"api_key": ""}},
            coordinator=_make_coordinator(config={}),
        )

        assert resolver.context_intelligence_api_key is None

    def test_coerces_non_string_to_string(self) -> None:
        """Coerces non-string values to str."""
        resolver = ConfigResolver(
            config={"server": {"api_key": 12345}},
            coordinator=_make_coordinator(config={}),
        )

        assert resolver.context_intelligence_api_key == "12345"
        assert isinstance(resolver.context_intelligence_api_key, str)

    def test_coordinator_config_api_key_used_when_hook_config_absent(self) -> None:
        """API key from coordinator.config['server']['api_key'] used when hook config has no server."""
        coordinator = _make_coordinator(config={"server": {"api_key": "coord-secret-key"}})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.context_intelligence_api_key == "coord-secret-key"


class TestServerConfig:
    """_server_config() helper method."""

    def test_returns_empty_dict_when_absent(self) -> None:
        """Returns empty dict when 'server' is not in config."""
        resolver = ConfigResolver(config={}, coordinator=None)

        assert resolver._server_config() == {}

    def test_returns_nested_dict_when_present(self) -> None:
        """Returns the nested dict when 'server' is configured."""
        resolver = ConfigResolver(
            config={"server": {"url": "http://localhost:8000"}},
            coordinator=None,
        )

        assert resolver._server_config() == {"url": "http://localhost:8000"}

    def test_returns_empty_dict_when_value_is_not_a_dict(self) -> None:
        """Returns empty dict when 'server' is not a dict."""
        resolver = ConfigResolver(
            config={"server": "not-a-dict"},
            coordinator=None,
        )

        assert resolver._server_config() == {}


class TestDispatchTimeout:
    def test_defaults_to_10(self) -> None:
        """dispatch_timeout returns 10.0 when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.dispatch_timeout == 10.0

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
        assert resolver.dispatch_timeout == 45.0


class TestDispatchFailureThreshold:
    def test_defaults_to_3(self) -> None:
        """dispatch_failure_threshold returns 3 when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.dispatch_failure_threshold == 3

    def test_reads_from_config(self) -> None:
        """dispatch_failure_threshold returns the configured value as an int."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"dispatch_failure_threshold": 5}, coordinator=coordinator)

        assert resolver.dispatch_failure_threshold == 5

    def test_returns_int_type(self) -> None:
        """dispatch_failure_threshold always returns an int even when config value is a string."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"dispatch_failure_threshold": "7"}, coordinator=coordinator
        )

        assert isinstance(resolver.dispatch_failure_threshold, int)
        assert resolver.dispatch_failure_threshold == 7


class TestDispatchQueueCapacity:
    def test_defaults_to_256(self) -> None:
        """dispatch_queue_capacity returns 256 when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.dispatch_queue_capacity == 256

    def test_reads_from_config(self) -> None:
        """dispatch_queue_capacity returns the configured value as an int."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"dispatch_queue_capacity": 64}, coordinator=coordinator)

        assert resolver.dispatch_queue_capacity == 64


class TestCloseDrainTimeout:
    def test_defaults_to_half_second(self) -> None:
        """close_drain_timeout returns 0.5 when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.close_drain_timeout == 0.5

    def test_reads_from_config(self) -> None:
        """close_drain_timeout returns the configured value as a float."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"close_drain_timeout": "1.25"}, coordinator=coordinator)

        assert resolver.close_drain_timeout == 1.25


class TestAdditionalEvents:
    def test_defaults_to_empty_set(self) -> None:
        """additional_events returns an empty frozenset when not set in config."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.additional_events == frozenset()

    def test_returns_frozenset_from_list(self) -> None:
        """additional_events converts a list from config to a frozenset."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"additional_events": ["delegate:agent_spawned", "delegate:agent_completed"]},
            coordinator=coordinator,
        )

        result = resolver.additional_events

        assert result == {"delegate:agent_spawned", "delegate:agent_completed"}
        assert isinstance(result, frozenset)

    def test_cached_after_first_access(self) -> None:
        """additional_events returns the same object on repeated access (cached)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"additional_events": ["delegate:agent_spawned"]},
            coordinator=coordinator,
        )

        first = resolver.additional_events
        second = resolver.additional_events

        assert first is second


class TestSettingsYamlFallback:
    """settings.yaml as lowest-priority fallback for server_url and api_key."""

    def test_server_url_falls_back_to_settings_yaml(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)

        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "overrides:\n"
            "  hook-context-intelligence:\n"
            "    config:\n"
            "      server:\n"
            "        url: http://from-settings-yaml\n"
        )

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            settings_file,
        )

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))

        assert resolver.context_intelligence_server_url == "http://from-settings-yaml"

    def test_env_var_wins_over_settings_yaml(self, monkeypatch, tmp_path):
        """Env var has higher priority than settings.yaml."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", "http://from-env")

        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "overrides:\n"
            "  hook-context-intelligence:\n"
            "    config:\n"
            "      server:\n"
            "        url: http://from-settings-yaml\n"
        )

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            settings_file,
        )

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))

        assert resolver.context_intelligence_server_url == "http://from-env"

    def test_api_key_falls_back_to_settings_yaml(self, monkeypatch, tmp_path):
        """When config, coordinator, and env var are all absent, context_intelligence_api_key falls back to settings.yaml."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)

        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "overrides:\n"
            "  hook-context-intelligence:\n"
            "    config:\n"
            "      server:\n"
            "        api_key: sk-from-settings-yaml\n"
        )

        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            settings_file,
        )

        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))

        assert resolver.context_intelligence_api_key == "sk-from-settings-yaml"

    def test_settings_yaml_returns_none_when_file_missing(self, monkeypatch, tmp_path):
        """When settings.yaml doesn't exist, still returns None gracefully."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.setattr(
            "amplifier_module_hook_context_intelligence.config_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))

        assert resolver.context_intelligence_server_url is None


class TestParentId:
    """parent_id property — config-only, no coordinator fallback, no env fallback.

    Empty string means absent / root session (preserves existing semantics).
    This is a per-session hook-config value supplied by the resolver via
    SessionFactory.create_phase_session (CR-1).
    """

    def test_parent_id_from_config(self) -> None:
        """ConfigResolver.parent_id reads from hook config['parent_id']."""
        cr = ConfigResolver({"parent_id": "parent-abc-123"}, _make_coordinator())
        assert cr.parent_id == "parent-abc-123"

    def test_parent_id_empty_when_absent(self) -> None:
        """ConfigResolver.parent_id returns empty string when config has no parent_id key."""
        cr = ConfigResolver({}, _make_coordinator())
        assert cr.parent_id == ""

    def test_parent_id_empty_when_none(self) -> None:
        """ConfigResolver.parent_id returns empty string when config has parent_id=None."""
        cr = ConfigResolver({"parent_id": None}, _make_coordinator())
        assert cr.parent_id == ""

    def test_parent_id_returns_str_type(self) -> None:
        """ConfigResolver.parent_id always returns a str."""
        cr = ConfigResolver({"parent_id": "abc"}, _make_coordinator())
        assert isinstance(cr.parent_id, str)

    def test_no_coordinator_fallback(self) -> None:
        """ConfigResolver.parent_id does NOT fall back to coordinator.config.

        parent_id is a per-session value stamped by the resolver; it must not
        bleed from a coordinator-level config that spans multiple sessions.
        """
        coordinator = _make_coordinator(config={"parent_id": "from-coordinator"})
        cr = ConfigResolver({}, coordinator)
        assert cr.parent_id == ""


class TestResolveInstanceId:
    """resolve_instance_id property — config-only, no coordinator fallback.

    Resolver instance ID supplied via SessionFactory. Empty string if absent.
    """

    def test_resolve_instance_id_from_config(self) -> None:
        """ConfigResolver.resolve_instance_id reads from hook config['resolve_instance_id']."""
        cr = ConfigResolver({"resolve_instance_id": "abc-def-123"}, _make_coordinator())
        assert cr.resolve_instance_id == "abc-def-123"

    def test_resolve_instance_id_empty_when_absent(self) -> None:
        """ConfigResolver.resolve_instance_id returns empty string when absent."""
        cr = ConfigResolver({}, _make_coordinator())
        assert cr.resolve_instance_id == ""

    def test_resolve_instance_id_empty_when_none(self) -> None:
        """ConfigResolver.resolve_instance_id returns empty string when config has None."""
        cr = ConfigResolver({"resolve_instance_id": None}, _make_coordinator())
        assert cr.resolve_instance_id == ""

    def test_resolve_instance_id_returns_str_type(self) -> None:
        """ConfigResolver.resolve_instance_id always returns a str."""
        cr = ConfigResolver({"resolve_instance_id": "xyz"}, _make_coordinator())
        assert isinstance(cr.resolve_instance_id, str)


class TestForwardingEnabled:
    """forwarding_enabled property — four-step resolution chain using new include/exclude config.

    Resolution order (first match wins):
    1. include is empty                     → False (deny-all default)
    2. workspace not in include patterns    → False (not opted in)
    3. workspace matches exclude pattern    → False (trimmed from include)
    4. default                              → True
    """

    def test_deny_all_no_include(self) -> None:
        """Empty include+exclude → False (deny-all at step 1)."""
        resolver = ConfigResolver(
            config={
                "server": {"include": [], "exclude": []},
                "workspace": "-home-dicolomb-amplifier-context-intelligence-graph-query",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is False

    def test_exact_match(self) -> None:
        """Exact slug -home-dicolomb-amplifier-context-intelligence-graph-query in include → True."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": ["-home-dicolomb-amplifier-context-intelligence-graph-query"],
                    "exclude": [],
                },
                "workspace": "-home-dicolomb-amplifier-context-intelligence-graph-query",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is True

    def test_suffix_wildcard(self) -> None:
        """-home-dicolomb-amplifier-bundle-* matches -home-dicolomb-amplifier-bundle-context-intelligence-design-mode → True."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": ["-home-dicolomb-amplifier-bundle-*"],
                    "exclude": [],
                },
                "workspace": "-home-dicolomb-amplifier-bundle-context-intelligence-design-mode",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is True

    def test_prefix_wildcard(self) -> None:
        """*-secrets matches -home-dicolomb-workspaces-cotnext-intelligence-configuration-secrets → True."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": ["*-secrets"],
                    "exclude": [],
                },
                "workspace": "-home-dicolomb-workspaces-cotnext-intelligence-configuration-secrets",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is True

    def test_multi_segment_wildcard(self) -> None:
        """-home-dicolomb-amplifier-*-context-intelligence* matches -home-dicolomb-amplifier-bundle-context-intelligence-design-mode → True (fnmatch * spans -)."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": ["-home-dicolomb-amplifier-*-context-intelligence*"],
                    "exclude": [],
                },
                "workspace": "-home-dicolomb-amplifier-bundle-context-intelligence-design-mode",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is True

    def test_wildcard_all(self) -> None:
        """* matches default → True (documented escape hatch)."""
        resolver = ConfigResolver(
            config={
                "server": {"include": ["*"], "exclude": []},
                "workspace": "default",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is True

    def test_no_pattern_match(self) -> None:
        """-home-dicolomb-amplifier-* vs -home-dicolomb-personal-projects-ecoflow-library → False."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": ["-home-dicolomb-amplifier-*"],
                    "exclude": [],
                },
                "workspace": "-home-dicolomb-personal-projects-ecoflow-library",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is False

    def test_exclude_trims_include(self) -> None:
        """include -home-dicolomb-workspaces-* + exclude *-secrets, workspace -home-dicolomb-workspaces-cotnext-intelligence-configuration-secrets → False."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": ["-home-dicolomb-workspaces-*"],
                    "exclude": ["*-secrets"],
                },
                "workspace": "-home-dicolomb-workspaces-cotnext-intelligence-configuration-secrets",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is False

    def test_exclude_without_include_deny_all(self) -> None:
        """Empty include + exclude *-secrets → False (deny-all fires at step 1)."""
        resolver = ConfigResolver(
            config={
                "server": {"include": [], "exclude": ["*-secrets"]},
                "workspace": "-home-dicolomb-workspaces-cotnext-intelligence-configuration-secrets",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is False

    def test_include_with_non_matching_exclude(self) -> None:
        """include -home-dicolomb-workspaces-* + exclude *-secrets, workspace -home-dicolomb-workspaces-team-pulse-bundle → True."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": ["-home-dicolomb-workspaces-*"],
                    "exclude": ["*-secrets"],
                },
                "workspace": "-home-dicolomb-workspaces-team-pulse-bundle",
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.forwarding_enabled is True


class TestSlugifyPath:
    """_slugify_path function — module-level helper for workspace slug derivation."""

    def test_unix_path_produces_expected_slug(self) -> None:
        assert _slugify_path("/home/user/project") == "-home-user-project"

    def test_unix_path_matches_workspace_slug(self) -> None:
        from context_intelligence.reconstruct.discover import workspace_slug

        path = "/home/user/project"
        assert _slugify_path(path) == workspace_slug(path)

    def test_handles_windows_path(self) -> None:
        """Windows-style path has backslashes replaced and colon stripped."""
        result = _slugify_path("C:\\Users\\user\\project")
        assert "\\" not in result
        assert ":" not in result
        assert "C" in result
        assert "Users" in result

    def test_empty_string_returns_default(self) -> None:
        """Empty path string returns the default project slug."""
        assert _slugify_path("") == "default"


class TestInclude:
    """include property — reads list from nested ``server`` config block."""

    def test_defaults_to_empty_list(self) -> None:
        """include returns [] when not set in config."""
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.include == []

    def test_returns_list_from_config(self) -> None:
        """include returns the configured list from the nested ``server`` block."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "include": [
                        "-home-dicolomb-amplifier-bundle-*",
                        "-home-dicolomb-workspaces-*",
                    ],
                },
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.include == [
            "-home-dicolomb-amplifier-bundle-*",
            "-home-dicolomb-workspaces-*",
        ]

    def test_returns_list_type(self) -> None:
        """include always returns a list instance."""
        resolver = ConfigResolver(
            config={"server": {"include": ["-home-dicolomb-amplifier-*"]}},
            coordinator=_make_coordinator(),
        )
        assert isinstance(resolver.include, list)


class TestExclude:
    """exclude property — reads list from nested ``server`` config block."""

    def test_defaults_to_empty_list(self) -> None:
        """exclude returns [] when not set in config."""
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.exclude == []

    def test_returns_list_from_config(self) -> None:
        """exclude returns the configured list from the nested ``server`` block."""
        resolver = ConfigResolver(
            config={
                "server": {
                    "exclude": ["*-secrets", "-home-dicolomb-workspaces-team-pulse-*"],
                },
            },
            coordinator=_make_coordinator(),
        )
        assert resolver.exclude == [
            "*-secrets",
            "-home-dicolomb-workspaces-team-pulse-*",
        ]

    def test_returns_list_type(self) -> None:
        """exclude always returns a list instance."""
        resolver = ConfigResolver(
            config={"server": {"exclude": ["*-secrets"]}},
            coordinator=_make_coordinator(),
        )
        assert isinstance(resolver.exclude, list)


class TestIncludeUnionSemantics:
    """include unions config, coordinator config, and env var (deduped, order-preserved)."""

    ENV = "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_INCLUDE"

    def test_include_from_config_only(self, monkeypatch) -> None:
        monkeypatch.delenv(self.ENV, raising=False)
        resolver = ConfigResolver(
            config={"server": {"include": ["-home-dicolomb-amplifier-*"]}},
            coordinator=_make_coordinator(),
        )
        assert resolver.include == ["-home-dicolomb-amplifier-*"]

    def test_include_from_coordinator_only(self, monkeypatch) -> None:
        monkeypatch.delenv(self.ENV, raising=False)
        coordinator = _make_coordinator(
            config={"server": {"include": ["-home-dicolomb-workspaces-*"]}},
        )
        resolver = ConfigResolver(config={}, coordinator=coordinator)
        assert resolver.include == ["-home-dicolomb-workspaces-*"]

    def test_include_from_env_var_only(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "-home-dicolomb-amplifier-*,-home-dicolomb-workspaces-*")
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.include == ["-home-dicolomb-amplifier-*", "-home-dicolomb-workspaces-*"]

    def test_include_union_all_three_layers(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "-home-dicolomb-personal-projects-*")
        coordinator = _make_coordinator(
            config={"server": {"include": ["-home-dicolomb-workspaces-*"]}}
        )
        resolver = ConfigResolver(
            config={"server": {"include": ["-home-dicolomb-amplifier-*"]}},
            coordinator=coordinator,
        )
        assert resolver.include == [
            "-home-dicolomb-amplifier-*",
            "-home-dicolomb-workspaces-*",
            "-home-dicolomb-personal-projects-*",
        ]

    def test_include_deduplication(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "-home-dicolomb-amplifier-*")
        resolver = ConfigResolver(
            config={"server": {"include": ["-home-dicolomb-amplifier-*"]}},
            coordinator=_make_coordinator(),
        )
        assert resolver.include == ["-home-dicolomb-amplifier-*"]

    def test_empty_env_var_contributes_nothing(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "")
        resolver = ConfigResolver(
            config={"server": {"include": ["-home-dicolomb-amplifier-*"]}},
            coordinator=_make_coordinator(),
        )
        assert resolver.include == ["-home-dicolomb-amplifier-*"]

    def test_whitespace_only_env_var_ignored(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "  ,  ")
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.include == []


class TestExcludeUnionSemantics:
    """exclude unions config, coordinator config, and env var (deduped, order-preserved)."""

    ENV = "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_EXCLUDE"

    def test_exclude_from_config_only(self, monkeypatch) -> None:
        monkeypatch.delenv(self.ENV, raising=False)
        resolver = ConfigResolver(
            config={"server": {"exclude": ["*-secrets"]}},
            coordinator=_make_coordinator(),
        )
        assert resolver.exclude == ["*-secrets"]

    def test_exclude_from_coordinator_only(self, monkeypatch) -> None:
        monkeypatch.delenv(self.ENV, raising=False)
        coordinator = _make_coordinator(
            config={"server": {"exclude": ["*-scratch"]}},
        )
        resolver = ConfigResolver(config={}, coordinator=coordinator)
        assert resolver.exclude == ["*-scratch"]

    def test_exclude_from_env_var_only(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "*-secrets,*-scratch")
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.exclude == ["*-secrets", "*-scratch"]

    def test_exclude_union_all_three_layers(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "*-personal")
        coordinator = _make_coordinator(config={"server": {"exclude": ["*-scratch"]}})
        resolver = ConfigResolver(
            config={"server": {"exclude": ["*-secrets"]}},
            coordinator=coordinator,
        )
        assert resolver.exclude == ["*-secrets", "*-scratch", "*-personal"]

    def test_exclude_deduplication(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "*-secrets")
        resolver = ConfigResolver(
            config={"server": {"exclude": ["*-secrets"]}},
            coordinator=_make_coordinator(),
        )
        assert resolver.exclude == ["*-secrets"]

    def test_empty_env_var_contributes_nothing(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "")
        resolver = ConfigResolver(
            config={"server": {"exclude": ["*-secrets"]}},
            coordinator=_make_coordinator(),
        )
        assert resolver.exclude == ["*-secrets"]

    def test_whitespace_only_env_var_ignored(self, monkeypatch) -> None:
        monkeypatch.setenv(self.ENV, "  ,  ")
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator())
        assert resolver.exclude == []
