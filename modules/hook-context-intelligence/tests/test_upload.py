"""Tests for upload.py — _canonical_json, _compute_idempotency_key, build_payload."""

from __future__ import annotations

import hashlib
import json

from amplifier_module_hook_context_intelligence.upload import (
    _canonical_json,
    _compute_idempotency_key,
    build_payload,
)


class TestCanonicalJson:
    def test_sorts_keys(self) -> None:
        result = _canonical_json({"z": 1, "a": 2, "m": 3})
        parsed = json.loads(result)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_compact_separators(self) -> None:
        result = _canonical_json({"key": "value"})
        # Compact separators means no spaces after : or ,
        assert " " not in result

    def test_deterministic_same_input(self) -> None:
        data = {"b": 2, "a": 1, "c": 3}
        assert _canonical_json(data) == _canonical_json(data)

    def test_nested_dicts_sorted(self) -> None:
        result = _canonical_json({"outer": {"z": 1, "a": 2}})
        parsed = json.loads(result)
        nested = parsed["outer"]
        assert list(nested.keys()) == sorted(nested.keys())


class TestComputeIdempotencyKey:
    def test_prefix_is_aci_event_v1(self) -> None:
        key = _compute_idempotency_key("test:event", "ws", {"x": 1})
        assert key.startswith("aci-event-v1:")

    def test_same_input_same_key(self) -> None:
        key1 = _compute_idempotency_key("session:start", "ws1", {"a": 1})
        key2 = _compute_idempotency_key("session:start", "ws1", {"a": 1})
        assert key1 == key2

    def test_different_data_different_key(self) -> None:
        key1 = _compute_idempotency_key("session:start", "ws1", {"a": 1})
        key2 = _compute_idempotency_key("session:start", "ws1", {"a": 2})
        assert key1 != key2

    def test_different_event_different_key(self) -> None:
        key1 = _compute_idempotency_key("session:start", "ws1", {"a": 1})
        key2 = _compute_idempotency_key("session:end", "ws1", {"a": 1})
        assert key1 != key2

    def test_different_workspace_different_key(self) -> None:
        key1 = _compute_idempotency_key("session:start", "ws1", {"a": 1})
        key2 = _compute_idempotency_key("session:start", "ws2", {"a": 1})
        assert key1 != key2

    def test_none_workspace_treated_as_empty_string(self) -> None:
        key_none = _compute_idempotency_key("session:start", None, {"a": 1})
        key_empty = _compute_idempotency_key("session:start", "", {"a": 1})
        assert key_none == key_empty

    def test_key_contains_sha256_hex(self) -> None:
        event = "session:start"
        workspace = "ws1"
        data = {"a": 1}
        canonical = _canonical_json({"event": event, "workspace": workspace or "", "data": data})
        expected_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        key = _compute_idempotency_key(event, workspace, data)
        assert key == f"aci-event-v1:{expected_digest}"


class TestBuildPayload:
    def test_has_required_keys(self) -> None:
        payload = build_payload("session:start", "ws1", {"x": 1})
        assert set(payload.keys()) >= {"event", "workspace", "idempotency_key", "data"}

    def test_event_field_matches_input(self) -> None:
        payload = build_payload("session:start", "ws1", {"x": 1})
        assert payload["event"] == "session:start"

    def test_workspace_field_matches_input(self) -> None:
        payload = build_payload("session:start", "my-workspace", {"x": 1})
        assert payload["workspace"] == "my-workspace"

    def test_none_workspace_becomes_empty_string(self) -> None:
        payload = build_payload("session:start", None, {"x": 1})
        assert payload["workspace"] == ""

    def test_data_field_is_passthrough(self) -> None:
        data = {"session_id": "abc", "timestamp": "2024-01-01"}
        payload = build_payload("session:start", "ws", data)
        assert payload["data"] == data

    def test_idempotency_key_matches_standalone_function(self) -> None:
        event = "session:start"
        workspace = "ws1"
        data = {"x": 1}
        payload = build_payload(event, workspace, data)
        expected_key = _compute_idempotency_key(event, workspace, data)
        assert payload["idempotency_key"] == expected_key
