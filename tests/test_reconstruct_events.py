"""Tests for context_intelligence.reconstruct.events (task-5).

Covers:
- Module imports correctly (extract_events, _make_event_line)
- _TS_NANO_RE regex exists and matches nanosecond timestamps
- _normalize_ts() truncates nanosecond to millisecond precision
- _make_event_line() builds proper events.jsonl lines with correct field ordering
- _make_event_line() handles missing/None/empty data_json_str gracefully
- _resolve_event_blobs() resolves $blob_ref values in-place for llm:request/llm:response/tool:post
- _resolve_event_blobs() uses blob caching (fetch called once per unique key)
- extract_events() queries all 7+ graph node types via cypher
- extract_events() returns events sorted by timestamp
- extract_events() optionally resolves blobs when resolve_blobs=True
- Imports: CIClient and _safe_json_loads from client, LOG_SCHEMA from config
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock


class TestImport:
    """Module must be importable with the required public API."""

    def test_extract_events_import(self):
        """extract_events must be importable from context_intelligence.reconstruct.events."""
        from context_intelligence.reconstruct.events import extract_events  # noqa: F401

    def test_make_event_line_import(self):
        """_make_event_line must be importable from context_intelligence.reconstruct.events."""
        from context_intelligence.reconstruct.events import _make_event_line  # noqa: F401

    def test_acceptance_criteria_command(self):
        """Simulate the acceptance criteria import command."""
        from context_intelligence.reconstruct.events import _make_event_line, extract_events

        assert extract_events is not None
        assert _make_event_line is not None

    def test_ts_nano_re_import(self):
        """_TS_NANO_RE must be importable."""
        from context_intelligence.reconstruct.events import _TS_NANO_RE  # noqa: F401

    def test_normalize_ts_import(self):
        """_normalize_ts must be importable."""
        from context_intelligence.reconstruct.events import _normalize_ts  # noqa: F401

    def test_resolve_event_blobs_import(self):
        """_resolve_event_blobs must be importable."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs  # noqa: F401

    def test_imports_ciclient_from_client(self):
        """Module must import CIClient from context_intelligence.client."""
        import inspect

        import context_intelligence.reconstruct.events as events_module

        # The source should reference CIClient (imported from client)
        source = inspect.getsource(events_module)
        assert "CIClient" in source

    def test_imports_safe_json_loads_from_client(self):
        """Module must import _safe_json_loads from context_intelligence.client."""
        import inspect

        import context_intelligence.reconstruct.events as events_module

        source = inspect.getsource(events_module)
        assert "_safe_json_loads" in source

    def test_imports_log_schema_from_config(self):
        """Module must import LOG_SCHEMA from context_intelligence.config."""
        import inspect

        import context_intelligence.reconstruct.events as events_module

        source = inspect.getsource(events_module)
        assert "LOG_SCHEMA" in source


class TestTsNanoRe:
    """_TS_NANO_RE must match nanosecond timestamps."""

    def test_matches_nanosecond_timestamp(self):
        """Nanosecond timestamp (9 decimal digits) should match."""
        from context_intelligence.reconstruct.events import _TS_NANO_RE

        ts = "2026-04-10T13:41:17.111671945+00:00"
        m = _TS_NANO_RE.match(ts)
        assert m is not None

    def test_captures_millisecond_prefix(self):
        """Match group(1) should be the millisecond part."""
        from context_intelligence.reconstruct.events import _TS_NANO_RE

        ts = "2026-04-10T13:41:17.111671945+00:00"
        m = _TS_NANO_RE.match(ts)
        assert m is not None
        assert m.group(1) == "2026-04-10T13:41:17.111"

    def test_captures_timezone_suffix(self):
        """Match group(2) should be the timezone suffix."""
        from context_intelligence.reconstruct.events import _TS_NANO_RE

        ts = "2026-04-10T13:41:17.111671945+00:00"
        m = _TS_NANO_RE.match(ts)
        assert m is not None
        assert m.group(2) == "+00:00"

    def test_no_match_for_millisecond_only(self):
        """A millisecond-precision timestamp should NOT match the nano regex."""
        from context_intelligence.reconstruct.events import _TS_NANO_RE

        ts = "2026-04-10T13:41:17.111+00:00"
        m = _TS_NANO_RE.match(ts)
        # millisecond only - no extra digits after 3
        assert m is None


