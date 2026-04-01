"""Tests for mount() — skill fetch phase (happy path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _make_coordinator(server_url: str | None, skill_path: Path | None) -> MagicMock:
    """Build a minimal coordinator mock for skill fetch phase tests.

    - coordinator.hooks.register returns MagicMock
    - coordinator.collect_contributions is AsyncMock returning []
    - coordinator.get_capability('skills_discovery') returns a mock with
      .find(skill_name) returning metadata (with .path = skill_path) when
      skill_path is not None, else returns None.
    """
    coordinator = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock())
    coordinator.collect_contributions = AsyncMock(return_value=[])

    # Configure skills_discovery capability
    if skill_path is not None:
        skills_discovery = MagicMock()
        metadata = MagicMock()
        metadata.path = skill_path
        skills_discovery.find = MagicMock(return_value=metadata)
        _skills_discovery_cap = skills_discovery
    else:
        _skills_discovery_cap = None

    def _get_capability(name: str) -> object:
        if name == "skills_discovery":
            return _skills_discovery_cap
        return None

    coordinator.get_capability = MagicMock(side_effect=_get_capability)
    # Put server_url in coordinator.config so ConfigResolver can find it
    coordinator.config = {"context_intelligence_server_url": server_url} if server_url else {}

    return coordinator


class TestMountSkillFetchHappyPath:
    """mount() fetches watched skills when server_url and skills_discovery are available."""

    async def test_fetch_called_for_watched_skill(self, tmp_path: Path) -> None:
        """SkillFetcher.fetch is called once with the watched skill name and its path."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            cleanup = await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        mock_fetcher_instance.fetch.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )
        assert callable(cleanup)

    async def test_cleanup_is_still_callable_after_fetch(self, tmp_path: Path) -> None:
        """cleanup() returned from mount() can be awaited without error after fetch."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            cleanup = await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        # Should be awaitable without error
        await cleanup()


class TestMountSkillFetchSkipsWhenUnconfigured:
    """mount() skips skill fetch gracefully when server_url or skills_discovery is absent."""

    async def test_no_fetch_when_server_url_is_none(self, tmp_path: Path) -> None:
        """SkillFetcher.fetch is NOT called and SKILL.md is unchanged when server_url is None."""
        from amplifier_module_hook_context_intelligence import mount

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        # skill_path is set so skills_discovery capability is available, but server_url=None
        coordinator = _make_coordinator(server_url=None, skill_path=skill_path)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch = AsyncMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            cleanup = await mount(coordinator, config={})

        mock_fetcher_instance.fetch.assert_not_called()
        # SKILL.md was never written
        assert not skill_path.exists()
        assert callable(cleanup)

    async def test_no_fetch_when_skills_discovery_is_none(self) -> None:
        """SkillFetcher.fetch is NOT called when skills_discovery capability is None."""
        from amplifier_module_hook_context_intelligence import mount

        # skill_path=None causes _make_coordinator to return skills_discovery capability as None
        coordinator = _make_coordinator(server_url="http://localhost:8000", skill_path=None)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch = AsyncMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        mock_fetcher_instance.fetch.assert_not_called()

    async def test_mount_still_returns_cleanup_when_fetch_skipped(self) -> None:
        """mount() returns a callable, awaitable cleanup even when the fetch phase is skipped."""
        from amplifier_module_hook_context_intelligence import mount

        # Both server_url=None and skill_path=None — fetch is skipped on both counts
        coordinator = _make_coordinator(server_url=None, skill_path=None)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch = AsyncMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            cleanup = await mount(coordinator, config={})

        assert callable(cleanup)
        # Must be awaitable without raising
        await cleanup()


class TestSkillUnloadedHandler:
    """mount() registers skill:unloaded handler that creates tasks for watched skills."""

    async def test_creates_task_for_watched_skill(self, tmp_path: Path) -> None:
        """Handler calls asyncio.create_task once for a skill in WATCHED_SKILLS."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        # Capture registered handlers via side_effect
        registered: dict[str, object] = {}

        def capture_register(event: str, handler: object, **kwargs: object) -> MagicMock:
            registered[event] = handler
            return MagicMock()

        coordinator.hooks.register = MagicMock(side_effect=capture_register)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        assert "skill:unloaded" in registered

        handler = registered["skill:unloaded"]
        with patch("asyncio.create_task") as mock_create_task:
            await handler(  # type: ignore[operator]
                "skill:unloaded", {"skill_name": "context-intelligence-graph-query"}
            )

        mock_create_task.assert_called_once()

    async def test_does_not_create_task_for_unwatched_skill(self, tmp_path: Path) -> None:
        """Handler does NOT call asyncio.create_task for a skill NOT in WATCHED_SKILLS."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        # Capture registered handlers via side_effect
        registered: dict[str, object] = {}

        def capture_register(event: str, handler: object, **kwargs: object) -> MagicMock:
            registered[event] = handler
            return MagicMock()

        coordinator.hooks.register = MagicMock(side_effect=capture_register)

        # Use AsyncMock for the entire fetcher instance so that attribute access
        # (e.g. .fetch) automatically returns awaitable AsyncMock children.
        # Note: a RuntimeWarning about unawaited coroutines may appear during teardown
        # in Python 3.13 — this is a known mock teardown artifact, not a bug.
        mock_fetcher_instance = AsyncMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        assert "skill:unloaded" in registered

        handler = registered["skill:unloaded"]
        with patch("asyncio.create_task") as mock_create_task:
            await handler(  # type: ignore[operator]
                "skill:unloaded", {"skill_name": "some-other-unrelated-skill"}
            )

        mock_create_task.assert_not_called()

    async def test_does_not_crash_when_metadata_not_found(self, tmp_path: Path) -> None:
        """Handler returns cleanly when skills_discovery.find() returns None."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        # Build coordinator where skills_discovery exists but find() returns None
        coordinator = MagicMock()
        coordinator.collect_contributions = AsyncMock(return_value=[])
        coordinator.config = {"context_intelligence_server_url": "http://localhost:8000"}

        skills_discovery = MagicMock()
        skills_discovery.find = MagicMock(return_value=None)

        def _get_capability(name: str) -> object:
            if name == "skills_discovery":
                return skills_discovery
            return None

        coordinator.get_capability = MagicMock(side_effect=_get_capability)

        # Capture registered handlers via side_effect
        registered: dict[str, object] = {}

        def capture_register(event: str, handler: object, **kwargs: object) -> MagicMock:
            registered[event] = handler
            return MagicMock()

        coordinator.hooks.register = MagicMock(side_effect=capture_register)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        assert "skill:unloaded" in registered

        handler = registered["skill:unloaded"]
        with patch("asyncio.create_task") as mock_create_task:
            # Should not raise AttributeError even though find() returned None
            await handler(  # type: ignore[operator]
                "skill:unloaded", {"skill_name": "context-intelligence-graph-query"}
            )

        mock_create_task.assert_not_called()


