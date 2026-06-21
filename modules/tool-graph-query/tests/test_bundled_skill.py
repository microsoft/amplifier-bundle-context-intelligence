"""Fail-loud guard for the vendored offline skill body.

The vendored ``bundled_skill/context-intelligence-graph-query.md`` is consumed at
runtime on the ``skill_sync_enabled=false`` path: when a server is configured we
swap the pessimistic "Server Unavailable" stub for this real body so the
graph-analyst is not stranded. A prior refactor already deleted the equivalent
``legacy_content`` fallback once; these tests make any future deletion, wheel
omission, or silent drift FAIL LOUD in CI instead of in production.
"""

from __future__ import annotations

import hashlib
from importlib import resources

_PKG = "amplifier_module_tool_graph_query.bundled_skill"
_SKILL_FILE = "context-intelligence-graph-query.md"


def test_vendored_body_is_packaged_and_importable() -> None:
    resource = resources.files(_PKG).joinpath(_SKILL_FILE)
    assert resource.is_file(), (
        f"vendored offline body {_SKILL_FILE!r} is missing from the "
        f"{_PKG} package — it must ship in the wheel (see pyproject force-include)"
    )


def test_vendored_body_hash_is_pinned() -> None:
    from amplifier_module_tool_graph_query.bundled_skill import EXPECTED_BUNDLED_SKILL_SHA256

    data = resources.files(_PKG).joinpath(_SKILL_FILE).read_text(encoding="utf-8")
    actual = hashlib.sha256(data.encode("utf-8")).hexdigest()
    assert actual == EXPECTED_BUNDLED_SKILL_SHA256, (
        "vendored offline body drifted from its pinned hash. If this was an "
        "intentional refresh from the canonical "
        "microsoft/amplifier-context-intelligence skill, update "
        "EXPECTED_BUNDLED_SKILL_SHA256 in bundled_skill/__init__.py and re-run the "
        "DTU proof."
    )


def test_vendored_body_is_the_real_skill_not_the_stub() -> None:
    """Guard against accidentally vendoring the 'Server Unavailable' stub."""
    data = resources.files(_PKG).joinpath(_SKILL_FILE).read_text(encoding="utf-8")
    assert "Server Unavailable" not in data, (
        "vendored body must be the REAL graph-query skill, not the stub"
    )
    assert "# Context Intelligence Graph Query" in data
    # The watched-skill name the swap logic resolves must match this file's stem.
    from amplifier_module_tool_graph_query.skill_fetcher import WATCHED_SKILLS

    assert _SKILL_FILE[: -len(".md")] in WATCHED_SKILLS
