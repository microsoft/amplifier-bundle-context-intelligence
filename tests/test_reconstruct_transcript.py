"""Tests for context_intelligence.reconstruct.transcript (task-6).

Covers:
- Module imports correctly (extract_transcript, _make_assistant_content)
- _extract_content_blocks() extracts content blocks from Anthropic response
- _content_blocks_to_tool_calls() converts to {id, tool, arguments} format
- _make_assistant_content() renames tool_use->tool_call, strips caller, adds visibility to thinking
- _stringify_tool_result() converts various types to string
- extract_transcript() walks graph: Session -> OrchestratorRun -> PromptStep -> AssistantStep -> ToolExecution
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock


class TestImport:
    """Module must be importable with the required public API."""

    def test_extract_transcript_import(self):
        """extract_transcript must be importable from context_intelligence.reconstruct.transcript."""
        from context_intelligence.reconstruct.transcript import extract_transcript  # noqa: F401

    def test_make_assistant_content_import(self):
        """_make_assistant_content must be importable from context_intelligence.reconstruct.transcript."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content  # noqa: F401

    def test_acceptance_criteria_command(self):
        """Simulate the acceptance criteria import command."""
        from context_intelligence.reconstruct.transcript import (
            _make_assistant_content,
            extract_transcript,
        )

        assert extract_transcript is not None
        assert _make_assistant_content is not None

    def test_extract_content_blocks_import(self):
        """_extract_content_blocks must be importable."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks  # noqa: F401

    def test_content_blocks_to_tool_calls_import(self):
        """_content_blocks_to_tool_calls must be importable."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls  # noqa: F401

    def test_stringify_tool_result_import(self):
        """_stringify_tool_result must be importable."""
        from context_intelligence.reconstruct.transcript import _stringify_tool_result  # noqa: F401


