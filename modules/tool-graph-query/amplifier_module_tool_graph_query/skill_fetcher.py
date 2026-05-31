"""SkillFetcher — conditional HTTP GET for dynamic skill population.

Relocated from hook-context-intelligence into tool-graph-query: skill-content
sync is an analytics-path concern, consumed by the graph-analyst sub-session,
NOT a logging concern.  The ETag + content-hash drift logic is unchanged; the
deprecated bundled-legacy-content writer was dropped during relocation.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

WATCHED_SKILLS: frozenset[str] = frozenset({"context-intelligence-graph-query"})

# Sidecar filenames stored alongside SKILL.md
_ETAG_FILENAME: str = ".etag"
_CONTENT_HASH_FILENAME: str = ".content_hash"


class VersionCheckResult(NamedTuple):
    """Result of a server version pre-check.

    reachable: True when the server responded (even with 404); False on network errors.
    version: The server version string from GET /version, or None if not available.
    """

    reachable: bool
    version: str | None


# DEPRECATED: Use server capability negotiation instead of version comparison.
_MIN_SKILLS_VERSION: tuple[int, ...] = (2, 0, 0)


def _is_skills_capable(version: str | None) -> bool:
    """Return True if *version* is >= 2.0.0, False otherwise (incl. None/unparseable)."""
    try:
        parsed = tuple(int(part) for part in version.split("."))  # type: ignore[union-attr]
    except (ValueError, AttributeError):
        return False
    return parsed >= _MIN_SKILLS_VERSION


def _sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of *path*'s content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SkillFetcher:
    """Fetches skill files from a remote server with conditional GET (ETag).

    Drift detection
    ---------------
    tool-skills loads skills from git at mount time, potentially overwriting a
    SKILL.md that was previously fetched from the server.  A ``.content_hash``
    sidecar (SHA-256 of the last server-written content) is stored alongside the
    ``.etag`` sidecar.  Before sending ``If-None-Match`` the fetcher verifies the
    local file's hash still matches the stored hash.  A mismatch means the file
    drifted (git, manual edit, etc.) and an unconditional GET is performed.
    """

    def __init__(self, server_url: str, timeout: float = 3.0) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout

    async def check_server_version(self) -> VersionCheckResult:
        """Check the server version via GET /version. Never raises."""
        import httpx  # noqa: PLC0415 — lazy import to avoid loading httpx at module init time

        url = f"{self._server_url}/version"
        try:
            response = await httpx.AsyncClient().get(url, timeout=self._timeout)
        except httpx.RequestError as exc:
            logger.debug("check_server_version: unreachable — %s", exc)
            return VersionCheckResult(reachable=False, version=None)

        if response.status_code == 404:
            return VersionCheckResult(reachable=True, version=None)

        if response.status_code == 200:
            version = response.json().get("version")
            return VersionCheckResult(reachable=True, version=version)

        logger.debug(
            "check_server_version: unexpected status %d — treating as unreachable",
            response.status_code,
        )
        return VersionCheckResult(reachable=False, version=None)

    async def fetch(self, skill_name: str, skill_path: Path) -> bool:
        """Fetch a skill file from the server (conditional GET via If-None-Match).

        Returns
        -------
        True  — 200 received; *skill_path*, ``.etag``, and ``.content_hash`` updated.
        False — 304, connection/timeout error, or unexpected status.
        """
        import httpx  # noqa: PLC0415 — lazy import to avoid loading httpx at module init time

        url = f"{self._server_url}/skills/{skill_name}"
        etag_path = skill_path.parent / _ETAG_FILENAME
        content_hash_path = skill_path.parent / _CONTENT_HASH_FILENAME

        headers: dict[str, str] = {}
        if etag_path.exists():
            stored_etag = etag_path.read_text().strip()
            if stored_etag:
                if skill_path.exists() and content_hash_path.exists():
                    stored_hash = content_hash_path.read_text().strip()
                    current_hash = _sha256(skill_path)
                    if current_hash == stored_hash:
                        headers["If-None-Match"] = stored_etag
                    else:
                        logger.info(
                            "skill_local_drift: %s — local content modified externally "
                            "(stored hash %s… → current %s…); "
                            "skipping If-None-Match for unconditional GET",
                            skill_name,
                            stored_hash[:8],
                            current_hash[:8],
                        )
                else:
                    logger.debug(
                        "skill_hash_missing: %s — no .content_hash sidecar; "
                        "skipping If-None-Match for unconditional GET",
                        skill_name,
                    )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=self._timeout)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("skill_fetch_failed: %s — %s", skill_name, exc)
            return False

        if response.status_code == 200:
            skill_path.write_text(response.text)
            etag = response.headers.get("etag", "")
            if etag:
                etag_path.write_text(etag)
            content_hash_path.write_text(_sha256(skill_path))
            return True

        if response.status_code == 304:
            logger.debug("Skill %s not modified (304)", skill_name)
            return False

        logger.warning(
            "skill_fetch_failed: unexpected status %d for %s",
            response.status_code,
            skill_name,
        )
        return False
