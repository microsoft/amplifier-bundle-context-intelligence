"""SkillFetcher — conditional HTTP GET for dynamic skill population."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

import httpx

logger = logging.getLogger(__name__)

WATCHED_SKILLS: frozenset[str] = frozenset({"context-intelligence-graph-query"})

# Coordinator capability key registered by the tool-skills module at mount time.
# tool-skills populates this with a SkillsDiscovery object that exposes
# .find(skill_name) -> SkillMetadata with the absolute filesystem path for each skill.
TOOL_SKILLS_DISCOVERY_CAPABILITY: str = "skills_discovery"


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
    """Return True if *version* is >= 2.0.0, False otherwise.

    Returns False for None, unparseable strings, and versions below 2.0.0.
    """
    try:
        parsed = tuple(int(part) for part in version.split("."))  # type: ignore[union-attr]
    except (ValueError, AttributeError):
        return False
    return parsed >= _MIN_SKILLS_VERSION


class SkillFetcher:
    """Fetches skill files from a remote server with conditional GET (ETag)."""

    def __init__(self, server_url: str, timeout: float = 3.0) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout

    async def check_server_version(self) -> VersionCheckResult:
        """Check the server version via GET /version.

        Returns
        -------
        VersionCheckResult with reachable=False, version=None on network errors.
        VersionCheckResult with reachable=True, version=None on 404.
        VersionCheckResult with reachable=True, version=<str|None> on 200.
        VersionCheckResult with reachable=False, version=None on any other status.
        Never raises — all exceptions are caught.
        """
        url = f"{self._server_url}/version"
        try:
            # Single GET — no context manager needed; httpx cleans up via __del__.
            response = await httpx.AsyncClient().get(url, timeout=self._timeout)
        except httpx.RequestError as exc:
            logger.debug("check_server_version: unreachable — %s", exc)
            return VersionCheckResult(reachable=False, version=None)

        if response.status_code == 404:
            logger.debug("check_server_version: server reachable, /version absent (404)")
            return VersionCheckResult(reachable=True, version=None)

        if response.status_code == 200:
            version = response.json().get("version")
            logger.debug("check_server_version: server at %s reported version=%s", url, version)
            return VersionCheckResult(reachable=True, version=version)

        logger.debug(
            "check_server_version: unexpected status %d — treating as unreachable",
            response.status_code,
        )
        return VersionCheckResult(reachable=False, version=None)

    # DEPRECATED: Remove once all servers >= 2.0.0.
    def write_legacy_content(self, skill_name: str, skill_path: Path) -> None:
        """Write bundled legacy skill content to *skill_path*.

        Reads the corresponding .md file from the ``legacy_content`` package
        directory and writes it to *skill_path*.  Any existing ``.etag`` sidecar
        alongside *skill_path* is removed so the next session performs an
        unconditional GET once the server is upgraded.

        Raises
        ------
        FileNotFoundError
            If no legacy content exists for *skill_name* (packaging error —
            must not be silenced).

        .. deprecated::
            Remove this method once all servers are >= 2.0.0.
        """
        legacy_path = Path(__file__).parent / "legacy_content" / f"{skill_name}.md"
        content = legacy_path.read_text(encoding="utf-8")
        skill_path.write_text(content, encoding="utf-8")

        etag_path = skill_path.parent / ".etag"
        if etag_path.exists():
            etag_path.unlink()

        logger.debug("legacy_skill_written: skill=%s [DEPRECATED]", skill_name)

    async def fetch(self, skill_name: str, skill_path: Path) -> bool:
        """Fetch a skill file from the server.

        Performs a conditional HTTP GET using If-None-Match when an ETag sidecar
        exists alongside *skill_path*.

        Returns
        -------
        True  — 200 received; *skill_path* and the .etag sidecar were updated.
        False — 304 (not modified), connection/timeout error, or unexpected status.
        """
        url = f"{self._server_url}/skills/{skill_name}"
        etag_path = skill_path.parent / ".etag"

        headers: dict[str, str] = {}
        if etag_path.exists():
            stored_etag = etag_path.read_text().strip()
            if stored_etag:
                headers["If-None-Match"] = stored_etag

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
