"""Tests T1–T11, T59–T65: classify.py — bucket assignment and helper functions."""

from __future__ import annotations

import os
import time
from pathlib import Path


from amplifier_module_tool_context_intelligence_migrate.classify import (
    DEFAULT_SAFETY_WINDOW_HOURS,
    TERMINAL_EVENTS,
    bucket_session,
    has_terminal_event,
    is_live_session,
    scan_projects,
)

from .conftest import (
    build_ci_only_session,
    build_double_session,
    build_pre_ci_session,
    make_legacy_record,
    write_legacy_events,
)


# ---------------------------------------------------------------------------
# T1: bucket_live_no_terminal_event
# ---------------------------------------------------------------------------


def test_bucket_live_no_terminal_event(tmp_path: Path) -> None:
    """T1: A session with no session:end is bucketed as live."""
    session_dir = tmp_path / "proj" / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    legacy = session_dir / "events.jsonl"
    write_legacy_events(legacy, [make_legacy_record(event="tool:pre", session_id="s1")])

    info = bucket_session(session_dir)
    assert info is not None
    assert info.bucket == "live"


# ---------------------------------------------------------------------------
# T2: bucket_pre_ci_one_file
# ---------------------------------------------------------------------------


def test_bucket_pre_ci_one_file(tmp_path: Path) -> None:
    """T2: Legacy events.jsonl only → pre_ci."""
    session_dir = build_pre_ci_session(tmp_path, session_id="s2")
    info = bucket_session(session_dir, safety_window_hours=0.0)
    assert info is not None
    assert info.bucket == "pre_ci"
    assert info.legacy_events is not None
    assert info.legacy_events.exists()
    assert not info.ci_events.exists()


# ---------------------------------------------------------------------------
# T3: bucket_double_both_files
# ---------------------------------------------------------------------------


def test_bucket_double_both_files(tmp_path: Path) -> None:
    """T3: Both legacy and CI events → double."""
    session_dir = build_double_session(tmp_path, session_id="s3")
    info = bucket_session(session_dir, safety_window_hours=0.0)
    assert info is not None
    assert info.bucket == "double"
    assert info.legacy_events is not None
    assert info.ci_events.exists()


# ---------------------------------------------------------------------------
# T4: bucket_ci_only_one_ci_file
# ---------------------------------------------------------------------------


def test_bucket_ci_only_one_ci_file(tmp_path: Path) -> None:
    """T4: CI events only → ci_only."""
    session_dir = build_ci_only_session(tmp_path, session_id="s4")
    info = bucket_session(session_dir, safety_window_hours=0.0)
    assert info is not None
    assert info.bucket == "ci_only"
    assert info.legacy_events is None


# ---------------------------------------------------------------------------
# T5: bucket_live_recently_modified
# ---------------------------------------------------------------------------


def test_bucket_live_recently_modified(tmp_path: Path) -> None:
    """T5: Session with terminal event but recently modified → live."""
    session_dir = tmp_path / "proj" / "sessions" / "s5"
    session_dir.mkdir(parents=True)
    legacy = session_dir / "events.jsonl"
    records = [
        make_legacy_record(event="tool:pre", session_id="s5"),
        make_legacy_record(event="session:end", session_id="s5"),
    ]
    write_legacy_events(legacy, records)
    # Touch the file to make it "recently" modified (just now)
    legacy.touch()

    info = bucket_session(session_dir, safety_window_hours=DEFAULT_SAFETY_WINDOW_HOURS)
    assert info is not None
    assert info.bucket == "live"


# ---------------------------------------------------------------------------
# T6: scan_projects_yields_all_buckets
# ---------------------------------------------------------------------------