class TestNormalizeTs:
    """_normalize_ts() must normalize nanosecond timestamps to millisecond precision."""

    def test_normalizes_nanosecond_timestamp(self):
        """Nanosecond timestamp is truncated to milliseconds."""
        from context_intelligence.reconstruct.events import _normalize_ts

        ts = "2026-04-10T13:41:17.111671945+00:00"
        result = _normalize_ts(ts)
        assert result == "2026-04-10T13:41:17.111+00:00"

    def test_passthrough_millisecond_timestamp(self):
        """Millisecond timestamp passes through unchanged."""
        from context_intelligence.reconstruct.events import _normalize_ts

        ts = "2026-04-10T13:41:17.111+00:00"
        result = _normalize_ts(ts)
        assert result == ts

    def test_passthrough_empty_string(self):
        """Empty string is returned as-is."""
        from context_intelligence.reconstruct.events import _normalize_ts

        result = _normalize_ts("")
        assert result == ""

    def test_passthrough_none(self):
        """None is returned as-is."""
        from context_intelligence.reconstruct.events import _normalize_ts

        result = _normalize_ts(None)  # type: ignore[arg-type]
        assert result is None

    def test_passthrough_non_string(self):
        """Non-string values are returned as-is."""
        from context_intelligence.reconstruct.events import _normalize_ts

        result = _normalize_ts(42)  # type: ignore[arg-type]
        assert result == 42


