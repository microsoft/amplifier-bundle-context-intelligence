"""Tests for tool-bundle-usage — verifies CI client is no longer required."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


# >>> EDIT THIS LINE to match the module layout you discovered in pre-flight <<<
from amplifier_module_tool_bundle_usage.bundle_usage_tool import BundleUsageTool


class _StubResolver:
    """Minimal stand-in for the lazy config resolver used by the tool."""

    def __init__(self, *, workspace: str, base_path: Path):
        self.workspace = workspace
        self.base_path = base_path
        # Deliberately omit context_intelligence_server_url / _api_key.
        # If the tool still touches them, the test will surface that.


class _StubCoordinator:
    """Coordinator stand-in that returns the stub resolver from lazy access."""

    def __init__(self, *, workspace: str, base_path: Path):
        self._resolver = _StubResolver(workspace=workspace, base_path=base_path)


class TestBundleUsageToolNoCIClient:
    def test_tool_source_does_not_import_async_ci_client(self):
        """The tool module must not import AsyncCIClient — proves the
        construction site is gone."""
        import importlib

        module = importlib.import_module(BundleUsageTool.__module__)
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "AsyncCIClient" not in source, (
            "AsyncCIClient must be removed from the tool — Phase 2 deliverable"
        )

    def test_execute_does_not_require_server_url(self, tmp_path):
        """The tool must succeed without any CI server URL configured."""
        coordinator = _StubCoordinator(workspace="test-ws", base_path=tmp_path)
        tool = BundleUsageTool(coordinator)
        result = asyncio.run(tool.execute({}))
        if not result.success:
            err = result.error or {}
            assert err.get("type") != "config_error", (
                f"Tool returned a config_error when no CI server URL was set: {err}"
            )

    def test_execute_passes_session_id_through(self, tmp_path, monkeypatch):
        """Verify the tool forwards session_id to run_bundle_analysis."""
        from context_intelligence.bundle_analysis import run_bundle_analysis  # noqa: F401

        captured: dict[str, Any] = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)
            return {
                "scope": None,
                "signals": {},
                "inventory": {},
                "gap": {"per_bundle": {}, "improvement": []},
            }

        import importlib

        tool_mod = importlib.import_module(BundleUsageTool.__module__)
        monkeypatch.setattr(tool_mod, "run_bundle_analysis", fake_run)

        coordinator = _StubCoordinator(workspace="ws-a", base_path=tmp_path)
        tool = BundleUsageTool(coordinator)
        asyncio.run(tool.execute({"session_id": "my-session-id"}))

        assert captured.get("workspace") == "ws-a"
        assert captured.get("session_id") == "my-session-id"
        assert captured.get("base_path") == tmp_path
        assert "client" not in captured, "Tool must NOT pass a client kwarg to run_bundle_analysis"

    def test_workspace_override_in_input(self, tmp_path, monkeypatch):
        """Explicit workspace in input dict overrides the resolver default."""
        import importlib

        captured: dict[str, Any] = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)
            return {
                "scope": None,
                "signals": {},
                "inventory": {},
                "gap": {"per_bundle": {}, "improvement": []},
            }

        tool_mod = importlib.import_module(BundleUsageTool.__module__)
        monkeypatch.setattr(tool_mod, "run_bundle_analysis", fake_run)

        coordinator = _StubCoordinator(workspace="resolver-ws", base_path=tmp_path)
        tool = BundleUsageTool(coordinator)
        asyncio.run(tool.execute({"workspace": "input-ws"}))
        assert captured.get("workspace") == "input-ws"

    def test_description_mentions_jsonl_not_cypher(self):
        """Sanity check the description has been updated."""
        tool = BundleUsageTool(_StubCoordinator(workspace="x", base_path=Path("/tmp")))
        desc = (
            tool.description if hasattr(tool, "description") else getattr(tool, "description", "")
        )
        if not isinstance(desc, str):
            desc = str(desc)
        assert "Cypher" not in desc, "Tool description must not mention Cypher anymore"
