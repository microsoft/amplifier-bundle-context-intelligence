"""Tests for legacy hooks-logging discovery targeting the legacy schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
    discover_legacy,
    legacy_discover,
)

from ._legacy_fixtures import (
    build_ci_session,
    build_legacy_session,
    make_legacy_record,
    prepend_line,
    write_legacy_events,
    write_raw_events,
)


def test_discovers_legacy_session(tmp_path: Path) -> None:
    build_legacy_session(tmp_path, session_id="s1")

    sessions = legacy_discover(tmp_path)

    session_ids = {meta["session_id"] for _, meta in sessions}
    assert "s1" in session_ids

    for session_dir, _ in sessions:
        assert (session_dir / "events.jsonl").exists()


def test_ci_tree_not_discovered_as_legacy(tmp_path: Path) -> None:
    build_ci_session(tmp_path, session_id="ci1")

    assert legacy_discover(tmp_path) == []


def test_mixed_tree_only_legacy_discovered(tmp_path: Path) -> None:
    build_legacy_session(tmp_path, session_id="legacy1")
    build_ci_session(tmp_path, session_id="ci1")

    sessions = legacy_discover(tmp_path)
    session_ids = [meta["session_id"] for _, meta in sessions]

    assert session_ids == ["legacy1"]


# ---------------------------------------------------------------------------
# O-4: live-skip and discovery robustness (tester-breaker TB-5/6/7/8, item G)
# ---------------------------------------------------------------------------


def test_live_session_skipped_and_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session with no terminal event is skipped and counted, not dropped silently."""
    build_legacy_session(tmp_path, session_id="live1", terminal=False)

    result = discover_legacy(tmp_path)

    assert result.sessions == []
    assert result.live_skipped == 1
    captured = capsys.readouterr()
    assert "live/in-progress" in captured.err


def test_non_utf8_events_does_not_crash_discovery(tmp_path: Path) -> None:
    """A sibling events.jsonl with non-UTF8 bytes must not abort discovery (TB-5)."""
    build_legacy_session(tmp_path, session_id="ok1")
    write_raw_events(
        tmp_path / "sessions" / "bad-bytes" / "events.jsonl",
        b"\xff\xfe not utf8 \x00\n",
    )

    sessions = legacy_discover(tmp_path)

    session_ids = {meta["session_id"] for _, meta in sessions}
    assert "ok1" in session_ids


def test_corrupt_first_line_still_discovers_legacy(tmp_path: Path) -> None:
    """A corrupt/non-JSON first line must not hide a legitimate legacy record (TB-6)."""
    session_dir = build_legacy_session(tmp_path, session_id="c1")
    prepend_line(session_dir / "events.jsonl", "NOT JSON GARBAGE")

    sessions = legacy_discover(tmp_path)

    session_ids = {meta["session_id"] for _, meta in sessions}
    assert "c1" in session_ids


def test_unresolvable_working_dir_is_session_level_signal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session whose working_dir cannot be resolved gets ONE session-level
    warning (not one-per-line), and is skipped + counted rather than raising (TB-7).
    """
    build_legacy_session(tmp_path, session_id="u1", working_dir=None, terminal=True)

    result = discover_legacy(tmp_path)

    assert result.sessions == []
    assert result.unresolved_workspace == 1
    captured = capsys.readouterr()
    assert "resolve workspace" in captured.err
    assert captured.err.count("WARNING") == 1


def test_legacy_file_with_more_than_sniff_bound_junk_leading_records_is_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file that IS legacy but whose first 6+ (more than _SNIFF_BOUND=5)
    leading records are corrupt/non-schema must not vanish from every
    counter -- it is skipped as ``unclassified`` with a stderr warning
    rather than silently dropped (regression for the uncounted-drop bug:
    it used to fail the bounded sniff and disappear from candidates_seen,
    live_skipped, AND unresolved_workspace alike)."""
    session_dir = tmp_path / "sessions" / "trunc1"
    session_dir.mkdir(parents=True)
    junk_lines = ["NOT JSON GARBAGE"] * 6
    legit_records = [
        make_legacy_record(session_id="trunc1"),
        make_legacy_record(event="session:end", session_id="trunc1"),
    ]
    lines = junk_lines + [json.dumps(r) for r in legit_records]
    (session_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8"
    )

    result = discover_legacy(tmp_path)

    assert result.sessions == []
    assert result.candidates_seen == 0
    assert result.live_skipped == 0
    assert result.unresolved_workspace == 0
    assert result.unclassified == 1
    assert str(session_dir) in result.unclassified_ids
    captured = capsys.readouterr()
    assert "unclassified" in captured.err
    assert "inconclusive" in captured.err


def test_read_working_dir_precedence_metadata_wins(tmp_path: Path) -> None:
    """metadata.json working_dir wins over a session:start event's working_dir
    (read_working_dir rule 1: metadata.json precedence, existing Phase 1 rule) (TB-8).
    """
    session_dir = tmp_path / "sessions" / "p1"
    session_dir.mkdir(parents=True)
    records = [
        make_legacy_record(
            event="session:start",
            session_id="p1",
            extra_data={"working_dir": "/from/session-start"},
        ),
        make_legacy_record(event="session:end", session_id="p1"),
    ]
    write_legacy_events(session_dir / "events.jsonl", records)
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/from/metadata"}), encoding="utf-8"
    )

    result = discover_legacy(tmp_path)

    workspaces = {meta["workspace"] for _, meta in result.sessions}
    assert workspaces == {"-from-metadata"}


# ---------------------------------------------------------------------------
# metadata.json `format` field as the AUTHORITATIVE legacy-vs-CI discriminator
# (replaces the `context-intelligence/` subfolder-name heuristic)
# ---------------------------------------------------------------------------


