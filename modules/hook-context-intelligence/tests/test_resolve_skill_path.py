"""Tests for _resolve_skill_path and _refresh_watched_skills helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestResolveSkillPath:
    """_resolve_skill_path uses skills_discovery first, then _BUNDLE_ROOT fallback."""

    def test_prefers_skills_discovery(self, tmp_path: Path) -> None:
        """When skills_discovery capability is available, return metadata.path."""
        from amplifier_module_hook_context_intelligence import _resolve_skill_path  # type: ignore[attr-defined]

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"

        # Build coordinator mock with skills_discovery capability
        metadata = MagicMock()
        metadata.path = skill_path
        discovery = MagicMock()
        discovery.find = MagicMock(return_value=metadata)

        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=discovery)

        result = _resolve_skill_path("context-intelligence-graph-query", coordinator)

        assert result == skill_path
        coordinator.get_capability.assert_called_once_with("skills_discovery")
        discovery.find.assert_called_once_with("context-intelligence-graph-query")

    def test_fallback_to_bundle_root(self, tmp_path: Path) -> None:
        """When skills_discovery is unavailable, fall back to _BUNDLE_ROOT/skills/."""
        import amplifier_module_hook_context_intelligence as mod
        from amplifier_module_hook_context_intelligence import _resolve_skill_path  # type: ignore[attr-defined]

        skill_name = "context-intelligence-graph-query"
        skill_dir = tmp_path / "skills" / skill_name
        skill_dir.mkdir(parents=True)

        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        with patch.object(mod, "_BUNDLE_ROOT", tmp_path):
            result = _resolve_skill_path(skill_name, coordinator)

        assert result == tmp_path / "skills" / skill_name / "SKILL.md"

    def test_returns_none_when_parent_missing(self, tmp_path: Path) -> None:
        """Returns None when _BUNDLE_ROOT doesn't contain the expected skills directory."""
        import amplifier_module_hook_context_intelligence as mod
        from amplifier_module_hook_context_intelligence import _resolve_skill_path  # type: ignore[attr-defined]

        nonexistent = tmp_path / "does_not_exist"

        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        with patch.object(mod, "_BUNDLE_ROOT", nonexistent):
            result = _resolve_skill_path("context-intelligence-graph-query", coordinator)

        assert result is None


class TestRefreshWatchedSkills:
    """_refresh_watched_skills routes to write_legacy_content or fetch based on skills_capable."""

    async def test_branch_b_legacy(self, tmp_path: Path) -> None:
        """Branch B: skills_capable=False calls write_legacy_content, not fetch."""
        from amplifier_module_hook_context_intelligence import _refresh_watched_skills  # type: ignore[attr-defined]

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"

        # Build coordinator mock with skills_discovery capability returning metadata with skill_path
        metadata = MagicMock()
        metadata.path = skill_path
        discovery = MagicMock()
        discovery.find = MagicMock(return_value=metadata)

        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=discovery)

        # Build fetcher mock
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock()

        await _refresh_watched_skills(coordinator, fetcher, skills_capable=False)

        fetcher.write_legacy_content.assert_called_once_with(
            "context-intelligence-graph-query", skill_path
        )
        fetcher.fetch.assert_not_called()

    async def test_branch_c_fetch(self, tmp_path: Path) -> None:
        """Branch C: skills_capable=True calls fetch, not write_legacy_content."""
        from amplifier_module_hook_context_intelligence import _refresh_watched_skills  # type: ignore[attr-defined]

        skill_path = tmp_path / "context-intelligence-graph-query" / "SKILL.md"

        # Build coordinator mock with skills_discovery capability returning metadata with skill_path
        metadata = MagicMock()
        metadata.path = skill_path
        discovery = MagicMock()
        discovery.find = MagicMock(return_value=metadata)

        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=discovery)

        # Build fetcher mock — fetch returns True via AsyncMock
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(return_value=True)

        await _refresh_watched_skills(coordinator, fetcher, skills_capable=True)

        fetcher.fetch.assert_called_once_with("context-intelligence-graph-query", skill_path)
        fetcher.write_legacy_content.assert_not_called()

    async def test_skips_when_path_none(self, tmp_path: Path) -> None:
        """When skill_path resolves to None, neither fetch nor write_legacy_content is called."""
        import amplifier_module_hook_context_intelligence as mod
        from amplifier_module_hook_context_intelligence import _refresh_watched_skills  # type: ignore[attr-defined]

        # skills_discovery not available (get_capability returns None)
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        # Build fetcher mock
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock()

        nonexistent = tmp_path / "does_not_exist"

        with patch.object(mod, "_BUNDLE_ROOT", nonexistent):
            await _refresh_watched_skills(coordinator, fetcher, skills_capable=True)

        fetcher.fetch.assert_not_called()
        fetcher.write_legacy_content.assert_not_called()