class TestMountNoOpWhenServerUrlAbsent:
    """When server_url is not configured, mount() must not touch skills_discovery at all."""

    async def test_get_capability_not_called_when_no_server_url(self) -> None:
        """coordinator.get_capability must NOT be called when server_url is None."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(server_url=None, skill_path=None)
        await mount(coordinator, config={})

        # get_capability should never have been called for skills_discovery
        for call in coordinator.get_capability.call_args_list:
            assert call.args[0] != "skills_discovery", (
                "get_capability('skills_discovery') was called even though server_url is None"
            )

    async def test_skill_unloaded_not_registered_when_no_server_url(self) -> None:
        """skill:unloaded handler must NOT be registered when server_url is None."""
        from amplifier_module_hook_context_intelligence import mount

        registered_events: list[str] = []

        def capture(event: str, handler: object, **kwargs: object) -> object:
            registered_events.append(event)
            return MagicMock()

        coordinator = _make_coordinator(server_url=None, skill_path=None)
        coordinator.hooks.register = MagicMock(side_effect=capture)

        await mount(coordinator, config={})

        assert "skill:unloaded" not in registered_events, (
            "skill:unloaded handler was registered even though server_url is None"
        )


class TestMountThreeWayBranch:
    """mount() routes to unreachable/old-server/new-server based on check_server_version."""

    async def test_unreachable_server_no_op(self, tmp_path: Path) -> None:
        """Unreachable server: SKILL.md untouched, fetch not called, write_legacy_content not called."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=False, version=None)
        )
        mock_fetcher_instance.fetch = AsyncMock()
        mock_fetcher_instance.write_legacy_content = MagicMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        assert not skill_path.exists()
        mock_fetcher_instance.fetch.assert_not_called()
        mock_fetcher_instance.write_legacy_content.assert_not_called()

    async def test_old_server_write_legacy_content(self, tmp_path: Path) -> None:
        """Old server (reachable=True, version=None): write_legacy_content called once, fetch not called."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version=None)
        )
        mock_fetcher_instance.fetch = AsyncMock()
        mock_fetcher_instance.write_legacy_content = MagicMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        mock_fetcher_instance.write_legacy_content.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )
        mock_fetcher_instance.fetch.assert_not_called()

    async def test_new_server_fetch(self, tmp_path: Path) -> None:
        """New server (reachable=True, version='2.0.0'): fetch called once, write_legacy_content not called."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_instance.write_legacy_content = MagicMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={"context_intelligence_server_url": "http://localhost:8000"},
            )

        mock_fetcher_instance.fetch.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )
        mock_fetcher_instance.write_legacy_content.assert_not_called()
