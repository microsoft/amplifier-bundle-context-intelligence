"""Seam 5 layer-1 guard: served SKILL.md body on disk.

`skill_sync` used to overwrite `skills/context-intelligence-graph-query/SKILL.md`
at runtime, swapping the pessimistic "Server Unavailable" stub for the real body.
Now that mechanism is deleted entirely (see docs/skill-sync-removal-plan.md §3c),
the file **on disk** IS what `tool-skills`/`load_skill` serves to every session --
there is no runtime swap left to protect the invariant. This test is the cheap,
always-on, server-free guard for that invariant (§4.5 Seam 5, §7 item 5):

1. valid YAML frontmatter naming this skill,
2. the stub is gone,
3. a pinned marker for EACH of the five §2 must-teach categories is present, and
4. the no-server delegation block appears BEFORE the real skill body.

A DTU end-to-end run (Seam 5 layer-2, §4.5) additionally proves *delivery* through
the real compose-time fetch path -- this test only proves the *artifact* is right.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# skills/context-intelligence-graph-query/SKILL.md, relative to the repo root.
# This file lives at modules/tool-context-intelligence-query/tests/, so the repo
# root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILL_PATH = _REPO_ROOT / "skills" / "context-intelligence-graph-query" / "SKILL.md"

# Pinned positive markers -- one per §2 must-teach category. Each is an exact
# literal confirmed present in the current served body. A stub-only file, a
# fetch failure, an empty file, or a slice that drops any one category must
# make the corresponding assertion below fail.
_MARKER_LEVERS = "Workspace scoping is mandatory"
_MARKER_BLOB_DATA = ".data"
_MARKER_BLOB_URI = "ci-blob"
_MARKER_PAGINATION = "Pagination"
_MARKER_GOTCHA_TEMPORAL = "ZONED DATETIME"
_MARKER_NO_SERVER = "session-navigator"

_STUB_MARKER = "Server Unavailable"
_NO_SERVER_HEADING = "## When the graph server is not configured"
_REAL_BODY_HEADING = "# Context Intelligence Graph Query"


def _read_skill_body() -> str:
    assert _SKILL_PATH.is_file(), (
        f"served skill file missing on disk: {_SKILL_PATH} -- the vendored "
        "SKILL.md must ship as a static file now that skill_sync is deleted"
    )
    return _SKILL_PATH.read_text(encoding="utf-8")


def _parse_frontmatter(body: str) -> dict:
    """Split the leading `---`-delimited YAML frontmatter and parse it."""
    assert body.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    _, _, rest = body.partition("---\n")
    frontmatter_text, sep, _ = rest.partition("\n---")
    assert sep, "SKILL.md frontmatter is not terminated by a closing '---'"
    parsed = yaml.safe_load(frontmatter_text)
    assert isinstance(parsed, dict), "SKILL.md frontmatter must parse to a mapping"
    return parsed


def test_served_skill_has_valid_frontmatter_naming_the_skill() -> None:
    body = _read_skill_body()
    frontmatter = _parse_frontmatter(body)
    assert frontmatter.get("name") == "context-intelligence-graph-query"


def test_served_skill_stub_is_gone() -> None:
    """The pessimistic 'Server Unavailable' stub must not be the served body."""
    body = _read_skill_body()
    assert _STUB_MARKER not in body, (
        "served SKILL.md is still the offline stub -- skill_sync used to swap "
        "this out at runtime; now the file on disk IS what gets served (issue #283)"
    )


def test_served_skill_teaches_search_levers() -> None:
    """Category (i): the right levers/label filters for scoping a search."""
    body = _read_skill_body()
    assert _MARKER_LEVERS in body


def test_served_skill_teaches_blob_handling() -> None:
    """Category (ii): `.data` JSON string + `ci-blob://` references."""
    body = _read_skill_body()
    assert _MARKER_BLOB_DATA in body
    assert _MARKER_BLOB_URI in body


def test_served_skill_teaches_pagination() -> None:
    """Category (iii): pagination / progressive discovery for large results."""
    body = _read_skill_body()
    assert _MARKER_PAGINATION in body


def test_served_skill_teaches_zoned_datetime_gotcha() -> None:
    """Category (iv): silent-wrong-result gotchas, incl. the ZONED DATETIME trap."""
    body = _read_skill_body()
    assert _MARKER_GOTCHA_TEMPORAL in body


def test_served_skill_has_no_server_block() -> None:
    """Category (v): the no-server delegation block (guards issue #283)."""
    body = _read_skill_body()
    assert _NO_SERVER_HEADING in body
    assert _MARKER_NO_SERVER in body


def test_no_server_block_precedes_real_body() -> None:
    """The safe-default delegation guidance must not be buried after the body.

    A future edit that appends the no-server block at the end (instead of the
    top) would still pass a marker-only check but silently defeat the safe
    default: an agent reading top-to-bottom would hit the real body first and
    might start issuing Cypher before reaching the delegation instruction.
    """
    body = _read_skill_body()
    no_server_index = body.index(_NO_SERVER_HEADING)
    real_body_index = body.index(_REAL_BODY_HEADING)
    assert no_server_index < real_body_index
