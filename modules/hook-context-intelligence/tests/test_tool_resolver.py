"""Tests for ToolConfigResolver resolution chains.

ToolConfigResolver is the analytics-only resolver for CI tools when the hook is
NOT mounted.  It shares the same priority chain as HookConfigResolver for the
three keys tools actually use (server_url, api_key, workspace), with one
deliberate difference: workspace does NOT fall back to project_slug because
there is no live capture session to derive it from.
"""

from unittest.mock import MagicMock

from context_intelligence.tool_resolver import ToolConfigResolver


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
# TestToolConfigResolverServerUrl
# ---------------------------------------------------------------------------


class TestToolConfigResolverServerUrl:
    """context_intelligence_server_url: config dict → coordinator → env var → settings.yaml."""

    def test_config_dict_wins_over_coordinator(self) -> None:
        """Mount-time config dict has highest priority over coordinator.config."""
        coordinator = _make_coordinator(
            config={"context_intelligence_server_url": "http://from-coordinator"}
        )
        resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": "http://from-config"},
            coordinator=coordinator,
        )
        assert resolver.context_intelligence_server_url == "http://from-config"

    def test_coordinator_fallback_when_config_absent(self) -> None:
        """Falls back to coordinator.config when config dict has no server URL."""
        coordinator = _make_coordinator(
            config={"context_intelligence_server_url": "http://from-coordinator"}
        )
        resolver = ToolConfigResolver(config={}, coordinator=coordinator)
        assert resolver.context_intelligence_server_url == "http://from-coordinator"

    def test_env_var_fallback_when_config_and_coordinator_absent(self, monkeypatch) -> None:
        """AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL env var is the third priority."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", "http://from-env")
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_server_url == "http://from-env"

    def test_config_wins_over_env_var(self, monkeypatch) -> None:
        """Config dict wins over env var."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", "http://from-env")
        resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": "http://from-config"},
            coordinator=_make_coordinator(config={}),
        )
        assert resolver.context_intelligence_server_url == "http://from-config"

    def test_coordinator_wins_over_env_var(self, monkeypatch) -> None:
        """Coordinator config wins over env var."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", "http://from-env")
        resolver = ToolConfigResolver(
            config={},
            coordinator=_make_coordinator(
                config={"context_intelligence_server_url": "http://from-coordinator"}
            ),
        )
        assert resolver.context_intelligence_server_url == "http://from-coordinator"

    def test_settings_yaml_fallback(self, monkeypatch, tmp_path) -> None:
        """~/.amplifier/settings.yaml is the lowest-priority fallback."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "overrides:\n"
            "  hook-context-intelligence:\n"
            "    config:\n"
            "      context_intelligence_server_url: http://from-settings-yaml\n"
        )
        monkeypatch.setattr("context_intelligence.tool_resolver.SETTINGS_PATH", settings_file)
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_server_url == "http://from-settings-yaml"

    def test_env_var_wins_over_settings_yaml(self, monkeypatch, tmp_path) -> None:
        """Env var beats settings.yaml."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", "http://from-env")
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "overrides:\n"
            "  hook-context-intelligence:\n"
            "    config:\n"
            "      context_intelligence_server_url: http://from-settings-yaml\n"
        )
        monkeypatch.setattr("context_intelligence.tool_resolver.SETTINGS_PATH", settings_file)
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_server_url == "http://from-env"

    def test_returns_none_when_all_absent(self, monkeypatch, tmp_path) -> None:
        """Returns None when no source has a server URL."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.setattr(
            "context_intelligence.tool_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_server_url is None

    def test_returns_none_for_empty_string(self, monkeypatch, tmp_path) -> None:
        """Empty string config value is treated as absent."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.setattr(
            "context_intelligence.tool_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": ""},
            coordinator=_make_coordinator(config={}),
        )
        assert resolver.context_intelligence_server_url is None

    def test_returns_string_type(self) -> None:
        """Returns a str when value is present."""
        resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": "http://localhost:8000"},
            coordinator=_make_coordinator(config={}),
        )
        result = resolver.context_intelligence_server_url
        assert isinstance(result, str)

    def test_bare_coordinator_falls_back_to_config(self, monkeypatch, tmp_path) -> None:
        """Coordinator without .config attribute safely skips to config dict."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.setattr(
            "context_intelligence.tool_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        bare = _make_bare_coordinator()
        resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": "http://from-config"},
            coordinator=bare,
        )
        assert resolver.context_intelligence_server_url == "http://from-config"


# ---------------------------------------------------------------------------
# TestToolConfigResolverApiKey
# ---------------------------------------------------------------------------


