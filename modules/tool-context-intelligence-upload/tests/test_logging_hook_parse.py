"""Tests for legacy_parse_line (GATE 1b): produces the (event, workspace, data) triple."""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_module_hook_context_intelligence.upload import build_payload
from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
    legacy_parse_line,
)

from ._legacy_fixtures import build_legacy_session, make_legacy_record


def test_parse_payload_matches_shared_builder(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, working_dir="/Users/me/project")
    record = make_legacy_record(
        event="tool:post",
        session_id="s1",
        ts="2026-03-18T00:00:01.000+00:00",
        extra_data={"tool_name": "bash"},
    )
    raw_line = json.dumps(record)
    metadata = {"workspace": "-Users-me-project"}

    result = legacy_parse_line(raw_line, session_dir, metadata)

    assert result is not None
    event, workspace, data = result
    payload = build_payload(event, workspace, data)

    assert payload["event"] == "tool:post"
    assert payload["workspace"] == "-Users-me-project"
    assert payload["data"]["timestamp"] == "2026-03-18T00:00:01.000+00:00"
    assert payload["data"]["tool_name"] == "bash"
    assert payload["idempotency_key"].startswith("aci-event-v1:")


def test_parse_returns_triple_with_timestamp(tmp_path: Path) -> None:
    session_dir = build_legacy_session(tmp_path, working_dir="/Users/me/project")
    record = make_legacy_record(
        event="tool:pre", session_id="s1", ts="2026-03-18T00:00:00.123+00:00"
    )
    raw_line = json.dumps(record)
    metadata = {"workspace": "-Users-me-project"}

    result = legacy_parse_line(raw_line, session_dir, metadata)

    assert result is not None
    event, workspace, data = result
    assert event == "tool:pre"
    assert workspace == "-Users-me-project"
    assert data["timestamp"] == "2026-03-18T00:00:00.123+00:00"
    assert data["session_id"] == "s1"
    assert "schema" not in data
    assert "lvl" not in data
