"""Tests for the legacy/CI tree fixture builders in ``_legacy_fixtures.py``.

These builders are used by every discovery/parse test in Phase 3. This file
proves the builders themselves produce the exact tree shapes those later
tests depend on.
"""

from __future__ import annotations

import json
from pathlib import Path

from ._legacy_fixtures import (
    build_ci_session,
    build_legacy_session,
    prepend_line,
    write_raw_events,
)


def test_build_legacy_session_creates_events_and_metadata(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="s1")
    assert session_dir == tmp_path / "sessions" / "s1"
    assert (session_dir / "events.jsonl").exists()
    assert (session_dir / "metadata.json").exists()


def test_build_legacy_session_has_no_context_intelligence_subfolder(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="s1")
    assert not (session_dir / "context-intelligence").exists()


def test_build_legacy_session_terminal_true_appends_session_end(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="s1", terminal=True)
    lines = (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert events[-1] == "session:end"


def test_build_legacy_session_terminal_false_omits_session_end(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="s1", terminal=False)
    lines = (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert "session:end" not in events


def test_build_legacy_session_working_dir_written_to_metadata(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="s1", working_dir="/Users/me/project")
    meta = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["working_dir"] == "/Users/me/project"


def test_build_legacy_session_working_dir_none_omits_field(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="s1", working_dir=None)
    meta = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert "working_dir" not in meta


def test_build_legacy_session_write_metadata_false_omits_file(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="s1", write_metadata=False)
    assert not (session_dir / "metadata.json").exists()


def test_build_legacy_session_custom_records_used_verbatim(tmp_path: Path) -> None:
    from ._legacy_fixtures import make_legacy_record

    custom = [make_legacy_record(event="tool:pre", session_id="s1")]
    session_dir = build_legacy_session(tmp_path, session_id="s1", records=custom, terminal=False)
    lines = (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "tool:pre"


def test_write_raw_events_writes_bytes_verbatim(tmp_path: Path) -> None:
    target = tmp_path / "sessions" / "bad" / "events.jsonl"
    raw = b"\xff\xfe not utf8 \x00\n"
    write_raw_events(target, raw)
    assert target.read_bytes() == raw


def test_prepend_line_inserts_first_line(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, session_id="c1")
    events_path = session_dir / "events.jsonl"
    original = events_path.read_text(encoding="utf-8")
    prepend_line(events_path, "NOT JSON GARBAGE")
    updated = events_path.read_text(encoding="utf-8")
    lines = updated.splitlines()
    assert lines[0] == "NOT JSON GARBAGE"
    assert updated.endswith(original)


def test_build_ci_session_creates_context_intelligence_tree(tmp_path: Path) -> None:
    session_dir = build_ci_session(tmp_path, session_id="ci1")
    ci_dir = session_dir / "context-intelligence"
    assert (ci_dir / "events.jsonl").exists()
    assert (ci_dir / "metadata.json").exists()
    meta = json.loads((ci_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["format"] == "context-intelligence"


def test_build_ci_session_returns_session_dir_not_ci_subdir(tmp_path: Path) -> None:
    session_dir = build_ci_session(tmp_path, session_id="ci1")
    assert session_dir == tmp_path / "sessions" / "ci1"
