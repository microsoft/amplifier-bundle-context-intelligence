"""Tests for skill_sync — offline integrity + per-skill sync helpers.

Ported from spike branch modules/tool-graph-query/tests/test_skill_sync.py.
Package retargeted: amplifier_module_tool_graph_query
              → amplifier_module_tool_context_intelligence_query

Reference fixes applied (noted inline):
  [FIX-1] All patch targets updated: amplifier_module_tool_graph_query.skill_sync.*
           → amplifier_module_tool_context_intelligence_query.skill_sync.*
  [FIX-2] All imports updated to the new package name.
  [FIX-3] on_session_ready now delegates to _resync_all_watched (refactor on main)
           rather than dispatching directly; tests that patch _sync_skill are
           unaffected because _resync_all_watched still calls _sync_skill with
           identical arguments.
  [FIX-4] Disabled-path tests use _apply_offline_skill_bodies internally on main
           (instead of a direct _install_vendored_body call); SkillFetcher is still
           never instantiated, so mock_fetcher.assert_not_called() still holds.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestInvalidateIfDrift:
    def test_drift_deletes_both_sidecars_keeps_content(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import _invalidate_if_drift

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
        from amplifier_module_tool_context_intelligence_query.skill_sync import _invalidate_if_drift

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
        from amplifier_module_tool_context_intelligence_query.skill_sync import _invalidate_if_drift

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
        from amplifier_module_tool_context_intelligence_query.skill_sync import _sync_skill

        skill = tmp_path / "SKILL.md"
        skill.write_text("# drifted content")
        etag = tmp_path / ".etag"
        etag.write_text("etag-value")
        chash = tmp_path / ".content_hash"
        chash.write_text("0" * 64)  # Drift state — hash does not match

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher"
        ) as mock_fetcher:  # [FIX-1]
            await _sync_skill("my-skill", skill, server_url=None, api_key=None)

        mock_fetcher.assert_not_called()
        assert not etag.exists(), ".etag must be deleted (offline drift detected)"
        assert not chash.exists(), ".content_hash must be deleted (offline drift detected)"

    async def test_unreachable_server_runs_offline_integrity_no_fetch(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            VersionCheckResult,
        )
        from amplifier_module_tool_context_intelligence_query.skill_sync import _sync_skill

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
            "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher",  # [FIX-1]
            return_value=instance,
        ):
            await _sync_skill("my-skill", skill, server_url="http://down:9000", api_key=None)

        instance.fetch.assert_not_awaited()
        assert not etag.exists(), ".etag must be deleted (unreachable server + drift)"
        assert not chash.exists(), ".content_hash must be deleted (unreachable server + drift)"

    async def test_reachable_server_calls_fetch(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            VersionCheckResult,
        )
        from amplifier_module_tool_context_intelligence_query.skill_sync import _sync_skill

        skill = tmp_path / "SKILL.md"
        skill.write_text("# content")

        instance = MagicMock()
        instance.check_server_version = AsyncMock(
            return_value=VersionCheckResult(reachable=True, version="2.0.0")
        )
        instance.fetch = AsyncMock(return_value=True)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher",  # [FIX-1]
            return_value=instance,
        ) as mock_fetcher_cls:
            await _sync_skill("my-skill", skill, server_url="http://up:9000", api_key="k")

        mock_fetcher_cls.assert_called_once_with("http://up:9000", api_key="k")
        instance.fetch.assert_awaited_once_with("my-skill", skill)


# ======================================================================
# Helpers for on_session_ready tests
# ======================================================================


def _make_tool(server_url: str, api_key: str = "k", workspace: str = "ws") -> MagicMock:
    tool = MagicMock()
    tool._resolve_server_config = MagicMock(return_value=(server_url, api_key, workspace))
    return tool


def _make_ready_coordinator(
    skill_path: Path,
    tool: MagicMock | None,
    *,
    discovery_present: bool = True,
    find_returns_meta: bool = True,
) -> MagicMock:
    discovery: MagicMock | None = None
    if discovery_present:
        discovery = MagicMock()
        meta = MagicMock()
        meta.path = skill_path
        discovery.find = MagicMock(return_value=meta if find_returns_meta else None)

    caps: dict[str, object] = {
        "skills_discovery": discovery,
        "context_intelligence._graph_query_tool": tool,
    }

    coord = MagicMock()
    coord.get_capability = MagicMock(side_effect=lambda name: caps.get(name))
    coord.hooks = MagicMock()
    coord.hooks.register = MagicMock(return_value=MagicMock())
    return coord


class TestOnSessionReadyHardGuards:
    async def test_missing_discovery_capability_is_loud_noop(self, tmp_path: Path, caplog) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        tool = _make_tool("http://up:9000")
        coord = _make_ready_coordinator(tmp_path / "SKILL.md", tool, discovery_present=False)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._sync_skill",  # [FIX-1]
            new_callable=AsyncMock,
        ) as mock_sync:
            with caplog.at_level(logging.WARNING):
                await on_session_ready(coord)

        mock_sync.assert_not_awaited()
        assert any("skill_sync" in record.message for record in caplog.records)

    async def test_find_returns_none_is_loud_noop(self, tmp_path: Path, caplog) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        tool = _make_tool("http://up:9000")
        coord = _make_ready_coordinator(tmp_path / "SKILL.md", tool, find_returns_meta=False)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._sync_skill",  # [FIX-1]
            new_callable=AsyncMock,
        ) as mock_sync:
            with caplog.at_level(logging.WARNING):
                await on_session_ready(coord)

        mock_sync.assert_not_awaited()
        assert any("skill_sync" in record.message for record in caplog.records)


class TestOnSessionReadyOrchestration:
    async def test_dispatches_sync_with_resolved_config(self, tmp_path: Path) -> None:
        # [FIX-3] on_session_ready → _resync_all_watched → _sync_skill; patch still intercepts.
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        tool = _make_tool("http://up:9000", api_key="key-1", workspace="ws-1")
        coord = _make_ready_coordinator(skill_path, tool)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._sync_skill",  # [FIX-1]
            new_callable=AsyncMock,
        ) as mock_sync:
            await on_session_ready(coord)

        mock_sync.assert_awaited_once_with(
            "context-intelligence-graph-query", skill_path, "http://up:9000", "key-1"
        )

    async def test_registers_skill_unloaded_handler(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        tool = _make_tool("http://up:9000")
        coord = _make_ready_coordinator(skill_path, tool)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._sync_skill",  # [FIX-1]
            new_callable=AsyncMock,
        ):
            await on_session_ready(coord)

        assert "skill:unloaded" in [c.args[0] for c in coord.hooks.register.call_args_list]


_STUB_BODY = (
    "---\nname: context-intelligence-graph-query\nversion: 2.0.0\n---\n\n"
    "# Context Intelligence Graph Query — Server Unavailable\n\n"
    "The context intelligence server is not reachable.\n"
    "Delegate immediately to `session-navigator`. Do not attempt Cypher queries.\n"
)
_ETAG = ".etag"
_CHASH = ".content_hash"


def _write_stub(skill_path: Path) -> str:
    skill_path.write_text(_STUB_BODY)
    return hashlib.sha256(skill_path.read_bytes()).hexdigest()


class TestOnSessionReadySkillSyncDisabled:
    """skill_sync_enabled=false gate at the top of on_session_ready.

    Disabled performs ZERO per-turn network and does NOT register the
    skill:unloaded handler. But it must NOT strand a working graph-analyst on the
    pessimistic "Server Unavailable" stub:
      - server configured -> swap stub for the vendored real body (local copy)
      - no server         -> retain the stub (graph genuinely absent)
    """

    async def test_disabled_server_configured_swaps_in_vendored_body(self, tmp_path: Path) -> None:
        # [FIX-4] Disabled path uses _apply_offline_skill_bodies which calls
        # _install_vendored_body; SkillFetcher is still never instantiated.
        from amplifier_module_tool_context_intelligence_query.bundled_skill import (
            EXPECTED_BUNDLED_SKILL_SHA256,
        )
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        # ZERO network: SkillFetcher must never be constructed, _sync_skill never awaited.
        with (
            patch(
                "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher"  # [FIX-1]
            ) as mock_fetcher,
            patch(
                "amplifier_module_tool_context_intelligence_query.skill_sync._sync_skill",  # [FIX-1]
                new_callable=AsyncMock,
            ) as mock_sync,
        ):
            await on_session_ready(coord)

        mock_fetcher.assert_not_called()
        mock_sync.assert_not_awaited()
        # The pessimistic stub has been replaced by the vendored real body.
        got = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        assert got == EXPECTED_BUNDLED_SKILL_SHA256
        assert "Server Unavailable" not in skill_path.read_text()
        # No per-turn reload handler.
        assert "skill:unloaded" not in [c.args[0] for c in coord.hooks.register.call_args_list]

    async def test_disabled_server_configured_removes_stale_etag_and_sets_hash(
        self, tmp_path: Path
    ) -> None:
        from amplifier_module_tool_context_intelligence_query.bundled_skill import (
            EXPECTED_BUNDLED_SKILL_SHA256,
        )
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        # Seed a STALE etag + content_hash (left over from a prior server fetch).
        (tmp_path / _ETAG).write_text('W/"stale-etag"')
        (tmp_path / _CHASH).write_text("0" * 64)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        await on_session_ready(coord)

        # Stale etag removed (so a later re-enabled sync does a clean unconditional GET).
        assert not (tmp_path / _ETAG).exists(), "stale .etag must be removed on vendored swap"
        # content_hash now matches the vendored body.
        assert (tmp_path / _CHASH).read_text().strip() == EXPECTED_BUNDLED_SKILL_SHA256

    async def test_disabled_server_configured_idempotent_second_turn_no_rewrite(
        self, tmp_path: Path
    ) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        await on_session_ready(coord)  # turn 1 — writes vendored body
        first_mtime = skill_path.stat().st_mtime_ns
        await on_session_ready(coord)  # turn 2 — content already correct
        second_mtime = skill_path.stat().st_mtime_ns

        assert first_mtime == second_mtime, "idempotent: SKILL.md must not be rewritten on turn 2"

    async def test_disabled_rewrites_when_content_differs_by_trailing_newline(
        self, tmp_path: Path
    ) -> None:
        # tester-breaker: idempotency must compare by sha256, not eyeballing. A
        # one-byte difference (extra trailing newline) is NOT the vendored body
        # and must be normalized back to it.
        from amplifier_module_tool_context_intelligence_query.bundled_skill import (
            EXPECTED_BUNDLED_SKILL_SHA256,
        )
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            _vendored_body,
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        body = _vendored_body("context-intelligence-graph-query")
        assert body is not None
        skill_path.write_text(body + "\n")  # differs by one trailing newline
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        await on_session_ready(coord)

        got = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        assert got == EXPECTED_BUNDLED_SKILL_SHA256

    async def test_disabled_no_server_retains_stub(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        stub_hash = _write_stub(skill_path)
        tool = _make_tool("")  # no server configured
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher"  # [FIX-1]
        ) as mock_fetcher:
            await on_session_ready(coord)

        mock_fetcher.assert_not_called()
        assert hashlib.sha256(skill_path.read_bytes()).hexdigest() == stub_hash, (
            "no server -> the 'Server Unavailable' stub must be retained untouched"
        )
        assert "skill:unloaded" not in [c.args[0] for c in coord.hooks.register.call_args_list]

    async def test_disabled_missing_vendored_body_fails_loud_and_leaves_file(
        self, tmp_path: Path, caplog
    ) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        stub_hash = _write_stub(skill_path)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._vendored_body",  # [FIX-1]
            return_value=None,
        ):
            with caplog.at_level(logging.ERROR):
                await on_session_ready(coord)

        # Fail loud + leave the on-disk file untouched (never a silent wrong result).
        assert any("skill_swap_unavailable" in r.message for r in caplog.records)
        assert hashlib.sha256(skill_path.read_bytes()).hexdigest() == stub_hash

    async def test_disabled_emits_legible_info_signal(self, tmp_path: Path, caplog) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        with caplog.at_level(logging.INFO):
            await on_session_ready(coord)

        assert any("skill_sync_disabled" in record.message for record in caplog.records), (
            "disabled gate must log a legible INFO signal"
        )

    async def test_enabled_explicit_true_still_syncs(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        tool = _make_tool("http://up:9000", api_key="key-1", workspace="ws-1")
        tool.skill_sync_enabled = True
        coord = _make_ready_coordinator(skill_path, tool)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._sync_skill",  # [FIX-1]
            new_callable=AsyncMock,
        ) as mock_sync:
            await on_session_ready(coord)

        mock_sync.assert_awaited_once_with(
            "context-intelligence-graph-query", skill_path, "http://up:9000", "key-1"
        )
        # Enabled path registers the reload handler.
        assert "skill:unloaded" in [c.args[0] for c in coord.hooks.register.call_args_list]

    async def test_tool_absent_falls_through_to_offline_path(self, tmp_path: Path) -> None:
        # Gate only fires when tool is present AND disabled.  With no tool the
        # existing offline-integrity path must run unchanged (server_url None).
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            on_session_ready,
        )

        skill_path = tmp_path / "SKILL.md"
        coord = _make_ready_coordinator(skill_path, tool=None)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._sync_skill",  # [FIX-1]
            new_callable=AsyncMock,
        ) as mock_sync:
            await on_session_ready(coord)

        mock_sync.assert_awaited_once_with(
            "context-intelligence-graph-query", skill_path, None, None
        )
        registered_events = [c.args[0] for c in coord.hooks.register.call_args_list]
        assert "skill:unloaded" in registered_events
