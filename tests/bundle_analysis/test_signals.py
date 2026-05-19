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
        assert all(c.args[1] == "my-workspace" or c.kwargs.get("workspace") == "my-workspace"
                   for c in calls)

    async def test_returns_bundle_keyed_dict(self, mock_ci_client):
        mock_ci_client.cypher = AsyncMock(return_value=[
            {"bundle": "foundation", "agent": "explorer", "invocations": 1},
        ])
        from context_intelligence.bundle_analysis.signals import run_signals

        result = await run_signals(client=mock_ci_client, workspace="ws")
        assert "foundation" in result
        assert isinstance(result["foundation"], dict)
        for key in ("agents", "skills", "modes", "recipes", "tools"):
            assert key in result["foundation"]

    async def test_agent_count_aggregated_from_rows(self, mock_ci_client):
        mock_ci_client.cypher = AsyncMock(return_value=[
            {"bundle": "foundation", "agent": "explorer", "invocations": 3},
            {"bundle": "foundation", "agent": "zen-architect", "invocations": 2},
        ])
        from context_intelligence.bundle_analysis.signals import run_signals

        result = await run_signals(client=mock_ci_client, workspace="ws")
        # 5 total agent invocations across two agents for foundation
        assert result["foundation"]["agents"] == 5

    async def test_session_id_passed_as_param(self, mock_ci_client):
        from context_intelligence.bundle_analysis.signals import run_signals

        await run_signals(client=mock_ci_client, workspace="ws", session_id="abc-123")
        calls = mock_ci_client.cypher.await_args_list
        # at least one call passes session_id in params
        assert any(
            (c.kwargs.get("params") or {}).get("session_id") == "abc-123"
            for c in calls
        )

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
