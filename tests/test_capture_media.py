"""Synthetic structured-image projection; no live captures or image I/O."""

from copy import deepcopy

import pytest

from context_intelligence.capture_media import (
    DIAGNOSTIC_MEDIA_KEY,
    IMAGE_NOT_RETAINED_TEXT,
    project_capture_media,
)


@pytest.mark.parametrize(
    "source",
    [
        {"type": "base64", "media_type": "image/png", "data": "synthetic-png-pixels"},
        {"type": "base64", "media_type": "image/jpeg", "data": "synthetic-jpeg-pixels"},
        {"type": "url", "url": "data:image/png;base64,synthetic-pixels"},
        {"type": "url", "url": "https://image.invalid/private.png"},
        {"type": "unknown-future-transport", "payload": "unqualified-pixels"},
        None,
    ],
)
def test_omit_entire_core_image_source_without_mutating_input(source):
    data = {
        "result": {
            "success": False,
            "error": {"code": "partial"},
            "content": [
                {"type": "text", "text": "before"},
                {"type": "image", "source": source, "visibility": "user"},
                {"type": "text", "text": "after"},
            ],
        },
        "usage": {"input_tokens": 17},
        "metadata": {"owner": {"continuation": "opaque"}},
    }
    original = deepcopy(data)
    result = project_capture_media(data)
    assert data == original
    image = result["result"]["content"][1]
    assert image == {
        "type": "text",
        "text": IMAGE_NOT_RETAINED_TEXT,
        "visibility": "user",
        DIAGNOSTIC_MEDIA_KEY: {
            "v": 1,
            "status": "not_retained",
            "reason": "diagnostic_capture",
            "original_type": "image",
        },
    }
    expected = deepcopy(original)
    expected["result"]["content"][1] = image
    assert result == expected
    result["metadata"]["owner"]["continuation"] = "changed copy"
    assert data == original


def test_refs_and_json_looking_text_are_data_not_promoted_or_hydrated():
    data = {
        "metadata": {
            "experimental.media.v1": {"refs": [{"v": 1, "sha256": "a" * 64}]},
            "reasoning": '{"type":"image","source":{"data":"opaque-not-an-image"}}',
        },
        "raw": {"$blob_ref": "ci-blob://existing-key", "bytes": 100, "sha256": "b" * 64},
        "output": '{"experimental.media.v1":{"refs":[{"v":1}]}}',
    }
    assert project_capture_media(data) == data


def test_projection_is_idempotent_and_preserves_adjacent_text_and_metadata():
    data = {
        "type": "image",
        "source": {"type": "base64", "data": "not-retained"},
        "text": "useful label",
        "metadata": {"tool_call_id": "call-1"},
    }
    result = project_capture_media(data)
    assert result["text"] == f"useful label\n{IMAGE_NOT_RETAINED_TEXT}"
    assert result["metadata"] == data["metadata"]
    assert project_capture_media(result) == result


def test_unknown_third_party_schema_is_not_falsely_claimed_supported():
    data = {"third_party_transport": {"pixel_buffer": "unknown-format"}}
    assert project_capture_media(data) == data


def test_source_less_image_tagged_metadata_is_not_an_image_transport():
    data = {
        "metadata": {"type": "image", "name": "screenshot.png"},
        "raw": {"$blob_ref": "ci-blob://key", "metadata": {"type": "image"}},
        "output": {"experimental.media.v1": {"refs": [], "metadata": {"type": "image"}}},
    }
    assert project_capture_media(data) == data


def test_unqualified_vendor_transports_are_not_claimed_supported():
    data = {
        "raw": {
            "content": [
                {"type": "input_image", "image_url": "data:image/png;base64,unqualified"},
                {"inline_data": {"mime_type": "image/png", "data": "unqualified"}},
            ]
        }
    }
    assert project_capture_media(data) == data
