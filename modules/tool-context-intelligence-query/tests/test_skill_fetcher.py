"""Tests for SkillFetcher (relocated into tool-context-intelligence-query) — conditional HTTP GET.

Ported from spike branch modules/tool-graph-query/tests/test_skill_fetcher.py.
Package retargeted: amplifier_module_tool_graph_query
              → amplifier_module_tool_context_intelligence_query
No structural fixes needed — all symbols exist verbatim on main.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_http_mock(status_code: int, text: str, etag: str) -> MagicMock:
    """Patch-ready mock for httpx.AsyncClient used as an async context manager."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = {"etag": etag} if etag else {}

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=client)


def _make_error_mock(exc: Exception) -> MagicMock:
    """Patch-ready mock for httpx.AsyncClient that raises exc on get()."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=exc)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=client)


def _make_version_http_mock(status_code: int, body: dict) -> MagicMock:
    """Mock for check_server_version() — calls AsyncClient().get() directly (no async with)."""
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=body)

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return MagicMock(return_value=client)


class TestConstants:
    def test_watched_skills_contains_only_graph_query(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import WATCHED_SKILLS

        assert WATCHED_SKILLS == frozenset({"context-intelligence-graph-query"})


class TestSkillFetcher200:
    async def test_returns_true_on_200(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(200, "skill content", 'W/"abc123"')):
            result = await fetcher.fetch("my-skill", skill_path)
        assert result is True

    async def test_writes_content_to_skill_path(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(200, "skill content here", 'W/"abc123"')):
            await fetcher.fetch("my-skill", skill_path)
        assert skill_path.read_text() == "skill content here"

    async def test_writes_etag_sidecar(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(200, "skill content", 'W/"etag-value"')):
            await fetcher.fetch("my-skill", skill_path)
        etag_path = tmp_path / ".etag"
        assert etag_path.exists()
        assert etag_path.read_text() == 'W/"etag-value"'

    async def test_writes_content_hash_sidecar(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(200, "abc", 'W/"e"')):
            await fetcher.fetch("my-skill", skill_path)
        content_hash_path = tmp_path / ".content_hash"
        assert content_hash_path.exists()
        assert content_hash_path.read_text() == hashlib.sha256(b"abc").hexdigest()


class TestSkillFetcher304:
    async def test_returns_false_on_304(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Existing Content")
        (tmp_path / ".etag").write_text('W/"abc123"')
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(304, "", "")):
            result = await fetcher.fetch("my-skill", skill_path)
        assert result is False

    async def test_does_not_overwrite_skill_on_304(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Existing Content")
        (tmp_path / ".etag").write_text('W/"abc123"')
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(304, "", "")):
            await fetcher.fetch("my-skill", skill_path)
        assert skill_path.read_text() == "# Existing Content"


class TestSkillFetcherUnexpectedStatus:
    async def test_returns_false_on_404(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(404, "not found", "")):
            result = await fetcher.fetch("my-skill", skill_path)
        assert result is False
        assert not skill_path.exists()

    async def test_logs_warning_on_unexpected_status(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with caplog.at_level(logging.WARNING):
            with patch("httpx.AsyncClient", _make_http_mock(500, "server error", "")):
                await fetcher.fetch("my-skill", skill_path)
        assert any("skill_fetch_failed" in r.getMessage() for r in caplog.records)


class TestSkillFetcherErrors:
    async def test_returns_false_on_connect_error(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_error_mock(httpx.ConnectError("refused"))):
            result = await fetcher.fetch("my-skill", skill_path)
        assert result is False
        assert not skill_path.exists()

    async def test_returns_false_on_timeout(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch(
            "httpx.AsyncClient",
            _make_error_mock(httpx.TimeoutException("timed out", request=None)),
        ):
            result = await fetcher.fetch("my-skill", skill_path)
        assert result is False
        assert not skill_path.exists()


class TestSkillFetcherETagSidecar:
    async def test_no_etag_sidecar_sends_unconditional_get(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        mock_cls = _make_http_mock(200, "skill content", "")
        with patch("httpx.AsyncClient", mock_cls):
            await fetcher.fetch("my-skill", skill_path)
        sent_headers = mock_cls.return_value.get.call_args.kwargs.get("headers", {})
        assert "If-None-Match" not in sent_headers

    async def test_existing_etag_sidecar_sends_if_none_match_when_hash_matches(
        self, tmp_path: Path
    ) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Existing skill content")
        (tmp_path / ".content_hash").write_text(hashlib.sha256(skill_path.read_bytes()).hexdigest())
        (tmp_path / ".etag").write_text("stored-etag-value")
        fetcher = SkillFetcher("http://localhost:8000")
        mock_cls = _make_http_mock(304, "", "")
        with patch("httpx.AsyncClient", mock_cls):
            await fetcher.fetch("my-skill", skill_path)
        sent_headers = mock_cls.return_value.get.call_args.kwargs.get("headers", {})
        assert sent_headers.get("If-None-Match") == "stored-etag-value"

    async def test_drift_skips_if_none_match_for_unconditional_get(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# drifted local content")
        # Stored hash deliberately does NOT match the current file -> drift.
        (tmp_path / ".content_hash").write_text("0" * 64)
        (tmp_path / ".etag").write_text("stored-etag-value")
        fetcher = SkillFetcher("http://localhost:8000")
        mock_cls = _make_http_mock(200, "new server content", 'W/"new"')
        with patch("httpx.AsyncClient", mock_cls):
            await fetcher.fetch("my-skill", skill_path)
        sent_headers = mock_cls.return_value.get.call_args.kwargs.get("headers", {})
        assert "If-None-Match" not in sent_headers

    async def test_no_etag_sidecar_written_when_response_omits_etag(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        etag_path = tmp_path / ".etag"
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(200, "skill content", "")):
            result = await fetcher.fetch("my-skill", skill_path)
        assert result is True
        assert skill_path.read_text() == "skill content"
        assert not etag_path.exists()

    async def test_etag_sidecar_updated_on_200(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        (tmp_path / ".etag").write_text("old-etag")
        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_http_mock(200, "new content", "new-etag")):
            await fetcher.fetch("my-skill", skill_path)
        assert (tmp_path / ".etag").read_text() == "new-etag"


class TestVersionCapability:
    def test_is_skills_capable_none_returns_false(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            _is_skills_capable,
        )

        assert _is_skills_capable(None) is False

    def test_is_skills_capable_old_version_returns_false(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            _is_skills_capable,
        )

        assert _is_skills_capable("1.9.0") is False

    def test_is_skills_capable_min_version_returns_true(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            _is_skills_capable,
        )

        assert _is_skills_capable("2.0.0") is True

    def test_is_skills_capable_unparseable_returns_false(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            _is_skills_capable,
        )

        assert _is_skills_capable("invalid") is False

    def test_version_check_result_namedtuple(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            VersionCheckResult,
        )

        result = VersionCheckResult(reachable=True, version="2.0.0")
        assert result.reachable is True
        assert result.version == "2.0.0"


class TestCheckServerVersion:
    async def test_connect_error_returns_unreachable(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            SkillFetcher,
            VersionCheckResult,
        )

        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_error_mock(httpx.ConnectError("refused"))):
            result = await fetcher.check_server_version()
        assert result == VersionCheckResult(reachable=False, version=None)

    async def test_404_returns_reachable_with_none_version(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            SkillFetcher,
            VersionCheckResult,
        )

        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_version_http_mock(404, {})):
            result = await fetcher.check_server_version()
        assert result == VersionCheckResult(reachable=True, version=None)

    async def test_200_with_version_returns_reachable_with_version(self) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import (
            SkillFetcher,
            VersionCheckResult,
        )

        fetcher = SkillFetcher("http://localhost:8000")
        with patch("httpx.AsyncClient", _make_version_http_mock(200, {"version": "2.0.0"})):
            result = await fetcher.check_server_version()
        assert result == VersionCheckResult(reachable=True, version="2.0.0")


class TestSkillFetcherAuth:
    async def test_bearer_header_present_when_api_key_set(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000", api_key="secret-token")
        mock_cls = _make_http_mock(200, "skill content", "")
        with patch("httpx.AsyncClient", mock_cls):
            await fetcher.fetch("my-skill", skill_path)
        sent_headers = mock_cls.return_value.get.call_args.kwargs.get("headers", {})
        assert sent_headers.get("Authorization") == "Bearer secret-token"

    async def test_no_auth_header_when_api_key_absent(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")
        mock_cls = _make_http_mock(200, "skill content", "")
        with patch("httpx.AsyncClient", mock_cls):
            await fetcher.fetch("my-skill", skill_path)
        sent_headers = mock_cls.return_value.get.call_args.kwargs.get("headers", {})
        assert "Authorization" not in sent_headers

    async def test_auth_and_if_none_match_coexist(self, tmp_path: Path) -> None:
        from amplifier_module_tool_context_intelligence_query.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        content = b"# Existing skill content"
        skill_path.write_bytes(content)
        (tmp_path / ".content_hash").write_text(hashlib.sha256(content).hexdigest())
        (tmp_path / ".etag").write_text("stored-etag-value")
        fetcher = SkillFetcher("http://localhost:8000", api_key="secret-token")
        mock_cls = _make_http_mock(304, "", "")
        with patch("httpx.AsyncClient", mock_cls):
            await fetcher.fetch("my-skill", skill_path)
        sent_headers = mock_cls.return_value.get.call_args.kwargs.get("headers", {})
        assert sent_headers.get("Authorization") == "Bearer secret-token"
        assert sent_headers.get("If-None-Match") == "stored-etag-value"