class TestExtractContentBlocks:
    """_extract_content_blocks() tests."""

    def test_extracts_text_block(self):
        """Should extract a text block from Anthropic response."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        raw = {"content": [{"type": "text", "text": "Hello!"}]}
        result = _extract_content_blocks(raw)
        assert result == [{"type": "text", "text": "Hello!"}]

    def test_extracts_tool_use_block(self):
        """Should extract a tool_use block from Anthropic response."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        raw = {
            "content": [
                {"type": "tool_use", "id": "toolu_123", "name": "bash", "input": {"cmd": "ls"}}
            ]
        }
        result = _extract_content_blocks(raw)
        assert len(result) == 1
        assert result[0]["type"] == "tool_use"
        assert result[0]["id"] == "toolu_123"

    def test_extracts_thinking_block(self):
        """Should extract a thinking block from Anthropic response."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        raw = {
            "content": [{"type": "thinking", "thinking": "Let me think...", "signature": "sig123"}]
        }
        result = _extract_content_blocks(raw)
        assert len(result) == 1
        assert result[0]["type"] == "thinking"

    def test_returns_empty_list_for_non_dict(self):
        """Should return empty list when raw_response is not a dict."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        assert _extract_content_blocks(None) == []
        assert _extract_content_blocks("not a dict") == []
        assert _extract_content_blocks([]) == []

    def test_returns_empty_list_for_empty_content(self):
        """Should return empty list when content is empty."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        assert _extract_content_blocks({"content": []}) == []

    def test_handles_json_string_input(self):
        """Should parse a JSON string input."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        raw = json.dumps({"content": [{"type": "text", "text": "Hello"}]})
        result = _extract_content_blocks(raw)
        assert result == [{"type": "text", "text": "Hello"}]

    def test_handles_nested_response_key(self):
        """Should handle response wrapped in a 'response' key when top-level content is non-list."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        # When "content" key is present but not a list, fall through to response.content
        raw = {
            "content": None,  # present but not a list
            "response": {"content": [{"type": "text", "text": "Hello from nested"}]},
        }
        result = _extract_content_blocks(raw)
        assert result == [{"type": "text", "text": "Hello from nested"}]

    def test_extracts_multiple_blocks(self):
        """Should extract multiple content blocks."""
        from context_intelligence.reconstruct.transcript import _extract_content_blocks

        raw = {
            "content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {}},
            ]
        }
        result = _extract_content_blocks(raw)
        assert len(result) == 3


class TestContentBlocksToToolCalls:
    """_content_blocks_to_tool_calls() tests."""

    def test_converts_tool_use_blocks(self):
        """Should convert tool_use blocks to {id, tool, arguments} format."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls

        blocks = [{"type": "tool_use", "id": "toolu_123", "name": "bash", "input": {"cmd": "ls"}}]
        result = _content_blocks_to_tool_calls(blocks)
        assert len(result) == 1
        assert result[0] == {"id": "toolu_123", "tool": "bash", "arguments": {"cmd": "ls"}}

    def test_uses_tool_not_name_key(self):
        """Should use 'tool' key (not 'name') for tool name."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls

        blocks = [{"type": "tool_use", "id": "toolu_1", "name": "my_tool", "input": {}}]
        result = _content_blocks_to_tool_calls(blocks)
        assert "tool" in result[0]
        assert "name" not in result[0]

    def test_uses_arguments_not_input_key(self):
        """Should use 'arguments' key (not 'input') for tool input."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls

        blocks = [{"type": "tool_use", "id": "t1", "name": "x", "input": {"a": 1}}]
        result = _content_blocks_to_tool_calls(blocks)
        assert "arguments" in result[0]
        assert "input" not in result[0]
        assert result[0]["arguments"] == {"a": 1}

    def test_skips_non_tool_use_blocks(self):
        """Should skip text and thinking blocks."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls

        blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {}},
            {"type": "thinking", "thinking": "..."},
        ]
        result = _content_blocks_to_tool_calls(blocks)
        assert len(result) == 1
        assert result[0]["id"] == "toolu_1"

    def test_returns_empty_list_for_no_tool_calls(self):
        """Should return empty list when no tool_use blocks present."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls

        blocks = [{"type": "text", "text": "Hello"}]
        assert _content_blocks_to_tool_calls(blocks) == []

    def test_returns_empty_list_for_empty_input(self):
        """Should return empty list for empty input."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls

        assert _content_blocks_to_tool_calls([]) == []

    def test_handles_tool_call_type_too(self):
        """Should also convert tool_call type blocks (not just tool_use)."""
        from context_intelligence.reconstruct.transcript import _content_blocks_to_tool_calls

        blocks = [{"type": "tool_call", "id": "tc_1", "name": "x", "input": {"k": "v"}}]
        result = _content_blocks_to_tool_calls(blocks)
        assert len(result) == 1
        assert result[0]["tool"] == "x"


class TestMakeAssistantContent:
    """_make_assistant_content() tests."""

    def test_renames_tool_use_to_tool_call(self):
        """Should rename 'tool_use' type to 'tool_call'."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [{"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {}}]
        result = _make_assistant_content(blocks)
        assert result[0]["type"] == "tool_call"

    def test_does_not_rename_text_type(self):
        """Should not rename text blocks."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [{"type": "text", "text": "Hello"}]
        result = _make_assistant_content(blocks)
        assert result[0]["type"] == "text"

    def test_strips_caller_field(self):
        """Should strip 'caller' field from all blocks."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [{"type": "tool_use", "id": "t1", "name": "x", "caller": "some_caller"}]
        result = _make_assistant_content(blocks)
        assert "caller" not in result[0]

    def test_adds_visibility_internal_to_thinking_blocks(self):
        """Should add visibility='internal' to thinking blocks."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [{"type": "thinking", "thinking": "Let me reason..."}]
        result = _make_assistant_content(blocks)
        assert result[0]["visibility"] == "internal"

    def test_does_not_overwrite_existing_visibility(self):
        """Should not overwrite existing visibility field in thinking blocks."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [{"type": "thinking", "thinking": "...", "visibility": "public"}]
        result = _make_assistant_content(blocks)
        assert result[0]["visibility"] == "public"

    def test_does_not_add_visibility_to_text_blocks(self):
        """Should not add visibility to text blocks."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [{"type": "text", "text": "Hello"}]
        result = _make_assistant_content(blocks)
        assert "visibility" not in result[0]

    def test_does_not_mutate_originals(self):
        """Should not mutate the original blocks."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]
        original_type = blocks[0]["type"]
        _make_assistant_content(blocks)
        assert blocks[0]["type"] == original_type

    def test_handles_non_dict_blocks(self):
        """Should pass through non-dict blocks unchanged."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = ["string_block"]
        result = _make_assistant_content(blocks)
        assert result == ["string_block"]

    def test_processes_multiple_blocks(self):
        """Should process multiple blocks correctly."""
        from context_intelligence.reconstruct.transcript import _make_assistant_content

        blocks = [
            {"type": "thinking", "thinking": "...", "caller": "c"},
            {"type": "text", "text": "Hi"},
            {"type": "tool_use", "id": "t1", "name": "bash", "caller": "me", "input": {}},
        ]
        result = _make_assistant_content(blocks)
        assert result[0]["type"] == "thinking"
        assert result[0]["visibility"] == "internal"
        assert "caller" not in result[0]
        assert result[1]["type"] == "text"
        assert result[2]["type"] == "tool_call"
        assert "caller" not in result[2]


class TestStringifyToolResult:
    """_stringify_tool_result() tests."""

    def test_returns_string_as_is(self):
        """Should return string values unchanged."""
        from context_intelligence.reconstruct.transcript import _stringify_tool_result

        assert _stringify_tool_result("hello") == "hello"

    def test_converts_dict_to_json(self):
        """Should convert dicts to JSON string."""
        from context_intelligence.reconstruct.transcript import _stringify_tool_result

        result = _stringify_tool_result({"key": "value"})
        assert result == json.dumps({"key": "value"}, ensure_ascii=False)

    def test_converts_list_to_json(self):
        """Should convert lists to JSON string."""
        from context_intelligence.reconstruct.transcript import _stringify_tool_result

        result = _stringify_tool_result([1, 2, 3])
        assert result == json.dumps([1, 2, 3], ensure_ascii=False)

    def test_converts_none_to_string(self):
        """Should convert None to string 'None'."""
        from context_intelligence.reconstruct.transcript import _stringify_tool_result

        assert _stringify_tool_result(None) == "None"

    def test_converts_int_to_string(self):
        """Should convert int to string."""
        from context_intelligence.reconstruct.transcript import _stringify_tool_result

        assert _stringify_tool_result(42) == "42"

    def test_converts_bool_to_string(self):
        """Should convert bool to string."""
        from context_intelligence.reconstruct.transcript import _stringify_tool_result

        assert _stringify_tool_result(True) == "True"


class TestExtractTranscript:
    """extract_transcript() rebuilds the transcript from raw Event nodes."""

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _make_event_row(
        self,
        event_name: str,
        ts: str,
        session_id: str = "sess1",
        **extra_data,
    ) -> dict:
        """Build one Event-node row as returned by the single cypher query."""
        data = {"timestamp": ts, "session_id": session_id, **extra_data}
        return {
            "event_name": event_name,
            "data": json.dumps(data),
            "occurred_at": ts,
        }

    def _make_client(
        self,
        event_rows: list | None = None,
        blob_side_effect=None,
    ) -> MagicMock:
        """Build a mock CIClient whose cypher() returns event rows.

        blob_side_effect may be a callable(session_id, key)->value or a fixed
        return value.  If None, fetch_blob returns None.
        """
        client = MagicMock()
        client.cypher.return_value = event_rows if event_rows is not None else []
        if callable(blob_side_effect):
            client.fetch_blob.side_effect = blob_side_effect
        elif blob_side_effect is not None:
            client.fetch_blob.return_value = blob_side_effect
        else:
            client.fetch_blob.return_value = None
        return client

    # -----------------------------------------------------------------------
    # Basic contract tests
    # -----------------------------------------------------------------------

    def test_returns_empty_list_when_no_events(self):
        """Returns empty list when no events in the session."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        result = extract_transcript(self._make_client([]), "sess1", "workspace1")
        assert result == []

    def test_returns_list(self):
        """Always returns a list."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        result = extract_transcript(self._make_client(), "sess1", "ws")
        assert isinstance(result, list)

    def test_single_cypher_call(self):
        """Internally makes exactly one cypher call (delegated to extract_events)."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = self._make_client()
        extract_transcript(client, "sess1", "ws")
        assert client.cypher.call_count == 1

    def test_uses_workspace_in_cypher_call(self):
        """Passes workspace to the underlying cypher call."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = self._make_client()
        extract_transcript(client, "sess1", "my_workspace")
        _, kwargs = client.cypher.call_args
        assert kwargs.get("workspace") == "my_workspace"

    # -----------------------------------------------------------------------
    # User messages from prompt:submit
    # -----------------------------------------------------------------------

    def test_returns_user_message_for_prompt_submit(self):
        """prompt:submit event produces a user message."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [
            self._make_event_row("prompt:submit", "2026-01-01T00:00:01Z", prompt="Hello, world!")
        ]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello, world!"

    def test_user_message_has_metadata_timestamp(self):
        """User messages include metadata.timestamp."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [self._make_event_row("prompt:submit", "2026-01-01T00:00:01Z", prompt="Hi")]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        assert result[0]["metadata"]["timestamp"] == "2026-01-01T00:00:01Z"

    def test_skips_prompt_submit_with_empty_text(self):
        """Skips prompt:submit events with empty prompt text."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [self._make_event_row("prompt:submit", "2026-01-01T00:00:01Z", prompt="")]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        assert result == []

    def test_skips_prompt_submit_with_no_prompt_field(self):
        """Skips prompt:submit events with no prompt field."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [self._make_event_row("prompt:submit", "2026-01-01T00:00:01Z")]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        assert result == []

    # -----------------------------------------------------------------------
    # Assistant messages from llm:response
    # -----------------------------------------------------------------------

    def test_returns_assistant_message_for_llm_response(self):
        """llm:response with a resolved raw blob produces an assistant message."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw = {"content": [{"type": "text", "text": "I will help you."}]}
        rows = [
            self._make_event_row(
                "llm:response",
                "2026-01-01T00:00:02Z",
                raw={"$blob_ref": "ci-blob://sess1/resp1"},
            )
        ]
        client = self._make_client(rows, blob_side_effect=lambda s, k: raw)
        result = extract_transcript(client, "sess1", "ws")
        assert any(m["role"] == "assistant" for m in result)

    def test_assistant_message_has_text_content_block(self):
        """Assistant message content blocks include text blocks."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw = {"content": [{"type": "text", "text": "Here is the answer."}]}
        rows = [
            self._make_event_row(
                "llm:response",
                "2026-01-01T00:00:02Z",
                raw={"$blob_ref": "ci-blob://sess1/resp1"},
            )
        ]
        client = self._make_client(rows, blob_side_effect=lambda s, k: raw)
        result = extract_transcript(client, "sess1", "ws")
        asst = next(m for m in result if m["role"] == "assistant")
        text_blocks = [
            b for b in asst["content"] if isinstance(b, dict) and b.get("type") == "text"
        ]
        assert any(b["text"] == "Here is the answer." for b in text_blocks)

    def test_assistant_message_has_tool_calls_when_tool_use_present(self):
        """tool_use blocks are extracted into tool_calls on the assistant message."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw = {
            "content": [
                {"type": "text", "text": "Running bash"},
                {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"cmd": "ls"}},
            ]
        }
        rows = [
            self._make_event_row(
                "llm:response",
                "2026-01-01T00:00:02Z",
                raw={"$blob_ref": "ci-blob://sess1/resp1"},
            )
        ]
        client = self._make_client(rows, blob_side_effect=lambda s, k: raw)
        result = extract_transcript(client, "sess1", "ws")
        asst = next(m for m in result if m["role"] == "assistant")
        assert "tool_calls" in asst
        assert asst["tool_calls"][0]["tool"] == "bash"
        assert asst["tool_calls"][0]["arguments"] == {"cmd": "ls"}

    def test_llm_response_without_blob_ref_is_skipped(self):
        """llm:response with no raw content produces no assistant message."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [self._make_event_row("llm:response", "2026-01-01T00:00:02Z")]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        assert not any(m["role"] == "assistant" for m in result)

    # -----------------------------------------------------------------------
    # Tool result messages from tool:post
    # -----------------------------------------------------------------------

    def test_returns_tool_message_for_tool_post(self):
        """tool:post event produces a tool result message."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [
            self._make_event_row(
                "tool:post",
                "2026-01-01T00:00:03Z",
                tool_name="bash",
                tool_call_id="tc_1",
                result={"$blob_ref": "ci-blob://sess1/res1"},
            )
        ]
        client = self._make_client(rows, blob_side_effect=lambda s, k: "file list output")
        result = extract_transcript(client, "sess1", "ws")
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["name"] == "bash"
        assert tool_msgs[0]["tool_call_id"] == "tc_1"

    def test_tool_message_content_from_resolved_result(self):
        """Tool message content is the resolved result blob."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [
            self._make_event_row(
                "tool:post",
                "2026-01-01T00:00:03Z",
                tool_name="bash",
                tool_call_id="tc_1",
                result="direct string result",
            )
        ]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert "direct string result" in tool_msgs[0]["content"]

    # -----------------------------------------------------------------------
    # Message ordering
    # -----------------------------------------------------------------------

    def test_messages_in_event_timestamp_order(self):
        """Messages appear in chronological order of their source events."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw = {"content": [{"type": "text", "text": "OK"}]}
        rows = [
            self._make_event_row("session:start", "2026-01-01T00:00:00Z"),
            self._make_event_row("prompt:submit", "2026-01-01T00:01:00Z", prompt="First"),
            self._make_event_row(
                "llm:response",
                "2026-01-01T00:02:00Z",
                raw={"$blob_ref": "ci-blob://sess1/r1"},
            ),
            self._make_event_row(
                "tool:post",
                "2026-01-01T00:03:00Z",
                tool_name="bash",
                tool_call_id="tc1",
                result="output",
            ),
            self._make_event_row("prompt:submit", "2026-01-01T00:04:00Z", prompt="Second"),
        ]
        client = self._make_client(rows, blob_side_effect=lambda s, k: raw)
        result = extract_transcript(client, "sess1", "ws")
        roles = [m["role"] for m in result]
        assert roles == ["user", "assistant", "tool", "user"]

    # -----------------------------------------------------------------------
    # Non-message events are ignored
    # -----------------------------------------------------------------------

    def test_session_start_end_events_ignored(self):
        """session:start and session:end events produce no transcript messages."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [
            self._make_event_row("session:start", "2026-01-01T00:00:00Z"),
            self._make_event_row("session:end", "2026-01-01T00:05:00Z"),
        ]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        assert result == []

    def test_execution_events_ignored(self):
        """execution:start / execution:end / orchestrator:complete produce no messages."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [
            self._make_event_row("execution:start", "2026-01-01T00:01:00Z"),
            self._make_event_row("execution:end", "2026-01-01T00:02:00Z"),
            self._make_event_row("orchestrator:complete", "2026-01-01T00:02:01Z"),
        ]
        result = extract_transcript(self._make_client(rows), "sess1", "ws")
        assert result == []

    # -----------------------------------------------------------------------
    # Blob resolution
    # -----------------------------------------------------------------------

    def test_blob_ref_resolved_for_llm_response(self):
        """fetch_blob is called for $blob_ref in llm:response.data.raw."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw = {"content": [{"type": "text", "text": "answer"}]}
        rows = [
            self._make_event_row(
                "llm:response",
                "2026-01-01T00:02:00Z",
                raw={"$blob_ref": "ci-blob://sess1/resp_key"},
            )
        ]
        client = self._make_client(rows, blob_side_effect=lambda s, k: raw)
        extract_transcript(client, "sess1", "ws")
        client.fetch_blob.assert_called_with("sess1", "resp_key")

    def test_blob_ref_resolved_for_tool_post(self):
        """fetch_blob is called for $blob_ref in tool:post.data.result."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        rows = [
            self._make_event_row(
                "tool:post",
                "2026-01-01T00:03:00Z",
                tool_name="bash",
                tool_call_id="tc1",
                result={"$blob_ref": "ci-blob://sess1/result_key"},
            )
        ]
        client = self._make_client(rows, blob_side_effect=lambda s, k: "output data")
        extract_transcript(client, "sess1", "ws")
        client.fetch_blob.assert_called_with("sess1", "result_key")

    # -----------------------------------------------------------------------
    # Round-trip: synthetic session
    # -----------------------------------------------------------------------

    def test_synthetic_session_round_trip(self):
        """Full session: user + assistant (with tool_call) + tool result."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw_response = {
            "content": [
                {"type": "text", "text": "Let me run that."},
                {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"cmd": "ls"}},
            ]
        }
        tool_result = "file1.py\nfile2.py"

        rows = [
            self._make_event_row("session:start", "2026-01-01T00:00:00Z"),
            self._make_event_row("prompt:submit", "2026-01-01T00:01:00Z", prompt="List files"),
            self._make_event_row(
                "llm:response",
                "2026-01-01T00:02:00Z",
                raw={"$blob_ref": "ci-blob://sess1/resp"},
            ),
            self._make_event_row(
                "tool:post",
                "2026-01-01T00:03:00Z",
                tool_name="bash",
                tool_call_id="toolu_1",
                result={"$blob_ref": "ci-blob://sess1/res"},
            ),
            self._make_event_row("session:end", "2026-01-01T00:04:00Z"),
        ]

        def blob_side_effect(s, k):
            return {"resp": raw_response, "res": tool_result}.get(k)

        client = self._make_client(rows, blob_side_effect=blob_side_effect)
        result = extract_transcript(client, "sess1", "ws")

        # 3 messages: user, assistant, tool
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "List files"
        assert result[1]["role"] == "assistant"
        assert "tool_calls" in result[1]
        assert result[1]["tool_calls"][0]["tool"] == "bash"
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "toolu_1"
        assert result[2]["name"] == "bash"
        assert "file1.py" in result[2]["content"]
