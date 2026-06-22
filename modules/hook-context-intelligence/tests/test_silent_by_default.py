"""Tests locking the silent-by-default contract for LoggingHandler.

Four guarantees under test:
1. Disk always writes even without server config (always-on JSONL)
2. No server config → zero log records at WARNING or above
3. URL set, no API key → zero destinations + ONE WARNING (graceful degradation) + mounts fine
4. Disk I/O error → exactly one WARNING record, zero ERROR or above
"""

import logging
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver
from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    LoggingHandler,
)

_LOGGER_NAME = "amplifier_module_hook_context_intelligence"
_RESOLVER_LOGGER = "amplifier_module_hook_context_intelligence.config_resolver"


def _make_resolver(tmp_path, *, server_url=None, api_key=None):
    """Build a minimal SimpleNamespace resolver for LoggingHandler.__init__.

    LoggingHandler uses getattr(resolver, attr, default) for all resolver
    access, so a SimpleNamespace with the required attributes is sufficient.
    """
    return types.SimpleNamespace(
        context_intelligence_server_url=server_url,
        context_intelligence_api_key=api_key,
        workspace="test-workspace",
        dispatch_timeout=10.0,
        dispatch_failure_threshold=3,
        dispatch_queue_capacity=256,
        close_drain_timeout=0.5,
        session_dir=lambda session_id: tmp_path / session_id / "context-intelligence",
    )


class TestDiskAlwaysWrites:
    """Guarantee 1: disk writes happen regardless of server configuration."""

    @pytest.mark.asyncio
    async def test_disk_always_writes_without_server_config(self, tmp_path):
        """events.jsonl is written and _dispatch_enabled is False when no server URL is configured.

        This test should be GREEN before and after the fix.
        """
        resolver = _make_resolver(tmp_path)
        handler = LoggingHandler(resolver)

        assert handler._dispatchers == [], (
            "_dispatchers should be empty when no destinations are configured"
        )

        data = {"session_id": "test-session-001", "timestamp": "2026-01-01T00:00:00"}
        await handler.__call__("tool:pre", data)

        expected_file = tmp_path / "test-session-001" / "context-intelligence" / "events.jsonl"
        assert expected_file.exists(), (
            f"events.jsonl was not written at {expected_file}. "
            "Disk writing must be unconditional regardless of server config."
        )


class TestSilentWithoutServerConfig:
    """Guarantee 2: no log noise when server is not configured."""

    @pytest.mark.asyncio
    async def test_no_server_config_emits_nothing_above_debug(self, tmp_path, caplog):
        """No WARNING or above records when no server URL is set."""
        resolver = _make_resolver(tmp_path)
        handler = LoggingHandler(resolver)

        data = {"session_id": "test-session-002", "timestamp": "2026-01-01T00:00:00"}

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            await handler.__call__("tool:pre", data)

        noisy_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(noisy_records) == 0, (
            f"Expected zero WARNING+ records but got {len(noisy_records)}: "
            + ", ".join(f"{r.levelname}: {r.message}" for r in noisy_records)
        )


class TestLegacyUrlWithoutKeyDegradesGracefully:
    """Guarantee 3: legacy url-without-api_key degrades to local-only with exactly one WARNING.

    Before the fix: resolver synthesized a 'default' destination with empty api_key, which
    made validate_destinations() raise ValueError at mount time — losing BOTH HTTP dispatch AND
    local JSONL capture (a regression from the pre-PR behavior of local-only + WARNING).

    After the fix: no destination is synthesized; validate_destinations() returns {} (no raise);
    local JSONL capture continues; exactly one WARNING is emitted so the misconfiguration is
    visible at default log level.
    """

    def _make_config_resolver(self, config: dict) -> ConfigResolver:
        coordinator = MagicMock()
        coordinator.config = {}
        coordinator.get_capability = MagicMock(return_value=None)
        return ConfigResolver(config, coordinator)

    def test_url_without_key_yields_zero_destinations(self, caplog) -> None:
        """Legacy url set + api_key unset → destinations == {} (local-only mode)."""
        resolver = self._make_config_resolver(
            {"context_intelligence_server_url": "http://localhost:9999"}
        )
        with caplog.at_level(logging.WARNING, logger=_RESOLVER_LOGGER):
            dests = resolver.destinations

        assert dests == {}, "url without api_key should yield empty destinations (local-only mode)"

    def test_url_without_key_emits_warning(self, caplog) -> None:
        """Legacy url set + api_key unset → at least one WARNING mentioning api_key."""
        resolver = self._make_config_resolver(
            {"context_intelligence_server_url": "http://localhost:9999"}
        )
        with caplog.at_level(logging.WARNING, logger=_RESOLVER_LOGGER):
            _ = resolver.destinations

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) >= 1, "url without api_key should emit at least one WARNING"
        assert "api_key" in warning_records[0].message.lower(), (
            f"WARNING should mention api_key, got: '{warning_records[0].message}'"
        )

    def test_url_without_key_validate_destinations_does_not_raise(self) -> None:
        """validate_destinations() must not raise when legacy url is set but api_key is absent."""
        resolver = self._make_config_resolver(
            {"context_intelligence_server_url": "http://localhost:9999"}
        )
        result = resolver.validate_destinations()
        assert result == {}

    def test_explicit_destination_missing_api_key_still_raises(self) -> None:
        """Explicit destinations.<name> with empty api_key still raises ValueError (fail-fast).

        This is intentionally different from the legacy synthesis path.  An explicit entry
        that the user wrote in config is a real misconfiguration and must fail loud.
        """
        resolver = self._make_config_resolver(
            {"destinations": {"broken": {"url": "http://x:8000", "api_key": ""}}}
        )
        with pytest.raises(ValueError, match="broken.*missing api_key"):
            resolver.validate_destinations()


class TestDiskErrorEmitsWarning:
    """Guarantee 4: disk I/O errors produce a WARNING, never an ERROR or traceback."""

    @pytest.mark.asyncio
    async def test_disk_error_emits_warning_not_error(self, tmp_path, monkeypatch, caplog):
        """A PermissionError on mkdir emits exactly one WARNING, zero ERROR or above.

        Before the fix (logger.exception() was used for disk write errors):
          - logger.exception() emits at ERROR level with a full traceback
          - This test FAILS because there is an ERROR-level record

        After the fix (changed to logger.warning()):
          - logger.warning() emits at WARNING level, no traceback
          - This test PASSES
        """
        resolver = _make_resolver(tmp_path)
        handler = LoggingHandler(resolver)

        def raise_permission_error(*args, **kwargs):
            raise PermissionError("disk full")

        monkeypatch.setattr(Path, "mkdir", raise_permission_error)

        data = {"session_id": "test-session-004", "timestamp": "2026-01-01T00:00:00"}

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            await handler.__call__("tool:pre", data)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0, (
            f"Expected zero ERROR+ records but got {len(error_records)}: "
            + ", ".join(f"{r.levelname}: {r.message}" for r in error_records)
            + "\nHint: logger.exception() emits at ERROR. Change it to logger.warning()."
        )

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1, (
            f"Expected exactly one WARNING record but got {len(warning_records)}: "
            + ", ".join(f"{r.levelname}: {r.message}" for r in warning_records)
        )
        assert "disk write error" in warning_records[0].message, (
            f"WARNING message should contain 'disk write error' but got: "
            f"'{warning_records[0].message}'"
        )
