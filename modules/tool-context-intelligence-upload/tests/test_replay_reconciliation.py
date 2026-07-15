"""Tests for the replay/dedup reconciliation (Council tester-breaker #3).

``run_upload`` posts with ``?replay=true`` by default, which deliberately
BYPASSES the server's idempotency-cache dedup -- correct for live capture,
but wrong for a re-runnable bulk import: a mid-batch abort + rerun with
``replay=true`` would re-POST everything with dedup blinded, risking
duplicates. The safe default for the logging-hook bulk import is dedup ON
(``replay=False``), so an aborted+rerun import is idempotent server-side
(the idempotency key is deterministic over ``(event, workspace, data)``).

This test pins that contract at the ``run_upload`` seam: calling it with
``replay=False`` must never attach the ``replay=true`` query param.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
    make_skip_wrapped_parse,
)
from amplifier_module_tool_context_intelligence_upload.uploader import run_upload

from ._legacy_fixtures import build_legacy_session, make_legacy_record


def _mock_response(status_code: int = 200) -> MagicMock:
    """Creates mock httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    return response


def test_logging_hook_upload_uses_dedup_when_replay_false(tmp_path: Path) -> None:
    """replay=False -> no ?replay=true query param on any POST -> dedup engaged.

    An aborted+rerun logging-hook import must not duplicate server-side.
    ``run_upload`` already honors ``replay`` by setting
    ``query_params = {"replay": "true"} if replay else None``; this test
    pins the contract that the logging-hook path defaults to
    ``replay=False`` so a rerun re-uses the server's dedup cache.
    """
    records = [
        make_legacy_record(event="tool:pre", session_id="s1"),
        make_legacy_record(event="tool:post", session_id="s1"),
    ]
    session_dir = build_legacy_session(tmp_path, session_id="s1", records=records, terminal=True)
    metadata: dict[str, Any] = {
        "session_id": "s1",
        "format": "logging-hook",
        "workspace": "-Users-me-project",
    }

    tracker = MagicMock()
    seen_params: list[Any] = []

    def _capture_post(*args: Any, **kwargs: Any) -> MagicMock:
        seen_params.append(kwargs.get("params"))
        return _mock_response(200)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = _capture_post

        result = run_upload(
            [(session_dir, metadata)],
            "https://server",
            "k",
            tracker,
            replay=False,
            parse_fn=make_skip_wrapped_parse(),
        )

    assert result.success is True
    assert len(seen_params) > 0
    assert all(p is None for p in seen_params)
