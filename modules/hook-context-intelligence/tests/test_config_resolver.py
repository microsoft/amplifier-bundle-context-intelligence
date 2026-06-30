"""Tests for HookConfigResolver resolution chains."""

from pathlib import Path
from unittest.mock import MagicMock

import fnmatch
import pytest

from amplifier_module_hook_context_intelligence.config_resolver import (
    HookConfigResolver as ConfigResolver,
)  # noqa: PLC0414
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
    def test_defaults_to_stream_delta_glob(self) -> None:
        """exclude_events defaults to the llm:stream_*delta convention glob when not set.

        The pattern expresses the transient-streaming-delta category (fnmatch), not one
        hardcoded event name.  It is intentionally IDENTICAL to amplifier-module-hooks-logging's
        _DEFAULT_EXCLUDE_EVENTS, aligned by the provider streaming contract convention, NOT
        by shared code or import — the two hooks must remain decoupled.
        """
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.exclude_events == {"llm:stream_*delta"}

    def test_explicit_empty_list_disables_filter(self) -> None:
        """exclude_events: [] (explicit empty) disables the filter entirely.

        An explicit empty list opts back in to full logging/dispatch.
        The distinction between "unset" (default applies) and "set to []" (no filter)
        is intentional: unset uses the default; [] means the operator wants everything.
        """
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"exclude_events": []},
            coordinator=coordinator,
        )

        assert resolver.exclude_events == frozenset()

    def test_stream_block_delta_excluded_by_default(self) -> None:
        """llm:stream_block_delta is matched by the default glob — treated as excluded."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert any(fnmatch.fnmatch("llm:stream_block_delta", p) for p in resolver.exclude_events)

    def test_stream_block_start_not_excluded_by_default(self) -> None:
        """llm:stream_block_start is NOT matched by the default glob — structural event spared."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert not any(
            fnmatch.fnmatch("llm:stream_block_start", p) for p in resolver.exclude_events
        )

    def test_stream_block_end_not_excluded_by_default(self) -> None:
        """llm:stream_block_end is NOT matched by the default glob — structural event spared."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert not any(fnmatch.fnmatch("llm:stream_block_end", p) for p in resolver.exclude_events)

    def test_stream_aborted_not_excluded_by_default(self) -> None:
        """llm:stream_aborted is NOT matched by the default glob — structural event spared."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert not any(fnmatch.fnmatch("llm:stream_aborted", p) for p in resolver.exclude_events)

    def test_ordinary_event_not_excluded_by_default(self) -> None:
        """Ordinary events like llm:response are NOT matched by the default glob."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert not any(fnmatch.fnmatch("llm:response", p) for p in resolver.exclude_events)

    def test_glob_spares_structural_streaming_events(self) -> None:
        """The llm:stream_*delta glob explicitly spares the structural streaming events.

        llm:stream_block_delta  -> matched  (suppressed by default)
        llm:stream_block_start  -> no match (passes through)
        llm:stream_block_end    -> no match (passes through)
        llm:stream_aborted      -> no match (passes through)
        """
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)
        patterns = resolver.exclude_events

        def is_excluded(event: str) -> bool:
            return any(fnmatch.fnmatch(event, p) for p in patterns)

        assert is_excluded("llm:stream_block_delta"), "delta must be suppressed"
        assert not is_excluded("llm:stream_block_start"), "block_start must pass through"
        assert not is_excluded("llm:stream_block_end"), "block_end must pass through"
        assert not is_excluded("llm:stream_aborted"), "stream_aborted must pass through"

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

    def test_returns_none_when_not_configured(self) -> None:
        """Returns None when context_intelligence_api_key not in config."""
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))

        assert resolver.context_intelligence_api_key is None

    def test_returns_string_when_configured(self) -> None:
        """Returns the API key string when configured."""
        resolver = ConfigResolver(
            config={"context_intelligence_api_key": "my-secret-key"},
            coordinator=_make_coordinator(config={}),
        )

        assert resolver.context_intelligence_api_key == "my-secret-key"

    def test_returns_none_for_empty_string(self) -> None:
        """Returns None when value is an empty string (falsy)."""
        resolver = ConfigResolver(
            config={"context_intelligence_api_key": ""},
            coordinator=_make_coordinator(config={}),
        )

        assert resolver.context_intelligence_api_key is None

    def test_coerces_non_string_to_string(self) -> None:
        """Coerces non-string values to str."""
        resolver = ConfigResolver(
            config={"context_intelligence_api_key": 12345},
            coordinator=_make_coordinator(config={}),
        )

        assert resolver.context_intelligence_api_key == "12345"
        assert isinstance(resolver.context_intelligence_api_key, str)


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

    def test_zero_is_clamped_to_one(self) -> None:
        """dispatch_queue_capacity clamps 0 up to 1 (prevents UNBOUNDED asyncio.Queue, TB-03)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"dispatch_queue_capacity": 0}, coordinator=coordinator)

        assert resolver.dispatch_queue_capacity == 1

    def test_negative_is_clamped_to_one(self) -> None:
        """dispatch_queue_capacity clamps negative values up to 1."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"dispatch_queue_capacity": -5}, coordinator=coordinator)

        assert resolver.dispatch_queue_capacity == 1

    def test_one_is_allowed(self) -> None:
        """dispatch_queue_capacity of exactly 1 is valid and stays 1."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={"dispatch_queue_capacity": 1}, coordinator=coordinator)

        assert resolver.dispatch_queue_capacity == 1


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
    """settings.yaml fallback was removed in D1 (contract fix). These tests verify the new behavior.

    The hook is now a pure mount-config consumer. Config arrives via the mount config dict,
    already resolved by the app layer. No env vars, no settings.yaml reads.
    """

    def test_server_url_not_read_from_settings_yaml(self) -> None:
        """server_url is NOT read from settings.yaml — the hook is a pure mount-config consumer."""
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        # Without the key in config, resolver returns None (no fallback to settings.yaml)
        assert resolver.context_intelligence_server_url is None

    def test_api_key_not_read_from_settings_yaml(self) -> None:
        """api_key is NOT read from settings.yaml — the hook is a pure mount-config consumer."""
        resolver = ConfigResolver(config={}, coordinator=_make_coordinator(config={}))
        assert resolver.context_intelligence_api_key is None

    def test_server_url_from_config_works(self) -> None:
        """server_url from mount config (already resolved by app) works correctly."""
        resolver = ConfigResolver(
            config={"context_intelligence_server_url": "http://from-config"},
            coordinator=_make_coordinator(config={}),
        )
        assert resolver.context_intelligence_server_url == "http://from-config"


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


