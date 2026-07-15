"""One-shot generator: capture PRE-refactor default-path golden payloads.

Drives the UNTOUCHED ``run_upload`` inline loop (mocking ``httpx.Client``) and
captures every POSTed ``/events`` body (``kwargs["json"]``) into
``tests/golden/default_path_golden.json``.  This snapshot is the GATE 2
byte-parity oracle (item F / TB-13): once committed, it becomes frozen truth
that any refactor of the upload loop must reproduce exactly.

Run with::

    uv run python tests/golden/generate_default_golden.py

MUST be run against the pre-refactor baseline (uploader.py unmodified) —
running it after refactor code lands would capture the NEW behavior instead
of the old one, defeating the purpose of the oracle.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from amplifier_module_tool_context_intelligence_upload.uploader import run_upload

GOLDEN_DIR = Path(__file__).resolve().parent
SCRATCH_DIR = GOLDEN_DIR / "_scratch_session"
LEGACY_INPUT = GOLDEN_DIR / "legacy_input.jsonl"
OUTPUT_FILE = GOLDEN_DIR / "default_path_golden.json"


def _mock_response(status_code: int = 200) -> MagicMock:
    """Creates mock httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    return response


def main() -> None:
    """Drive run_upload against legacy_input.jsonl and capture posted bodies."""
    if SCRATCH_DIR.exists():
        shutil.rmtree(SCRATCH_DIR)
    SCRATCH_DIR.mkdir(parents=True)

    metadata: dict[str, Any] = {"session_id": "golden", "format": "context-intelligence"}
    (SCRATCH_DIR / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    shutil.copyfile(LEGACY_INPUT, SCRATCH_DIR / "events.jsonl")

    sessions = [(SCRATCH_DIR, metadata)]
    tracker = MagicMock()
    captured: list[Any] = []

    try:
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                captured.append(kwargs.get("json"))
                return _mock_response(200)

            mock_client.post.side_effect = capture_post

            run_upload(sessions, "https://server", "k", tracker)
    finally:
        shutil.rmtree(SCRATCH_DIR, ignore_errors=True)

    OUTPUT_FILE.write_text(
        json.dumps(captured, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(captured)} golden payloads")


if __name__ == "__main__":
    main()
