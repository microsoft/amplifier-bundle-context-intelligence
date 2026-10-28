"""Pure text projection of Foundation-owned persisted message records.

This is not a persistence schema, CLI display policy, or recovery implementation.
Only explicit user/assistant strings and typed text blocks leave this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .native_transcript import TranscriptIssue, TranscriptMessage, TranscriptTextPart

_OMITTED_ROLES = {"system", "developer", "tool"}
_OMITTED_BLOCKS = {
    "tool_use",
    "tool_call",
    "tool_result",
    "thinking",
    "redacted_thinking",
    "reasoning",
    "image",
    "image_url",
}


@dataclass(frozen=True)
class PersistedProjection:
    message: TranscriptMessage | None = None
    issues: tuple[TranscriptIssue, ...] = ()
    omitted_messages: int = 0
    omitted_blocks: int = 0


def decode_persisted_message(record: dict[str, Any], line: int) -> PersistedProjection:
    """Project one record without stringifying or searching nested payloads."""
    issues: list[TranscriptIssue] = []

    def issue(code: str, detail: str) -> None:
        # A malformed block array need not amplify one physical line into
        # thousands of identical diagnostics.
        if not any(existing.code == code for existing in issues):
            issues.append(TranscriptIssue(code, line, detail))

    role = record.get("role")
    if not isinstance(role, str) or not role:
        issue("invalid_message_role", "stored message requires a nonempty string role")
        return PersistedProjection(issues=tuple(issues))
    if role in _OMITTED_ROLES:
        return PersistedProjection(omitted_messages=1)
    if role not in {"user", "assistant"}:
        issue("unsupported_message_role", "stored message role is not supported")
        return PersistedProjection(issues=tuple(issues))

    timestamp = record.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, str):
        issue("invalid_message_timestamp", "stored timestamp must be a string when present")
        timestamp = None
    flags: list[tuple[str, bool]] = []
    metadata = record.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            issue("invalid_context_flags", "stored message metadata must be an object")
        else:
            for key in ("ephemeral", "persisted"):
                if key in metadata:
                    if isinstance(metadata[key], bool):
                        flags.append((key, metadata[key]))
                    else:
                        issue("invalid_context_flags", "stored context flags must be booleans")

    content = record.get("content")
    text: str | tuple[TranscriptTextPart, ...]
    omitted_blocks = 0
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[TranscriptTextPart] = []
        for index, block in enumerate(content):
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                issue("invalid_content_part", "stored content part requires a string type")
                continue
            kind = block["type"]
            if kind == "text":
                if isinstance(block.get("text"), str):
                    parts.append(TranscriptTextPart(index, block["text"]))
                else:
                    issue("invalid_text_part", "stored text part requires string text")
            elif kind in _OMITTED_BLOCKS:
                omitted_blocks += 1
            else:
                issue("unsupported_content_part", "stored content part type is not supported")
        if not parts:
            return PersistedProjection(
                issues=tuple(issues), omitted_messages=1, omitted_blocks=omitted_blocks
            )
        text = tuple(parts)
    elif (
        content is None
        and role == "assistant"
        and isinstance(record.get("tool_calls"), list)
        and bool(record["tool_calls"])
        and all(isinstance(call, dict) for call in record["tool_calls"])
    ):
        # Compatibility tool-call field: check shape only, never read arguments.
        return PersistedProjection(issues=tuple(issues), omitted_messages=1)
    else:
        issue("invalid_message_content", "stored message requires string or typed text content")
        return PersistedProjection(issues=tuple(issues))

    return PersistedProjection(
        message=TranscriptMessage(
            sequence=0,
            role="user" if role == "user" else "assistant",
            content=text,
            timestamp=timestamp,
            event_line=line,
            source_event="transcript:message",
            context_flags=tuple(flags),
        ),
        issues=tuple(issues),
        omitted_blocks=omitted_blocks,
    )