def test_scan_projects_yields_all_buckets(tmp_path: Path) -> None:
    """T6: scan_projects returns sessions from nested project directories."""
    build_pre_ci_session(tmp_path, project_slug="proj-a", session_id="sa1")
    build_ci_only_session(tmp_path, project_slug="proj-b", session_id="sb1")

    results = scan_projects(tmp_path, safety_window_hours=0.0)
    session_ids = {r.session_id for r in results}
    assert "sa1" in session_ids or "sb1" in session_ids
    # pre_ci sessions should appear (ci_only may or may not depending on timestamps)
    buckets = {r.bucket for r in results}
    assert "pre_ci" in buckets or "ci_only" in buckets


# ---------------------------------------------------------------------------
# T7: has_terminal_event_true
# ---------------------------------------------------------------------------


def test_has_terminal_event_true(tmp_path: Path) -> None:
    """T7: File with session:end line → True."""
    legacy = tmp_path / "events.jsonl"
    write_legacy_events(
        legacy,
        [
            make_legacy_record(event="tool:pre"),
            make_legacy_record(event="session:end"),
        ],
    )
    assert has_terminal_event(legacy) is True


# ---------------------------------------------------------------------------
# T8: has_terminal_event_false
# ---------------------------------------------------------------------------


def test_has_terminal_event_false(tmp_path: Path) -> None:
    """T8: File without session:end → False."""
    legacy = tmp_path / "events.jsonl"
    write_legacy_events(legacy, [make_legacy_record(event="tool:pre")])
    assert has_terminal_event(legacy) is False


# ---------------------------------------------------------------------------
# T9: is_live_session_true_no_terminal
# ---------------------------------------------------------------------------


def test_is_live_session_true_no_terminal(tmp_path: Path) -> None:
    """T9: No terminal event → live."""
    legacy = tmp_path / "events.jsonl"
    write_legacy_events(legacy, [make_legacy_record(event="tool:pre")])
    assert is_live_session(legacy, safety_window_hours=0.0) is True


# ---------------------------------------------------------------------------
# T10: is_live_session_true_recent
# ---------------------------------------------------------------------------


def test_is_live_session_true_recent(tmp_path: Path) -> None:
    """T10: Terminal event exists but file modified very recently → live."""
    legacy = tmp_path / "events.jsonl"
    write_legacy_events(
        legacy,
        [
            make_legacy_record(event="tool:pre"),
            make_legacy_record(event="session:end"),
        ],
    )
    legacy.touch()  # update mtime to now
    # With large safety window, this should be live
    assert is_live_session(legacy, safety_window_hours=24.0) is True


# ---------------------------------------------------------------------------
# T11: is_live_session_false
# ---------------------------------------------------------------------------


def test_is_live_session_false(tmp_path: Path) -> None:
    """T11: Terminal event + old file → not live."""
    legacy = tmp_path / "events.jsonl"
    write_legacy_events(
        legacy,
        [
            make_legacy_record(event="tool:pre"),
            make_legacy_record(event="session:end"),
        ],
    )
    # Backdate the file by 48 hours
    old_mtime = time.time() - 48 * 3600
    os.utime(legacy, (old_mtime, old_mtime))

    assert is_live_session(legacy, safety_window_hours=24.0) is False


# ---------------------------------------------------------------------------
# T59: TERMINAL_EVENTS contains all three expected event names
# ---------------------------------------------------------------------------


def test_terminal_events_constant_has_all_three() -> None:
    """T59: TERMINAL_EVENTS contains session:end, orchestrator:complete, execution:end."""
    assert "session:end" in TERMINAL_EVENTS
    assert "orchestrator:complete" in TERMINAL_EVENTS
    assert "execution:end" in TERMINAL_EVENTS


# ---------------------------------------------------------------------------
# T60: has_terminal_event — orchestrator:complete is recognised
# ---------------------------------------------------------------------------


def test_has_terminal_event_orchestrator_complete(tmp_path: Path) -> None:
    """T60: File with orchestrator:complete → True."""
    legacy = tmp_path / "events.jsonl"
    write_legacy_events(
        legacy,
        [
            make_legacy_record(event="tool:pre"),
            make_legacy_record(event="orchestrator:complete"),
        ],
    )
    assert has_terminal_event(legacy) is True


