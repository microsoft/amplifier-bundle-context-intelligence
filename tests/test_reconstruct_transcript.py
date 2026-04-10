"""Tests for context_intelligence.reconstruct.transcript (task-6).

Covers:
- Module imports correctly (extract_transcript, _make_assistant_content)
- _extract_content_blocks() extracts content blocks from Anthropic response
- _content_blocks_to_tool_calls() converts to {id, tool, arguments} format
- _make_assistant_content() renames tool_use->tool_call, strips caller, adds visibility to thinking
- _stringify_tool_result() converts various types to string
- _resolve_blob_ref() resolves $blob_ref with cache (Level 2)
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

    def test_resolve_blob_ref_import(self):
        """_resolve_blob_ref must be importable."""
        from context_intelligence.reconstruct.transcript import _resolve_blob_ref  # noqa: F401


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


class TestResolveBlobRef:
    """_resolve_blob_ref() tests."""

    def test_returns_value_when_no_blob_ref(self):
        """Should return value unchanged when no $blob_ref present."""
        from context_intelligence.reconstruct.transcript import _resolve_blob_ref

        client = MagicMock()
        data = {"result": "plain_value"}
        result = _resolve_blob_ref(data, "result", {}, client, "sess1")
        assert result == "plain_value"

    def test_resolves_from_blob_index_cache(self):
        """Should use blob_index cache without calling client.fetch_blob."""
        from context_intelligence.reconstruct.transcript import _resolve_blob_ref

        client = MagicMock()
        blob_index = {"mykey": {"resolved": "data"}}
        data = {"result": {"$blob_ref": "ci-blob://sess1/mykey"}}
        result = _resolve_blob_ref(data, "result", blob_index, client, "sess1")
        assert result == {"resolved": "data"}
        client.fetch_blob.assert_not_called()

    def test_fetches_blob_from_client_when_not_cached(self):
        """Should call client.fetch_blob when key not in cache."""
        from context_intelligence.reconstruct.transcript import _resolve_blob_ref

        client = MagicMock()
        client.fetch_blob.return_value = {"fetched": "blob_data"}
        blob_index: dict = {}
        data = {"result": {"$blob_ref": "ci-blob://sess1/somekey"}}
        result = _resolve_blob_ref(data, "result", blob_index, client, "sess1")
        assert result == {"fetched": "blob_data"}
        client.fetch_blob.assert_called_once_with("sess1", "somekey")

    def test_caches_fetched_blob(self):
        """Should cache fetched blob in blob_index."""
        from context_intelligence.reconstruct.transcript import _resolve_blob_ref

        client = MagicMock()
        client.fetch_blob.return_value = {"fetched": "data"}
        blob_index: dict = {}
        data = {"result": {"$blob_ref": "ci-blob://sess1/newkey"}}
        _resolve_blob_ref(data, "result", blob_index, client, "sess1")
        assert "newkey" in blob_index
        assert blob_index["newkey"] == {"fetched": "data"}

    def test_returns_original_when_fetch_fails(self):
        """Should return original value when fetch_blob returns None."""
        from context_intelligence.reconstruct.transcript import _resolve_blob_ref

        client = MagicMock()
        client.fetch_blob.return_value = None
        blob_ref = {"$blob_ref": "ci-blob://sess1/badkey"}
        data = {"result": blob_ref}
        result = _resolve_blob_ref(data, "result", {}, client, "sess1")
        # Returns the original blob_ref value when fetch fails
        assert result == blob_ref

    def test_handles_missing_field(self):
        """Should return None when field not present in data."""
        from context_intelligence.reconstruct.transcript import _resolve_blob_ref

        client = MagicMock()
        data = {}
        result = _resolve_blob_ref(data, "nonexistent", {}, client, "sess1")
        assert result is None


class TestExtractTranscript:
    """extract_transcript() tests."""

    def _make_client(
        self,
        runs=None,
        prompts=None,
        steps=None,
        tools=None,
        blob_keys=None,
    ) -> MagicMock:
        """Build a mock CIClient with predictable return values."""
        client = MagicMock()
        client.list_blob_keys.return_value = blob_keys or set()

        def cypher_side_effect(query, workspace):
            if "OrchestratorRun" in query and "RETURN r.node_id" in query:
                return runs or []
            elif "PromptStep" in query:
                return prompts or []
            # TRIGGERED query mentions both AssistantStep and ToolExecution; check ToolExecution first
            elif "TRIGGERED" in query and "ToolExecution" in query:
                return tools or []
            elif "AssistantStep" in query:
                return steps or []
            return []

        client.cypher.side_effect = cypher_side_effect
        return client

    def test_returns_empty_list_when_no_runs(self):
        """Should return empty list when session has no OrchestratorRuns."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = self._make_client(runs=[])
        result = extract_transcript(client, "sess1", "workspace1")
        assert result == []

    def test_returns_user_message_for_prompt(self):
        """Should return a user message for each PromptStep."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = self._make_client(
            runs=[{"r.node_id": "run1", "r.started_at": "2026-01-01T00:00:00Z"}],
            prompts=[
                {
                    "p.prompt_text": "Hello, world!",
                    "p.occurred_at": "2026-01-01T00:00:01Z",
                }
            ],
            steps=[],
            tools=[],
        )
        result = extract_transcript(client, "sess1", "workspace1")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello, world!"

    def test_user_message_has_metadata_timestamp(self):
        """User messages should include metadata.timestamp."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = self._make_client(
            runs=[{"r.node_id": "run1", "r.started_at": "2026-01-01"}],
            prompts=[{"p.prompt_text": "Hello", "p.occurred_at": "2026-01-01T00:00:01Z"}],
            steps=[],
        )
        result = extract_transcript(client, "sess1", "workspace1")
        assert result[0]["metadata"]["timestamp"] == "2026-01-01T00:00:01Z"

    def test_skips_empty_prompt_text(self):
        """Should skip prompts with empty/falsy text."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = self._make_client(
            runs=[{"r.node_id": "run1", "r.started_at": "t"}],
            prompts=[{"p.prompt_text": "", "p.occurred_at": "t"}],
            steps=[],
        )
        result = extract_transcript(client, "sess1", "workspace1")
        assert result == []

    def test_returns_assistant_message_for_step_with_content(self):
        """Should return assistant message for AssistantStep with content blocks."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw_response = {"raw": {"content": [{"type": "text", "text": "I will help you."}]}}
        llm_resp_data = json.dumps(raw_response)

        client = MagicMock()
        client.list_blob_keys.return_value = set()

        def cypher_side_effect(query, workspace):
            if "OrchestratorRun" in query and "RETURN r.node_id" in query:
                return [{"r.node_id": "run1", "r.started_at": "t"}]
            elif "PromptStep" in query and "RETURN p.prompt_text" in query:
                return []
            elif "AssistantStep" in query:
                return [
                    {
                        "a.node_id": "step1",
                        "a.iteration": 1,
                        "a.response_at": "2026-01-01T00:00:02Z",
                        "a.data_llm_response": llm_resp_data,
                    }
                ]
            elif "ToolExecution" in query:
                return []
            return []

        client.cypher.side_effect = cypher_side_effect
        result = extract_transcript(client, "sess1", "workspace1")
        assert any(m["role"] == "assistant" for m in result)

    def test_deduplicates_assistant_steps(self):
        """Should deduplicate AssistantSteps by node_id."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw = {"raw": {"content": [{"type": "text", "text": "Hi"}]}}
        llm_resp = json.dumps(raw)

        client = MagicMock()
        client.list_blob_keys.return_value = set()

        def cypher_side_effect(query, workspace):
            if "OrchestratorRun" in query and "RETURN r.node_id" in query:
                return [{"r.node_id": "run1", "r.started_at": "t"}]
            elif "PromptStep" in query and "RETURN p.prompt_text" in query:
                return []
            elif "AssistantStep" in query:
                # Duplicate step_id
                return [
                    {
                        "a.node_id": "step1",
                        "a.iteration": 1,
                        "a.response_at": "t",
                        "a.data_llm_response": llm_resp,
                    },
                    {
                        "a.node_id": "step1",
                        "a.iteration": 1,
                        "a.response_at": "t",
                        "a.data_llm_response": llm_resp,
                    },
                ]
            elif "ToolExecution" in query:
                return []
            return []

        client.cypher.side_effect = cypher_side_effect
        result = extract_transcript(client, "sess1", "workspace1")
        assistant_msgs = [m for m in result if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1

    def test_returns_tool_message_for_tool_execution(self):
        """Should return tool message for each ToolExecution."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        tool_post_data = json.dumps({"result": "file list output"})
        llm_resp = json.dumps({"raw": {"content": [{"type": "text", "text": "ok"}]}})

        client = MagicMock()
        client.list_blob_keys.return_value = set()

        def cypher_side_effect(query, workspace):
            if "OrchestratorRun" in query and "RETURN r.node_id" in query:
                return [{"r.node_id": "run1", "r.started_at": "t"}]
            elif "PromptStep" in query and "RETURN p.prompt_text" in query:
                return []
            # TRIGGERED query contains both AssistantStep and ToolExecution; check ToolExecution first
            elif "TRIGGERED" in query and "ToolExecution" in query:
                return [
                    {
                        "t.tool_name": "bash",
                        "t.tool_call_id": "tc_1",
                        "t.ended_at": "t",
                        "t.data_tool_post": tool_post_data,
                    }
                ]
            elif "AssistantStep" in query:
                return [
                    {
                        "a.node_id": "step1",
                        "a.iteration": 1,
                        "a.response_at": "t",
                        "a.data_llm_response": llm_resp,
                    }
                ]
            return []

        client.cypher.side_effect = cypher_side_effect
        result = extract_transcript(client, "sess1", "workspace1")
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["name"] == "bash"
        assert tool_msgs[0]["tool_call_id"] == "tc_1"

    def test_queries_runs_ordered_by_started_at(self):
        """Should query OrchestratorRuns ordered by started_at."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = MagicMock()
        client.list_blob_keys.return_value = set()
        client.cypher.return_value = []
        extract_transcript(client, "sess1", "ws1")

        # Verify the first cypher call queries for runs ordered by started_at
        calls = client.cypher.call_args_list
        run_query_calls = [
            c for c in calls if "OrchestratorRun" in c[0][0] and "ORDER BY r.started_at" in c[0][0]
        ]
        assert len(run_query_calls) >= 1

    def test_queries_assistant_steps_ordered_by_iteration(self):
        """Should query AssistantSteps ordered by iteration."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = MagicMock()
        client.list_blob_keys.return_value = set()

        def cypher_side_effect(query, workspace):
            if "OrchestratorRun" in query and "RETURN r.node_id" in query:
                return [{"r.node_id": "run1", "r.started_at": "t"}]
            return []

        client.cypher.side_effect = cypher_side_effect
        extract_transcript(client, "sess1", "ws1")

        calls = client.cypher.call_args_list
        step_query_calls = [
            c for c in calls if "AssistantStep" in c[0][0] and "ORDER BY a.iteration" in c[0][0]
        ]
        assert len(step_query_calls) >= 1

    def test_assistant_message_has_tool_calls_when_tool_use_present(self):
        """Should include tool_calls in assistant message when content has tool_use."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        raw = {
            "raw": {
                "content": [
                    {"type": "text", "text": "Running bash"},
                    {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"cmd": "ls"}},
                ]
            }
        }
        llm_resp = json.dumps(raw)

        client = MagicMock()
        client.list_blob_keys.return_value = set()

        def cypher_side_effect(query, workspace):
            if "OrchestratorRun" in query and "RETURN r.node_id" in query:
                return [{"r.node_id": "run1", "r.started_at": "t"}]
            elif "PromptStep" in query and "RETURN p.prompt_text" in query:
                return []
            elif "AssistantStep" in query:
                return [
                    {
                        "a.node_id": "step1",
                        "a.iteration": 1,
                        "a.response_at": "t",
                        "a.data_llm_response": llm_resp,
                    }
                ]
            elif "ToolExecution" in query:
                return []
            return []

        client.cypher.side_effect = cypher_side_effect
        result = extract_transcript(client, "sess1", "ws1")
        assistant_msgs = [m for m in result if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "tool_calls" in assistant_msgs[0]
        assert assistant_msgs[0]["tool_calls"][0]["tool"] == "bash"
        assert assistant_msgs[0]["tool_calls"][0]["arguments"] == {"cmd": "ls"}

    def test_uses_workspace_in_all_queries(self):
        """Should pass workspace to all cypher queries."""
        from context_intelligence.reconstruct.transcript import extract_transcript

        client = MagicMock()
        client.list_blob_keys.return_value = set()
        client.cypher.return_value = []
        extract_transcript(client, "sess1", "my_workspace")

        # Every cypher call should use "my_workspace"
        for c in client.cypher.call_args_list:
            assert c[1].get("workspace") == "my_workspace" or c[0][1] == "my_workspace"
