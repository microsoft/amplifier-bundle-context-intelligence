"""Reference-safe diagnostic copies of known structured image transports.

This is an omission backstop, not a media store or a provider request projector.
The six-operation ``experimental.media.v1`` capability has no diagnostic-only
externalization operation. Ingest/publish would invent canonical acceptance;
resolve/prepare_request would hydrate pixels without diagnostic-upload authority.
Consequently even a mounted capability does not enable retention here.

Only actual mappings/lists are traversed. Strings (including JSON-looking text,
opaque reasoning and continuation tokens) are never parsed. Existing media refs
and CI's distinct ``$blob_ref`` remain data, not evidence of validation or sight.
Unqualified producer formats are not covered by this backstop. Producers must
independently project before hooks.emit; this late copy cannot prevent the
initial Core/Rust event serialization cost.
"""

from __future__ import annotations

from typing import Any

DIAGNOSTIC_MEDIA_KEY = "context_intelligence.media"
IMAGE_NOT_RETAINED_TEXT = (
    "[Image not retained in diagnostic capture; no pixels stored or forwarded.]"
)


def project_capture_media(data: dict[str, Any]) -> dict[str, Any]:
    """Copy sanitized event data, omitting known image transports before sinks.

    Core ImageBlock uses ``type=image, source``. A vendor block with the same
    structure is omitted too; this does not qualify other vendor transports.
    Remove the entire source, including unsupported source versions: an omission
    is not a claim that the image could be decoded or retained. Keep adjacent
    text, visibility, status, usage and metadata. A caller MUST refuse capture
    on failure rather than fall back to the original event.
    """
    return _copy_mapping(data)


def _copy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _copy_mapping(value)
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    return value


def _copy_mapping(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("type") == "image" and "source" in value:
        # Never put references in ImageBlock.source. Replace the diagnostic
        # block with readable text, not a malformed/resolvable image block.
        result = {key: _copy_value(item) for key, item in value.items() if key != "source"}
        result["type"] = "text"
        text = result.get("text")
        result["text"] = (
            f"{text}\n{IMAGE_NOT_RETAINED_TEXT}"
            if isinstance(text, str)
            else IMAGE_NOT_RETAINED_TEXT
        )
        result[DIAGNOSTIC_MEDIA_KEY] = {
            "v": 1,
            "status": "not_retained",
            "reason": "diagnostic_capture",
            "original_type": "image",
        }
        return result
    return {key: _copy_value(item) for key, item in value.items()}
