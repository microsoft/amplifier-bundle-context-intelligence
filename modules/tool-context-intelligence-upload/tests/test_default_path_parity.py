"""GATE 2 byte-parity test: default path vs. the pre-refactor golden.

Proves routing through ``FORMATS['context-intelligence']`` yields ``/events``
payloads byte-identical to the golden captured in Task 1 (item F / TB-13).

The oracle is the committed snapshot file ``golden/default_path_golden.json``,
NOT a re-implementation of the rules in this test -- a re-implemented oracle
can drift with the bug (TB-13). If this test fails, the refactor changed a
posted body -- a real GATE 2 violation. Revert the behavioral difference; do
NOT re-capture the golden to make it pass.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from amplifier_module_tool_context_intelligence_upload.uploader import run_upload

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _load_golden() -> list[Any]:
    """Load the committed pre-refactor golden payloads."""
    return json.loads((GOLDEN_DIR / "default_path_golden.json").read_text(encoding="utf-8"))


def _mock_response(status_code: int = 200) -> MagicMock:
    """Creates mock httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    return response


def test_default_run_upload_matches_pre_refactor_golden(tmp_path: Path) -> None:
    """Default-path run_upload output is byte-identical to the pre-refactor golden.

    Builds a synthetic session from the SAME fixed input the golden was
    captured from (``golden/legacy_input.jsonl``), replays it through the
    CURRENT ``run_upload``, and compares the posted ``/events`` bodies to the
    committed golden snapshot -- not to a re-derived expectation.
    """
    session_dir = tmp_path / "golden-session"
    session_dir.mkdir(parents=True)

    # metadata deliberately omits 'workspace' -- matches the golden capture,
    # so the _workspace_from_path fallback path is exercised identically.
    metadata: dict[str, Any] = {"session_id": "golden", "format": "context-intelligence"}
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    shutil.copyfile(GOLDEN_DIR / "legacy_input.jsonl", session_dir / "events.jsonl")

    sessions = [(session_dir, metadata)]
    tracker = MagicMock()
    captured: list[Any] = []

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        def capture_post(url: str, **kwargs: Any) -> MagicMock:
            captured.append(kwargs.get("json"))
            return _mock_response(200)

        mock_client.post.side_effect = capture_post

        run_upload(sessions, "https://server", "k", tracker)

    golden = _load_golden()

    assert [json.dumps(p, sort_keys=True) for p in captured] == [
        json.dumps(p, sort_keys=True) for p in golden
    ], "default path diverged from the pre-refactor golden — GATE 2 violation"