class TestWorkingDir:
    """working_dir property — live read of the session.working_dir capability.

    Unlike project_slug (cached), working_dir is read live on every access so it
    reflects mid-session working-directory changes.  It returns "" when the
    capability is unavailable.
    """

    def test_returns_capability_value_when_present(self) -> None:
        """working_dir returns the path string from the session.working_dir capability."""
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value="/home/user/project")
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.working_dir == "/home/user/project"

    def test_returns_empty_string_when_capability_returns_none(self) -> None:
        """working_dir returns '' when get_capability('session.working_dir') is None."""
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.working_dir == ""

    def test_returns_empty_string_when_capability_returns_empty_string(self) -> None:
        """working_dir returns '' when the capability returns '' (present-but-empty)."""
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value="")
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.working_dir == ""

    def test_returns_empty_string_when_capability_returns_non_str(self) -> None:
        """working_dir returns '' when the capability returns a non-string type."""
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=42)
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.working_dir == ""

    def test_returns_empty_string_when_coordinator_lacks_get_capability(self) -> None:
        """working_dir returns '' when the coordinator has no get_capability method."""
        bare = _make_bare_coordinator()  # plain object(), no get_capability
        resolver = ConfigResolver(config={}, coordinator=bare)

        assert resolver.working_dir == ""

    def test_returns_empty_string_when_coordinator_is_none(self) -> None:
        """working_dir returns '' when coordinator is None."""
        resolver = ConfigResolver(config={}, coordinator=None)

        assert resolver.working_dir == ""

    def test_not_cached_reads_live(self) -> None:
        """working_dir is NOT cached — each access re-reads the capability.

        This is intentional: working_dir must reflect mid-session cwd changes,
        unlike project_slug which is cached after first access.
        """
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value="/first/path")
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        first = resolver.working_dir
        assert first == "/first/path"

        # Simulate capability value changing mid-session.
        coordinator.get_capability = MagicMock(return_value="/second/path")
        second = resolver.working_dir
        assert second == "/second/path"

        # Contrast with project_slug which IS cached.
        assert first != second

    def test_returns_str_type(self) -> None:
        """working_dir always returns a str."""
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value="/some/path")
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert isinstance(resolver.working_dir, str)


