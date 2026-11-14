"""Synthetic CI-owned complete-origin enumeration (no Foundation dependency)."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import tracemalloc
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from context_intelligence.optimize._json import CHUNK, FormatError
from context_intelligence.optimize.capture import (
    StagedImage,
    capture_group_sources,
    rewrite_capture,
)


def image(pixels: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(pixels).decode(),
        },
    }


def record(*blocks: dict[str, Any], call: str = "call") -> dict[str, Any]:
    return {
        "event": "tool:post",
        "session_id": "s",
        "event_id": "event",
        "timestamp": "2025-01-01T00:00:00Z",
        "data": {
            "tool_call_id": call,
            "status": "unknown",
            "usage": {"input": 17},
            "result": {"success": False, "content": list(blocks)},
        },
    }


def policy(snapshot: dict[str, Any]) -> dict[str, Any]:
    # Explicit fixture authorization: these source call groups were durable.
    return {
        "kind": "tool",
        "session_id": "s",
        "id": snapshot["tool_call_id"],
        "created_at": "2025-01-01T00:00:00Z",
        "expires_at": None,
    }


def snapshots(source: io.BytesIO) -> list[dict[str, Any]]:
    values = []

    class Spy:
        def stage_image(self, image: BinaryIO, *, mime: str, origin: dict[str, Any]) -> StagedImage:
            values.append(copy.deepcopy(origin))
            return StagedImage({"type": "text", "text": "test-only"}, {}, 0)

        def publish(self, manifest: Path) -> None:
            pytest.fail("source-origin outbound spy must not publish")

        def verify(self, manifest: Path) -> None:
            pytest.fail("source-origin outbound spy must not verify")

        def acknowledge(self, manifest: Path, *, source: Path, sha256: str) -> None:
            pytest.fail("source-origin outbound spy must not acknowledge")

    source.seek(0)
    rewrite_capture(
        source,
        io.BytesIO(),
        session_id="s",
        source_sha256=digest(source),
        media=Spy(),
        manifest=io.BytesIO(),
    )
    return values


def digest(source: io.BytesIO) -> str:
    return hashlib.sha256(source.getvalue()).hexdigest()


def test_complete_policy_group_across_records_order_stream_lifetime_and_exact_origin() -> None:
    source = io.BytesIO(
        b"\n".join(
            json.dumps(row).encode()
            for row in (
                record(image(b"one"), image(b"two")),
                record(image(b"unrelated"), call="other"),
                record(image(b"three")),
            )
        )
        + b"\n"
    )
    selected = snapshots(source)
    before = source.getvalue()
    with capture_group_sources(
        source,
        session_id="s",
        source_sha256=digest(source),
        origin_for=policy,
        complete_group_for=lambda *_: True,
    ) as groups:
        for selection in (selected[0], selected[1], selected[3]):
            previous = None
            with groups(selection, policy(selection)) as members:
                observed, positions = [], []
                for member in members:
                    if previous is not None:
                        assert previous.closed
                    previous = member["stream"]
                    observed.append(previous.read())
                    positions.append(member["origin"])
                    assert member["mime"] == "image/png"
                assert observed == [b"one", b"two", b"three"]
                assert positions == [selected[0], selected[1], selected[3]]
            assert previous is not None and previous.closed
        for key, value in (
            ("bytes", 0),
            ("event_id", "forged"),
            ("source_sha256", "0" * 64),
            ("ordinal", True),
        ):
            wrong = {**selected[0], key: value}
            with (
                pytest.raises(FormatError, match="exact admitted"),
                groups(wrong, policy(selected[0])),
            ):
                pass
        for bad_position in (-1, True, None, [], "17"):
            with (
                pytest.raises(FormatError, match="exact CI position"),
                groups({**selected[0], "image_offset": bad_position}, policy(selected[0])),
            ):
                pass
        with (
            pytest.raises(FormatError, match="exact admitted"),
            groups(selected[0], {**policy(selected[0]), "expires_at": "2030-01-01T00:00:00Z"}),
        ):
            pass
    with pytest.raises(FormatError, match="inactive"), groups(selected[0], policy(selected[0])):
        pass
    assert source.getvalue() == before


def test_group_context_refuses_partial_consumption_nested_use_and_source_drift() -> None:
    source = io.BytesIO(json.dumps(record(image(b"one"), image(b"two"))).encode())
    selected = snapshots(source)[0]
    with capture_group_sources(
        source,
        session_id="s",
        source_sha256=digest(source),
        origin_for=policy,
        complete_group_for=lambda *_: True,
    ) as groups:
        with (
            pytest.raises(FormatError, match="not consumed"),
            groups(selected, policy(selected)) as members,
        ):
            leaked = next(members)["stream"]
        assert leaked.closed
        with groups(selected, policy(selected)) as members:
            with (
                pytest.raises(FormatError, match="already in use"),
                groups(selected, policy(selected)),
            ):
                pass
            for _ in members:
                pass
        with (
            pytest.raises(FormatError, match="generation changed"),
            groups(selected, policy(selected)) as members,
        ):
            for _ in members:
                pass
            source.seek(0, 2)
            source.write(b" ")
        # Restore the fixture so the outer context's independent check succeeds.
        source.truncate(len(source.getvalue()) - 1)


@pytest.mark.parametrize("fault", ["hash", "policy", "unsupported", "session", "malformed"])
def test_group_admission_refuses_before_yield(fault: str) -> None:
    row = record(image(b"one"), image(b"two"))
    if fault == "unsupported":
        row["data"]["result"]["content"][1]["source"]["type"] = "url"
    if fault == "malformed":
        row["data"]["result"]["content"][1]["source"]["data"] = "a==="
    source = io.BytesIO(json.dumps(row).encode())
    with (
        pytest.raises(FormatError),
        capture_group_sources(
            source,
            session_id="wrong" if fault == "session" else "s",
            source_sha256="0" * 64 if fault == "hash" else digest(source),
            origin_for=(lambda _: {}) if fault == "policy" else policy,
            complete_group_for=lambda *_: True,
        ),
    ):
        pytest.fail("incomplete/unknown group was admitted")


def test_strings_and_vendor_payloads_are_never_group_members() -> None:
    row = record(image(b"real"))
    row["data"]["raw"] = {"choices": [{"message": {"content": [image(b"vendor")]}}]}
    row["data"]["result"]["output"] = json.dumps(record(image(b"string")))
    source = io.BytesIO(json.dumps(row).encode())
    selected = snapshots(source)[0]
    with (
        capture_group_sources(
            source,
            session_id="s",
            source_sha256=digest(source),
            origin_for=policy,
            complete_group_for=lambda *_: True,
        ) as groups,
        groups(selected, policy(selected)) as members,
    ):
        assert [member["stream"].read() for member in members] == [b"real"]


def test_huge_single_record_and_image_are_chunk_bounded(tmp_path: Any) -> None:
    # Generate on disk before tracing; never materialize the huge record in the test.
    path = tmp_path / "synthetic.jsonl"
    size = 12 * 1024 * 1024
    with path.open("wb") as output:
        output.write(
            b'{"event":"tool:post","session_id":"s","data":{"tool_call_id":"call",'
            b'"result":{"content":[{"type":"image","source":{"type":"base64",'
            b'"media_type":"image/png","data":"'
        )
        for _ in range(size // CHUNK):
            output.write(base64.b64encode(b"x" * (CHUNK // 4 * 3)))
        output.write(b'"}},{"type":"text","text":"')
        for _ in range(size // CHUNK):
            output.write(b"z" * CHUNK)
        output.write(b'"}]}}}\n')
    with path.open("rb") as source:
        source_hash = hashlib.file_digest(source, "sha256").hexdigest()
    chosen = []

    def admitted(snapshot: dict[str, Any]) -> dict[str, Any]:
        chosen.append(snapshot)
        return policy(snapshot)

    tracemalloc.start()
    try:
        with (
            path.open("rb") as source,
            capture_group_sources(
                source,
                session_id="s",
                source_sha256=source_hash,
                origin_for=admitted,
                complete_group_for=lambda *_: True,
            ) as groups,
            groups(chosen[0], policy(chosen[0])) as members,
        ):
            for member in members:
                total = 0
                while chunk := member["stream"].read(CHUNK):
                    total += len(chunk)
                assert total == size // 4 * 3
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024, peak


def test_visible_images_do_not_attest_complete_original_group() -> None:
    source = io.BytesIO(json.dumps(record(image(b"one"))).encode())
    selected = snapshots(source)[0]
    with (
        capture_group_sources(
            source,
            session_id="s",
            source_sha256=digest(source),
            origin_for=policy,
            complete_group_for=lambda *_: False,
        ) as groups,
        pytest.raises(FormatError, match="not attested"),
        groups(selected, policy(selected)),
    ):
        pytest.fail("visible image alone was treated as complete original group")
