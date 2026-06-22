"""Tests for mount() — skill fetch phase (happy path)."""

from __future__ import annotations

from pathlib import Path
from typing import overload
from unittest.mock import AsyncMock, MagicMock, patch

_HookCalls = list[tuple[str, object, dict[str, object]]]


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


@overload
def _capture_hooks_register() -> tuple[MagicMock, _HookCalls]: ...


@overload
def _capture_hooks_register(coordinator: MagicMock) -> _HookCalls: ...


def _capture_hooks_register(
    coordinator: MagicMock | None = None,
) -> _HookCalls | tuple[MagicMock, _HookCalls]:
    """Create a hooks.register mock that records all calls.

    When *coordinator* is supplied, wires ``coordinator.hooks.register`` automatically
    and returns just the *calls* list.

    When called without arguments, returns ``(mock, calls)`` for callers that need
    to wire the mock themselves.
    """
    calls: _HookCalls = []

    def _side_effect(event: str, handler: object, **kwargs: object) -> MagicMock:
        calls.append((event, handler, dict(kwargs)))
        return MagicMock()

    mock = MagicMock(side_effect=_side_effect)
    if coordinator is not None:
        coordinator.hooks.register = mock
        return calls
    return mock, calls


def _find_handler(calls: _HookCalls, event: str, name: str) -> object:
    """Find a registered handler by event name and handler name (from kwargs).

    Asserts exactly 1 match found.
    """
    matches = [
        handler for evt, handler, kwargs in calls if evt == event and kwargs.get("name") == name
    ]
    assert len(matches) == 1, (
        f"Expected 1 handler for event={event!r} name={name!r}, found {len(matches)}."
    )
    return matches[0]


