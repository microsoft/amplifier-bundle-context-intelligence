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

    def test_resolve_blobs_in_value_import(self):
        """_resolve_blobs_in_value must be importable (shared generic helper)."""
        from context_intelligence.reconstruct.events import _resolve_blobs_in_value  # noqa: F401

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

        _resolve_event_blobs(events, mock_client)

        assert events[0]["data"]["raw"] == blob_content
        mock_client.fetch_blob.assert_called_once_with("sess1", "key1")

    def test_resolves_llm_response_raw(self):
        """Resolves data.raw for llm:response events."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"choices": [{"message": {"content": "response"}}]}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        events = [self._make_event("llm:response", "raw", {"$blob_ref": "ci-blob://sess1/key2"})]

        _resolve_event_blobs(events, mock_client)

        assert events[0]["data"]["raw"] == blob_content

    def test_resolves_tool_post_result(self):
        """Resolves data.result for tool:post events."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"output": "tool result"}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        events = [self._make_event("tool:post", "result", {"$blob_ref": "ci-blob://sess1/key3"})]

        _resolve_event_blobs(events, mock_client)

        assert events[0]["data"]["result"] == blob_content

    def test_resolves_all_event_types_generically(self):
        """Resolves $blob_ref for ANY event type, including session:start (no allow-list)."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"schema": "1.0", "tools": ["bash"]}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        # session:start.raw was the live bug: 10 unresolved markers on CI server
        events = [self._make_event("session:start", "raw", {"$blob_ref": "ci-blob://sess1/key4"})]

        _resolve_event_blobs(events, mock_client)

        assert events[0]["data"]["raw"] == blob_content
        mock_client.fetch_blob.assert_called_once_with("sess1", "key4")

    def test_resolves_deeply_nested_blob_ref_in_list(self):
        """Resolves a $blob_ref nested inside a list within data (generic recursion)."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = {"output": "nested result"}
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        events = [
            {
                "event": "some:event",
                "session_id": "sess1",
                "data": {
                    "items": [
                        {"x": 1},
                        {"$blob_ref": "ci-blob://sess1/nested_key"},
                    ]
                },
            }
        ]

        _resolve_event_blobs(events, mock_client)

        assert events[0]["data"]["items"][1] == blob_content
        mock_client.fetch_blob.assert_called_once_with("sess1", "nested_key")

    def test_resolves_deeply_nested_blob_ref_in_subdict(self):
        """Resolves a $blob_ref nested inside a sub-dict within data (generic recursion)."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        blob_content = "deep string value"
        mock_client = MagicMock()
        mock_client.fetch_blob.return_value = blob_content

        events = [
            {
                "event": "some:event",
                "session_id": "sess1",
                "data": {
                    "outer": {
                        "inner": {"$blob_ref": "ci-blob://sess1/deep_key"},
                    }
                },
            }
        ]

        _resolve_event_blobs(events, mock_client)

        assert events[0]["data"]["outer"]["inner"] == blob_content
        mock_client.fetch_blob.assert_called_once_with("sess1", "deep_key")

    def test_skips_when_no_blob_ref(self):
        """Does not call fetch_blob when field has no $blob_ref."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        mock_client = MagicMock()

        events = [self._make_event("llm:request", "raw", {"messages": []})]

        _resolve_event_blobs(events, mock_client)

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

        _resolve_event_blobs(events, mock_client)

        # Should only fetch once despite two events with the same blob ref
        mock_client.fetch_blob.assert_called_once()

    def test_skips_when_data_not_dict(self):
        """Gracefully skips events where data is not a dict."""
        from context_intelligence.reconstruct.events import _resolve_event_blobs

        mock_client = MagicMock()

        events = [{"event": "llm:request", "session_id": "sess1", "data": "not-a-dict"}]

        _resolve_event_blobs(events, mock_client)

        mock_client.fetch_blob.assert_not_called()


