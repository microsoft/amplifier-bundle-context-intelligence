"""Tests for context_intelligence.bundle_analysis entry point.

These tests verify that:
- The bundle_analysis subpackage is importable.
- run_bundle_analysis() is exported from the package.
- run_bundle_analysis() composes signals + inventory + gap into a single dict.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest


class TestPackageImport:
    """Tests that the bundle_analysis subpackage is importable."""

    def test_subpackage_importable(self) -> None:
        import context_intelligence.bundle_analysis  # noqa: F401

    def test_run_bundle_analysis_exported(self) -> None:
        from context_intelligence.bundle_analysis import run_bundle_analysis  # noqa: F401


@pytest.mark.asyncio
class TestRunBundleAnalysis:
    """Tests for the run_bundle_analysis() orchestration function."""

    async def test_returns_dict_with_three_top_level_keys(
        self,
        mock_ci_client: AsyncMock,
        fake_bundle_cache: Path,
    ) -> None:
        from context_intelligence.bundle_analysis import run_bundle_analysis

        result = await run_bundle_analysis(
            client=mock_ci_client,
            workspace="any-workspace",
            cache_root=fake_bundle_cache,
        )

        assert isinstance(result, dict)
        assert set(result.keys()) >= {"signals", "inventory", "gap"}

    async def test_signals_calls_client(
        self,
        mock_ci_client: AsyncMock,
        fake_bundle_cache: Path,
    ) -> None:
        from context_intelligence.bundle_analysis import run_bundle_analysis

        await run_bundle_analysis(
            client=mock_ci_client,
            workspace="any-workspace",
            cache_root=fake_bundle_cache,
        )

        assert mock_ci_client.cypher.await_count >= 1

    async def test_session_id_passed_to_signals(
        self,
        mock_ci_client: AsyncMock,
        fake_bundle_cache: Path,
    ) -> None:
        from context_intelligence.bundle_analysis import run_bundle_analysis

        result = await run_bundle_analysis(
            client=mock_ci_client,
            workspace="any-workspace",
            cache_root=fake_bundle_cache,
            session_id="abc-123",
        )

        assert "scope" in result
        assert result["scope"].session_id == "abc-123"
