"""Tests for skill_sync — offline integrity + per-skill sync helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestInvalidateIfDrift:
    def test_drift_deletes_both_sidecars_keeps_content(self, tmp_path: Path) -> None:
        from amplifier_module_tool_graph_query.skill_sync import _invalidate_if_drift

        skill = tmp_path / "SKILL.md"
        skill.write_text("# drifted content")
        etag = tmp_path / ".etag"
        etag.write_text("etag-value")
        chash = tmp_path / ".content_hash"
        chash.write_text("0" * 64)  # Does NOT match actual content -> drift

        _invalidate_if_drift("my-skill", skill, etag, chash)

        assert skill.exists(), "Content file must be retained"
        assert not etag.exists(), ".etag sidecar must be deleted"
        assert not chash.exists(), ".content_hash sidecar must be deleted"

    def test_match_is_noop(self, tmp_path: Path) -> None:
        from amplifier_module_tool_graph_query.skill_sync import _invalidate_if_drift

        skill = tmp_path / "SKILL.md"
        skill.write_text("# matching content")
        etag = tmp_path / ".etag"
        etag.write_text("etag-value")
        chash = tmp_path / ".content_hash"
        chash.write_text(hashlib.sha256(skill.read_bytes()).hexdigest())  # Matches -> in sync

        _invalidate_if_drift("my-skill", skill, etag, chash)

        assert skill.exists()
        assert etag.exists(), ".etag must remain when hash matches"
        assert chash.exists(), ".content_hash must remain when hash matches"

    def test_no_content_hash_sidecar_is_noop(self, tmp_path: Path) -> None:
        from amplifier_module_tool_graph_query.skill_sync import _invalidate_if_drift

        skill = tmp_path / "SKILL.md"
        skill.write_text("# some content")
        etag = tmp_path / ".etag"
        etag.write_text("etag-value")
        # No .content_hash created — _invalidate_if_drift should return early

        _invalidate_if_drift("my-skill", skill, etag, tmp_path / ".content_hash")

        assert etag.exists(), ".etag must be untouched when no .content_hash present"
        assert etag.read_text() == "etag-value"


class TestSyncSkill:
    async def test_no_server_url_runs_offline_integrity_no_fetch(self, tmp_path: Path) -> None:
        from amplifier_module_tool_graph_query.skill_sync import _sync_skill

        skill = tmp_path / "SKILL.md"
        skill.write_text("# drifted content")
        etag = tmp_path / ".etag"
        etag.write_text("etag-value")
        chash = tmp_path / ".content_hash"
        chash.write_text("0" * 64)  # Drift state — hash does not match

        with patch("amplifier_module_tool_graph_query.skill_sync.SkillFetcher") as mock_fetcher:
            await _sync_skill("my-skill", skill, server_url=None, api_key=None)

        mock_fetcher.assert_not_called()
        assert not etag.exists(), ".etag must be deleted (offline drift detected)"
        assert not chash.exists(), ".content_hash must be deleted (offline drift detected)"

    async def test_unreachable_server_runs_offline_integrity_no_fetch(self, tmp_path: Path) -> None:
        from amplifier_module_tool_graph_query.skill_fetcher import VersionCheckResult
        from amplifier_module_tool_graph_query.skill_sync import _sync_skill

        skill = tmp_path / "SKILL.md"
        skill.write_text("# drifted content")
        etag = tmp_path / ".etag"
        etag.write_text("etag-value")
        chash = tmp_path / ".content_hash"
        chash.write_text("0" * 64)  # Drift state

        instance = MagicMock()
        instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=False, version=None)
        )
        instance.fetch = AsyncMock()

        with patch(
            "amplifier_module_tool_graph_query.skill_sync.SkillFetcher",
            return_value=instance,
        ):
            await _sync_skill("my-skill", skill, server_url="http://down:9000", api_key=None)

        instance.fetch.assert_not_awaited()
        assert not etag.exists(), ".etag must be deleted (unreachable server + drift)"
        assert not chash.exists(), ".content_hash must be deleted (unreachable server + drift)"

    async def test_reachable_server_calls_fetch(self, tmp_path: Path) -> None:
        from amplifier_module_tool_graph_query.skill_fetcher import VersionCheckResult
        from amplifier_module_tool_graph_query.skill_sync import _sync_skill

        skill = tmp_path / "SKILL.md"
        skill.write_text("# content")

        instance = MagicMock()
        instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        instance.fetch = AsyncMock(return_value=True)

        with patch(
            "amplifier_module_tool_graph_query.skill_sync.SkillFetcher",
            return_value=instance,
        ) as mock_fetcher_cls:
            await _sync_skill("my-skill", skill, server_url="http://up:9000", api_key="k")

        mock_fetcher_cls.assert_called_once_with("http://up:9000", api_key="k")
        instance.fetch.assert_awaited_once_with("my-skill", skill)