class TestToolConfigResolverApiKey:
    """context_intelligence_api_key: config dict → coordinator → env var → settings.yaml."""

    def test_config_dict_wins_over_coordinator(self) -> None:
        """Mount-time config dict has highest priority."""
        coordinator = _make_coordinator(
            config={"context_intelligence_api_key": "key-from-coordinator"}
        )
        resolver = ToolConfigResolver(
            config={"context_intelligence_api_key": "key-from-config"},
            coordinator=coordinator,
        )
        assert resolver.context_intelligence_api_key == "key-from-config"

    def test_coordinator_fallback_when_config_absent(self) -> None:
        """Falls back to coordinator.config when config dict has no API key."""
        coordinator = _make_coordinator(
            config={"context_intelligence_api_key": "key-from-coordinator"}
        )
        resolver = ToolConfigResolver(config={}, coordinator=coordinator)
        assert resolver.context_intelligence_api_key == "key-from-coordinator"

    def test_env_var_fallback(self, monkeypatch) -> None:
        """AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY env var is the third priority."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", "key-from-env")
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_api_key == "key-from-env"

    def test_settings_yaml_fallback(self, monkeypatch, tmp_path) -> None:
        """settings.yaml is lowest-priority fallback for api_key."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "overrides:\n"
            "  hook-context-intelligence:\n"
            "    config:\n"
            "      context_intelligence_api_key: sk-from-settings-yaml\n"
        )
        monkeypatch.setattr("context_intelligence.tool_resolver.SETTINGS_PATH", settings_file)
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_api_key == "sk-from-settings-yaml"

    def test_returns_none_when_all_absent(self, monkeypatch, tmp_path) -> None:
        """Returns None when no source has an API key."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)
        monkeypatch.setattr(
            "context_intelligence.tool_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_api_key is None

    def test_returns_none_for_empty_string(self, monkeypatch, tmp_path) -> None:
        """Empty string config value is treated as absent."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)
        monkeypatch.setattr(
            "context_intelligence.tool_resolver.SETTINGS_PATH",
            tmp_path / "nonexistent.yaml",
        )
        resolver = ToolConfigResolver(
            config={"context_intelligence_api_key": ""},
            coordinator=_make_coordinator(config={}),
        )
        assert resolver.context_intelligence_api_key is None


# ---------------------------------------------------------------------------
# TestToolConfigResolverWorkspace
# ---------------------------------------------------------------------------


class TestToolConfigResolverWorkspace:
    """workspace: config dict → coordinator → env var → 'default'.

    Key difference from HookConfigResolver: does NOT fall back to project_slug
    (auto-derived from session.working_dir).  In analytics-only mode there is
    no live capture session to derive it from.
    """

    def test_config_dict_wins_over_coordinator(self) -> None:
        """Mount-time config dict has highest priority."""
        coordinator = _make_coordinator(config={"workspace": "from-coordinator"})
        resolver = ToolConfigResolver(config={"workspace": "from-config"}, coordinator=coordinator)
        assert resolver.workspace == "from-config"

    def test_coordinator_fallback_when_config_absent(self) -> None:
        """Falls back to coordinator.config when config dict has no workspace."""
        coordinator = _make_coordinator(config={"workspace": "from-coordinator"})
        resolver = ToolConfigResolver(config={}, coordinator=coordinator)
        assert resolver.workspace == "from-coordinator"

    def test_env_var_fallback(self, monkeypatch) -> None:
        """AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE env var is the third priority."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", "from-env")
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.workspace == "from-env"

    def test_config_wins_over_env_var(self, monkeypatch) -> None:
        """Config dict wins over env var."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", "from-env")
        resolver = ToolConfigResolver(
            config={"workspace": "from-config"}, coordinator=_make_coordinator(config={})
        )
        assert resolver.workspace == "from-config"

    def test_coordinator_wins_over_env_var(self, monkeypatch) -> None:
        """Coordinator config wins over env var."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", "from-env")
        resolver = ToolConfigResolver(
            config={},
            coordinator=_make_coordinator(config={"workspace": "from-coordinator"}),
        )
        assert resolver.workspace == "from-coordinator"

    def test_defaults_to_default_when_all_absent(self, monkeypatch) -> None:
        """Falls back to 'default' string when no source provides workspace."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", raising=False)
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.workspace == "default"

    def test_does_not_use_project_slug_derivation(self, monkeypatch) -> None:
        """workspace does NOT auto-derive from session.working_dir in analytics-only mode.

        HookConfigResolver falls back to project_slug (slugified from session.working_dir)
        when no workspace is configured.  ToolConfigResolver MUST NOT do this — it is
        designed for use without an active capture session.  The fallback is 'default'.
        """
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", raising=False)
        coordinator = _make_coordinator(config={})
        # Simulate a coordinator that has a working_dir capability (as hook would see)
        coordinator.get_capability = MagicMock(return_value="/home/user/myproject")
        resolver = ToolConfigResolver(config={}, coordinator=coordinator)
        # ToolConfigResolver must ignore get_capability and return 'default'
        assert resolver.workspace == "default"

    def test_returns_str_type(self, monkeypatch) -> None:
        """workspace always returns a str."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", raising=False)
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert isinstance(resolver.workspace, str)

    def test_bare_coordinator_falls_back_to_default(self, monkeypatch) -> None:
        """Coordinator without .config attribute safely falls back to 'default'."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", raising=False)
        bare = _make_bare_coordinator()
        resolver = ToolConfigResolver(config={}, coordinator=bare)
        assert resolver.workspace == "default"