class TestDispatchBackoffKnobs:
    """dispatch_backoff_initial, dispatch_backoff_max, dispatch_backoff_jitter properties."""

    def test_backoff_initial_defaults_to_1_0(self) -> None:
        """dispatch_backoff_initial returns 1.0 when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.dispatch_backoff_initial == 1.0

    def test_backoff_initial_reads_string_as_float(self) -> None:
        """dispatch_backoff_initial reads the string '2' as 2.0."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"dispatch_backoff_initial": "2"}, coordinator=coordinator
        )

        assert resolver.dispatch_backoff_initial == 2.0
        assert isinstance(resolver.dispatch_backoff_initial, float)

    def test_backoff_max_defaults_to_30_0(self) -> None:
        """dispatch_backoff_max returns 30.0 when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.dispatch_backoff_max == 30.0

    def test_backoff_max_reads_string_as_float(self) -> None:
        """dispatch_backoff_max reads the string '10' as 10.0."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"dispatch_backoff_max": "10"}, coordinator=coordinator
        )

        assert resolver.dispatch_backoff_max == 10.0
        assert isinstance(resolver.dispatch_backoff_max, float)

    def test_backoff_jitter_defaults_to_true(self) -> None:
        """dispatch_backoff_jitter returns True when not configured."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(config={}, coordinator=coordinator)

        assert resolver.dispatch_backoff_jitter is True

    def test_backoff_jitter_python_false_stays_false(self) -> None:
        """dispatch_backoff_jitter: Python bool False stays False (not str coercion path)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"dispatch_backoff_jitter": False}, coordinator=coordinator
        )

        assert resolver.dispatch_backoff_jitter is False

    def test_backoff_jitter_string_false_is_false(self) -> None:
        """REGRESSION GUARD: literal string 'false' -> False (avoids bool('false')==True footgun)."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"dispatch_backoff_jitter": "false"}, coordinator=coordinator
        )

        assert resolver.dispatch_backoff_jitter is False

    def test_backoff_jitter_string_true_is_true(self) -> None:
        """REGRESSION GUARD: literal string 'true' -> True."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"dispatch_backoff_jitter": "true"}, coordinator=coordinator
        )

        assert resolver.dispatch_backoff_jitter is True

    @pytest.mark.parametrize("falsey", ["0", "no", "off", "FALSE", " false ", ""])
    def test_backoff_jitter_falsey_strings_are_false(self, falsey: str) -> None:
        """All falsey string variants ('0','no','off','FALSE',' false ','') resolve to False."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={"dispatch_backoff_jitter": falsey}, coordinator=coordinator
        )

        assert resolver.dispatch_backoff_jitter is False

    def test_all_three_knobs_resolve_together(self) -> None:
        """All three backoff knobs resolve correctly from a single plain config dict."""
        coordinator = _make_coordinator(config={})
        resolver = ConfigResolver(
            config={
                "dispatch_backoff_initial": "5",
                "dispatch_backoff_max": "60",
                "dispatch_backoff_jitter": "false",
            },
            coordinator=coordinator,
        )

        assert resolver.dispatch_backoff_initial == 5.0
        assert resolver.dispatch_backoff_max == 60.0
        assert resolver.dispatch_backoff_jitter is False
