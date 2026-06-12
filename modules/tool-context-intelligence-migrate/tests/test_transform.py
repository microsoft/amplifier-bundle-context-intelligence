"""Tests T12–T29, T65: transform.py — reassemble, per-line transform, session transform."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_module_tool_context_intelligence_migrate.transform import (
    SchemaVersionError,
    _reassemble_data,
    _slugify_path,
    _transform_line,
    is_content_superset,
    transform_session,
)

from .conftest import make_legacy_record, write_legacy_events


# ---------------------------------------------------------------------------
# T12: slugify_path_basic
# ---------------------------------------------------------------------------


def test_slugify_path_basic() -> None:
    """T12: Absolute path with slashes becomes dash-separated string."""
    result = _slugify_path("/Users/me/project")
    assert "/" not in result
    assert result == "-Users-me-project"


# ---------------------------------------------------------------------------
# T13: slugify_path_trailing_slash
# ---------------------------------------------------------------------------


def test_slugify_path_trailing_slash() -> None:
    """T13: Trailing slash is stripped before slugifying."""
    result = _slugify_path("/Users/me/project/")
    assert result == "-Users-me-project"


# ---------------------------------------------------------------------------
# T14: reassemble_restores_promoted_keys
# ---------------------------------------------------------------------------


def test_reassemble_restores_promoted_keys() -> None:
    """T14: session_id and status land back inside data."""
    rec = make_legacy_record(session_id="abc", status="ok")
    rec["data"] = {"tool_name": "bash"}  # stripped in legacy record
    data = _reassemble_data(rec)
    assert data["session_id"] == "abc"
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# T15: reassemble_moves_ts_to_timestamp
# ---------------------------------------------------------------------------


def test_reassemble_moves_ts_to_timestamp() -> None:
    """T15: output data["timestamp"] == legacy["ts"]."""
    rec = make_legacy_record(ts="2026-03-18T00:00:00.123+00:00")
    data = _reassemble_data(rec)
    assert data["timestamp"] == "2026-03-18T00:00:00.123+00:00"


# ---------------------------------------------------------------------------
# T16: reassemble_drops_logging_artifacts
# ---------------------------------------------------------------------------


def test_reassemble_drops_logging_artifacts() -> None:
    """T16: lvl, schema, event NOT present in data output."""
    rec = make_legacy_record(event="tool:pre")
    data = _reassemble_data(rec)
    assert "lvl" not in data
    assert "schema" not in data
    assert "event" not in data


# ---------------------------------------------------------------------------
# T17: reassemble_error_key
# ---------------------------------------------------------------------------


def test_reassemble_error_key() -> None:
    """T17: error key is promoted back if present in legacy record."""
    rec = make_legacy_record()
    rec["error"] = "some error"
    data = _reassemble_data(rec)
    assert data["error"] == "some error"


# ---------------------------------------------------------------------------
# T18: transform_line_event_field
# ---------------------------------------------------------------------------


def test_transform_line_event_field() -> None:
    """T18: Transformed line has 'event' key matching the original."""
    rec = make_legacy_record(event="tool:post", session_id="s1")
    line = json.dumps(rec)
    output = _transform_line(line, workspace="-Users-me-project")
    parsed = json.loads(output)
    assert parsed["event"] == "tool:post"


# ---------------------------------------------------------------------------
# T19: transform_line_sorted_keys
# ---------------------------------------------------------------------------


def test_transform_line_sorted_keys() -> None:
    """T19: Output JSON has sorted keys at the top level."""
    rec = make_legacy_record(session_id="s1")
    line = json.dumps(rec)
    output = _transform_line(line, workspace="ws")
    parsed = json.loads(output)
    keys = list(parsed.keys())
    assert keys == sorted(keys), f"Keys not sorted: {keys}"


# ---------------------------------------------------------------------------
# T20: byte_identity_single_event
# ---------------------------------------------------------------------------


def test_byte_identity_single_event(tmp_path: Path) -> None:
    """T20: Single synthetic event round-trips through CI hook and transform; bytes equal."""
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
        LoggingHandler,
    )

    session_id = "byte-id-single"
    workspace = "-Users-me-project"

    # Original event data (what kernel would have emitted)
    original_data = {
        "session_id": session_id,
        "status": "ok",
        "timestamp": "2026-03-18T00:00:00.000+00:00",
        "tool_name": "bash",
        "tool_input": {},
    }

    # --- File A: written by the real CI LoggingHandler._append_event ---
    ci_dir_a = tmp_path / "ci_a"
    ci_dir_a.mkdir()
    LoggingHandler._append_event(ci_dir_a, "tool:pre", original_data, workspace)
    file_a = (ci_dir_a / "events.jsonl").read_bytes()

    # --- Legacy record (what hooks-logging would have written) ---
    legacy_data_copy = dict(original_data)
    legacy_data_copy.pop("session_id", None)
    legacy_data_copy.pop("status", None)
    # Note: timestamp stays in data; hooks-logging ALSO records it as ts
    legacy_rec = {
        "ts": original_data["timestamp"],
        "lvl": "INFO",
        "schema": {"name": "amplifier.log", "ver": "1.0.0"},
        "event": "tool:pre",
        "session_id": original_data["session_id"],
        "status": original_data["status"],
        "data": legacy_data_copy,
    }
    legacy_path = tmp_path / "legacy" / "events.jsonl"
    write_legacy_events(legacy_path, [legacy_rec])

    # Write metadata.json so transform_session can derive workspace
    metadata_path = tmp_path / "legacy" / "metadata.json"
    metadata_path.write_text(json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8")

    # --- File B: written by transform_session ---
    ci_dir_b = tmp_path / "ci_b"
    ci_dir_b.mkdir()
    transform_session(legacy_path, ci_dir_b, session_dir=tmp_path / "legacy")
    file_b = (ci_dir_b / "events.jsonl").read_bytes()

    assert file_a == file_b, f"Byte mismatch!\nA: {file_a!r}\nB: {file_b!r}\n"


# ---------------------------------------------------------------------------
# T21: byte_identity_ten_events
# ---------------------------------------------------------------------------


def test_byte_identity_ten_events(tmp_path: Path) -> None:
    """T21: Ten synthetic events; bytes equal between CI hook and transform."""
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
        LoggingHandler,
    )

    session_id = "byte-id-ten"
    workspace = "-Users-me-project"

    events = [("tool:pre", {"tool_name": f"tool_{i}", "tool_input": {"n": i}}) for i in range(10)]

    ci_dir_a = tmp_path / "ci_a"
    ci_dir_a.mkdir()
    legacy_records = []

    for ev_name, ev_extra in events:
        original_data = {
            "session_id": session_id,
            "status": "ok",
            "timestamp": f"2026-03-18T00:00:0{len(legacy_records):01d}.000+00:00",
            **ev_extra,
        }
        LoggingHandler._append_event(ci_dir_a, ev_name, original_data, workspace)

        legacy_data_copy = dict(original_data)
        legacy_data_copy.pop("session_id", None)
        legacy_data_copy.pop("status", None)
        legacy_rec = {
            "ts": original_data["timestamp"],
            "lvl": "INFO",
            "schema": {"name": "amplifier.log", "ver": "1.0.0"},
            "event": ev_name,
            "session_id": original_data["session_id"],
            "status": original_data["status"],
            "data": legacy_data_copy,
        }
        legacy_records.append(legacy_rec)

    file_a = (ci_dir_a / "events.jsonl").read_bytes()

    legacy_path = tmp_path / "legacy" / "events.jsonl"
    write_legacy_events(legacy_path, legacy_records)
    (tmp_path / "legacy" / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8"
    )

    ci_dir_b = tmp_path / "ci_b"
    ci_dir_b.mkdir()
    transform_session(legacy_path, ci_dir_b, session_dir=tmp_path / "legacy")
    file_b = (ci_dir_b / "events.jsonl").read_bytes()

    assert file_a == file_b, (
        f"Byte mismatch at line level!\n"
        f"lines A: {file_a.decode().splitlines()}\n"
        f"lines B: {file_b.decode().splitlines()}\n"
    )


# ---------------------------------------------------------------------------
# T22: transform_session_creates_metadata
# ---------------------------------------------------------------------------


def test_transform_session_creates_metadata(tmp_path: Path) -> None:
    """T22: transform_session writes context-intelligence/metadata.json."""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    legacy = session_dir / "events.jsonl"
    write_legacy_events(
        legacy,
        [make_legacy_record(event="session:end", session_id="s1")],
    )
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/proj"}), encoding="utf-8"
    )

    ci_dir = session_dir / "context-intelligence"
    transform_session(legacy, ci_dir, session_dir=session_dir)

    meta_path = ci_dir / "metadata.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta.get("format") == "context-intelligence"


# ---------------------------------------------------------------------------
# T23: transform_session_line_count
# ---------------------------------------------------------------------------


def test_transform_session_line_count(tmp_path: Path) -> None:
    """T23: Output JSONL has same number of non-empty lines as input."""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    legacy = session_dir / "events.jsonl"
    records = [make_legacy_record(session_id="s1") for _ in range(5)]
    write_legacy_events(legacy, records)
    # Provide working_dir directly so workspace derivation can proceed
    (session_dir / "metadata.json").write_text(
        '{"working_dir": "/Users/me/project"}', encoding="utf-8"
    )

    ci_dir = session_dir / "context-intelligence"
    ci_events, _ = transform_session(legacy, ci_dir, session_dir=session_dir)

    out_lines = [ln for ln in ci_events.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(out_lines) == 5


# ---------------------------------------------------------------------------
# T24: transform_line_workspace_from_session_dir
# ---------------------------------------------------------------------------


def test_transform_line_workspace_from_session_dir(tmp_path: Path) -> None:
    """T24: workspace in output equals slugified working_dir from metadata.json."""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    legacy = session_dir / "events.jsonl"
    write_legacy_events(legacy, [make_legacy_record(session_id="s1")])
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/proj"}), encoding="utf-8"
    )

    ci_dir = session_dir / "context-intelligence"
    ci_events, _ = transform_session(legacy, ci_dir, session_dir=session_dir)

    lines = [ln for ln in ci_events.read_text(encoding="utf-8").splitlines() if ln.strip()]
    parsed = json.loads(lines[0])
    expected_ws = _slugify_path("/Users/me/proj")
    assert parsed["workspace"] == expected_ws


# ---------------------------------------------------------------------------
# T25: schema_version_guard_raises
# ---------------------------------------------------------------------------


def test_schema_version_guard_raises(tmp_path: Path) -> None:
    """T25: A line with schema.ver != 1.0.0 raises SchemaVersionError."""
    bad_line = json.dumps(
        {
            "ts": "2026-01-01T00:00:00Z",
            "lvl": "INFO",
            "schema": {"name": "amplifier.log", "ver": "9.9.9"},
            "event": "tool:pre",
            "session_id": "s",
            "status": "ok",
            "data": {},
        }
    )
    with pytest.raises(SchemaVersionError):
        _transform_line(bad_line, workspace="ws")


# ---------------------------------------------------------------------------
# T26: schema_version_guard_passes
# ---------------------------------------------------------------------------


def test_schema_version_guard_passes(tmp_path: Path) -> None:
    """T26: Line with schema.ver == 1.0.0 passes without error."""
    rec = make_legacy_record()
    line = json.dumps(rec)
    output = _transform_line(line, workspace="ws")
    assert output  # non-empty


# ---------------------------------------------------------------------------
# T27: is_content_superset_true
# ---------------------------------------------------------------------------


def test_is_content_superset_true(tmp_path: Path) -> None:
    """T27: CI events file is a superset of legacy events (same events_ids/timestamps)."""
    legacy = tmp_path / "legacy.jsonl"
    ci = tmp_path / "ci.jsonl"

    # Write legacy records
    records = [
        make_legacy_record(ts=f"2026-01-{i:02d}T00:00:00Z", session_id="s") for i in range(1, 4)
    ]
    write_legacy_events(legacy, records)

    # Transform them to build CI file
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_bytes(legacy.read_bytes())
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8"
    )
    ci_dir = session_dir / "context-intelligence"
    ci_events_path, _ = transform_session(
        session_dir / "events.jsonl", ci_dir, session_dir=session_dir
    )
    # Copy CI events to our ci path
    ci.write_bytes(ci_events_path.read_bytes())

    assert is_content_superset(legacy, ci) is True


# ---------------------------------------------------------------------------
# T28: is_content_superset_false_missing_event
# ---------------------------------------------------------------------------


def test_is_content_superset_false_missing_event(tmp_path: Path) -> None:
    """T28: CI file missing an event from legacy → not superset."""
    legacy = tmp_path / "legacy.jsonl"
    ci = tmp_path / "ci.jsonl"

    records = [
        make_legacy_record(ts=f"2026-01-{i:02d}T00:00:00Z", session_id="s") for i in range(1, 4)
    ]
    write_legacy_events(legacy, records)

    # CI has only 2 of the 3 events — deliberately fewer
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_bytes(legacy.read_bytes())
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8"
    )
    ci_dir = session_dir / "context-intelligence"
    ci_events_path, _ = transform_session(
        session_dir / "events.jsonl", ci_dir, session_dir=session_dir
    )

    # Drop last line from CI file to simulate missing event
    ci_lines = ci_events_path.read_text(encoding="utf-8").splitlines()
    ci.write_text("\n".join(ci_lines[:-1]) + "\n", encoding="utf-8")

    assert is_content_superset(legacy, ci) is False


# ---------------------------------------------------------------------------
# T29: transform_session_idempotent
# ---------------------------------------------------------------------------


def test_transform_session_idempotent(tmp_path: Path) -> None:
    """T29: Running transform_session twice produces identical output."""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    legacy = session_dir / "events.jsonl"
    write_legacy_events(legacy, [make_legacy_record(session_id="s1")])
    (session_dir / "metadata.json").write_text(
        json.dumps({"working_dir": "/Users/me/project"}), encoding="utf-8"
    )

    ci_dir = session_dir / "context-intelligence"
    transform_session(legacy, ci_dir, session_dir=session_dir)
    out1 = (ci_dir / "events.jsonl").read_bytes()

    transform_session(legacy, ci_dir, session_dir=session_dir)
    out2 = (ci_dir / "events.jsonl").read_bytes()

    assert out1 == out2


# ---------------------------------------------------------------------------
# T65: is_content_superset raises SchemaVersionError on bad-schema legacy line
# ---------------------------------------------------------------------------


def test_is_content_superset_bad_schema_raises(tmp_path: Path) -> None:
    """T65: is_content_superset raises SchemaVersionError on a bad-schema legacy line.

    The schema guard must fire on the superset path just as it does on the main
    transform path — an unknown schema version must fail loud, not be silently skipped.
    """
    # Build a minimal valid CI file (superset check reads this first)
    ci_dir = tmp_path / "ci"
    ci_dir.mkdir()
    ci_events = ci_dir / "events.jsonl"
    # Write one valid CI record (json produced by transform_session would look like this)
    ci_record = json.dumps(
        {
            "event": "tool:pre",
            "workspace": "-Users-me-project",
            "timestamp": "2026-01-01T00:00:00Z",
            "data": {"session_id": "s", "timestamp": "2026-01-01T00:00:00Z"},
        }
    )
    ci_events.write_text(ci_record + "\n", encoding="utf-8")

    # Build a legacy file with an unsupported schema version
    legacy = tmp_path / "bad_legacy.jsonl"
    bad_record = make_legacy_record(session_id="s")
    bad_record["schema"] = {"name": "amplifier.log", "ver": "9.9.9"}  # bad version
    legacy.write_text(json.dumps(bad_record) + "\n", encoding="utf-8")

    with pytest.raises(SchemaVersionError):
        is_content_superset(legacy, ci_events)