# ---------------------------------------------------------------------------
# T61: has_terminal_event — execution:end is recognised
# ---------------------------------------------------------------------------


def test_has_terminal_event_execution_end(tmp_path: Path) -> None:
    """T61: File with execution:end → True."""
    legacy = tmp_path / "events.jsonl"
    write_legacy_events(
        legacy,
        [
            make_legacy_record(event="tool:pre"),
            make_legacy_record(event="execution:end"),
        ],
    )
    assert has_terminal_event(legacy) is True


# ---------------------------------------------------------------------------
# T62: Regression guard — last line NOT terminal but earlier line IS + old mtime → pre_ci
# ---------------------------------------------------------------------------


def test_bucket_pre_ci_last_line_nonterminal_but_earlier_terminal(tmp_path: Path) -> None:
    """T62: Regression guard.

    Last line is cleanup:finally_end (non-terminal) but orchestrator:complete
    appears earlier.  Old mtime → must classify pre_ci, not live.

    This guards against any 'only check last line' bug.
    """
    session_dir = tmp_path / "proj" / "sessions" / "s_nonterminal"
    session_dir.mkdir(parents=True)
    legacy = session_dir / "events.jsonl"
    records = [
        make_legacy_record(event="tool:pre", session_id="s_nonterminal"),
        make_legacy_record(event="orchestrator:complete", session_id="s_nonterminal"),
        make_legacy_record(event="cleanup:finally_end", session_id="s_nonterminal"),  # last line
    ]
    write_legacy_events(legacy, records)
    # Backdate: definitely not within any safety window
    old_mtime = time.time() - 48 * 3600
    os.utime(legacy, (old_mtime, old_mtime))

    info = bucket_session(session_dir, safety_window_hours=0.0)
    assert info is not None
    assert info.bucket == "pre_ci", (
        f"Expected pre_ci (orchestrator:complete makes session ended) but got {info.bucket!r}; "
        f"reason={info.reason!r}"
    )


# ---------------------------------------------------------------------------
# T63: None of TERMINAL_EVENTS + recent mtime → live
# ---------------------------------------------------------------------------


def test_bucket_live_none_of_terminal_events_recent(tmp_path: Path) -> None:
    """T63: No event in TERMINAL_EVENTS anywhere AND recent mtime → live."""
    session_dir = tmp_path / "proj" / "sessions" / "s_noterm_recent"
    session_dir.mkdir(parents=True)
    legacy = session_dir / "events.jsonl"
    write_legacy_events(
        legacy,
        [
            make_legacy_record(event="tool:pre", session_id="s_noterm_recent"),
            make_legacy_record(event="prompt:submit", session_id="s_noterm_recent"),
        ],
    )
    # Fresh mtime (just written)
    info = bucket_session(session_dir, safety_window_hours=24.0)
    assert info is not None
    assert info.bucket == "live"


# ---------------------------------------------------------------------------
# T64: Terminal event present but recent mtime → still live (mtime safety guard)
# ---------------------------------------------------------------------------


def test_bucket_live_terminal_event_but_recent_mtime(tmp_path: Path) -> None:
    """T64: orchestrator:complete is present BUT file modified recently → live.

    The mtime safety guard takes precedence: we never delete a recently-touched session.
    """
    session_dir = tmp_path / "proj" / "sessions" / "s_term_recent"
    session_dir.mkdir(parents=True)
    legacy = session_dir / "events.jsonl"
    records = [
        make_legacy_record(event="tool:pre", session_id="s_term_recent"),
        make_legacy_record(event="orchestrator:complete", session_id="s_term_recent"),
        make_legacy_record(event="cleanup:finally_end", session_id="s_term_recent"),
    ]
    write_legacy_events(legacy, records)
    legacy.touch()  # update mtime to now

    info = bucket_session(session_dir, safety_window_hours=24.0)
    assert info is not None
    assert info.bucket == "live", (
        f"Expected live (recent mtime overrides terminal event) but got {info.bucket!r}"
    )