def _ci_native_metadata(session_id: str) -> dict[str, Any]:
    """A CI-native metadata.json body (the authoritative CI marker)."""
    return {
        "format": "context-intelligence",
        "version": "1.0.0",
        "session_id": session_id,
        "parent_id": "",
        "working_dir": "/Users/me/project",
    }


def test_ci_native_metadata_skips_session_even_outside_ci_named_folder(
    tmp_path: Path,
) -> None:
    """A session dir NOT named `context-intelligence` whose metadata.json
    declares format=context-intelligence must still be skipped -- the folder
    name is not the signal, the metadata is."""
    session_dir = tmp_path / "sessions" / "not-named-ci"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text(
        '{"event":"tool:pre","workspace":"-Users-me-project",'
        '"timestamp":"2026-01-01T00:00:00Z","data":{}}\n',
        encoding="utf-8",
    )
    (session_dir / "metadata.json").write_text(
        json.dumps(_ci_native_metadata("flat-ci-1")), encoding="utf-8"
    )

    assert legacy_discover(tmp_path) == []


def test_ci_metadata_format_wins_over_legacy_shaped_events(tmp_path: Path) -> None:
    """Even when events.jsonl carries the legacy schema, a sibling metadata.json
    declaring format=context-intelligence must still exclude the session --
    metadata format is authoritative over both the schema sniff and folder
    naming."""
    session_dir = tmp_path / "sessions" / "weird-folder-name"
    session_dir.mkdir(parents=True)
    records = [
        make_legacy_record(session_id="flat-ci-2"),
        make_legacy_record(event="session:end", session_id="flat-ci-2"),
    ]
    write_legacy_events(session_dir / "events.jsonl", records)
    (session_dir / "metadata.json").write_text(
        json.dumps(_ci_native_metadata("flat-ci-2")), encoding="utf-8"
    )

    assert legacy_discover(tmp_path) == []


def test_legacy_discovered_when_metadata_lacks_ci_format(tmp_path: Path) -> None:
    """A session whose metadata.json does NOT declare format=context-intelligence
    is discovered as legacy -- the absence of the CI marker (not the folder
    name) is what makes it eligible."""
    session_dir = tmp_path / "sessions" / "s2"
    session_dir.mkdir(parents=True)
    records = [
        make_legacy_record(session_id="s2"),
        make_legacy_record(event="session:end", session_id="s2"),
    ]
    write_legacy_events(session_dir / "events.jsonl", records)
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8"
    )

    sessions = legacy_discover(tmp_path)
    session_ids = {meta["session_id"] for _, meta in sessions}
    assert "s2" in session_ids


def test_real_mixed_session_top_level_legacy_plus_ci_subfolder(
    tmp_path: Path,
) -> None:
    """A session dir with BOTH a top-level legacy events.jsonl/metadata.json
    AND a nested context-intelligence/ subfolder: the top-level legacy one is
    discovered, the CI one is skipped -- now decided by metadata format, not
    folder name."""
    build_legacy_session(tmp_path, session_id="mixed1")
    build_ci_session(tmp_path, session_id="mixed1")

    sessions = legacy_discover(tmp_path)
    session_ids = [meta["session_id"] for _, meta in sessions]

    assert session_ids == ["mixed1"]
    ((session_dir, _),) = sessions
    assert session_dir.name == "mixed1"
    assert session_dir.parent.name == "sessions"


def test_legacy_discovered_under_oddly_named_folder(tmp_path: Path) -> None:
    """Folder name is irrelevant to discovery -- metadata + schema decide."""
    session_dir = tmp_path / "foo" / "bar"
    session_dir.mkdir(parents=True)
    records = [
        make_legacy_record(session_id="odd1"),
        make_legacy_record(event="session:end", session_id="odd1"),
    ]
    write_legacy_events(session_dir / "events.jsonl", records)
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8"
    )

    sessions = legacy_discover(tmp_path)
    session_ids = {meta["session_id"] for _, meta in sessions}
    assert "odd1" in session_ids


# ---------------------------------------------------------------------------
# Phase 2 / Q4: discovery surfaces the session's REAL working_dir in metadata
# ---------------------------------------------------------------------------


def test_discovery_metadata_carries_the_sessions_real_working_dir(tmp_path: Path) -> None:
    """Legacy discovery records the raw working_dir alongside the derived slug.

    The upload-time filter matches on the session's OWN recorded working
    directory (never on --path), so discovery must surface it. The slug is
    lossy; the raw path is not.
    """
    build_legacy_session(tmp_path, session_id="wd1", working_dir="/x/y")

    result = discover_legacy(tmp_path)

    ((_, meta),) = result.sessions
    assert meta["working_dir"] == "/x/y"
    # The existing keys are untouched.
    assert meta["workspace"] == "-x-y"
    assert meta["format"] == "logging-hook"
    assert meta["session_id"] == "wd1"


def test_unresolvable_session_is_still_skipped_not_surfaced_with_empty_working_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session whose working_dir cannot be resolved is skipped + counted as
    before -- it must NOT be surfaced with an empty/None working_dir now that
    the key exists. Only the resolvable session comes back.
    """
    build_legacy_session(tmp_path, session_id="ok1", working_dir="/x/y")
    build_legacy_session(tmp_path, session_id="bad1", working_dir=None)

    result = discover_legacy(tmp_path)

    assert [meta["session_id"] for _, meta in result.sessions] == ["ok1"]
    assert result.unresolved_workspace == 1
    assert all(meta["working_dir"] for _, meta in result.sessions)
    assert "resolve workspace" in capsys.readouterr().err
