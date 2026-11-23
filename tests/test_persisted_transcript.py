"""Synthetic-only tests for the pure persisted-message text projection."""

from __future__ import annotations

import json

import pytest

from context_intelligence.persisted_transcript import decode_persisted_message


def test_string_content_preserves_unicode_whitespace_and_empty_strings():
    for content in ("", " \n" + "界" * 8192 + "\n\nEND-SENTINEL  "):
        result = decode_persisted_message({"role": "assistant", "content": content}, 7)
        assert not result.issues
        assert result.message is not None
        assert result.message.content == content
        assert result.message.content_chars == len(content)
        assert result.message.timestamp is None
        assert result.message.source_event == "transcript:message"
        assert result.message.as_dict() == {
            "sequence": 0,
            "role": "assistant",
            "content": content,
            "timestamp": None,
            "event_line": 7,
            "source_event": "transcript:message",
        }


def test_typed_parts_preserve_original_indices_boundaries_and_exact_strings():
    first, second = "  first\n\n", "\n" + "界" * 8192 + "\nEND-SENTINEL  "
    record = {
        "role": "user",
        "content": [
            {"type": "text", "text": first},
            {"type": "tool_result", "content": "TOOL-SECRET"},
            {"type": "text", "text": ""},
            {"type": "text", "text": second},
        ],
        "provider_continuation": {"text": "PROVIDER-SECRET"},
        "metadata": {"unrecognized": "METADATA-SECRET"},
    }
    result = decode_persisted_message(record, 1)
    assert result.message is not None
    assert result.message.as_dict()["content"] == [
        {"index": 0, "text": first},
        {"index": 2, "text": ""},
        {"index": 3, "text": second},
    ]
    assert result.message.content_chars == len(first) + len(second)
    assert result.omitted_blocks == 1
    assert not result.issues
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("role", ["system", "developer", "tool"])
def test_known_nonconversation_roles_are_intentionally_omitted(role):
    result = decode_persisted_message(
        {"role": role, "content": "ROLE-SECRET", "metadata": {"persisted": "SECRET"}}, 2
    )
    assert result.message is None
    assert not result.issues
    assert result.omitted_messages == 1
    assert "SECRET" not in str(result)


@pytest.mark.parametrize(
    "kind",
    [
        "tool_use",
        "tool_call",
        "tool_result",
        "thinking",
        "redacted_thinking",
        "reasoning",
        "image",
        "image_url",
    ],
)
def test_known_nontext_blocks_are_not_walked_or_returned(kind):
    result = decode_persisted_message(
        {
            "role": "assistant",
            "content": [
                {"type": kind, "text": "NON-TEXT-SECRET", "input": {"text": "NESTED-SECRET"}}
            ],
        },
        3,
    )
    assert result.message is None
    assert result.omitted_messages == result.omitted_blocks == 1
    assert not result.issues
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("include_content", [False, True])
def test_explicit_compatibility_tool_calls_allow_null_or_missing_assistant_content(include_content):
    record = {"role": "assistant", "tool_calls": [{"arguments": "TOOL-SECRET"}]}
    if include_content:
        record["content"] = None
    result = decode_persisted_message(record, 4)
    assert result.message is None
    assert not result.issues
    assert result.omitted_messages == 1
    assert "SECRET" not in str(result)


@pytest.mark.parametrize(
    "record,code",
    [
        ({}, "invalid_message_role"),
        ({"role": []}, "invalid_message_role"),
        ({"role": ""}, "invalid_message_role"),
        ({"role": "ROLE-SECRET", "content": "SECRET"}, "unsupported_message_role"),
        ({"role": "user"}, "invalid_message_content"),
        ({"role": "assistant", "content": None}, "invalid_message_content"),
        ({"role": "assistant", "tool_calls": []}, "invalid_message_content"),
        ({"role": "assistant", "tool_calls": ["SECRET"]}, "invalid_message_content"),
        ({"role": "user", "content": None, "tool_calls": [{}]}, "invalid_message_content"),
        ({"role": "assistant", "content": {"text": "SECRET"}}, "invalid_message_content"),
        ({"role": "assistant", "content": 1}, "invalid_message_content"),
    ],
)
def test_invalid_records_report_fixed_safe_issues(record, code):
    result = decode_persisted_message(record, 5)
    assert result.message is None
    assert result.issues[0].code == code
    assert result.issues[0].event_line == 5
    assert "SECRET" not in str(result)


@pytest.mark.parametrize(
    "part,code",
    [
        ("SECRET", "invalid_content_part"),
        ({"type": []}, "invalid_content_part"),
        ({"type": "UNKNOWN-SECRET", "text": "SECRET"}, "unsupported_content_part"),
        ({"type": "text", "text": {"text": "SECRET"}}, "invalid_text_part"),
    ],
)
def test_invalid_part_does_not_hide_independently_valid_parts(part, code):
    result = decode_persisted_message(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "before"},
                part,
                {"type": "text", "text": "after"},
            ],
        },
        6,
    )
    assert result.message is not None
    assert result.message.as_dict()["content"] == [
        {"index": 0, "text": "before"},
        {"index": 2, "text": "after"},
    ]
    assert [issue.code for issue in result.issues] == [code]
    assert "SECRET" not in str(result)


def test_stored_reminder_text_and_boolean_context_flags_are_not_display_filtered():
    reminder = " \n<system-reminder>synthetic reminder</system-reminder>\n"
    result = decode_persisted_message(
        {
            "role": "user",
            "content": reminder,
            "metadata": {"ephemeral": True, "persisted": False, "private": "SECRET"},
        },
        8,
    )
    assert result.message is not None
    assert result.message.content == reminder
    assert result.message.as_dict()["context_flags"] == {"ephemeral": True, "persisted": False}
    assert not result.issues
    assert "SECRET" not in json.dumps(result.message.as_dict())


@pytest.mark.parametrize("metadata", ["SECRET", {"ephemeral": "SECRET"}, {"persisted": 1}])
def test_invalid_flags_are_not_echoed_and_text_is_preserved(metadata):
    result = decode_persisted_message(
        {"role": "user", "content": "intact", "metadata": metadata}, 9
    )
    assert result.message is not None
    assert result.message.content == "intact"
    assert [issue.code for issue in result.issues] == ["invalid_context_flags"]
    assert "context_flags" not in result.message.as_dict()
    assert "SECRET" not in str(result)


def test_invalid_timestamp_is_not_stringified_or_fabricated():
    result = decode_persisted_message(
        {"role": "assistant", "content": "intact", "timestamp": {"text": "SECRET"}}, 10
    )
    assert result.message is not None
    assert result.message.timestamp is None
    assert [issue.code for issue in result.issues] == ["invalid_message_timestamp"]
    assert "SECRET" not in str(result)
