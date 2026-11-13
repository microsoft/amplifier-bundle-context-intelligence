"""Prospective hook sink tests on synthetic events only.

These focused tests observe outbound enqueue, not a fabricated server response.
The live hook/network/auth seam still requires the coordinated DTU gate.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from amplifier_core.message_models import ImageBlock
from amplifier_core.models import ToolResult

from amplifier_module_hook_context_intelligence import mount
from amplifier_module_hook_context_intelligence.handlers import logging_handler as handler_module
from context_intelligence.capture_media import IMAGE_NOT_RETAINED_TEXT

# A synthetic 1x1 PNG; never read from a user's image or screen.
PIXELS = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def _resolver(tmp_path: Path):
    return SimpleNamespace(
        workspace="synthetic-workspace",
        working_dir="/synthetic",
        session_dir=lambda session_id: tmp_path / session_id,
    )


def _event():
    return {
        "session_id": "synthetic-session",
        "timestamp": "2026-01-15T10:00:00Z",
        "tool_call_id": "call-1",
        "status": "partial",
        "result": {
            "success": False,
            "error": {"code": "partial"},
            "output": {"label": "useful"},
            "content": [
                {"type": "text", "text": "before"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": PIXELS},
                },
                {"type": "text", "text": "after"},
            ],
        },
        "usage": {"input_tokens": 23, "output_tokens": 7},
        "metadata": {"opaque_signature": "untouched"},
        "raw": {"$blob_ref": "ci-blob://separate-existing-key"},
    }


async def test_file_and_enqueue_receive_only_projected_copy(tmp_path, monkeypatch):
    handler = handler_module.LoggingHandler(_resolver(tmp_path))
    dispatcher = SimpleNamespace(enqueue=Mock(return_value=True))
    monkeypatch.setattr(handler, "_dispatchers", [dispatcher])
    data = _event()
    original = deepcopy(data)
    response = await handler("tool:post", data)
    assert response.action == "continue"
    assert data == original
    disk = json.loads((tmp_path / data["session_id"] / "events.jsonl").read_text())
    queued_event, queued = dispatcher.enqueue.call_args.args
    assert queued_event == "tool:post"
    assert disk["data"] == queued
    assert PIXELS not in json.dumps(disk)
    assert IMAGE_NOT_RETAINED_TEXT in json.dumps(disk)
    expected = deepcopy(original)
    expected["result"]["content"][1] = queued["result"]["content"][1]
    assert queued == expected
    assert disk["timestamp"] == data["timestamp"]
    assert disk["workspace"] == "synthetic-workspace"
    data["result"]["output"]["label"] = "caller later changed"
    assert queued["result"]["output"]["label"] == "useful"


async def test_core_model_image_is_omitted_after_sanitization(tmp_path):
    handler = handler_module.LoggingHandler(_resolver(tmp_path))
    data = _event()
    image = ImageBlock(source={"type": "base64", "media_type": "image/png", "data": PIXELS})
    data["result"]["content"][1] = image
    await handler("tool:post", data)
    saved = (tmp_path / data["session_id"] / "events.jsonl").read_text()
    assert PIXELS not in saved
    assert IMAGE_NOT_RETAINED_TEXT in saved
    assert image.source["data"] == PIXELS


async def test_core_rich_tool_result_is_preserved_except_image_transport(tmp_path):
    if "content" not in ToolResult.model_fields:
        pytest.skip("canonical ToolResult rich content requires Core 2")
    image = ImageBlock(source={"type": "base64", "media_type": "image/png", "data": PIXELS})
    tool_result = ToolResult.model_validate(
        {
            "success": False,
            "output": {"label": "useful", "experimental.media.v1": {"refs": []}},
            "error": {"code": "partial"},
            "content": [{"type": "text", "text": "useful tool text"}, image],
        }
    )
    original = tool_result.model_dump()
    handler = handler_module.LoggingHandler(_resolver(tmp_path))
    await handler(
        "tool:post",
        {"session_id": "synthetic-session", "result": tool_result, "usage": {"input_tokens": 23}},
    )
    saved = json.loads((tmp_path / "synthetic-session" / "events.jsonl").read_text())["data"]
    assert tool_result.model_dump() == original
    assert PIXELS not in json.dumps(saved)
    assert IMAGE_NOT_RETAINED_TEXT in json.dumps(saved)
    expected = deepcopy(original)
    expected["content"][1] = saved["result"]["content"][1]
    assert saved["result"] == expected
    assert saved["usage"] == {"input_tokens": 23}


@pytest.mark.parametrize("cap", [None, SimpleNamespace(version=2), SimpleNamespace(version=1)])
async def test_missing_incompatible_or_present_media_cap_does_not_grant_pixels(
    tmp_path, cap, monkeypatch
):
    # Even a compatible six-op capability has no diagnostic operation.
    # Any accidental call here is a test failure, not a fake externalization.
    if cap is not None:
        cap.ingest = Mock(side_effect=AssertionError("diagnostics must not ingest"))
        cap.resolve = Mock(side_effect=AssertionError("diagnostics must not hydrate"))
        cap.publish = Mock(side_effect=AssertionError("diagnostics must not publish"))
    capabilities: dict[str, Any] = {"experimental.media.v1": cap}
    coordinator = SimpleNamespace(
        config={},
        get_capability=capabilities.get,
        register_capability=lambda name, value: capabilities.__setitem__(name, value),
    )
    cleanup = await mount(
        coordinator, {"base_path": str(tmp_path), "project_slug": "synthetic-project"}
    )
    handler = capabilities["context_intelligence._hook_state"]["logging_handler"]
    dispatcher = SimpleNamespace(enqueue=Mock(return_value=True), close=Mock())
    monkeypatch.setattr(handler, "_dispatchers", [dispatcher])
    await handler("tool:post", _event())
    queued = dispatcher.enqueue.call_args.args[1]
    assert PIXELS not in json.dumps(queued)
    assert IMAGE_NOT_RETAINED_TEXT in json.dumps(queued)
    if cap is not None:
        cap.ingest.assert_not_called()
        cap.resolve.assert_not_called()
        cap.publish.assert_not_called()
    handler._dispatchers = []
    await cleanup()


async def test_disk_failure_still_enqueues_only_omitted_image(tmp_path, monkeypatch):
    handler = handler_module.LoggingHandler(_resolver(tmp_path))
    dispatcher = SimpleNamespace(enqueue=Mock(return_value=True))
    monkeypatch.setattr(handler, "_dispatchers", [dispatcher])
    monkeypatch.setattr(handler, "_persist_to_disk", Mock(return_value="degraded"))
    await handler("tool:post", _event())
    assert PIXELS not in json.dumps(dispatcher.enqueue.call_args.args[1])


@pytest.mark.parametrize("fail_at", ["project_capture_media", "_sanitize_for_json"])
async def test_projection_failure_refuses_both_sinks_without_logging_payload(
    tmp_path, monkeypatch, caplog, fail_at
):
    handler = handler_module.LoggingHandler(_resolver(tmp_path))
    disk = Mock()
    dispatcher = SimpleNamespace(enqueue=Mock())
    monkeypatch.setattr(handler, "_persist_to_disk", disk)
    monkeypatch.setattr(handler, "_dispatchers", [dispatcher])
    monkeypatch.setattr(handler_module, fail_at, Mock(side_effect=ValueError(PIXELS)))
    response = await handler("tool:post", _event())
    assert response.action == "continue"
    disk.assert_not_called()
    dispatcher.enqueue.assert_not_called()
    assert "event not captured" in caplog.text
    assert PIXELS not in caplog.text


async def test_cyclic_event_refuses_both_sinks_without_exception_details(
    tmp_path, monkeypatch, caplog
):
    handler = handler_module.LoggingHandler(_resolver(tmp_path))
    disk = Mock()
    dispatcher = SimpleNamespace(enqueue=Mock())
    monkeypatch.setattr(handler, "_persist_to_disk", disk)
    monkeypatch.setattr(handler, "_dispatchers", [dispatcher])
    data = _event()
    data["cyclic"] = data
    response = await handler("tool:post", data)
    assert response.action == "continue"
    disk.assert_not_called()
    dispatcher.enqueue.assert_not_called()
    assert "event not captured" in caplog.text
    assert "Traceback" not in caplog.text
    assert "RecursionError" not in caplog.text
    assert PIXELS not in caplog.text