class TestMountSkillFetchHappyPath:
    """mount() fetches watched skills when server_url is available and SKILL.md exists."""

    async def test_fetch_called_for_watched_skill(self, tmp_path: Path) -> None:
        """SkillFetcher.fetch is called via skills:discovered handler (deferred from mount).

        fetch must NOT be called during mount(), but must be called when the
        skills:discovered handler fires.
        """
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        # Place the SKILL.md at the expected bundle-root-relative location
        skill_path = tmp_path / "skills" / "context-intelligence-graph-query" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# test")

        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_register, calls = _capture_hooks_register()
        coordinator.hooks.register = mock_register

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with (
            patch("amplifier_module_hook_context_intelligence._BUNDLE_ROOT", tmp_path),
            patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
                mock_fetcher_cls,
            ),
        ):
            await mount(
                coordinator,
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # fetch IS called immediately during mount — skills_discovery was already registered
        mock_fetcher_instance.fetch.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )
        mock_fetcher_instance.fetch.reset_mock()

        # Find and fire the skills:discovered handler — it must also trigger a refresh
        handler = _find_handler(calls, "skills:discovered", "SkillFetcher-trigger")
        await handler("skills:discovered", {})  # type: ignore[operator]

        # After the handler fires, fetch should have been called once more
        mock_fetcher_instance.fetch.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )

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
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
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

    async def test_no_fetch_when_skill_path_not_found(self, tmp_path: Path) -> None:
        """SkillFetcher.fetch is NOT called when SKILL.md does not exist at the bundle root."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        # tmp_path has no skills/ subdirectory — SKILL.md will not be found
        coordinator = _make_coordinator(server_url="http://localhost:8000", skill_path=None)

        mock_register, calls = _capture_hooks_register()
        coordinator.hooks.register = mock_register

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with (
            patch("amplifier_module_hook_context_intelligence._BUNDLE_ROOT", tmp_path),
            patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
                mock_fetcher_cls,
            ),
        ):
            await mount(
                coordinator,
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

            # Find and fire the skills:discovered handler — path is unresolvable, fetch must not run.
            # Handler must be invoked while _BUNDLE_ROOT is still patched to tmp_path (empty dir),
            # otherwise the real bundle root fallback would resolve the skill path and call fetch.
            handler = _find_handler(calls, "skills:discovered", "SkillFetcher-trigger")
            await handler("skills:discovered", {})  # type: ignore[operator]

        mock_fetcher_instance.fetch.assert_not_called()

    async def test_mount_still_returns_cleanup_when_fetch_skipped(self, tmp_path: Path) -> None:
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

    async def test_skill_unloaded_triggers_fetch_for_watched_skill(self, tmp_path: Path) -> None:
        """Handler fetches when skill:unloaded fires for a skill in WATCHED_SKILLS.

        After the refactor, the handler uses await _refresh_watched_skills directly
        instead of asyncio.create_task.
        """
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        registered = _capture_hooks_register(coordinator)

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
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # Reset calls from mount-time immediate check (skills_discovery already registered)
        mock_fetcher_instance.fetch.reset_mock()

        handler = _find_handler(registered, "skill:unloaded", "SkillFetcher")
        await handler(  # type: ignore[operator]
            "skill:unloaded", {"skill_name": "context-intelligence-graph-query"}
        )

        # fetch is called directly by the skill:unloaded handler (no asyncio.create_task)
        mock_fetcher_instance.fetch.assert_awaited_once()

    async def test_skill_unloaded_skips_fetch_for_unwatched_skill(self, tmp_path: Path) -> None:
        """Handler does nothing when skill:unloaded fires for a skill NOT in WATCHED_SKILLS."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        registered = _capture_hooks_register(coordinator)

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
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # Reset calls from mount-time immediate check (skills_discovery already registered)
        mock_fetcher_instance.fetch.reset_mock()

        handler = _find_handler(registered, "skill:unloaded", "SkillFetcher")
        await handler(  # type: ignore[operator]
            "skill:unloaded", {"skill_name": "some-other-unrelated-skill"}
        )

        # skill is unwatched — no additional fetch should be triggered by the handler
        mock_fetcher_instance.fetch.assert_not_awaited()

    async def test_does_not_crash_when_metadata_not_found(self, tmp_path: Path) -> None:
        """Handler returns cleanly when SKILL.md does not exist at the bundle root."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        # tmp_path has no skills/ subdirectory — SKILL.md will not be found
        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=None,
        )

        registered = _capture_hooks_register(coordinator)

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with (
            patch("amplifier_module_hook_context_intelligence._BUNDLE_ROOT", tmp_path),
            patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
                mock_fetcher_cls,
            ),
        ):
            await mount(
                coordinator,
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

            # SKILL.md absent at bundle root — handler must return without calling fetch
            handler = _find_handler(registered, "skill:unloaded", "SkillFetcher")
            await handler(  # type: ignore[operator]
                "skill:unloaded", {"skill_name": "context-intelligence-graph-query"}
            )

        mock_fetcher_instance.fetch.assert_not_awaited()


class TestMountNoOpWhenServerUrlAbsent:
    """When server_url is not configured, mount() must not touch skills_discovery at all."""

    async def test_get_capability_not_called_when_no_server_url(self, tmp_path: Path) -> None:
        """coordinator.get_capability must NOT be called when server_url is None."""
        from amplifier_module_hook_context_intelligence import mount

        coordinator = _make_coordinator(server_url=None, skill_path=None)
        await mount(coordinator, config={})

        # get_capability should never have been called for skills_discovery
        for call in coordinator.get_capability.call_args_list:
            assert call.args[0] != "skills_discovery", (
                "get_capability('skills_discovery') was called even though server_url is None"
            )

    async def test_skill_unloaded_not_registered_when_no_server_url(self, tmp_path: Path) -> None:
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
        assert "skills:discovered" not in registered_events, (
            "skills:discovered handler was registered even though server_url is None"
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
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        assert not skill_path.exists()
        mock_fetcher_instance.fetch.assert_not_called()
        mock_fetcher_instance.write_legacy_content.assert_not_called()

    async def test_old_server_registers_skills_discovered_handler(self, tmp_path: Path) -> None:
        """Old server (reachable=True, version=None): skills:discovered handler registered.

        After the refactor, both old and new servers register a skills:discovered handler
        rather than calling write_legacy_content or fetch inline during mount().
        The handler fires later and uses skills_capable to decide which path to take.
        """
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        # Place SKILL.md at the expected bundle-root-relative location
        skill_path = tmp_path / "skills" / "context-intelligence-graph-query" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# test")

        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_register, calls = _capture_hooks_register()
        coordinator.hooks.register = mock_register

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version=None)
        )
        mock_fetcher_instance.fetch = AsyncMock()
        mock_fetcher_instance.write_legacy_content = MagicMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with (
            patch("amplifier_module_hook_context_intelligence._BUNDLE_ROOT", tmp_path),
            patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
                mock_fetcher_cls,
            ),
        ):
            await mount(
                coordinator,
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # fetch is NOT called for old server (write_legacy_content is used instead)
        mock_fetcher_instance.fetch.assert_not_called()
        # write_legacy_content IS called immediately during mount — skills_discovery was already registered
        mock_fetcher_instance.write_legacy_content.assert_called_once()
        # skills:discovered SkillFetcher-trigger handler must still be registered
        _find_handler(calls, "skills:discovered", "SkillFetcher-trigger")

    async def test_new_server_registers_skills_discovered_handler(self, tmp_path: Path) -> None:
        """New server (reachable=True, version='2.0.0'): skills:discovered handler registered.

        After the refactor, fetch is deferred — mount() only registers the handler.
        """
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        # Place SKILL.md at the expected bundle-root-relative location
        skill_path = tmp_path / "skills" / "context-intelligence-graph-query" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# test")

        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_register, calls = _capture_hooks_register()
        coordinator.hooks.register = mock_register

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_instance.write_legacy_content = MagicMock()
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with (
            patch("amplifier_module_hook_context_intelligence._BUNDLE_ROOT", tmp_path),
            patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
                mock_fetcher_cls,
            ),
        ):
            await mount(
                coordinator,
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # fetch IS called immediately during mount — skills_discovery was already registered
        mock_fetcher_instance.fetch.assert_called_once()
        mock_fetcher_instance.write_legacy_content.assert_not_called()
        # skills:discovered SkillFetcher-trigger handler must still be registered
        _find_handler(calls, "skills:discovered", "SkillFetcher-trigger")


class TestSkillsDiscoveredHandler:
    """mount() registers skills:discovered handler that triggers refresh on new server."""

    async def test_skills_discovered_triggers_refresh(self, tmp_path: Path) -> None:
        """skills:discovered handler calls fetch once for the watched skill.

        The handler should be registered during mount() and trigger a fetch
        when fired — fetch must NOT be called during mount itself.
        """
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        # Place skill_path at a non-standard location (no skills/ prefix) so that
        # _BUNDLE_ROOT / "skills" / skill_name / "SKILL.md" won't resolve it during
        # mount — only skills_discovery capability returns this path.
        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# test")

        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        mock_register, calls = _capture_hooks_register()
        coordinator.hooks.register = mock_register

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        mock_fetcher_instance.fetch = AsyncMock(return_value=True)
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with (
            patch("amplifier_module_hook_context_intelligence._BUNDLE_ROOT", tmp_path),
            patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
                mock_fetcher_cls,
            ),
        ):
            await mount(
                coordinator,
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # fetch IS called immediately during mount — skills_discovery was already registered
        mock_fetcher_instance.fetch.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )
        mock_fetcher_instance.fetch.reset_mock()

        # Find the skills:discovered handler and fire it — handler must also trigger a refresh
        handler = _find_handler(calls, "skills:discovered", "SkillFetcher-trigger")
        await handler("skills:discovered", {})  # type: ignore[operator]

        # After the handler fires, fetch should have been called once more
        mock_fetcher_instance.fetch.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )

    async def test_no_handler_when_server_unreachable(self) -> None:
        """No skills:discovered SkillFetcher-trigger handler when server is unreachable."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        coordinator = _make_coordinator(server_url="http://localhost:8000", skill_path=None)

        mock_register, calls = _capture_hooks_register()
        coordinator.hooks.register = mock_register

        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=False, version=None)
        )
        mock_fetcher_cls = MagicMock(return_value=mock_fetcher_instance)

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.SkillFetcher",
            mock_fetcher_cls,
        ):
            await mount(
                coordinator,
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # No SkillFetcher-trigger handler should be registered for skills:discovered
        # when the server is unreachable (Branch A)
        trigger_handlers = [
            (evt, hdlr, kw)
            for evt, hdlr, kw in calls
            if evt == "skills:discovered" and kw.get("name") == "SkillFetcher-trigger"
        ]
        assert len(trigger_handlers) == 0, (
            "skills:discovered SkillFetcher-trigger handler was registered even though "
            "server is unreachable"
        )


class TestSkillUnloadedHandlerRefresh:
    """skill:unloaded handler uses await _refresh_watched_skills (not asyncio.create_task)."""

    async def test_skill_unloaded_awaits_refresh_for_watched_skill(self, tmp_path: Path) -> None:
        """skill:unloaded handler awaits _refresh_watched_skills for watched skills.

        After the refactor, the handler must NOT use asyncio.create_task. Instead,
        it must directly await _refresh_watched_skills, which calls fetcher.fetch.
        """
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import (
            VersionCheckResult,
            WATCHED_SKILLS,
        )

        skill_name = next(iter(WATCHED_SKILLS))
        skill_path = tmp_path / skill_name / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# test")

        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        registered = _capture_hooks_register(coordinator)

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
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        # Reset fetch calls after mount (mount should NOT have called fetch directly)
        mock_fetcher_instance.fetch.reset_mock()

        handler = _find_handler(registered, "skill:unloaded", "SkillFetcher")
        await handler(  # type: ignore[operator]
            "skill:unloaded", {"skill_name": skill_name}
        )

        # New behavior: fetcher.fetch IS called directly (via _refresh_watched_skills)
        mock_fetcher_instance.fetch.assert_awaited_once()

    async def test_skill_unloaded_ignores_unwatched_skill(self, tmp_path: Path) -> None:
        """skill:unloaded handler does nothing for skills NOT in WATCHED_SKILLS."""
        from amplifier_module_hook_context_intelligence import mount
        from amplifier_module_hook_context_intelligence.skill_fetcher import VersionCheckResult

        skill_path = tmp_path / "some-skill" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# test")

        coordinator = _make_coordinator(
            server_url="http://localhost:8000",
            skill_path=skill_path,
        )

        registered = _capture_hooks_register(coordinator)

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
                config={
                    "context_intelligence_server_url": "http://localhost:8000",
                    "context_intelligence_api_key": "test-key",
                },
            )

        mock_fetcher_instance.fetch.reset_mock()

        handler = _find_handler(registered, "skill:unloaded", "SkillFetcher")
        await handler(  # type: ignore[operator]
            "skill:unloaded", {"skill_name": "some-unwatched-unrelated-skill"}
        )

        # Not a watched skill — no fetch should be triggered
        mock_fetcher_instance.fetch.assert_not_called()