class TestExtractEvents:
    """extract_events() uses a single Event-node query — the live schema source of truth."""

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _make_event_row(
        self,
        event_name: str,
        ts: str,
        session_id: str = "sess-abc",
        **extra_data,
    ) -> dict:
        """Build one row as returned by the single-Event-node cypher query.

        data is a JSON string containing the inner event payload (timestamp +
        session_id + event-specific fields), matching the live graph schema.
        """
        data = {"timestamp": ts, "session_id": session_id, **extra_data}
        return {
            "event_name": event_name,
            "data": json.dumps(data),
            "occurred_at": ts,
        }

    def _make_mock_client(self, event_rows: list | None = None) -> MagicMock:
        """Build a mock CIClient whose cypher() returns event_rows."""
        mock_client = MagicMock()
        mock_client.cypher.return_value = event_rows if event_rows is not None else []
        mock_client.fetch_blob.return_value = None
        return mock_client

    # -----------------------------------------------------------------------
    # Basic contract tests
    # -----------------------------------------------------------------------

    def test_returns_list(self):
        """extract_events returns a list."""
        from context_intelligence.reconstruct.events import extract_events

        result = extract_events(self._make_mock_client(), "sess-abc", "ws")
        assert isinstance(result, list)

    def test_single_cypher_call(self):
        """Issues exactly one cypher call (single Event-node query)."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "ws")
        assert mock_client.cypher.call_count == 1

    def test_query_targets_event_node_by_session_id(self):
        """The single cypher query matches Event nodes on session_id."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "ws")
        query = mock_client.cypher.call_args[0][0]
        assert "Event" in query
        assert "session_id" in query
        assert "sess-abc" in query

    def test_passes_workspace_to_cypher(self):
        """Passes the workspace keyword argument to cypher."""
        from context_intelligence.reconstruct.events import extract_events

        mock_client = self._make_mock_client()
        extract_events(mock_client, "sess-abc", "my-workspace")
        _, kwargs = mock_client.cypher.call_args
        assert kwargs.get("workspace") == "my-workspace"

    def test_empty_when_no_events(self):
        """Returns empty list when no Event rows returned."""
        from context_intelligence.reconstruct.events import extract_events

        result = extract_events(self._make_mock_client([]), "sess-abc", "ws")
        assert result == []

    # -----------------------------------------------------------------------
    # Event-type coverage
    # -----------------------------------------------------------------------

    def test_returns_session_start_event(self):
        """session:start event is returned (attached via SOURCED_FROM, NOT HAS_EVENT)."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [self._make_event_row("session:start", "2026-04-10T10:00:00.000+00:00")]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert any(e["event"] == "session:start" for e in result)

    def test_returns_session_end_event(self):
        """session:end event is returned."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [self._make_event_row("session:end", "2026-04-10T11:00:00.000+00:00")]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert any(e["event"] == "session:end" for e in result)

    def test_returns_prompt_submit_event(self):
        """prompt:submit event is returned."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [
            self._make_event_row("prompt:submit", "2026-04-10T10:05:00.000+00:00", prompt="Hello")
        ]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert any(e["event"] == "prompt:submit" for e in result)

    def test_returns_llm_response_event(self):
        """llm:response event is returned."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [self._make_event_row("llm:response", "2026-04-10T10:10:00.000+00:00")]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert any(e["event"] == "llm:response" for e in result)

    def test_returns_tool_post_event(self):
        """tool:post event is returned."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [
            self._make_event_row(
                "tool:post",
                "2026-04-10T10:11:00.000+00:00",
                tool_name="bash",
                tool_call_id="tc1",
            )
        ]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert any(e["event"] == "tool:post" for e in result)

    def test_all_events_returned_including_session_events(self):
        """All 5 event types in the fixture are present (including session:start/end)."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [
            self._make_event_row("session:start", "2026-04-10T10:00:00.000+00:00"),
            self._make_event_row("prompt:submit", "2026-04-10T10:01:00.000+00:00", prompt="Hi"),
            self._make_event_row("llm:response", "2026-04-10T10:02:00.000+00:00"),
            self._make_event_row("tool:post", "2026-04-10T10:03:00.000+00:00"),
            self._make_event_row("session:end", "2026-04-10T10:04:00.000+00:00"),
        ]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        types = {e["event"] for e in result}
        assert types == {
            "session:start",
            "prompt:submit",
            "llm:response",
            "tool:post",
            "session:end",
        }

    # -----------------------------------------------------------------------
    # Ordering
    # -----------------------------------------------------------------------

    def test_events_sorted_by_timestamp_ascending(self):
        """Events are returned in ascending timestamp order."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [
            self._make_event_row("session:end", "2026-04-10T10:03:00.000+00:00"),
            self._make_event_row("prompt:submit", "2026-04-10T10:01:00.000+00:00"),
            self._make_event_row("session:start", "2026-04-10T10:00:00.000+00:00"),
            self._make_event_row("llm:response", "2026-04-10T10:02:00.000+00:00"),
        ]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        ts = [e["ts"] for e in result]
        assert ts == sorted(ts)

    # -----------------------------------------------------------------------
    # Envelope synthesis
    # -----------------------------------------------------------------------

    def test_synthesises_envelope_fields(self):
        """Each event has ts, lvl, schema, event, session_id, data."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [self._make_event_row("prompt:submit", "2026-04-10T10:00:00.000+00:00")]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert len(result) == 1
        ev = result[0]
        assert "ts" in ev
        assert "lvl" in ev
        assert "schema" in ev
        assert "event" in ev
        assert "session_id" in ev
        assert "data" in ev

    def test_data_field_is_dict_not_string(self):
        """data field is parsed into a dict, not left as a JSON string."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [self._make_event_row("prompt:submit", "2026-04-10T10:00:00.000+00:00", prompt="Hi")]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert isinstance(result[0]["data"], dict)

    def test_event_specific_data_preserved(self):
        """Event-specific data fields (e.g. prompt) are preserved in data."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [
            self._make_event_row(
                "prompt:submit", "2026-04-10T10:00:00.000+00:00", prompt="Hello world"
            )
        ]
        result = extract_events(self._make_mock_client(rows), "sess-abc", "ws")
        assert result[0]["data"]["prompt"] == "Hello world"

    # -----------------------------------------------------------------------
    # Blob resolution
    # -----------------------------------------------------------------------

    def test_resolve_blobs_false_by_default(self):
        """resolve_blobs=False (default) does not call fetch_blob."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [
            self._make_event_row(
                "llm:response",
                "2026-04-10T10:00:00.000+00:00",
                raw={"$blob_ref": "ci-blob://sess-abc/k1"},
            )
        ]
        mock_client = self._make_mock_client(rows)
        extract_events(mock_client, "sess-abc", "ws")
        mock_client.fetch_blob.assert_not_called()

    def test_resolve_blobs_true_resolves_llm_response_raw(self):
        """resolve_blobs=True fetches and substitutes the raw blob for llm:response."""
        from context_intelligence.reconstruct.events import extract_events

        resolved_content = {"content": [{"type": "text", "text": "Hello"}]}
        rows = [
            self._make_event_row(
                "llm:response",
                "2026-04-10T10:00:00.000+00:00",
                raw={"$blob_ref": "ci-blob://sess-abc/k1"},
            )
        ]
        mock_client = self._make_mock_client(rows)
        mock_client.fetch_blob.return_value = resolved_content

        result = extract_events(mock_client, "sess-abc", "ws", resolve_blobs=True)
        mock_client.fetch_blob.assert_called_once_with("sess-abc", "k1")
        assert result[0]["data"]["raw"] == resolved_content

    def test_resolve_blobs_true_resolves_tool_post_result(self):
        """resolve_blobs=True fetches and substitutes the result blob for tool:post."""
        from context_intelligence.reconstruct.events import extract_events

        resolved_result = "some tool output"
        rows = [
            self._make_event_row(
                "tool:post",
                "2026-04-10T10:00:00.000+00:00",
                tool_call_id="tc1",
                result={"$blob_ref": "ci-blob://sess-abc/k2"},
            )
        ]
        mock_client = self._make_mock_client(rows)
        mock_client.fetch_blob.return_value = resolved_result

        result = extract_events(mock_client, "sess-abc", "ws", resolve_blobs=True)
        mock_client.fetch_blob.assert_called_once_with("sess-abc", "k2")
        assert result[0]["data"]["result"] == resolved_result

    def test_resolve_blobs_caches_repeated_keys(self):
        """Same blob key is fetched only once even when referenced twice."""
        from context_intelligence.reconstruct.events import extract_events

        blob_data = {"content": [{"type": "text", "text": "Hi"}]}
        rows = [
            self._make_event_row(
                "llm:response",
                "2026-04-10T10:00:00.000+00:00",
                raw={"$blob_ref": "ci-blob://sess-abc/same_key"},
            ),
            self._make_event_row(
                "llm:response",
                "2026-04-10T10:01:00.000+00:00",
                raw={"$blob_ref": "ci-blob://sess-abc/same_key"},
            ),
        ]
        mock_client = self._make_mock_client(rows)
        mock_client.fetch_blob.return_value = blob_data

        extract_events(mock_client, "sess-abc", "ws", resolve_blobs=True)
        assert mock_client.fetch_blob.call_count == 1

    # -----------------------------------------------------------------------
    # Round-trip: synthetic session
    # -----------------------------------------------------------------------

    def test_synthetic_session_round_trip(self):
        """A complete synthetic session returns all event types in order."""
        from context_intelligence.reconstruct.events import extract_events

        blob_response = {"content": [{"type": "text", "text": "I can help."}]}
        blob_result = "file1.txt\nfile2.txt"

        rows = [
            self._make_event_row("session:start", "2026-04-10T10:00:00.000+00:00"),
            self._make_event_row(
                "prompt:submit", "2026-04-10T10:01:00.000+00:00", prompt="List files"
            ),
            self._make_event_row("execution:start", "2026-04-10T10:02:00.000+00:00"),
            self._make_event_row(
                "llm:response",
                "2026-04-10T10:03:00.000+00:00",
                raw={"$blob_ref": "ci-blob://sess-abc/resp_key"},
            ),
            self._make_event_row(
                "tool:post",
                "2026-04-10T10:04:00.000+00:00",
                tool_call_id="tc1",
                tool_name="bash",
                result={"$blob_ref": "ci-blob://sess-abc/result_key"},
            ),
            self._make_event_row("execution:end", "2026-04-10T10:05:00.000+00:00"),
            self._make_event_row("session:end", "2026-04-10T10:06:00.000+00:00"),
        ]

        mock_client = self._make_mock_client(rows)
        mock_client.fetch_blob.side_effect = lambda s, k: {
            "resp_key": blob_response,
            "result_key": blob_result,
        }.get(k)

        result = extract_events(mock_client, "sess-abc", "ws", resolve_blobs=True)

        # All 7 events present
        assert len(result) == 7
        # In time order
        ts = [e["ts"] for e in result]
        assert ts == sorted(ts)
        # Blobs resolved
        llm_ev = next(e for e in result if e["event"] == "llm:response")
        assert llm_ev["data"]["raw"] == blob_response
        tool_ev = next(e for e in result if e["event"] == "tool:post")
        assert tool_ev["data"]["result"] == blob_result
        # Session events present
        assert any(e["event"] == "session:start" for e in result)
        assert any(e["event"] == "session:end" for e in result)

    def test_resolve_blobs_true_resolves_session_start_raw(self):
        """extract_events with resolve_blobs=True resolves $blob_ref in session:start.data.raw."""
        from context_intelligence.reconstruct.events import extract_events

        resolved_schema = {"version": "1.0", "mount_plan": {"providers": []}}
        rows = [
            self._make_event_row(
                "session:start",
                "2026-04-10T10:00:00.000+00:00",
                raw={"$blob_ref": "ci-blob://sess-abc/session_raw_key"},
            )
        ]
        mock_client = self._make_mock_client(rows)
        mock_client.fetch_blob.return_value = resolved_schema

        result = extract_events(mock_client, "sess-abc", "ws", resolve_blobs=True)

        mock_client.fetch_blob.assert_called_once_with("sess-abc", "session_raw_key")
        assert result[0]["data"]["raw"] == resolved_schema

    def test_resolve_blobs_leaves_unresolvable_ref_in_place(self):
        """When fetch_blob returns None the original marker is left intact (fail-soft)."""
        from context_intelligence.reconstruct.events import extract_events

        rows = [
            self._make_event_row(
                "session:start",
                "2026-04-10T10:00:00.000+00:00",
                raw={"$blob_ref": "ci-blob://sess-abc/missing_key"},
            )
        ]
        mock_client = self._make_mock_client(rows)
        # fetch_blob already returns None by default in _make_mock_client

        result = extract_events(mock_client, "sess-abc", "ws", resolve_blobs=True)

        # Marker remains; no exception raised
        assert result[0]["data"]["raw"] == {"$blob_ref": "ci-blob://sess-abc/missing_key"}
