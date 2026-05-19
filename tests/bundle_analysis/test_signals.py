"""Tests for context_intelligence.bundle_analysis.signals.

signals.py must:
- Read .cypher files from the packaged queries directory
- Call client.cypher(query, workspace, params=...) once per query file
- Parse rows into {bundle: {agents: N, skills: N, modes: N, recipes: N, tools: N}}
- Return an empty dict when the client raises or returns nothing (graceful)
- Pass session_id as a Cypher parameter when provided
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
class TestRunSignals:
    async def test_calls_client_at_least_once(self, mock_ci_client):
        from context_intelligence.bundle_analysis.signals import run_signals

        await run_signals(client=mock_ci_client, workspace="ws")
        assert mock_ci_client.cypher.await_count >= 1

    async def test_passes_workspace_to_client(self, mock_ci_client):
        from context_intelligence.bundle_analysis.signals import run_signals

        await run_signals(client=mock_ci_client, workspace="my-workspace")
        # workspace appears as the second positional arg to client.cypher
        calls = mock_ci_client.cypher.await_args_list
        assert all(
            c.args[1] == "my-workspace" or c.kwargs.get("workspace") == "my-workspace"
            for c in calls
        )

    async def test_returns_bundle_keyed_dict(self, mock_ci_client):
        mock_ci_client.cypher = AsyncMock(
            return_value=[
                {"bundle": "foundation", "agent": "explorer", "invocations": 1},
            ]
        )
        from context_intelligence.bundle_analysis.signals import run_signals

        # Use session_id so the session map is selected; it uses "invocations" as count key
        result = await run_signals(client=mock_ci_client, workspace="ws", session_id="test-session")
        assert "foundation" in result
        assert isinstance(result["foundation"], dict)
        for key in ("agents", "skills", "modes", "recipes", "tools"):
            assert key in result["foundation"]

    async def test_agent_count_aggregated_from_rows(self, mock_ci_client):
        mock_ci_client.cypher = AsyncMock(
            return_value=[
                {"bundle": "foundation", "agent": "explorer", "invocations": 3},
                {"bundle": "foundation", "agent": "zen-architect", "invocations": 2},
            ]
        )
        from context_intelligence.bundle_analysis.signals import run_signals

        # Use session_id so the session map is selected; it uses "invocations" as count key
        result = await run_signals(client=mock_ci_client, workspace="ws", session_id="test-session")
        # 5 total agent invocations across two agents for foundation
        assert result["foundation"]["agents"] == 5

    async def test_session_id_passed_as_param(self, mock_ci_client):
        from context_intelligence.bundle_analysis.signals import run_signals

        await run_signals(client=mock_ci_client, workspace="ws", session_id="abc-123")
        calls = mock_ci_client.cypher.await_args_list
        # at least one call passes session_id in params
        assert any((c.kwargs.get("params") or {}).get("session_id") == "abc-123" for c in calls)

    async def test_graceful_on_client_exception(self, mock_ci_client):
        mock_ci_client.cypher = AsyncMock(side_effect=RuntimeError("boom"))
        from context_intelligence.bundle_analysis.signals import run_signals

        result = await run_signals(client=mock_ci_client, workspace="ws")
        assert result == {}

    async def test_graceful_on_empty_results(self, mock_ci_client):
        mock_ci_client.cypher = AsyncMock(return_value=[])
        from context_intelligence.bundle_analysis.signals import run_signals

        result = await run_signals(client=mock_ci_client, workspace="ws")
        assert result == {}

    async def test_fallback_to_jsonl_when_all_queries_fail(
        self, mock_ci_client, monkeypatch, tmp_path
    ):
        """When every Cypher query raises, run_signals falls back to JSONL extraction."""
        from context_intelligence.bundle_analysis import signals

        # All Cypher calls raise ConnectionError (server unreachable)
        mock_ci_client.cypher = AsyncMock(side_effect=ConnectionError("no server"))

        # Seed a real events.jsonl so the JSONL path returns something
        session_dir = tmp_path / "ws" / "sessions" / "s1" / "context-intelligence"
        session_dir.mkdir(parents=True)
        (session_dir / "events.jsonl").write_text(
            json.dumps(
                {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}
            )
            + "\n"
        )

        # Patch the queries directory so it contains at least one file for the map
        q_dir = tmp_path / "queries"
        q_dir.mkdir()
        for _component, (stem, _bk, _ck) in signals._SESSION_QUERY_MAP.items():
            (q_dir / f"{stem}.cypher").write_text("MATCH (n) RETURN n LIMIT 1\n")
        monkeypatch.setattr(signals, "_queries_dir", lambda: q_dir)

        result = await signals.run_signals(
            client=mock_ci_client,
            workspace="ws",
            session_id="s1",
            base_path=tmp_path,
        )

        # JSONL fallback should have found the foundation:explorer agent
        assert "foundation" in result
        assert result["foundation"]["agents"] == 1

    async def test_no_fallback_when_server_returns_empty(
        self, mock_ci_client, monkeypatch, tmp_path
    ):
        """When the server responds (even with empty rows), JSONL fallback is NOT used."""
        from context_intelligence.bundle_analysis import signals

        # Client succeeds but returns no rows
        mock_ci_client.cypher = AsyncMock(return_value=[])

        # Seed a JSONL file that WOULD produce results if fallback ran
        session_dir = tmp_path / "ws" / "sessions" / "s1" / "context-intelligence"
        session_dir.mkdir(parents=True)
        (session_dir / "events.jsonl").write_text(
            json.dumps(
                {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}
            )
            + "\n"
        )

        # Patch queries dir
        q_dir = tmp_path / "queries"
        q_dir.mkdir()
        for _component, (stem, _bk, _ck) in signals._SESSION_QUERY_MAP.items():
            (q_dir / f"{stem}.cypher").write_text("MATCH (n) RETURN n LIMIT 1\n")
        monkeypatch.setattr(signals, "_queries_dir", lambda: q_dir)

        with patch(
            "context_intelligence.bundle_analysis.jsonl_signals.run_signals_from_jsonl"
        ) as mock_jsonl:
            result = await signals.run_signals(
                client=mock_ci_client,
                workspace="ws",
                session_id="s1",
                base_path=tmp_path,
            )

        # JSONL function must NOT have been called
        mock_jsonl.assert_not_called()
        # Server returned empty → result is empty dict
        assert result == {}


class TestQueryFiles:
    """Tests for the individual query files in bundle_analysis/queries/."""

    def test_expected_query_files_exist(self):
        """Every file stem in both query maps resolves to an existing .cypher file."""
        from context_intelligence.bundle_analysis.signals import (
            _SESSION_QUERY_MAP,
            _WORKSPACE_QUERY_MAP,
            _queries_dir,
        )

        qdir = _queries_dir()
        missing: list[str] = []
        for query_map in (_SESSION_QUERY_MAP, _WORKSPACE_QUERY_MAP):
            for _component, (stem, _bk, _ck) in query_map.items():
                f = qdir / f"{stem}.cypher"
                if not f.exists():
                    missing.append(str(f))

        assert not missing, (
            "Expected all map-referenced query files to exist. Missing:\n" + "\n".join(missing)
        )

    def test_each_cypher_file_has_no_query_markers(self):
        """No .cypher file in queries/ may contain a // QUERY: delimiter marker.

        These markers were used only in the old multi-query files to separate
        named sections.  Each file now contains exactly one statement.
        """
        from context_intelligence.bundle_analysis.signals import _queries_dir

        qdir = _queries_dir()
        violations: list[str] = []
        for f in sorted(qdir.glob("*.cypher")):
            if "// QUERY:" in f.read_text(encoding="utf-8"):
                violations.append(f.name)

        assert not violations, (
            "Expected no // QUERY: markers in individual query files. "
            "Found in:\n" + "\n".join(violations)
        )

    @pytest.mark.asyncio
    async def test_run_signals_sends_one_query_per_component(
        self, mock_ci_client, monkeypatch, tmp_path
    ):
        """run_signals sends exactly one call per component — one file, one query.

        With one .cypher file per query there is no selection logic; run_signals
        reads the file and sends its content directly.  No // QUERY: markers
        should appear in any transmitted query string.
        """
        from context_intelligence.bundle_analysis import signals

        # Build a controlled query directory: one file per stem in the session map
        q_dir = tmp_path / "queries"
        q_dir.mkdir()
        for _component, (stem, _bk, _ck) in signals._SESSION_QUERY_MAP.items():
            (q_dir / f"{stem}.cypher").write_text(
                "MATCH (s:Session {session_id: $session_id})"
                " RETURN 'bundle' AS bundle, 1 AS invocations;\n"
            )

        monkeypatch.setattr(signals, "_queries_dir", lambda: q_dir)

        captured: list[str] = []

        async def capturing_cypher(query: str, workspace: str, *, params=None):
            captured.append(query)
            return []

        mock_ci_client.cypher.side_effect = capturing_cypher

        await signals.run_signals(client=mock_ci_client, workspace="ws", session_id="abc-123")

        assert len(captured) == len(signals._SESSION_QUERY_MAP), (
            f"Expected one Cypher call per component "
            f"({len(signals._SESSION_QUERY_MAP)}), got {len(captured)}"
        )
        for q in captured:
            assert "// QUERY:" not in q, (
                f"Expected no // QUERY: markers in transmitted queries, but got:\n{q}"
            )
