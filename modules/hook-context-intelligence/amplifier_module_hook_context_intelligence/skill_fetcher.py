"""SkillFetcher — conditional HTTP GET for dynamic skill population."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

WATCHED_SKILLS: frozenset[str] = frozenset({"context-intelligence-graph-query"})

# Coordinator capability key registered by the tool-skills module at mount time.
# tool-skills populates this with a SkillsDiscovery object that exposes
# .find(skill_name) -> SkillMetadata with the absolute filesystem path for each skill.
TOOL_SKILLS_DISCOVERY_CAPABILITY: str = "skills_discovery"

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
    """Return True if *version* is >= 2.0.0, False otherwise.

    Returns False for None, unparseable strings, and versions below 2.0.0.
    """
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
    SKILL.md that was previously fetched from the server.  To avoid the fetcher
    incorrectly trusting a stale ETag after such an external write, a
    ``.content_hash`` sidecar (SHA-256 of the last server-written content) is
    stored alongside the ``.etag`` sidecar.  Before sending ``If-None-Match``,
    the fetcher verifies that the local file's hash still matches the stored
    hash.  A mismatch means the file drifted (git, manual edit, etc.) and an
    unconditional GET is performed instead.
    """

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
        import httpx  # noqa: PLC0415 — lazy import to avoid loading httpx at module init time

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
        unconditional GET once the server is upgraded.  The ``.content_hash``
        sidecar is updated to reflect what was written so drift detection
        remains accurate.

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

        etag_path = skill_path.parent / _ETAG_FILENAME
        if etag_path.exists():
            etag_path.unlink()

        # Keep .content_hash in sync with what was written so the next
        # fetch() can detect if git later overwrites the file again.
        content_hash_path = skill_path.parent / _CONTENT_HASH_FILENAME
        content_hash_path.write_text(_sha256(skill_path))

        logger.debug("legacy_skill_written: skill=%s [DEPRECATED]", skill_name)

    async def fetch(self, skill_name: str, skill_path: Path) -> bool:
        """Fetch a skill file from the server.

        Performs a conditional HTTP GET using If-None-Match when an ETag sidecar
        exists alongside *skill_path* **and** the local file's SHA-256 still
        matches the stored ``.content_hash`` sidecar.  A mismatch between the
        local file and the stored hash means the file was modified externally
        (e.g. tool-skills loaded a newer version from git) — in that case the
        ETag is stale relative to the local state and an unconditional GET is
        performed to re-align the local file with the server.

        Returns
        -------
        True  — 200 received; *skill_path*, ``.etag``, and ``.content_hash``
                sidecars were all updated.
        False — 304 (not modified), connection/timeout error, or unexpected status.
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
                        # Local file unchanged since last server fetch — safe to
                        # use the cached ETag for a conditional GET.
                        headers["If-None-Match"] = stored_etag
                    else:
                        # Local file drifted (e.g. git overwrote it).  The stored
                        # ETag no longer corresponds to local content; skip it to
                        # force an unconditional GET and re-align with the server.
                        logger.info(
                            "skill_local_drift: %s — local content modified externally "
                            "(stored hash %s… → current %s…); "
                            "skipping If-None-Match for unconditional GET",
                            skill_name,
                            stored_hash[:8],
                            current_hash[:8],
                        )
                else:
                    # No content_hash sidecar yet (first run after upgrade, or
                    # legacy session).  We cannot verify whether the local file
                    # still matches the server's ETag, so skip If-None-Match and
                    # let the server decide authoritatively.
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
            # Record the hash of exactly what we wrote so drift detection works
            # on the next session start.
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
