"""Tests for context_intelligence.bundle_analysis entry point.

These tests verify that:
- The bundle_analysis subpackage is importable.
- run_bundle_analysis() is exported from the package.
- run_bundle_analysis() signature has no `client` parameter.
- run_bundle_analysis() calls scan_cache BEFORE run_signals (inventory-first).
- run_bundle_analysis() composes signals + inventory + gap into a single dict.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


class TestPackageImport:
    """Tests that the bundle_analysis subpackage is importable."""

    def test_subpackage_importable(self) -> None:
        import context_intelligence.bundle_analysis  # noqa: F401

    def test_run_bundle_analysis_exported(self) -> None:
        from context_intelligence.bundle_analysis import run_bundle_analysis  # noqa: F401


# ---------------------------------------------------------------------------
# Shape tests
# ---------------------------------------------------------------------------


class TestRunBundleAnalysisShape:
    """Verify the public signature of run_bundle_analysis."""

    def test_signature_has_no_client_param(self) -> None:
        """The `client` parameter must NOT appear in the signature."""
        from context_intelligence.bundle_analysis import run_bundle_analysis

        sig = inspect.signature(run_bundle_analysis)
        assert "client" not in sig.parameters

    def test_signature_keeps_workspace_and_session_id(self) -> None:
        """workspace, session_id, cache_root, and base_path must all be present."""
        from context_intelligence.bundle_analysis import run_bundle_analysis

        sig = inspect.signature(run_bundle_analysis)
        assert "workspace" in sig.parameters
        assert "session_id" in sig.parameters
        assert "cache_root" in sig.parameters
        assert "base_path" in sig.parameters

    def test_is_async(self) -> None:
        """run_bundle_analysis must be an async function."""
        from context_intelligence.bundle_analysis import run_bundle_analysis

        assert inspect.iscoroutinefunction(run_bundle_analysis)


# ---------------------------------------------------------------------------
# Orchestration order tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunBundleAnalysisOrchestration:
    """Verify the inventory-first call order: scan_cache → run_signals → compute_gap."""

    async def test_calls_scan_cache_before_run_signals(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import context_intelligence.bundle_analysis as ba_module

        call_order: list[str] = []

        def fake_scan_cache(*, cache_root: Path) -> dict:
            call_order.append("scan_cache")
            return {"_meta": {"scan_source": "fake"}}

        def fake_run_signals(
            *,
            workspace: str,
            session_id=None,
            base_path=None,
            inventory: dict,
        ) -> dict:
            assert inventory is not None, "inventory must be passed to run_signals"
            call_order.append("run_signals")
            return {}

        def fake_compute_gap(*, signals: dict, inventory: dict) -> dict:
            call_order.append("compute_gap")
            return {"per_bundle": {}, "improvement": []}

        monkeypatch.setattr(ba_module, "scan_cache", fake_scan_cache)
        monkeypatch.setattr(ba_module, "run_signals", fake_run_signals)
        monkeypatch.setattr(ba_module, "compute_gap", fake_compute_gap)

        from context_intelligence.bundle_analysis import run_bundle_analysis

        result = await run_bundle_analysis(workspace="ws", cache_root=tmp_path)

        assert call_order == ["scan_cache", "run_signals", "compute_gap"]
        assert set(result.keys()) >= {"scope", "signals", "inventory", "gap"}
