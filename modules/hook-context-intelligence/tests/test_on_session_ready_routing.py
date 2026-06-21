"""Tests for on_session_ready fan-out — destination selection and dispatcher installation."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_coordinator(
    working_dir: str | None = "/home/user/project",
    contributed_events: list | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = {}
    capabilities: dict[str, Any] = {}

    def _register_capability(name: str, value: Any) -> None:
        capabilities[name] = value

    coordinator.register_capability = MagicMock(side_effect=_register_capability)

    def _get_capability(name: str) -> Any:
        if name == "session.working_dir":
            return working_dir
        return capabilities.get(name)

    coordinator.get_capability = MagicMock(side_effect=_get_capability)
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock())
    coordinator.collect_contributions = AsyncMock(return_value=contributed_events or [])
    coordinator._capabilities = capabilities
    return coordinator


async def _mount_and_ready(config: dict, working_dir: str | None = "/home/user/project") -> tuple:
    """Helper: run mount + on_session_ready and return (coordinator, handler, cleanup)."""
    from amplifier_module_hook_context_intelligence import mount, on_session_ready

    coordinator = _make_coordinator(working_dir=working_dir)
    cleanup = await mount(coordinator, config=config)
    handler = coordinator._capabilities["context_intelligence._hook_state"]["logging_handler"]
    await on_session_ready(coordinator)
    return coordinator, handler, cleanup


class TestDestinationSelection:
    async def test_matching_destination_installs_dispatcher(self) -> None:
        config = {
            "destinations": {
                "team": {
                    "url": "http://team:8000",
                    "api_key": "tk",
                    "include": ["**/client-x/**"],
                }
            }
        }
        _, handler, cleanup = await _mount_and_ready(config, working_dir="/home/user/client-x/app")
        assert len(handler._dispatchers) == 1
        assert handler._dispatchers[0]._name == "team"
        await cleanup()

    async def test_no_match_installs_no_dispatcher(self, caplog: pytest.LogCaptureFixture) -> None:
        config = {
            "destinations": {
                "team": {
                    "url": "http://team:8000",
                    "api_key": "tk",
                    "include": ["**/client-x/**"],
                }
            }
        }
        with caplog.at_level(logging.WARNING, logger="amplifier_module_hook_context_intelligence"):
            _, handler, cleanup = await _mount_and_ready(
                config, working_dir="/home/user/neutral-project"
            )
        assert len(handler._dispatchers) == 0, "No match → no dispatchers"
        assert any("routed to none" in r.message.lower() for r in caplog.records), (
            "Expected WARNING about routed to none"
        )
        await cleanup()

    async def test_multiple_matching_dispatchers(self) -> None:
        config = {
            "destinations": {
                "personal": {"url": "http://p:8000", "api_key": "pk"},
                "team": {
                    "url": "http://t:8000",
                    "api_key": "tk",
                    "include": ["**/client-x/**"],
                },
            }
        }
        # Working dir matches both: personal (["**"]) and team (**/client-x/**)
        _, handler, cleanup = await _mount_and_ready(config, working_dir="/home/user/client-x/app")
        assert len(handler._dispatchers) == 2
        names = {d._name for d in handler._dispatchers}
        assert names == {"personal", "team"}
        await cleanup()

    async def test_active_destinations_info_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        config = {
            "log_level": "INFO",  # ensure INFO messages are not suppressed by WARNING default
            "destinations": {"personal": {"url": "http://p:8000", "api_key": "pk"}},
        }
        with caplog.at_level(logging.INFO, logger="amplifier_module_hook_context_intelligence"):
            _, handler, cleanup = await _mount_and_ready(config)
        assert any("active" in r.message.lower() for r in caplog.records), (
            "Expected INFO message about active destinations"
        )
        await cleanup()


class TestWorkingDirRequirement:
    async def test_absent_working_dir_raises_when_destinations_configured(self) -> None:
        """session.working_dir absent + destinations configured → RuntimeError (C2)."""
        from amplifier_module_hook_context_intelligence import mount, on_session_ready

        config = {"destinations": {"default": {"url": "http://x:8000", "api_key": "k"}}}
        coordinator = _make_coordinator(working_dir=None)
        cleanup = await mount(coordinator, config=config)
        with pytest.raises(RuntimeError, match="session.working_dir"):
            await on_session_ready(coordinator)
        await cleanup()

    async def test_absent_working_dir_ok_when_no_destinations(self) -> None:
        """session.working_dir absent is fine when no destinations (local-only)."""
        from amplifier_module_hook_context_intelligence import mount, on_session_ready

        config: dict[str, Any] = {}
        coordinator = _make_coordinator(working_dir=None)
        cleanup = await mount(coordinator, config=config)
        # Must not raise
        await on_session_ready(coordinator)
        await cleanup()


class TestS1MigrationWarning:
    async def test_legacy_config_emits_migration_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """S1: using context_intelligence_server_url without destinations → WARNING at mount."""
        config = {
            "context_intelligence_server_url": "http://legacy:8000",
            "context_intelligence_api_key": "lk",
        }
        with caplog.at_level(logging.WARNING, logger="amplifier_module_hook_context_intelligence"):
            coordinator = _make_coordinator()
            from amplifier_module_hook_context_intelligence import mount

            cleanup = await mount(coordinator, config=config)
        assert any(
            "legacy" in r.message.lower() and "context_intelligence_server_url" in r.message
            for r in caplog.records
        ), "Expected S1 migration warning about legacy config"
        await cleanup()

    async def test_destinations_config_no_migration_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """S1: using destinations dict → no migration warning."""
        config = {"destinations": {"default": {"url": "http://x:8000", "api_key": "k"}}}
        with caplog.at_level(logging.WARNING, logger="amplifier_module_hook_context_intelligence"):
            coordinator = _make_coordinator()
            from amplifier_module_hook_context_intelligence import mount

            cleanup = await mount(coordinator, config=config)
        migration_warnings = [
            r
            for r in caplog.records
            if "legacy" in r.message.lower() and "context_intelligence_server_url" in r.message
        ]
        assert not migration_warnings, "Should not emit migration warning when destinations set"
        await cleanup()


class TestNoDestinations:
    async def test_empty_config_info_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        config: dict[str, Any] = {
            "log_level": "INFO"
        }  # ensure INFO not filtered by WARNING default
        with caplog.at_level(logging.INFO, logger="amplifier_module_hook_context_intelligence"):
            _, handler, cleanup = await _mount_and_ready(config)
        assert len(handler._dispatchers) == 0
        assert any("no destinations" in r.message.lower() for r in caplog.records)
        await cleanup()