class TestMakeEventLine:
    """_make_event_line() must build valid events.jsonl lines."""

    def _make_data_str(self, **kwargs) -> str:
        """Helper to create a JSON data string."""
        data = {
            "timestamp": "2026-04-10T13:41:17.111671945+00:00",
            "session_id": "test-session-123",
        }
        data.update(kwargs)
        return json.dumps(data)

    def test_returns_dict_for_valid_input(self):
        """Returns a dict for valid event_type and data_json_str."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert isinstance(result, dict)

    def test_returns_none_for_none_data(self):
        """Returns None when data_json_str is None."""
        from context_intelligence.reconstruct.events import _make_event_line

        result = _make_event_line("session:start", None, "test-session-123")
        assert result is None

    def test_returns_none_for_empty_string(self):
        """Returns None when data_json_str is empty string."""
        from context_intelligence.reconstruct.events import _make_event_line

        result = _make_event_line("session:start", "", "test-session-123")
        assert result is None

    def test_returns_none_for_invalid_json(self):
        """Returns None when data_json_str is not a dict-shaped JSON."""
        from context_intelligence.reconstruct.events import _make_event_line

        result = _make_event_line("session:start", "[1, 2, 3]", "test-session-123")
        assert result is None

    def test_field_ordering_ts_first(self):
        """Field ordering: ts must be the first key."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        keys = list(result.keys())
        assert keys[0] == "ts"

    def test_field_ordering_lvl_second(self):
        """Field ordering: lvl must be the second key."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        keys = list(result.keys())
        assert keys[1] == "lvl"

    def test_field_ordering_schema_third(self):
        """Field ordering: schema must be the third key."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        keys = list(result.keys())
        assert keys[2] == "schema"

    def test_field_ordering_event_fourth(self):
        """Field ordering: event must be the fourth key."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        keys = list(result.keys())
        assert keys[3] == "event"

    def test_field_ordering_session_id_fifth(self):
        """Field ordering: session_id must be the fifth key."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        keys = list(result.keys())
        assert keys[4] == "session_id"

    def test_field_ordering_data_last(self):
        """Field ordering: data must be the last key (when no redaction)."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        keys = list(result.keys())
        assert keys[-1] == "data"

    def test_ts_normalized_to_milliseconds(self):
        """ts field is normalized to millisecond precision."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str(timestamp="2026-04-10T13:41:17.111671945+00:00")
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert result["ts"] == "2026-04-10T13:41:17.111+00:00"

    def test_lvl_is_info(self):
        """lvl field is always 'INFO'."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert result["lvl"] == "INFO"

    def test_schema_is_log_schema(self):
        """schema field matches LOG_SCHEMA from config."""
        from context_intelligence.config import LOG_SCHEMA
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert result["schema"] == LOG_SCHEMA

    def test_event_type_preserved(self):
        """event field contains the passed event_type."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("tool:pre", data_str, "test-session-123")
        assert result is not None
        assert result["event"] == "tool:pre"

    def test_session_id_from_data(self):
        """session_id is taken from data, not the fallback argument."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str(session_id="data-session-456")
        result = _make_event_line("session:start", data_str, "fallback-session")
        assert result is not None
        assert result["session_id"] == "data-session-456"

    def test_session_id_fallback(self):
        """session_id falls back to the argument when not in data."""
        from context_intelligence.reconstruct.events import _make_event_line

        data = {"timestamp": "2026-04-10T13:41:17.111+00:00", "some_field": "val"}
        data_str = json.dumps(data)
        result = _make_event_line("session:start", data_str, "fallback-session")
        assert result is not None
        assert result["session_id"] == "fallback-session"

    def test_redaction_field_included_when_present(self):
        """redaction field appears in result when present in data."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str(redaction="PARTIAL")
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert "redaction" in result
        assert result["redaction"] == "PARTIAL"

    def test_redaction_field_ordering_before_data(self):
        """redaction field appears before data."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str(redaction="PARTIAL")
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        keys = list(result.keys())
        assert keys.index("redaction") < keys.index("data")

    def test_redaction_not_in_data_dict(self):
        """redaction is popped from data dict, not duplicated inside."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str(redaction="PARTIAL")
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert "redaction" not in result["data"]

    def test_timestamp_not_in_data_dict(self):
        """timestamp is popped from data dict (moved to ts top-level)."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert "timestamp" not in result["data"]

    def test_session_id_not_in_data_dict(self):
        """session_id is popped from data dict (moved to session_id top-level)."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str(session_id="test-session-123")
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert "session_id" not in result["data"]

    def test_redaction_absent_when_not_in_data(self):
        """redaction key is absent from result when not in data."""
        from context_intelligence.reconstruct.events import _make_event_line

        data_str = self._make_data_str()
        result = _make_event_line("session:start", data_str, "test-session-123")
        assert result is not None
        assert "redaction" not in result


