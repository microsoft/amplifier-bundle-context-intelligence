"""Tests for blob_processor module.

16 tests covering:
- BLOB_FIELDS constant (expected set, is frozenset)
- clone immutability (original dict unchanged, nested dicts unchanged — CRITICAL)
- blob ref substitution (single field replaced, multiple fields replaced,
  URI contains node_id and field)
- non-blob fields pass through (small fields unchanged, no fields removed)
- None value handling (None blob field not processed, missing field not added)
- error handling (write failure produces $blob_error, failure doesn't block other fields)
- return value (new dict not same object, data without blob fields returns identical clone)
"""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.blob_processor import (
    BLOB_FIELDS,
    process_event_data,
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


class MockBlobStore:
    """In-memory blob store that records writes and returns ci-blob:// URIs."""

    def __init__(self) -> None:
        self.written: dict[str, object] = {}

    async def write(self, session_id: str, key: str, value: object) -> str:
        self.written[key] = value
        return f"ci-blob://{session_id}/{key}"


class FailingBlobStore:
    """Blob store that always raises on write."""

    async def write(self, session_id: str, key: str, value: object) -> str:
        raise OSError("disk full")


class PartialFailingBlobStore:
    """Blob store that fails for specific keys, succeeds for others."""

    def __init__(self, fail_keys: set[str]) -> None:
        self.fail_keys = fail_keys
        self.written: dict[str, object] = {}

    async def write(self, session_id: str, key: str, value: object) -> str:
        if key in self.fail_keys:
            raise IOError(f"write error for {key}")
        self.written[key] = value
        return f"ci-blob://{session_id}/{key}"


# ---------------------------------------------------------------------------
# BLOB_FIELDS constant
# ---------------------------------------------------------------------------


class TestBlobFieldsConstant:
    """BLOB_FIELDS is a frozenset with the expected field names."""

    def test_blob_fields_expected_set(self) -> None:
        """BLOB_FIELDS contains exactly the expected 6 field names."""
        expected = {"raw", "result", "messages", "mount_plan", "context_snapshot", "debug"}
        assert BLOB_FIELDS == expected

    def test_blob_fields_is_frozenset(self) -> None:
        """BLOB_FIELDS is a frozenset (immutable)."""
        assert isinstance(BLOB_FIELDS, frozenset)


# ---------------------------------------------------------------------------
# Clone immutability
# ---------------------------------------------------------------------------


class TestCloneImmutability:
    """Original data dict is NEVER modified — top-level and nested."""

    async def test_original_dict_unchanged(self) -> None:
        """process_event_data() does not mutate the original dict."""
        store = MockBlobStore()
        data = {"raw": {"key": "value"}, "session_id": "abc"}
        original_raw = data["raw"]

        await process_event_data(data, store, session_id="s1", node_id="n1")

        # Top-level original still holds the original dict object
        assert data["raw"] is original_raw
        assert data["raw"] == {"key": "value"}

    async def test_nested_dicts_unchanged(self) -> None:
        """process_event_data() deep-clones so nested structures in original are untouched."""
        store = MockBlobStore()
        nested = {"inner": [1, 2, 3]}
        data = {"raw": nested, "other": {"x": 42}}

        await process_event_data(data, store, session_id="s1", node_id="n1")

        # Nested dict in original must be exactly the same object and value
        assert data["raw"] is nested
        assert nested == {"inner": [1, 2, 3]}
        assert data["other"] == {"x": 42}


# ---------------------------------------------------------------------------
# Blob ref substitution
# ---------------------------------------------------------------------------


class TestBlobRefSubstitution:
    """Blob fields in the clone are replaced with {"$blob_ref": uri}."""

    async def test_single_field_replaced(self) -> None:
        """A single blob field is replaced with a $blob_ref dict in the clone."""
        store = MockBlobStore()
        data = {"raw": {"events": [1, 2, 3]}, "name": "test"}

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert "$blob_ref" in result["raw"]
        assert result["raw"]["$blob_ref"].startswith("ci-blob://")

    async def test_multiple_fields_replaced(self) -> None:
        """All present blob fields are replaced with $blob_ref dicts."""
        store = MockBlobStore()
        data = {
            "raw": {"a": 1},
            "result": {"b": 2},
            "messages": [{"role": "user", "content": "hi"}],
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert "$blob_ref" in result["raw"]
        assert "$blob_ref" in result["result"]
        assert "$blob_ref" in result["messages"]

    async def test_uri_contains_node_id_and_field(self) -> None:
        """The blob URI contains both the node_id and the field name."""
        store = MockBlobStore()
        data = {"result": {"answer": 42}}

        result = await process_event_data(
            data, store, session_id="sess-1", node_id="mynode__tool_pre__123"
        )

        uri = result["result"]["$blob_ref"]
        assert "mynode__tool_pre__123" in uri
        assert "result" in uri

    async def test_blob_key_format_uses_double_underscore(self) -> None:
        """blob_store.write() is called with key = '{node_id}__{field_name}'."""
        store = MockBlobStore()
        data = {"raw": {"payload": "large"}, "debug": {"trace": [1, 2, 3]}}

        await process_event_data(data, store, session_id="s1", node_id="node42__step__100")

        assert "node42__step__100__raw" in store.written
        assert "node42__step__100__debug" in store.written


# ---------------------------------------------------------------------------
# Non-blob fields pass through
# ---------------------------------------------------------------------------


class TestNonBlobFieldsPassThrough:
    """Fields not in BLOB_FIELDS are returned unchanged in the clone."""

    async def test_small_fields_unchanged(self) -> None:
        """Non-blob fields retain their original values in the clone."""
        store = MockBlobStore()
        data = {
            "event_type": "tool_pre",
            "timestamp": "2026-01-01T00:00:00Z",
            "session_id": "abc-123",
            "tool_name": "read_file",
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert result["event_type"] == "tool_pre"
        assert result["timestamp"] == "2026-01-01T00:00:00Z"
        assert result["session_id"] == "abc-123"
        assert result["tool_name"] == "read_file"

    async def test_no_fields_removed(self) -> None:
        """The clone contains every field from the original — none are deleted."""
        store = MockBlobStore()
        data = {
            "event_type": "step",
            "raw": {"payload": "large"},
            "result": {"output": "done"},
            "extra": "keep-me",
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        # All keys must be present
        assert set(result.keys()) == set(data.keys())


# ---------------------------------------------------------------------------
# None value handling
# ---------------------------------------------------------------------------


class TestNoneValueHandling:
    """None blob fields are skipped; missing blob fields are not added."""

    async def test_none_blob_field_not_processed(self) -> None:
        """A blob field set to None is not replaced with a blob_ref."""
        store = MockBlobStore()
        data = {"raw": None, "event_type": "step"}

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        # None should stay as None — no blob_ref, no write
        assert result["raw"] is None
        assert "raw" not in store.written

    async def test_missing_field_not_added(self) -> None:
        """A blob field absent from data is not injected into the clone."""
        store = MockBlobStore()
        data = {"event_type": "step", "tool_name": "bash"}

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        # No blob fields should appear in the result
        for field in BLOB_FIELDS:
            assert field not in result


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Write failures produce $blob_error; one failure doesn't block others."""

    async def test_write_failure_produces_blob_error(self) -> None:
        """When blob_store.write() raises, the field is replaced with $blob_error."""
        store = FailingBlobStore()
        data = {"raw": {"big": "data"}}

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert "$blob_error" in result["raw"]
        assert "write failed:" in result["raw"]["$blob_error"]
        assert "disk full" in result["raw"]["$blob_error"]

    async def test_failure_doesnt_block_other_fields(self) -> None:
        """A write failure for one field does not prevent others from being processed."""
        # "raw" will fail, "result" will succeed
        node_id = "n1__step__000"
        fail_key = f"{node_id}__raw"
        store = PartialFailingBlobStore(fail_keys={fail_key})
        data = {"raw": {"big": "data"}, "result": {"output": "ok"}}

        result = await process_event_data(data, store, session_id="s1", node_id=node_id)

        # raw failed
        assert "$blob_error" in result["raw"]
        # result succeeded
        assert "$blob_ref" in result["result"]


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------


class TestReturnValue:
    """process_event_data() returns a new dict object, never the original."""

    async def test_returns_new_dict_not_original(self) -> None:
        """The returned dict is a different object from the input data."""
        store = MockBlobStore()
        data = {"event_type": "step", "raw": {"x": 1}}

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert result is not data

    async def test_data_without_blob_fields_returns_identical_clone(self) -> None:
        """When no blob fields are present, the clone is identical in content."""
        store = MockBlobStore()
        data = {"event_type": "step", "tool_name": "bash", "timestamp": "now"}

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert result == data
        assert result is not data
