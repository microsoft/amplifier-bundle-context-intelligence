"""Synthetic bounded diagnostic tombstones; no deletion/network side effects."""

from __future__ import annotations

import io
import json
import tempfile
from typing import Any

import pytest

from context_intelligence.optimize import rewrite_diagnostics
from context_intelligence.optimize._json import FormatError

from test_optimize_stream import BoundedBytes

DIGEST = "a" * 64
STAMP = "2026-01-01T00:00:00+00:00"


def marker(**changes: Any) -> dict[str, Any]:
    return {
        "v": 1,
        "sha256": DIGEST,
        "bytes": 20,
        "expired_at": STAMP,
        "reason": "retention",
        **changes,
    }


def record(**extra: Any) -> bytes:
    return (
        json.dumps(
            {
                "event": "llm:request",
                "session_id": "s1",
                "timestamp": STAMP,
                "data": {
                    "raw": {"messages": ["huge in real producer"]},
                    "raw_response": {"keep": True},
                    "provider_payloads": {
                        "raw": {
                            "sha256": DIGEST,
                            "bytes": 20,
                            "redacted": True,
                            "truncated": False,
                        },
                        "raw_response": {"sha256": "b" * 64, "bytes": 12, "redacted": True},
                    },
                    "usage": {"tokens": 8},
                    "status": "unknown",
                    **extra,
                },
            }
        ).encode()
        + b"\n"
    )


def test_expire_only_selected_direct_raw_and_live_descriptor() -> None:
    original = record()
    output = io.BytesIO()
    counts = rewrite_diagnostics(
        BoundedBytes(original), output, session_id="s1", expire=lambda _: {"raw": marker()}
    )
    expected = json.loads(original)
    del expected["data"]["raw"]
    del expected["data"]["provider_payloads"]["raw"]
    expected["data"]["provider_payloads_expired"] = {"raw": marker()}
    assert json.loads(output.getvalue()) == expected
    assert counts == {"records": 1, "changed_records": 1, "expired_fields": 1}
    repeated = io.BytesIO()

    def expire(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {"raw": marker()} if "raw" in row["provider_payloads"] else {}

    rewrite_diagnostics(BoundedBytes(output.getvalue()), repeated, session_id="s1", expire=expire)
    assert repeated.getvalue() == output.getvalue()


def test_unknown_numerical_text_and_non_target_spans_copied_verbatim() -> None:
    original = record().replace(b'"usage": {"tokens": 8}', b'"usage" : [1.234567890123456789e123]')
    output = io.BytesIO()
    rewrite_diagnostics(
        BoundedBytes(original), output, session_id="s1", expire=lambda _: {"raw": marker()}
    )
    assert b'"usage" : [1.234567890123456789e123]' in output.getvalue()


@pytest.mark.parametrize(
    "changes",
    [
        {"v": True},
        {"sha256": "b" * 64},
        {"bytes": True},
        {"bytes": 21},
        {"expired_at": "2026-01-01T00:00:00Z"},
        {"reason": "other"},
        {"unknown": True},
    ],
)
def test_mismatched_marker_refuses(changes: dict[str, Any]) -> None:
    with pytest.raises(FormatError):
        rewrite_diagnostics(
            BoundedBytes(record()),
            io.BytesIO(),
            session_id="s1",
            expire=lambda _: {"raw": marker(**changes)},
        )


def test_blob_refs_protected_and_arbitrary_callback_fields_refuse() -> None:
    for raw, selected in (
        ({"$blob_ref": "ci-blob://s1/key"}, {"raw": marker()}),
        ({}, {"tool_output": marker()}),
    ):
        with pytest.raises(FormatError):
            rewrite_diagnostics(
                BoundedBytes(record(raw=raw)),
                io.BytesIO(),
                session_id="s1",
                expire=lambda _: selected,
            )


def test_giant_single_diagnostic_record_never_read_by_line() -> None:
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
        source.write(b'{"event":"llm:request","session_id":"s1","data":{"raw":"')
        for _ in range(512):
            source.write(b"x" * 65536)
        source.write(
            b'","provider_payloads":{"raw":{"sha256":"'
            + DIGEST.encode()
            + b'","bytes":20,"redacted":true}},"status":"unknown"}}\n'
        )
        source.seek(0)
        counts = rewrite_diagnostics(
            source, output, session_id="s1", expire=lambda _: {"raw": marker()}
        )
        assert counts["expired_fields"] == 1
        assert output.tell() < 1024
        output.seek(0)
        assert json.load(output)["data"]["status"] == "unknown"
