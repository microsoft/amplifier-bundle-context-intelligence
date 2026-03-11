"""Tests for ConfigResolver base_path resolution chain."""

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
