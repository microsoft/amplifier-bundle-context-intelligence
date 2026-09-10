"""Tests for SetIngestionFiltersTool (modules/tool-context-intelligence-set-filters).

That module has no test infrastructure of its own (no tests/ dir, no lock
file) and its pyproject depends on the *published* amplifier-bundle-context-
intelligence package rather than this local checkout, so it cannot be
installed here without network access. Its own runtime dependency is just
``amplifier_core.models.ToolResult``, which IS available in this module's
venv (amplifier-core is already a dev dependency here) -- so the tool's
source is imported directly by adding its package directory to sys.path for
the duration of these tests (via ``monkeypatch.syspath_prepend``, which
pytest itself reverts on teardown). No source file is modified to make this
work.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_TOOL_MODULE_DIR = (
    Path(__file__).parent.parent.parent.parent / "modules" / "tool-context-intelligence-set-filters"
)


@pytest.fixture
def set_filters_tool_module(monkeypatch: pytest.MonkeyPatch):
    """Import amplifier_module_tool_context_intelligence_set_filters from source.

    Skips (rather than fails) if the sibling module directory is not present
    at the expected workspace layout -- this test is a bonus cross-module
    check, not a load-bearing requirement of the hook module's own test
    suite.
    """
    if not _TOOL_MODULE_DIR.is_dir():
        pytest.skip(f"tool module directory not found at {_TOOL_MODULE_DIR}")

    monkeypatch.syspath_prepend(str(_TOOL_MODULE_DIR))
    module_name = "amplifier_module_tool_context_intelligence_set_filters"
    sys.modules.pop(module_name, None)
    import importlib

    module = importlib.import_module(module_name)
    yield module
    sys.modules.pop(module_name, None)


class TestSetIngestionFiltersToolCapabilityPresent:
    async def test_execute_returns_success_with_report(self, set_filters_tool_module) -> None:
        report = {
            "match_key": "[REDACTED:SECRET]",
            "active": ["d1"],
            "inherited_snapshot_patched": True,
            "destinations": {"d1": {"include": ["**"], "exclude": []}},
            "disk_consistent": None,
        }
        set_filters_capability = AsyncMock(return_value=report)

        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(
            side_effect=lambda name: (
                set_filters_capability
                if name == "context_intelligence.set_ingestion_filters"
                else None
            )
        )

        tool = set_filters_tool_module.SetIngestionFiltersTool(coordinator)
        result = await tool.execute({})

        assert result.success is True
        assert result.output == report
        set_filters_capability.assert_awaited_once()
        assert set_filters_capability.await_args is not None
        assert set_filters_capability.await_args.kwargs["verify_disk"] is True

    async def test_execute_passes_custom_settings_path(self, set_filters_tool_module) -> None:
        set_filters_capability = AsyncMock(return_value={"active": []})
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(
            side_effect=lambda name: (
                set_filters_capability
                if name == "context_intelligence.set_ingestion_filters"
                else None
            )
        )

        tool = set_filters_tool_module.SetIngestionFiltersTool(coordinator)
        await tool.execute({"settings_path": "/tmp/custom-settings.yaml"})

        assert set_filters_capability.await_args is not None
        assert (
            set_filters_capability.await_args.kwargs["settings_path"] == "/tmp/custom-settings.yaml"
        )

    async def test_execute_surfaces_capability_exception(self, set_filters_tool_module) -> None:
        set_filters_capability = AsyncMock(side_effect=RuntimeError("disk disagrees"))
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(
            side_effect=lambda name: (
                set_filters_capability
                if name == "context_intelligence.set_ingestion_filters"
                else None
            )
        )

        tool = set_filters_tool_module.SetIngestionFiltersTool(coordinator)
        result = await tool.execute({})

        assert result.success is False
        assert "disk disagrees" in result.output["error"]


class TestSetIngestionFiltersToolCapabilityAbsent:
    async def test_execute_fails_loud_when_capability_unavailable(
        self, set_filters_tool_module
    ) -> None:
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        tool = set_filters_tool_module.SetIngestionFiltersTool(coordinator)
        result = await tool.execute({})

        assert result.success is False
        assert "unavailable" in result.output["error"]
        assert "context_intelligence.set_ingestion_filters" in result.output["error"]


class TestSetIngestionFiltersToolVerifyOnly:
    async def test_verify_only_uses_verify_capability(self, set_filters_tool_module) -> None:
        verify_result = {"live_exclude": {}, "disk_exclude": {}, "consistent": True}
        verify_capability = MagicMock(return_value=verify_result)
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(
            side_effect=lambda name: (
                verify_capability
                if name == "context_intelligence.verify_ingestion_consistency"
                else None
            )
        )

        tool = set_filters_tool_module.SetIngestionFiltersTool(coordinator)
        result = await tool.execute({"verify_only": True})

        assert result.success is True
        assert result.output == verify_result
        verify_capability.assert_called_once()

    async def test_verify_only_fails_loud_when_capability_unavailable(
        self, set_filters_tool_module
    ) -> None:
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        tool = set_filters_tool_module.SetIngestionFiltersTool(coordinator)
        result = await tool.execute({"verify_only": True})

        assert result.success is False
        assert "unavailable" in result.output["error"]

    async def test_verify_only_surfaces_capability_exception(self, set_filters_tool_module) -> None:
        verify_capability = MagicMock(side_effect=RuntimeError("live/disk mismatch"))
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(
            side_effect=lambda name: (
                verify_capability
                if name == "context_intelligence.verify_ingestion_consistency"
                else None
            )
        )

        tool = set_filters_tool_module.SetIngestionFiltersTool(coordinator)
        result = await tool.execute({"verify_only": True})

        assert result.success is False
        assert "live/disk mismatch" in result.output["error"]
