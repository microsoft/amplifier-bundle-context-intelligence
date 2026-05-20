"""Tests for context_intelligence.bundle_analysis.signals (fetchers+processor orchestrator).

signals.py must be a thin orchestrator:
- When client.server_url is set, use GraphFetcher first.
- If graph responds (server_ok=True), its result is authoritative — no JSONL fallback.
- If graph fails (server_ok=False), fall back to JSONLFetcher.
- When client.server_url is None, skip graph and read JSONL directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_intelligence.bundle_analysis.fetchers import RawSignalEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_jsonl_path(base: Path, ws: str, sid: str) -> Path:
    """Return the path to a session's events.jsonl file."""
    return base / ws / "sessions" / sid / "context-intelligence" / "events.jsonl"


def _write_events(path: Path, records: list[dict]) -> None:
    """Write JSONL event records to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_AGENT_SPAWNED_EVENT = {"event": "delegate:agent_spawned", "data": {"agent": "foundation:explorer"}}


# ---------------------------------------------------------------------------
# TestRunSignalsGraphPath
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunSignalsGraphPath:
    async def test_uses_graph_when_server_url_set_and_succeeds(self, tmp_path):
        """When server_url is set and graph succeeds, uses graph result; JSONL not called."""
        from context_intelligence.bundle_analysis.signals import run_signals

        client = MagicMock()
        client.server_url = "http://localhost:7474"

        graph_events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]

        with (
            patch("context_intelligence.bundle_analysis.signals.GraphFetcher") as MockGraphFetcher,
            patch("context_intelligence.bundle_analysis.signals.JSONLFetcher") as MockJSONLFetcher,
        ):
            mock_graph_instance = AsyncMock()
            mock_graph_instance.fetch = AsyncMock(return_value=(graph_events, True))
            MockGraphFetcher.return_value = mock_graph_instance

            result = await run_signals(client=client, workspace="ws", base_path=tmp_path)

        assert result["foundation"]["agents"] == 1
        MockJSONLFetcher.return_value.fetch.assert_not_called()

    async def test_empty_graph_result_is_authoritative(self, tmp_path):
        """When graph returns ([], True), result is {} — JSONL not read even with events on disk."""
        from context_intelligence.bundle_analysis.signals import run_signals

        client = MagicMock()
        client.server_url = "http://localhost:7474"

        # Seed events on disk that WOULD produce results if JSONL fallback ran
        path = _session_jsonl_path(tmp_path, "ws", "s1")
        _write_events(path, [_AGENT_SPAWNED_EVENT])

        with (
            patch("context_intelligence.bundle_analysis.signals.GraphFetcher") as MockGraphFetcher,
            patch("context_intelligence.bundle_analysis.signals.JSONLFetcher") as MockJSONLFetcher,
        ):
            mock_graph_instance = AsyncMock()
            mock_graph_instance.fetch = AsyncMock(return_value=([], True))
            MockGraphFetcher.return_value = mock_graph_instance

            result = await run_signals(
                client=client, workspace="ws", session_id="s1", base_path=tmp_path
            )

        assert result == {}
        MockJSONLFetcher.return_value.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunSignalsJsonlFallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunSignalsJsonlFallback:
    async def test_falls_back_when_graph_fails(self, tmp_path):
        """When graph returns ([], False), falls back to JSONL and reads events from disk."""
        from context_intelligence.bundle_analysis.signals import run_signals

        client = MagicMock()
        client.server_url = "http://localhost:7474"

        # Seed events on disk for the JSONL fallback
        path = _session_jsonl_path(tmp_path, "ws", "s1")
        _write_events(path, [_AGENT_SPAWNED_EVENT])

        with patch("context_intelligence.bundle_analysis.signals.GraphFetcher") as MockGraphFetcher:
            mock_graph_instance = AsyncMock()
            mock_graph_instance.fetch = AsyncMock(return_value=([], False))
            MockGraphFetcher.return_value = mock_graph_instance

            result = await run_signals(
                client=client, workspace="ws", session_id="s1", base_path=tmp_path
            )

        assert result["foundation"]["agents"] == 1

    async def test_no_server_url_goes_straight_to_jsonl(self, tmp_path):
        """When client.server_url is None, skips graph entirely and reads JSONL directly."""
        from context_intelligence.bundle_analysis.signals import run_signals

        client = MagicMock()
        client.server_url = None

        # Seed events on disk
        path = _session_jsonl_path(tmp_path, "ws", "s1")
        _write_events(path, [_AGENT_SPAWNED_EVENT])

        with patch("context_intelligence.bundle_analysis.signals.GraphFetcher") as MockGraphFetcher:
            result = await run_signals(
                client=client, workspace="ws", session_id="s1", base_path=tmp_path
            )

        MockGraphFetcher.return_value.fetch.assert_not_called()
        assert result["foundation"]["agents"] == 1


# ---------------------------------------------------------------------------
# TestRunSignalsSchema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunSignalsSchema:
    async def test_result_has_six_component_keys(self, tmp_path):
        """Each bundle in result has exactly six keys; modes and tools are always 0."""
        from context_intelligence.bundle_analysis.signals import run_signals

        client = MagicMock()
        client.server_url = "http://localhost:7474"

        graph_events = [RawSignalEvent(kind="agent_spawned", agent="foundation:explorer")]

        with patch("context_intelligence.bundle_analysis.signals.GraphFetcher") as MockGraphFetcher:
            mock_graph_instance = AsyncMock()
            mock_graph_instance.fetch = AsyncMock(return_value=(graph_events, True))
            MockGraphFetcher.return_value = mock_graph_instance

            result = await run_signals(client=client, workspace="ws", base_path=tmp_path)

        assert set(result["foundation"].keys()) == {
            "agents",
            "skills",
            "recipes",
            "context",
            "modes",
            "tools",
        }
        assert result["foundation"]["modes"] == 0
        assert result["foundation"]["tools"] == 0
