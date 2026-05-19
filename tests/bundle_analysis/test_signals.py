"""Tests for context_intelligence.bundle_analysis.signals.

signals.py must:
- Read .cypher files from the packaged queries directory
- Call client.cypher(query, workspace, params=...) once per query file
- Parse rows into {bundle: {agents: N, skills: N, modes: N, recipes: N, tools: N}}
- Return an empty dict when the client raises or returns nothing (graceful)
- Pass session_id as a Cypher parameter when provided
"""

from __future__ import annotations

from unittest.mock import AsyncMock

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

        result = await run_signals(client=mock_ci_client, workspace="ws")
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

        result = await run_signals(client=mock_ci_client, workspace="ws")
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


class TestSelectQuery:
    """Unit tests for _select_query and _extract_cypher helpers."""

    _FAKE_CYPHER = """\
// =============================================================================
// Fake query file for testing
// =============================================================================

// -----------------------------------------------------------------------------
// QUERY: fake_query_in_session
// Signal: S-X — single-session variant
// Parameters: $session_id
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id})
      -[:HAS_EVENT]->(e:SomeEvent)
RETURN s.session_id, count(e) AS n
ORDER BY n DESC;

// -----------------------------------------------------------------------------
// QUERY: fake_query_cross_session
// Signal: S-X — workspace-wide variant
// Parameters: $workspace
// -----------------------------------------------------------------------------
MATCH (s:Session {workspace: $workspace})
      -[:HAS_EVENT]->(e:SomeEvent)
RETURN count(e) AS n;
"""

    def test_select_in_session_when_session_id_given(self):
        """_select_query returns the _in_session query when session_id is provided."""
        from context_intelligence.bundle_analysis.signals import _select_query

        result = _select_query(self._FAKE_CYPHER, session_id="abc-123")

        assert result is not None, "Expected a query to be selected but got None"
        assert "$session_id" in result, (
            "Expected the _in_session query (which uses $session_id) to be "
            f"selected. Got:\n{result}"
        )
        assert "$workspace" not in result, (
            f"Expected only the _in_session query but got one containing $workspace. Got:\n{result}"
        )

    def test_select_cross_session_when_no_session_id(self):
        """_select_query returns the _cross_session query when session_id is None."""
        from context_intelligence.bundle_analysis.signals import _select_query

        result = _select_query(self._FAKE_CYPHER, session_id=None)

        assert result is not None, "Expected a query to be selected but got None"
        assert "$workspace" in result, (
            "Expected the _cross_session query (which uses $workspace) to be "
            f"selected. Got:\n{result}"
        )
        assert "$session_id" not in result, (
            "Expected only the _cross_session query but got one containing $session_id. "
            f"Got:\n{result}"
        )

    def test_returns_none_when_no_matching_suffix(self):
        """_select_query returns None when the file has no query matching the suffix."""
        from context_intelligence.bundle_analysis.signals import _select_query

        cypher_no_cross = """\
// QUERY: only_in_session_query
MATCH (s:Session {session_id: $session_id}) RETURN s;
"""
        result = _select_query(cypher_no_cross, session_id=None)
        assert result is None, (
            f"Expected None when no _cross_session query exists but got: {result!r}"
        )

    def test_extract_cypher_strips_header_comments(self):
        """_extract_cypher strips leading doc-comment lines before the statement."""
        from context_intelligence.bundle_analysis.signals import _extract_cypher

        body = """\

// Signal: S-X
// Parameters: $session_id
// Output: n
// -----------------------------------------------------------------------------
MATCH (s:Session {session_id: $session_id}) RETURN count(s) AS n;

// trailing comment after statement
"""
        result = _extract_cypher(body)

        assert result.startswith("MATCH"), (
            f"Expected extracted Cypher to start with MATCH but got: {result[:80]!r}"
        )
        assert "$session_id" in result
        assert result.endswith(";"), (
            "Expected extracted Cypher to end with ';' (statement terminator)"
        )

    @pytest.mark.asyncio
    async def test_run_signals_sends_single_query_not_whole_file(
        self, mock_ci_client, monkeypatch, tmp_path
    ):
        """run_signals must send one query at a time, not the full multi-query file.

        When session_id is provided, every call to client.cypher must contain
        only the _in_session query (uses $session_id), never the full file.
        """
        from context_intelligence.bundle_analysis import signals

        # Point _queries_dir at a controlled directory
        q_dir = tmp_path / "_queries"
        q_dir.mkdir()
        fake = """\
// QUERY: test_in_session
MATCH (s:Session {session_id: $session_id}) RETURN count(s) AS invocations, 'bundle' AS bundle;

// QUERY: test_cross_session
MATCH (s:Session {workspace: $workspace}) RETURN count(s) AS invocations, 'bundle' AS bundle;
"""
        for stem in [
            "s01-s02-agents",
            "s04-s05-s09-s12-s13-skills-modes",
            "s03-s10-s11-recipes",
            "s08-s15-coverage-tools",
        ]:
            (q_dir / f"{stem}.cypher").write_text(fake)

        monkeypatch.setattr(signals, "_queries_dir", lambda: q_dir)

        captured_queries: list[str] = []
        original_cypher = mock_ci_client.cypher

        async def capturing_cypher(query: str, workspace: str, *, params=None):
            captured_queries.append(query)
            return await original_cypher(query, workspace, params=params)

        mock_ci_client.cypher.side_effect = capturing_cypher

        from context_intelligence.bundle_analysis.signals import run_signals

        await run_signals(client=mock_ci_client, workspace="ws", session_id="abc-123")

        assert captured_queries, "Expected at least one Cypher call but got zero"
        for q in captured_queries:
            assert "$session_id" in q, (
                "Expected only _in_session queries to be sent when session_id "
                f"is provided, but got a query without $session_id:\n{q}"
            )
            assert "$workspace" not in q, (
                "Expected no _cross_session queries when session_id is provided, "
                f"but got a query containing $workspace:\n{q}"
            )
