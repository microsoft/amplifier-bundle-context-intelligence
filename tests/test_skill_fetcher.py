"""Tests for SkillFetcher drift-detection and conditional GET logic.

Scenarios covered
-----------------
1. Fresh fetch (no sidecars)       — unconditional GET, 200 — writes SKILL.md + .etag + .content_hash
2. Hash matches stored hash        — sends If-None-Match, 304 — nothing written
3. Hash mismatch (git drift)       — skips If-None-Match, unconditional GET, 200 — all sidecars updated
4. Legacy: .etag present, no hash  — skips If-None-Match, unconditional GET
5. Server connection/timeout error — returns False, no file changes
6. write_legacy_content            — writes content + .content_hash, clears .etag
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from amplifier_module_hook_context_intelligence.skill_fetcher import SkillFetcher


# ── helpers ────────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_response(status_code: int, text: str = "", etag: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"etag": etag} if etag else {}
    return resp


def _populate_sidecars(skill_path: Path, content: str, etag: str) -> None:
    """Simulate a previous successful server fetch: write all three files."""
    skill_path.write_text(content)
    (skill_path.parent / ".etag").write_text(etag)
    (skill_path.parent / ".content_hash").write_text(_sha256(skill_path))


def _patch_httpx(response: MagicMock):
    """Context manager that wires an httpx.AsyncClient mock to return *response*."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)

    patcher = patch("httpx.AsyncClient")

    class _Ctx:
        def __enter__(self):
            self._mock_cls = patcher.__enter__()
            self._mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            self._mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            return mock_client

        def __exit__(self, *args):
            patcher.__exit__(*args)

    return _Ctx()


# ── fixtures ───────────────────────────────────────────────────────────────────


SKILL_NAME = "context-intelligence-graph-query"


@pytest.fixture()
def skill_dir(tmp_path: Path) -> Path:
    d = tmp_path / SKILL_NAME
    d.mkdir()
    return d


@pytest.fixture()
def skill_path(skill_dir: Path) -> Path:
    p = skill_dir / "SKILL.md"
    p.write_text("# stub\n")
    return p


@pytest.fixture()
def fetcher() -> SkillFetcher:
    return SkillFetcher("http://ci-server.local")


# ── 1. Fresh fetch ─────────────────────────────────────────────────────────────


