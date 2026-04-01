"""SkillFetcher — conditional HTTP GET for dynamic skill population."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

WATCHED_SKILLS: frozenset[str] = frozenset({"context-intelligence-graph-query"})

# Coordinator capability key registered by the tool-skills module at mount time.
# tool-skills populates this with a SkillsDiscovery object that exposes
# .find(skill_name) -> SkillMetadata with the absolute filesystem path for each skill.
TOOL_SKILLS_DISCOVERY_CAPABILITY: str = "skills_discovery"


class SkillFetcher:
    """Fetches skill files from a remote server with conditional GET (ETag)."""

    def __init__(self, server_url: str, timeout: float = 3.0) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout

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
