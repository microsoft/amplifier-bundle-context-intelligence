"""Tests T39–T44: ledger.py — read/write, query helpers, idempotency guard."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


from amplifier_module_tool_context_intelligence_migrate.ledger import (
    already_complete,
    append_entry,
    last_phase,
    read_ledger,
)


def _entry(
    session_id: str,
    phase: str,
    bucket: str = "pre_ci",
    project_slug: str = "proj",
) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "project_slug": project_slug,
        "bucket": bucket,
        "phase": phase,
        "workspace": "",
        "jsonl_lines": None,
        "graph_count": None,
        "archive_path": None,
        "error": None,
    }


# ---------------------------------------------------------------------------
# T39: append_and_read_roundtrip
# ---------------------------------------------------------------------------


def test_append_and_read_roundtrip(tmp_path: Path) -> None:
    """T39: append_entry + read_ledger returns the written entries."""
    ledger = tmp_path / "ledger.jsonl"
    e1 = _entry("sess-1", "classified")
    e2 = _entry("sess-1", "transformed")
    append_entry(ledger, e1)
    append_entry(ledger, e2)

    entries = read_ledger(ledger)
    assert len(entries) == 2
    assert entries[0]["session_id"] == "sess-1"
    assert entries[0]["phase"] == "classified"
    assert entries[1]["phase"] == "transformed"


# ---------------------------------------------------------------------------
# T40: read_missing_file_returns_empty
# ---------------------------------------------------------------------------


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    """T40: read_ledger on a non-existent path returns []."""
    result = read_ledger(tmp_path / "does_not_exist.jsonl")
    assert result == []


# ---------------------------------------------------------------------------
# T41: last_phase_returns_most_recent
# ---------------------------------------------------------------------------


def test_last_phase_returns_most_recent(tmp_path: Path) -> None:
    """T41: last_phase returns the most-recent phase for a session."""
    entries = [
        _entry("s1", "classified"),
        _entry("s1", "transformed"),
        _entry("s1", "archived"),
        _entry("s2", "classified"),
    ]
    assert last_phase(entries, "s1") == "archived"
    assert last_phase(entries, "s2") == "classified"


# ---------------------------------------------------------------------------
# T42: last_phase_missing_returns_none
# ---------------------------------------------------------------------------


def test_last_phase_missing_returns_none() -> None:
    """T42: last_phase returns None when session_id not found."""
    assert last_phase([], "missing-sess") is None


# ---------------------------------------------------------------------------
# T43: already_complete_deleted
# ---------------------------------------------------------------------------


def test_already_complete_deleted() -> None:
    """T43: already_complete is True when last phase is 'deleted'."""
    entries = [
        _entry("sess-x", "classified"),
        _entry("sess-x", "deleted"),
    ]
    assert already_complete(entries, "sess-x") is True


# ---------------------------------------------------------------------------
# T44: already_complete_ci_only_verified
# ---------------------------------------------------------------------------


def test_already_complete_ci_only_verified() -> None:
    """T44: already_complete is True for ci_only when last phase is 'verified'."""
    entries = [
        _entry("sess-y", "uploaded", bucket="ci_only"),
        _entry("sess-y", "verified", bucket="ci_only"),
    ]
    assert already_complete(entries, "sess-y") is True


# Bonus: not complete if last phase is "verified" for non-ci_only
def test_already_complete_verified_pre_ci_is_false() -> None:
    """Extra: verified is NOT terminal for pre_ci (delete must still happen)."""
    entries = [
        _entry("sess-z", "verified", bucket="pre_ci"),
    ]
    assert already_complete(entries, "sess-z") is False
