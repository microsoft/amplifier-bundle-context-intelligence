"""Tests for _resolve_skill_path and _refresh_watched_skills helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


class TestResolveSkillPath:
    """_resolve_skill_path uses skills_discovery first, then _BUNDLE_ROOT fallback."""

    def test_prefers_skills_discovery(self, tmp_path: Path) -> None:
        """When skills_discovery capability is available, return metadata.path."""
        from amplifier_module_hook_context_intelligence import _resolve_skill_path

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
