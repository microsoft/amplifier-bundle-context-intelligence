"""Tests for formats.py — the FORMATS dispatch table and default parse_fn."""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_tool_context_intelligence_upload.formats import (
    FORMATS,
    MalformedRecordError,
    ci_parse_line,
)


def test_formats_has_context_intelligence_default():
    assert "context-intelligence" in FORMATS
    discover_fn, parse_fn = FORMATS["context-intelligence"]
    assert callable(discover_fn)
    assert callable(parse_fn)


def test_ci_parse_line_returns_triple():
    session_dir = Path("/tmp/some/session/context-intelligence")
    metadata: dict = {"workspace": "meta-ws"}
    raw_line = '{"event": "tool:pre", "workspace": "ws", "data": {"n": 1}}'

    result = ci_parse_line(raw_line, session_dir, metadata)

    assert result == ("tool:pre", "ws", {"n": 1})


def test_ci_parse_line_blank_returns_none():
    session_dir = Path("/tmp/some/session/context-intelligence")
    metadata: dict = {}

    assert ci_parse_line("", session_dir, metadata) is None
    assert ci_parse_line("   \n", session_dir, metadata) is None


def test_ci_parse_line_workspace_fallback_to_metadata():
    session_dir = Path("/tmp/some/session/context-intelligence")
    metadata: dict = {"workspace": "meta-ws"}
    raw_line = '{"event": "tool:pre", "data": {"n": 1}}'

    result = ci_parse_line(raw_line, session_dir, metadata)

    assert result is not None
    event, workspace, data = result
    assert event == "tool:pre"
    assert workspace == "meta-ws"
    assert data == {"n": 1}


@pytest.mark.parametrize("raw_line", ["null", "42", "[1, 2, 3]", '"a string"'])
def test_ci_parse_line_non_dict_record_raises_malformed(raw_line: str):
    """TB-1: a valid-JSON but non-dict record must raise MalformedRecordError,
    not AttributeError from record.get(...) on a non-dict value."""
    session_dir = Path("/tmp/some/session/context-intelligence")
    metadata: dict = {}

    with pytest.raises(MalformedRecordError):
        ci_parse_line(raw_line, session_dir, metadata)


def test_ci_parse_line_non_dict_data_raises_malformed():
    """TB-15: a non-dict 'data' value must raise MalformedRecordError."""
    session_dir = Path("/tmp/some/session/context-intelligence")
    metadata: dict = {}
    raw_line = '{"event": "e", "data": 42}'

    with pytest.raises(MalformedRecordError):
        ci_parse_line(raw_line, session_dir, metadata)


def test_formats_has_logging_hook_pair():
    assert "logging-hook" in FORMATS
    discover_fn, parse_fn = FORMATS["logging-hook"]
    assert callable(discover_fn)
    assert callable(parse_fn)