# ---------------------------------------------------------------------------
# TestToolConfigResolverWorkspaceCaching
# ---------------------------------------------------------------------------


class TestToolConfigResolverWorkspaceCaching:
    """workspace value is cached after first access."""

    def test_workspace_cached_after_first_access(self, monkeypatch) -> None:
        """workspace returns the same object on repeated access (cached)."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", raising=False)
        resolver = ToolConfigResolver(
            config={"workspace": "my-workspace"}, coordinator=_make_coordinator(config={})
        )
        first = resolver.workspace
        second = resolver.workspace
        assert first is second

    def test_workspace_cache_does_not_read_env_twice(self, monkeypatch) -> None:
        """Env var is read once; subsequent accesses return the cached value."""
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", "from-env-first")
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator(config={}))

        first = resolver.workspace
        assert first == "from-env-first"

        # Change the env var — cached value must NOT change
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", "from-env-second")
        second = resolver.workspace
        assert second == "from-env-first"


# ---------------------------------------------------------------------------
# TestToolConfigResolverDuckTyping
# ---------------------------------------------------------------------------


class TestToolConfigResolverDuckTyping:
    """ToolConfigResolver exposes the same interface the tools depend on.

    Tools access exactly three attributes: context_intelligence_server_url,
    context_intelligence_api_key, and workspace.  This class verifies they
    exist and return the expected types — enabling duck-type compatibility
    with HookConfigResolver for the tool-facing contract.
    """

    def test_has_server_url_property(self) -> None:
        """context_intelligence_server_url attribute exists and returns str or None."""
        resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": "http://x"},
            coordinator=_make_coordinator(),
        )
        result = resolver.context_intelligence_server_url
        assert result is None or isinstance(result, str)

    def test_has_api_key_property(self) -> None:
        """context_intelligence_api_key attribute exists and returns str or None."""
        resolver = ToolConfigResolver(
            config={"context_intelligence_api_key": "key"},
            coordinator=_make_coordinator(),
        )
        result = resolver.context_intelligence_api_key
        assert result is None or isinstance(result, str)

    def test_has_workspace_property(self, monkeypatch) -> None:
        """workspace attribute exists and always returns a str."""
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE", raising=False)
        resolver = ToolConfigResolver(config={}, coordinator=_make_coordinator())
        result = resolver.workspace
        assert isinstance(result, str)

    def test_interface_is_property_based_not_method_based(self) -> None:
        """All three attributes are properties (accessed without calling them)."""
        resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": "http://x"},
            coordinator=_make_coordinator(),
        )
        # Properties: access via attribute, not via call
        assert not callable(resolver.context_intelligence_server_url)
        # workspace is always a str (not a callable)
        assert isinstance(resolver.workspace, str)

    def test_same_keys_as_hook_resolver(self) -> None:
        """ToolConfigResolver exposes the three keys that tools read from HookConfigResolver.

        Verifies the duck-type contract: a tool that does
          ``resolver.context_intelligence_server_url``
          ``resolver.context_intelligence_api_key``
          ``resolver.workspace``
        will work with EITHER HookConfigResolver or ToolConfigResolver.
        """
        from amplifier_module_hook_context_intelligence.config_resolver import HookConfigResolver

        hook_resolver = HookConfigResolver(
            config={"context_intelligence_server_url": "http://h"},
            coordinator=_make_coordinator(),
        )
        tool_resolver = ToolConfigResolver(
            config={"context_intelligence_server_url": "http://t"},
            coordinator=_make_coordinator(),
        )

        for resolver in (hook_resolver, tool_resolver):
            assert hasattr(resolver, "context_intelligence_server_url")
            assert hasattr(resolver, "context_intelligence_api_key")
            assert hasattr(resolver, "workspace")
