"""Tests for the context-intelligence-graph-query skill fallback SKILL.md.

Validates that SKILL.md contains ONLY the YAML frontmatter plus the
minimal fallback message — no Cypher patterns, no schema documentation.
This is the cold-start fallback for when the server is unreachable.

Acceptance Criteria:
  AC-1: File exists at skills/context-intelligence-graph-query/SKILL.md
  AC-2: YAML frontmatter is unchanged (name, version, description, license)
  AC-3: Body contains only the fallback message — no Cypher patterns
  AC-4: File is ~15 lines
  AC-5: Body contains delegation instruction to session-navigator
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BUNDLE_DIR = Path(__file__).resolve().parent.parent
SKILL_FILE = _BUNDLE_DIR / "skills" / "context-intelligence-graph-query" / "SKILL.md"

# ---------------------------------------------------------------------------
# Content loading
# ---------------------------------------------------------------------------

try:
    _SKILL_CONTENT: str = SKILL_FILE.read_text() if SKILL_FILE.exists() else ""
except OSError:
    _SKILL_CONTENT = ""


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1))


_FRONTMATTER: dict = _parse_frontmatter(_SKILL_CONTENT) if _SKILL_CONTENT else {}


def _get_body(content: str) -> str:
    """Return everything after the closing --- of the frontmatter."""
    match = re.match(r"^---\n.*?\n---\n(.*)", content, re.DOTALL)
    if not match:
        return ""
    return match.group(1)


_BODY: str = _get_body(_SKILL_CONTENT) if _SKILL_CONTENT else ""


# ===========================================================================
# AC-1: File existence
# ===========================================================================


def test_ac1_skill_file_exists() -> None:
    """SKILL.md must exist at skills/context-intelligence-graph-query/SKILL.md."""
    assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"


# ===========================================================================
# AC-2: YAML frontmatter unchanged
# ===========================================================================


def test_ac2_frontmatter_name() -> None:
    """Frontmatter name must be context-intelligence-graph-query."""
    assert _FRONTMATTER.get("name") == "context-intelligence-graph-query", (
        f"Expected name='context-intelligence-graph-query', got: {_FRONTMATTER.get('name')!r}"
    )


def test_ac2_frontmatter_version() -> None:
    """Frontmatter version must be 1.0.0."""
    assert _FRONTMATTER.get("version") == "1.0.0", (
        f"Expected version='1.0.0', got: {_FRONTMATTER.get('version')!r}"
    )


def test_ac2_frontmatter_license() -> None:
    """Frontmatter license must be MIT."""
    assert _FRONTMATTER.get("license") == "MIT", (
        f"Expected license='MIT', got: {_FRONTMATTER.get('license')!r}"
    )


def test_ac2_frontmatter_description_present() -> None:
    """Frontmatter must have a non-empty description."""
    desc = _FRONTMATTER.get("description", "")
    assert desc, "Frontmatter must include a non-empty description"


# ===========================================================================
# AC-3: Body is fallback message only — no Cypher
# ===========================================================================


def test_ac3_no_cypher_blocks_in_body() -> None:
    """Body must not contain any ```cypher code blocks."""
    assert "```cypher" not in _BODY, (
        "SKILL.md body must not contain Cypher code blocks (this is the fallback version)"
    )


def test_ac3_no_cypher_keyword_in_body() -> None:
    """Body must not use the word 'Cypher' in the body content (except in heading title)."""
    # The heading is allowed to say "Server Unavailable" but not teach Cypher
    assert "MATCH" not in _BODY, (
        "SKILL.md body must not contain Cypher MATCH clauses"
    )
    assert "RETURN" not in _BODY, (
        "SKILL.md body must not contain Cypher RETURN clauses"
    )


def test_ac3_body_contains_server_unavailable_message() -> None:
    """Body must state that the context intelligence server is not reachable."""
    body_lower = _BODY.lower()
    assert "not reachable" in body_lower or "unavailable" in body_lower, (
        "Body must state that the server is not reachable or unavailable"
    )


def test_ac3_body_contains_fallback_heading() -> None:
    """Body must contain the fallback heading."""
    assert "# Context Intelligence Graph Query" in _BODY, (
        "Body must contain the fallback heading"
    )


# ===========================================================================
# AC-4: File is ~15 lines
# ===========================================================================


def test_ac4_file_line_count_is_approximately_15() -> None:
    """File must be approximately 15 lines (between 10 and 20)."""
    line_count = len(_SKILL_CONTENT.splitlines())
    assert 10 <= line_count <= 20, (
        f"Expected ~15 lines, got {line_count}. "
        "The fallback SKILL.md should be minimal."
    )


# ===========================================================================
# AC-5: Delegation instruction to session-navigator
# ===========================================================================


def test_ac5_body_delegates_to_session_navigator() -> None:
    """Body must instruct delegation to session-navigator."""
    assert "session-navigator" in _BODY, (
        "Body must instruct delegation to `session-navigator`"
    )


def test_ac5_body_says_do_not_attempt_cypher() -> None:
    """Body must tell the agent not to attempt Cypher queries."""
    body_lower = _BODY.lower()
    assert "do not attempt" in body_lower or "not available" in body_lower, (
        "Body must instruct agent not to attempt Cypher queries"
    )
