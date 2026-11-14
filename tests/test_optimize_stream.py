"""Synthetic only: huge individual records, strict parsing and span preservation."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tempfile
import tracemalloc
from typing import Any

import pytest

from context_intelligence.optimize import iter_capture_projection, rewrite_capture
from context_intelligence.optimize._json import CHUNK, FormatError, Index, Reader, string_chunks
from context_intelligence.optimize.capture import _decoded_image
from context_intelligence.optimize import write_record_mapping


def image(data: bytes = b"synthetic-png") -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(data).decode(),
        },
    }


def event(content: list[Any] | None = None, **extra: Any) -> bytes:
    return (
        json.dumps(
            {
                "event": "tool:post",
                "session_id": "s1",
                "id": "event-1",
                "timestamp": "2026-01-01T00:00:00.123456789Z",
                "data": {
                    "tool_call_id": "call1",
                    "usage": {"n": 1},
                    "status": "success",
                    "result": {
                        "success": True,
                        "output": "retained",
                        "content": content or [image()],
                    },
                    **extra,
                },
            }
        ).encode()
        + b"\r\n"
    )


class BoundedBytes(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= CHUNK, "whole-record read attempted"
        return super().read(size)

    def readline(self, size: int = -1) -> bytes:
        raise AssertionError("readline is not bounded on huge individual records")


def test_preview_and_projection_never_hydrate_or_change_bytes() -> None:
    original = event(provider_payloads={"raw": {"sha256": "a" * 64, "bytes": 123}})
    output = io.BytesIO()
    counts = rewrite_capture(
        BoundedBytes(original), output, session_id="s1", source_sha256="a" * 64
    )
    assert counts["images"] == 1 and counts["records"] == 1
    assert output.getvalue() == original
    projected = list(iter_capture_projection(BoundedBytes(original), session_id="s1"))
    assert projected[0]["provider_payloads"]["raw"]["bytes"] == 123
    assert projected[0]["id"] == "event-1"
    assert "result" not in projected[0]


@pytest.mark.parametrize(
    "value",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":01}',
        b'{"a":1e}',
        b'{"a":"\\ud800"}',
        b'{"a":"\\udc00"}',
        b'{"a":"\\uQQQQ"}',
        b'{"a":"\xff"}',
        b'{"a":"\x01"}',
        b'{"a":1,}',
        b'{"a":1}{"a":2}',
        b'{"a":\n1}',
    ],
)
def test_parser_refuses_ambiguous_or_malformed_json(value: bytes) -> None:
    with pytest.raises(FormatError):
        with Index(BoundedBytes(value)):
            pass


def test_streaming_escaped_unicode_across_chunk_edges() -> None:
    raw = json.dumps("é" * CHUNK + '😀\n\\"/').encode()
    decoded = b"".join(string_chunks(Reader(BoundedBytes(raw))))
    assert decoded.decode() == "é" * CHUNK + '😀\n\\"/'
    raw_utf8 = b'"' + ("é" * CHUNK).encode() + b'"'
    assert b"".join(string_chunks(Reader(BoundedBytes(raw_utf8)))).decode() == "é" * CHUNK


@pytest.mark.parametrize("encoded", ["A", "Zg=", "Zg==AA==", "Zh==", "Z g==", ""])
def test_strict_base64(encoded: str) -> None:
    value = image()
    value["source"]["data"] = encoded
    with pytest.raises(FormatError):
        rewrite_capture(BoundedBytes(event([value])), None, session_id="s1", source_sha256="a" * 64)


def test_unknown_and_blob_refs_and_json_looking_text_are_not_guessed() -> None:
    original = event(
        [
            {"type": "text", "text": json.dumps({"content": [image()]})},
            {"type": "image", "source": {"type": "url", "url": "https://example.invalid"}},
        ],
        raw={"$blob_ref": "ci-blob://s1/key"},
        raw_request=json.dumps({"messages": [{"role": "user", "content": [image()]}]}),
    )
    output = io.BytesIO()
    counts = rewrite_capture(
        BoundedBytes(original), output, session_id="s1", source_sha256="a" * 64
    )
    assert counts["images"] == 0 and counts["unsupported"] == 1
    assert counts["protected_strings"] == 1 and output.getvalue() == original


def test_non_target_numeric_and_utf8_spans_remain_exact() -> None:
    original = (
        b' { "event":"llm:request", "session_id":"s1", "data":'
        b'{"usage":1.23456789012345678901234567890123456789e123,"raw":'
        b'{"messages":[{"role":"user","content":[{"type":"text","text":"caf\xc3\xa9"}]}]}}} \n'
    )
    output = io.BytesIO()
    rewrite_capture(BoundedBytes(original), output, session_id="s1", source_sha256="a" * 64)
    assert output.getvalue() == original


def test_giant_single_record_and_escaped_string_use_bounded_memory() -> None:
    # Generate on disk; the fixture must not hide a resident giant bytes value.
    with tempfile.TemporaryFile() as source:
        source.write(
            b'{"event":"tool:post","session_id":"s1","data":{"result":'
            b'{"content":[{"type":"image","source":{"type":"base64",'
            b'"media_type":"image/png","data":"'
        )
        for _ in range(256):
            source.write(b"QUFB" * 16384)  # 16 MiB in ONE string/record
        source.write(b'"}}]},"opaque":"')
        for _ in range(256):
            source.write(b'\\"\\\\\\u00e9' * 8192)  # huge JSON-in-string, NOT parsed as JSON
        source.write(b'"}}\n')
        source.seek(0)
        tracemalloc.start()
        counts = rewrite_capture(source, None, session_id="s1", source_sha256="a" * 64)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert counts["images"] == 1 and counts["decoded_bytes"] == 256 * 16384 * 3
    assert peak < 5 * 1024 * 1024, f"peak retained memory {peak}"


def test_many_elements_do_not_build_a_python_tree() -> None:
    with tempfile.TemporaryFile() as source:
        source.write(b'{"event":"llm:request","data":{"opaque":[')
        for _ in range(10000):
            source.write(b'{"x":1},')
        source.write(b"null]}}\n")
        source.seek(0)
        with Index(source) as index:
            assert index.records == 1
            assert index.db.execute("SELECT count(*) FROM node").fetchone()[0] > 20000


def test_schema_and_session_fail_closed() -> None:
    for original in (
        event().replace(b'"s1"', b'"foreign"'),
        b'{"schema":{"name":"future","ver":"2"},"event":"tool:post","data":{}}\n',
    ):
        with pytest.raises(FormatError):
            list(iter_capture_projection(BoundedBytes(original), session_id="s1"))


def test_projection_metadata_is_bounded_not_silently_truncated() -> None:
    original = event(provider_payloads={"unknown": "x" * 20000})
    with pytest.raises(FormatError, match="oversized"):
        list(iter_capture_projection(BoundedBytes(original), session_id="s1"))


def test_base64_chunk_boundary_hash() -> None:
    value = bytes(range(256)) * 1000
    with Index(BoundedBytes(event([image(value)]))) as index:
        node = index.db.execute(
            "SELECT id FROM node WHERE key='data' AND kind='string'"
        ).fetchone()[0]
        output = io.BytesIO()
        digest, size = _decoded_image(index, node, output)
        assert digest == hashlib.sha256(value).hexdigest()
        assert size == len(value) and output.getvalue() == value


@pytest.mark.parametrize("field", ["timestamp", "ts", "event_id", "id", "idempotency_key"])
@pytest.mark.parametrize(
    "token",
    [
        b"1.234567890123456789",
        b"true",
        b'{"opaque":"identity"}',
        b'["identity"]',
    ],
)
def test_mapping_refuses_unsupported_identity_types_without_rounding(
    field: str, token: bytes
) -> None:
    original = (
        b'{"event":"tool:post","session_id":"s1","data":{},"'
        + field.encode()
        + b'":'
        + token
        + b"}\n"
    )
    # Identical nonstandard identity tokens must not become rounded mapping facts.
    with pytest.raises(FormatError, match="identity"):
        write_record_mapping(
            BoundedBytes(original), BoundedBytes(original), io.BytesIO(), session_id="s1"
        )


def test_mapping_preserves_string_and_null_identity_fields() -> None:
    original = (
        b'{"event":"tool:post","session_id":"s1","data":{},'
        b'"timestamp":null,"ts":"2025-01-01T00:00:00.123456789Z",'
        b'"event_id":"123456789012345678901234567890","id":null}\n'
    )
    output = io.BytesIO()
    assert (
        write_record_mapping(
            BoundedBytes(original), BoundedBytes(original), output, session_id="s1"
        )
        == 1
    )
    row = json.loads(output.getvalue().splitlines()[1])
    assert row["eventId"] == "123456789012345678901234567890"
    assert row["timestamp"] is None and row["id"] is None
    assert row["ts"] == "2025-01-01T00:00:00.123456789Z"