class TestFreshFetch:
    """No sidecars at all — must do an unconditional GET."""

    @pytest.mark.asyncio
    async def test_200_writes_skill_etag_and_hash(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        server_content = "# Live Cypher patterns\n"
        server_etag = '"abc123"'

        with _patch_httpx(_make_response(200, text=server_content, etag=server_etag)):
            result = await fetcher.fetch(SKILL_NAME, skill_path)

        assert result is True
        assert skill_path.read_text() == server_content
        assert (skill_path.parent / ".etag").read_text() == server_etag
        hash_path = skill_path.parent / ".content_hash"
        assert hash_path.exists()
        assert hash_path.read_text() == _sha256(skill_path)

    @pytest.mark.asyncio
    async def test_no_if_none_match_sent(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        with _patch_httpx(_make_response(200, text="x")) as mock_client:
            await fetcher.fetch(SKILL_NAME, skill_path)

        _, kwargs = mock_client.get.call_args
        assert "If-None-Match" not in kwargs.get("headers", {})


# ── 2. Hash matches — use If-None-Match ────────────────────────────────────────


class TestHashMatches:
    """Local file unchanged since last server fetch — must send If-None-Match."""

    @pytest.mark.asyncio
    async def test_304_leaves_all_files_unchanged(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        original_content = "# Previously fetched\n"
        _populate_sidecars(skill_path, original_content, '"v1"')
        original_hash = _sha256(skill_path)

        with _patch_httpx(_make_response(304)):
            result = await fetcher.fetch(SKILL_NAME, skill_path)

        assert result is False
        assert skill_path.read_text() == original_content
        assert (skill_path.parent / ".content_hash").read_text() == original_hash

    @pytest.mark.asyncio
    async def test_if_none_match_header_present(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        _populate_sidecars(skill_path, "# content\n", '"v1-etag"')

        with _patch_httpx(_make_response(304)) as mock_client:
            await fetcher.fetch(SKILL_NAME, skill_path)

        _, kwargs = mock_client.get.call_args
        assert kwargs.get("headers", {}).get("If-None-Match") == '"v1-etag"'


# ── 3. Hash mismatch — drift detected ─────────────────────────────────────────


class TestHashMismatch:
    """File was overwritten externally (e.g. git) — must bypass If-None-Match."""

    @pytest.mark.asyncio
    async def test_no_if_none_match_sent_on_drift(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        _populate_sidecars(skill_path, "# Server v1\n", '"etag-v1"')
        skill_path.write_text("# Git version (different!)\n")  # drift

        with _patch_httpx(_make_response(200, text="# Server v2\n", etag='"etag-v2"')) as mc:
            await fetcher.fetch(SKILL_NAME, skill_path)

        _, kwargs = mc.get.call_args
        assert "If-None-Match" not in kwargs.get("headers", {})

    @pytest.mark.asyncio
    async def test_200_after_drift_updates_all_sidecars(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        _populate_sidecars(skill_path, "# Old server\n", '"old-etag"')
        skill_path.write_text("# Git overwrote me\n")  # drift

        new_content = "# Fresh server content\n"
        new_etag = '"new-etag"'

        with _patch_httpx(_make_response(200, text=new_content, etag=new_etag)):
            result = await fetcher.fetch(SKILL_NAME, skill_path)

        assert result is True
        assert skill_path.read_text() == new_content
        assert (skill_path.parent / ".etag").read_text() == new_etag
        assert (skill_path.parent / ".content_hash").read_text() == _sha256(skill_path)

    @pytest.mark.asyncio
    async def test_drift_emits_info_log(
        self, fetcher: SkillFetcher, skill_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _populate_sidecars(skill_path, "# Server content\n", '"etag"')
        skill_path.write_text("# Git overwrote me\n")  # drift

        with _patch_httpx(_make_response(200, text="# New\n")):
            with caplog.at_level(logging.INFO, logger="amplifier_module_hook_context_intelligence"):
                await fetcher.fetch(SKILL_NAME, skill_path)

        drift_records = [r for r in caplog.records if "skill_local_drift" in r.message]
        assert len(drift_records) == 1, "Expected exactly one skill_local_drift INFO record"


# ── 4. Legacy upgrade path (.etag only, no .content_hash) ─────────────────────


class TestLegacyUpgradePath:
    """.etag present but no .content_hash — treat as unknown, force unconditional GET."""

    @pytest.mark.asyncio
    async def test_no_if_none_match_when_hash_missing(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        skill_path.write_text("# Possibly stale\n")
        (skill_path.parent / ".etag").write_text('"legacy-etag"')
        # Deliberately no .content_hash

        with _patch_httpx(_make_response(200, text="# Fresh\n", etag='"new-etag"')) as mc:
            result = await fetcher.fetch(SKILL_NAME, skill_path)

        assert result is True
        _, kwargs = mc.get.call_args
        assert "If-None-Match" not in kwargs.get("headers", {}), (
            "If-None-Match must not be sent when .content_hash is absent"
        )

    @pytest.mark.asyncio
    async def test_content_hash_created_after_first_upgraded_fetch(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        skill_path.write_text("# stale\n")
        (skill_path.parent / ".etag").write_text('"legacy-etag"')

        with _patch_httpx(_make_response(200, text="# Fresh\n", etag='"new-etag"')):
            await fetcher.fetch(SKILL_NAME, skill_path)

        hash_path = skill_path.parent / ".content_hash"
        assert hash_path.exists(), ".content_hash must be created on first upgraded fetch"
        assert hash_path.read_text() == _sha256(skill_path)


# ── 5. Network errors ──────────────────────────────────────────────────────────


class TestNetworkErrors:
    """Connection/timeout failures must return False without touching local files."""

    @pytest.mark.asyncio
    async def test_connect_error_returns_false(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        original = skill_path.read_text()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await fetcher.fetch(SKILL_NAME, skill_path)

        assert result is False
        assert skill_path.read_text() == original

    @pytest.mark.asyncio
    async def test_timeout_returns_false(
        self, fetcher: SkillFetcher, skill_path: Path
    ) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await fetcher.fetch(SKILL_NAME, skill_path)

        assert result is False


# ── 6. write_legacy_content ────────────────────────────────────────────────────


class TestWriteLegacyContent:
    """write_legacy_content must keep .content_hash in sync and clear .etag."""

    def test_writes_content_hash_matching_file(
        self, fetcher: SkillFetcher, skill_dir: Path
    ) -> None:
        """After write_legacy_content the .content_hash must match what was written."""
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text("# stub\n")

        # The legacy content file exists in the package; call directly.
        fetcher.write_legacy_content(SKILL_NAME, skill_path)

        hash_path = skill_dir / ".content_hash"
        assert hash_path.exists(), ".content_hash must be created by write_legacy_content"
        assert hash_path.read_text() == _sha256(skill_path), (
            ".content_hash must equal SHA-256 of the written file"
        )

    def test_clears_existing_etag(
        self, fetcher: SkillFetcher, skill_dir: Path
    ) -> None:
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text("# stub\n")
        (skill_dir / ".etag").write_text('"old-etag"')

        fetcher.write_legacy_content(SKILL_NAME, skill_path)

        assert not (skill_dir / ".etag").exists(), ".etag must be removed"

    def test_hash_changes_when_file_later_modified(
        self, fetcher: SkillFetcher, skill_dir: Path
    ) -> None:
        """After write_legacy_content, modifying the file and re-checking shows drift."""
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text("# stub\n")
        fetcher.write_legacy_content(SKILL_NAME, skill_path)

        stored_hash = (skill_dir / ".content_hash").read_text()

        # Simulate git overwriting the file after legacy write
        skill_path.write_text("# Git version\n")

        assert _sha256(skill_path) != stored_hash, (
            "After external modification the file hash must differ from stored hash"
        )
