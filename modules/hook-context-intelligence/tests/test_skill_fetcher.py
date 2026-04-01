"""Tests for SkillFetcher — conditional HTTP GET with ETag sidecar."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_http_mock(status_code: int, text: str, etag: str) -> MagicMock:
    """Build a patch-ready mock for httpx.AsyncClient as async context manager."""
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
    """Build a patch-ready mock for httpx.AsyncClient that raises exc on get()."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=exc)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=client)


class TestSkillFetcher200:
    """SkillFetcher returns True and writes files on 200 response."""

    async def test_returns_true_on_200(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_http_mock(200, "skill content", 'W/"abc123"'),
        ):
            result = await fetcher.fetch("my-skill", skill_path)

        assert result is True

    async def test_writes_content_to_skill_path(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_http_mock(200, "skill content here", 'W/"abc123"'),
        ):
            await fetcher.fetch("my-skill", skill_path)

        assert skill_path.read_text() == "skill content here"

    async def test_writes_etag_sidecar(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_http_mock(200, "skill content", 'W/"etag-value"'),
        ):
            await fetcher.fetch("my-skill", skill_path)

        etag_path = tmp_path / ".etag"
        assert etag_path.exists()
        assert etag_path.read_text() == 'W/"etag-value"'


class TestSkillFetcher304:
    """SkillFetcher returns False and does not overwrite files on 304 response."""

    async def test_returns_false_on_304(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Existing Content")
        etag_path = tmp_path / ".etag"
        etag_path.write_text('W/"abc123"')
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_http_mock(304, "", ""),
        ):
            result = await fetcher.fetch("my-skill", skill_path)

        assert result is False

    async def test_does_not_overwrite_skill_on_304(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Existing Content")
        etag_path = tmp_path / ".etag"
        etag_path.write_text('W/"abc123"')
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_http_mock(304, "", ""),
        ):
            await fetcher.fetch("my-skill", skill_path)

        assert skill_path.read_text() == "# Existing Content"


class TestSkillFetcherUnexpectedStatus:
    """SkillFetcher returns False and logs a warning on unexpected HTTP status codes."""

    async def test_returns_false_on_404(self, tmp_path: Path) -> None:
        """fetch() returns False and logs a warning when the server returns 404."""
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_http_mock(404, "not found", ""),
        ):
            result = await fetcher.fetch("my-skill", skill_path)

        assert result is False
        assert not skill_path.exists()

    async def test_logs_warning_on_unexpected_status(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """fetch() emits a skill_fetch_failed warning for any non-200/304 status."""
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with caplog.at_level(logging.WARNING):
            with patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
                _make_http_mock(500, "server error", ""),
            ):
                await fetcher.fetch("my-skill", skill_path)

        assert any("skill_fetch_failed" in record.getMessage() for record in caplog.records)


class TestSkillFetcherErrors:
    """SkillFetcher returns False on connection errors and timeouts."""

    async def test_returns_false_on_connect_error(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_error_mock(httpx.ConnectError("refused")),
        ):
            result = await fetcher.fetch("my-skill", skill_path)

        assert result is False
        assert not skill_path.exists()

    async def test_returns_false_on_timeout(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_error_mock(httpx.TimeoutException("timed out", request=None)),
        ):
            result = await fetcher.fetch("my-skill", skill_path)

        assert result is False
        assert not skill_path.exists()

    async def test_logs_warning_on_connect_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        with caplog.at_level(logging.WARNING):
            with patch(
                "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
                _make_error_mock(httpx.ConnectError("refused")),
            ):
                await fetcher.fetch("my-skill", skill_path)

        assert any("skill_fetch_failed" in record.getMessage() for record in caplog.records)


class TestSkillFetcherETagSidecar:
    """SkillFetcher uses ETag sidecar for conditional GET requests."""

    async def test_no_etag_sidecar_sends_unconditional_get(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        fetcher = SkillFetcher("http://localhost:8000")

        mock_cls = _make_http_mock(200, "skill content", "")
        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            mock_cls,
        ):
            await fetcher.fetch("my-skill", skill_path)

        mock_client = mock_cls.return_value
        sent_headers = mock_client.get.call_args.kwargs.get("headers", {})
        assert "If-None-Match" not in sent_headers

    async def test_existing_etag_sidecar_sends_if_none_match(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        etag_path = tmp_path / ".etag"
        etag_path.write_text("stored-etag-value")
        fetcher = SkillFetcher("http://localhost:8000")

        mock_cls = _make_http_mock(304, "", "")
        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            mock_cls,
        ):
            await fetcher.fetch("my-skill", skill_path)

        mock_client = mock_cls.return_value
        sent_headers = mock_client.get.call_args.kwargs.get("headers", {})
        assert sent_headers.get("If-None-Match") == "stored-etag-value"

    async def test_no_etag_sidecar_written_when_response_omits_etag(self, tmp_path: Path) -> None:
        """fetch() must NOT write a .etag sidecar when the server omits the ETag header.

        An empty .etag file would be indistinguishable from an intentional empty-string
        ETag and can confuse debugging.  When no ETag is returned, skip the write.
        """
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        etag_path = tmp_path / ".etag"
        fetcher = SkillFetcher("http://localhost:8000")

        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            _make_http_mock(200, "skill content", ""),  # no ETag in response
        ):
            result = await fetcher.fetch("my-skill", skill_path)

        assert result is True
        assert skill_path.read_text() == "skill content"
        assert not etag_path.exists(), ".etag must not be written when response has no ETag"

    async def test_etag_sidecar_updated_on_200(self, tmp_path: Path) -> None:
        from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher

        skill_path = tmp_path / "SKILL.md"
        etag_path = tmp_path / ".etag"
        etag_path.write_text("old-etag")
        fetcher = SkillFetcher("http://localhost:8000")

        mock_cls = _make_http_mock(200, "new content", "new-etag")
        with patch(
            "amplifier_module_hook_context_intelligence.skill_fetcher.httpx.AsyncClient",
            mock_cls,
        ):
            await fetcher.fetch("my-skill", skill_path)

        assert etag_path.read_text() == "new-etag"
