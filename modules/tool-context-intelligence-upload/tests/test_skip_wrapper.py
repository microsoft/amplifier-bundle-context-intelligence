"""Tests for the skip wrapper (Council v2 decision #3 -- ONE error contract).

Named-error legacy-parse failures degrade to a counted skip-with-warning
instead of crashing the upload; a genuine bug (unexpected error type) is
NOT swallowed and still crashes loud.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
    make_skip_wrapped_parse,
)
from amplifier_module_tool_context_intelligence_upload.uploader import (
    UploadResult,
    run_upload,
)

from ._legacy_fixtures import build_legacy_session, make_legacy_record


def _mock_response(status_code: int = 200) -> MagicMock:
    """Creates mock httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    return response


def _run(session_dir: Path, metadata: dict[str, Any]) -> UploadResult:
    """Runs run_upload against a single (session_dir, metadata) with a mocked
    httpx.Client (always 200) and the skip-wrapped legacy parse_fn."""
    tracker = MagicMock()
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(200)

        result = run_upload(
            [(session_dir, metadata)],
            "https://server",
            "api-key",
            tracker,
            parse_fn=make_skip_wrapped_parse(),
        )
    return result


def test_missing_event_line_skipped_not_crashed(tmp_path: Path) -> None:
    """A good record plus one with 'event' deleted (LegacyEventError) --
    the upload still succeeds and the bad line is counted as skipped."""
    good = make_legacy_record(event="tool:pre", session_id="s1")
    bad = make_legacy_record(event="tool:post", session_id="s1")
    del bad["event"]
    session_dir = build_legacy_session(
        tmp_path, session_id="s1", records=[good, bad], terminal=False
    )
    metadata = {"session_id": "s1", "workspace": "-Users-me-project"}

    result = _run(session_dir, metadata)

    assert result.success is True
    assert result.events_skipped >= 1


def test_unknown_major_line_skipped_not_crashed(tmp_path: Path) -> None:
    """A record with an unsupported schema MAJOR version ('2.0.0') is
    skipped and counted (D1: unsupported major, not tolerated drift)."""
    good = make_legacy_record(event="tool:pre", session_id="s1")
    bad = make_legacy_record(event="tool:post", session_id="s1")
    bad["schema"] = {"name": "amplifier.log", "ver": "2.0.0"}
    session_dir = build_legacy_session(
        tmp_path, session_id="s1", records=[good, bad], terminal=False
    )
    metadata = {"session_id": "s1", "workspace": "-Users-me-project"}

    result = _run(session_dir, metadata)

    assert result.success is True
    assert result.events_skipped == 1


def test_patch_bump_line_is_NOT_skipped(tmp_path: Path) -> None:
    """A record with a patch-bumped schema version ('1.0.1') is
    forward-compatible drift (D1) -- it is UPLOADED, not skipped."""
    rec = make_legacy_record(event="tool:pre", session_id="s1")
    rec["schema"] = {"name": "amplifier.log", "ver": "1.0.1"}
    session_dir = build_legacy_session(tmp_path, session_id="s1", records=[rec], terminal=False)
    metadata = {"session_id": "s1", "workspace": "-Users-me-project"}

    result = _run(session_dir, metadata)

    assert result.success is True
    assert result.events_skipped == 0
    assert result.events_uploaded == 1


def test_unexpected_error_is_NOT_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected error type (a genuine bug, not a named legacy-parse
    error) is NOT caught by the skip wrapper and crashes loud."""
    rec = make_legacy_record(event="tool:pre", session_id="s1")
    session_dir = build_legacy_session(tmp_path, session_id="s1", records=[rec], terminal=False)
    metadata = {"session_id": "s1", "workspace": "-Users-me-project"}

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AttributeError("boom")

    monkeypatch.setattr(
        "amplifier_module_tool_context_intelligence_upload.logging_hook_format.reassemble_event_data",
        _boom,
    )

    with pytest.raises(AttributeError):
        _run(session_dir, metadata)