class TestResolveEventBlobs:
    """_resolve_event_blobs() must resolve $blob_ref values in-place."""

    def _make_event(self, event_type: str, field: str, value: object) -> dict:
        """Helper to create a mock event with a specific field value."""
        return {"event": event_type, "session_id": "sess1", "data": {field: value}}

    def test_resolves_llm_request_raw(self):
        """Resolves data.raw for llm:request events."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"messages": [{"role": "user", "content": "hello"}]}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        events = [self._make_event("llm:request", "raw", {"$blob_ref": "ci-blob://sess1/key1"})]

        _resolve_event_blobs(events, mock_client, "sess1")

        assert events[0]["data"]["raw"] == blob_content
        mock_client.fetch_blob.assert_called_once_with("sess1", "key1")

    def test_resolves_llm_response_raw(self):
        """Resolves data.raw for llm:response events."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"choices": [{"message": {"content": "response"}}]}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        events = [self._make_event("llm:response", "raw", {"$blob_ref": "ci-blob://sess1/key2"})]

        _resolve_event_blobs(events, mock_client, "sess1")

        assert events[0]["data"]["raw"] == blob_content

    def test_resolves_tool_post_result(self):
        """Resolves data.result for tool:post events."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"output": "tool result"}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        events = [self._make_event("tool:post", "result", {"$blob_ref": "ci-blob://sess1/key3"})]

        _resolve_event_blobs(events, mock_client, "sess1")

        assert events[0]["data"]["result"] == blob_content

    def test_skips_other_event_types(self):
        """Does not attempt blob resolution for other event types."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        mock_client = MagicMock()

        events = [self._make_event("session:start", "raw", {"$blob_ref": "ci-blob://sess1/key4"})]

        _resolve_event_blobs(events, mock_client, "sess1")

        mock_client.fetch_blob.assert_not_called()

    def test_skips_when_no_blob_ref(self):
        """Does not call fetch_blob when field has no $blob_ref."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        mock_client = MagicMock()

        events = [self._make_event("llm:request", "raw", {"messages": []})]

        _resolve_event_blobs(events, mock_client, "sess1")

        mock_client.fetch_blob.assert_not_called()

    def test_blob_caching_calls_fetch_once_per_unique_key(self):
        """fetch_blob is called only once per unique key (caching)."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"messages": []}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        blob_ref = {"$blob_ref": "ci-blob://sess1/same-key"}
        events = [
            self._make_event("llm:request", "raw", blob_ref),
            self._make_event("llm:response", "raw", blob_ref),
        ]

        _resolve_event_blobs(events, mock_client, "sess1")

        # Should only fetch once despite two events with the same blob ref
        mock_client.fetch_blob.assert_called_once()

    def test_skips_when_data_not_dict(self):
        """Gracefully skips events where data is not a dict."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        mock_client = MagicMock()

        events = [{"event": "llm:request", "session_id": "sess1", "data": "not-a-dict"}]

        _resolve_event_blobs(events, mock_client, "sess1")

        mock_client.fetch_blob.assert_not_called()


class TestExtractEvents:
    """extract_events() must query all graph node types and return sorted events."""

    def _make_mock_client(self, cypher_returns: dict | None = None) -> MagicMock:
        """Create a mock CIClient.

        cypher_returns maps query substrings to list-of-dict return values.
        """
        mock_client = MagicMock()

        def cypher_side_effect(query: str, workspace: str = "*"):
            if cypher_returns:
                for key, value in cypher_returns.items():
                    if key in query:
                        return value
            return []

        mock_client.cypher.side_effect = cypher_side_effect
        return mock_client

    def _make_ts_data(self, ts: str, session_id: str = "sess-abc", **extra) -> str:
        data = {"timestamp": ts, "session_id": session_id, **extra}
        return json.dumps(data)

    def test_returns_list(self):
        """extract_events returns a list."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        result = extract_events(mock_client, "sess-abc", "workspace1")
        assert isinstance(result, list)

    def test_queries_session_node(self):
        """Queries Session node type."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        # Check that at least one cypher call references :Session
        calls = [str(c) for c in mock_client.cypher.call_args_list]
        assert any(":Session" in c or "Session" in c for c in calls)

    def test_queries_subsession_node(self):
        """Queries Subsession node type."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        calls = [str(c) for c in mock_client.cypher.call_args_list]
        assert any("Subsession" in c for c in calls)

    def test_queries_orchestrator_run_node(self):
        """Queries OrchestratorRun node type."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        calls = [str(c) for c in mock_client.cypher.call_args_list]
        assert any("OrchestratorRun" in c for c in calls)

    def test_queries_prompt_step_node(self):
        """Queries PromptStep node type."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        calls = [str(c) for c in mock_client.cypher.call_args_list]
        assert any("PromptStep" in c for c in calls)

    def test_queries_assistant_step_node(self):
        """Queries AssistantStep node type."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        calls = [str(c) for c in mock_client.cypher.call_args_list]
        assert any("AssistantStep" in c for c in calls)

    def test_queries_tool_execution_non_delegate(self):
        """Queries ToolExecution (non-delegate) node type."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        calls = [str(c) for c in mock_client.cypher.call_args_list]
        # Non-delegate: filters out delegate tool_name
        assert any("ToolExecution" in c and "delegate" in c for c in calls)

    def test_queries_event_node(self):
        """Queries Event node type."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        calls = [str(c) for c in mock_client.cypher.call_args_list]
        assert any(":Event" in c or "HAS_EVENT" in c for c in calls)

    def test_passes_workspace_to_cypher(self):
        """Passes the workspace argument to all cypher calls."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "my-workspace")

        for c in mock_client.cypher.call_args_list:
            # workspace is the second positional arg or keyword arg
            assert "my-workspace" in str(c)

    def test_returns_events_sorted_by_timestamp(self):
        """Events are sorted by timestamp ascending."""
        from context_intelligence.reconstruct.events import extract_events

        ts1 = "2026-04-10T10:00:00.000+00:00"
        ts2 = "2026-04-10T10:01:00.000+00:00"
        ts3 = "2026-04-10T10:02:00.000+00:00"

        # Return session:start event first, then session:end, in reversed order
        data1 = json.dumps({"timestamp": ts3, "session_id": "sess-abc"})
        data2 = json.dumps({"timestamp": ts1, "session_id": "sess-abc"})
        data3 = json.dumps({"timestamp": ts2, "session_id": "sess-abc"})

        # Return events in non-sorted order from Session query
        cypher_returns = {
            "Session": [
                {"s.data": data1, "s.data_session_end": data2},
            ],
            "Subsession": [],
            "OrchestratorRun": [],
            "PromptStep": [{"p.data": data3}],
            "AssistantStep": [],
            "ToolExecution": [],
            "HAS_EVENT": [],
        }

        mock_client = self._make_mock_client(cypher_returns)
        result = extract_events(mock_client, "sess-abc", "workspace1")

        # Should be sorted ascending
        timestamps = [e["ts"] for e in result]
        assert timestamps == sorted(timestamps)

    def test_empty_when_no_graph_data(self):
        """Returns empty list when all queries return empty results."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        result = extract_events(mock_client, "sess-abc", "workspace1")
        assert result == []

    def test_resolve_blobs_false_by_default(self):
        """resolve_blobs defaults to False, fetch_blob not called."""
        from context_intelligence.reconstruct.events import extract_events

        ts = "2026-04-10T10:00:00.000+00:00"
        data_str = json.dumps(
            {
                "timestamp": ts,
                "session_id": "sess-abc",
                "raw": {"$blob_ref": "ci-blob://sess-abc/k1"},
            }
        )
        cypher_returns = {
            "AssistantStep": [
                {
                    "a.data": None,
                    "a.data_llm_request": data_str,
                    "a.data_llm_response": None,
                }
            ]
        }

        mock_client = self._make_mock_client(cypher_returns)
        extract_events(mock_client, "sess-abc", "workspace1")

        mock_client.fetch_blob.assert_not_called()

    def test_resolve_blobs_true_calls_fetch_blob(self):
        """When resolve_blobs=True, blob refs are resolved."""
        from context_intelligence.reconstruct.events import extract_events

        ts = "2026-04-10T10:00:00.000+00:00"
        data_str = json.dumps(
            {
                "timestamp": ts,
                "session_id": "sess-abc",
                "raw": {"$blob_ref": "ci-blob://sess-abc/k1"},
            }
        )
        cypher_returns = {
            "AssistantStep": [
                {
                    "a.data": None,
                    "a.data_llm_request": data_str,
                    "a.data_llm_response": None,
                }
            ]
        }

        mock_client = self._make_mock_client(cypher_returns)
        mock_client.fetch_blob.return_value = {"messages": []}
        extract_events(mock_client, "sess-abc", "workspace1", resolve_blobs=True)

        mock_client.fetch_blob.assert_called()

    def test_at_least_seven_cypher_calls(self):
        """At least 7 Cypher queries (one per node type)."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "workspace1")

        # 7 node types: Session, Subsession, OrchestratorRun, PromptStep,
        # AssistantStep, ToolExecution non-delegate, ToolExecution delegate, Event
        assert mock_client.cypher.call_count >= 7
