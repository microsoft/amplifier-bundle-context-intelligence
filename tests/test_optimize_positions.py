"""Offline real-file CI position verification; synthetic captures, no owner imports."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
import tracemalloc
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from context_intelligence.optimize._json import CHUNK, FormatError
from context_intelligence.optimize.capture import StagedImage, rewrite_capture, write_record_mapping
from context_intelligence.optimize.positions import CaptureImageVerifier, capture_image_verifier


def png(red: int) -> bytes:
    """Actual distinct, valid one-pixel PNGs without optional image dependencies."""

    def chunk(kind: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes([0, red, 0, 0])))
        + chunk(b"IEND", b"")
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


def record(*blocks: dict[str, Any], event_id: str = "tool-event") -> dict[str, Any]:
    return {
        "event": "tool:post",
        "session_id": "s",
        "event_id": event_id,
        "timestamp": "2025-01-01T00:00:00.123456789Z",
        "data": {
            "tool_call_id": "call",
            "status": "unknown",
            "usage": {"input": 17},
            "result": {"success": False, "content": list(blocks)},
        },
    }


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(b"\n".join(json.dumps(row).encode() for row in rows) + b"\n")


def read_rows(path: Path) -> list[dict[str, Any]]:
    # These deliberately tiny fixtures are not a production capture reader.
    return [json.loads(row) for row in path.read_bytes().splitlines()]


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class OwnerSpy:
    """Outbound-only spy: fixtures inspect actual CI-selected origin snapshots."""

    def __init__(self) -> None:
        self.origins: list[dict[str, Any]] = []
        self.replacements: list[dict[str, Any]] = []

    def stage_image(self, image: BinaryIO, *, mime: str, origin: dict[str, Any]) -> StagedImage:
        hashed = hashlib.sha256()
        size = 0
        while block := image.read(CHUNK):
            hashed.update(block)
            size += len(block)
        assert hashed.hexdigest() == origin["sha256"] and size == origin["bytes"]
        assert mime in ("image/png", "image/jpeg")
        replacement = {
            "type": "text",
            "text": json.dumps({"synthetic_ref": origin["sha256"], "at": origin["image_offset"]}),
        }
        self.origins.append(copy.deepcopy(origin))
        self.replacements.append(replacement)
        return StagedImage(replacement, {}, 0)

    def publish(self, manifest: Path) -> None:
        pytest.fail("offline outbound spy must not publish")

    def verify(self, manifest: Path) -> None:
        pytest.fail("offline outbound spy must not verify")

    def acknowledge(self, manifest: Path, *, source: Path, sha256: str) -> None:
        pytest.fail("offline outbound spy must not acknowledge")


def stage(original: Path, installed: Path) -> OwnerSpy:
    owner = OwnerSpy()
    with original.open("rb") as source, installed.open("wb") as output:
        with (original.parent / "synthetic-manifest").open("wb") as manifest:
            rewrite_capture(
                source,
                output,
                session_id="s",
                source_sha256=digest(original),
                media=owner,
                manifest=manifest,
            )
    return owner


@contextmanager
def verifier(original: Path, installed: Path, **kwargs: Any) -> Iterator[CaptureImageVerifier]:
    with (
        original.open("rb") as before,
        installed.open("rb") as after,
        capture_image_verifier(
            before,
            after,
            session_id=kwargs.pop("session_id", "s"),
            source_sha256=kwargs.pop("source_sha256", digest(original)),
            installed_sha256=kwargs.pop("installed_sha256", digest(installed)),
            **kwargs,
        ) as read_selected,
    ):
        yield read_selected


@pytest.fixture
def captures(tmp_path: Path) -> tuple[Path, Path, OwnerSpy]:
    first, second = png(255), png(128)
    tool = record(
        image(first),
        {"type": "text", "text": json.dumps(image(second))},
        image(second),
        image(first),
    )
    tool["data"]["result"]["output"] = json.dumps({"content": [image(second)]})
    llm = {
        "event": "llm:request",
        "session_id": "s",
        "event_id": "request-event",
        "timestamp": "2025-01-01T00:00:00Z",
        "data": {
            "raw_request": {"messages": [{"role": "user", "content": [image(second)]}]},
            "raw": {"choices": [{"message": {"content": [image(first)]}}]},
            "raw_response": json.dumps({"messages": [{"role": "user", "content": [image(first)]}]}),
        },
    }
    original, installed = tmp_path / "original", tmp_path / "installed"
    write_rows(original, [tool, llm])
    owner = stage(original, installed)
    assert len(owner.origins) == 4
    return original, installed, owner


def test_exact_distinct_and_duplicate_images_map_to_text_only_refs_and_keep_protected_spans(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    before, after = original.read_bytes(), installed.read_bytes()
    with verifier(original, installed) as read_selected:
        for position, content_index in enumerate((0, 2, 3)):
            selected = read_selected(owner.origins[position])
            assert selected == {
                "status": "present",
                "value": owner.replacements[position],
                "path": ("data", "result", "content", content_index),
            }
        assert read_selected(owner.origins[3]) == {
            "status": "present",
            "value": owner.replacements[3],
            "path": ("data", "raw_request", "messages", 0, "content", 0),
        }
        # Duplicate bytes remain DISTINCT positioned originals, not digest lookup aliases.
        assert owner.origins[0]["sha256"] == owner.origins[2]["sha256"]
        assert read_selected(owner.origins[0]) != read_selected(owner.origins[2])
    with pytest.raises(FormatError, match="inactive"):
        read_selected(owner.origins[0])
    assert original.read_bytes() == before and installed.read_bytes() == after
    old_rows, new_rows = read_rows(original), read_rows(installed)
    assert old_rows[0]["data"]["result"]["output"] == new_rows[0]["data"]["result"]["output"]
    assert (
        old_rows[0]["data"]["result"]["content"][1] == new_rows[0]["data"]["result"]["content"][1]
    )
    for field in ("raw", "raw_response"):
        assert old_rows[1]["data"][field] == new_rows[1]["data"][field]
    # Exercise the actual independent CI record-map contract on this installed file.
    with original.open("rb") as old, installed.open("rb") as new:
        with (original.parent / "record-map").open("wb") as mapping:
            assert write_record_mapping(old, new, mapping, session_id="s") == 2


def test_absent_means_removed_whole_raw_field_only_not_expiry_authority(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    rows = read_rows(installed)
    del rows[1]["data"]["raw_request"]
    rows[1]["data"]["expired"] = {"raw_request": "fixture-only-untrusted-marker"}
    write_rows(installed, rows)
    with verifier(original, installed) as read_selected:
        assert read_selected(owner.origins[3]) == {
            "status": "absent",
            "value": None,
            "path": ("data", "raw_request", "messages", 0, "content", 0),
        }
        assert read_selected(owner.origins[0])["status"] == "present"


@pytest.mark.parametrize("field", ["raw", "raw_request", "raw_response"])
def test_each_known_llm_raw_field_can_be_absent(tmp_path: Path, field: str) -> None:
    original, installed = tmp_path / "original", tmp_path / "installed"
    row = {
        "event": "llm:response",
        "session_id": "s",
        "data": {
            field: {"messages": [{"role": "assistant", "content": [image(png(1))]}]},
        },
    }
    write_rows(original, [row])
    owner = stage(original, installed)
    del row["data"][field]
    write_rows(installed, [row])
    with verifier(original, installed) as read_selected:
        assert read_selected(owner.origins[0])["status"] == "absent"


def test_subset_not_installed_is_present_inline_not_group_wide_reachability(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    rows, old = read_rows(installed), read_rows(original)
    rows[0]["data"]["result"]["content"][2] = old[0]["data"]["result"]["content"][2]
    write_rows(installed, rows)
    with verifier(original, installed) as read_selected:
        assert read_selected(owner.origins[0])["value"] == owner.replacements[0]
        assert read_selected(owner.origins[1])["value"] == old[0]["data"]["result"]["content"][2]
        assert read_selected(owner.origins[1])["status"] == "present"


@pytest.mark.parametrize(
    "fault", ["content", "element", "result", "type", "raw-child", "raw-null", "role"]
)
def test_missing_or_changed_ancestry_refuses_not_absent(
    captures: tuple[Path, Path, OwnerSpy],
    fault: str,
) -> None:
    original, installed, owner = captures
    rows = read_rows(installed)
    selected = owner.origins[0]
    result = rows[0]["data"]["result"]
    if fault == "content":
        del result["content"]
    elif fault == "element":
        result["content"].pop(0)
    elif fault == "result":
        del rows[0]["data"]["result"]
    elif fault == "type":
        result["content"] = {"0": owner.replacements[0]}
    else:
        selected = owner.origins[3]
        raw = rows[1]["data"]["raw_request"]
        if fault == "raw-child":
            del raw["messages"]
        elif fault == "raw-null":
            rows[1]["data"]["raw_request"] = None
        else:
            raw["messages"][0]["role"] = "assistant"
    write_rows(installed, rows)
    with pytest.raises(FormatError), verifier(original, installed) as read_selected:
        read_selected(selected)


def test_json_null_at_selected_node_is_present_not_absent(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    rows = read_rows(installed)
    rows[0]["data"]["result"]["content"][0] = None
    write_rows(installed, rows)
    with verifier(original, installed) as read_selected:
        assert read_selected(owner.origins[0]) == {
            "status": "present",
            "value": None,
            "path": ("data", "result", "content", 0),
        }


@pytest.mark.parametrize(
    "field", ["event", "event_id", "timestamp", "status", "call", "success", "count", "reorder"]
)
def test_record_identity_and_ordinal_changes_refuse(
    captures: tuple[Path, Path, OwnerSpy],
    field: str,
) -> None:
    original, installed, _ = captures
    rows = read_rows(installed)
    if field in ("event", "event_id", "timestamp"):
        rows[0][field] = "different"
    elif field == "status":
        rows[0]["data"]["status"] = "success"
    elif field == "call":
        rows[0]["data"]["tool_call_id"] = "other"
    elif field == "success":
        rows[0]["data"]["result"]["success"] = True
    elif field == "count":
        rows.pop()
    else:
        rows.reverse()
    write_rows(installed, rows)
    with pytest.raises(FormatError, match="identity|record count"), verifier(original, installed):
        pytest.fail("changed record identity was admitted")


@pytest.mark.parametrize("key", ["timestamp", "ts", "event_id", "id", "idempotency_key"])
def test_numeric_identities_refuse_even_if_both_sides_match(tmp_path: Path, key: str) -> None:
    original, installed = tmp_path / "original", tmp_path / "installed"
    raw = json.dumps(record(image(png(1)))).encode()
    # Preserve an exact long float lexeme in both generations; never compare rounded floats.
    raw = raw[:-1] + b',"' + key.encode() + b'":1.2345678901234567890123456789}\n'
    if key in ("timestamp", "event_id"):
        row = record(image(png(1)))
        del row[key]
        raw = (
            json.dumps(row).encode()[:-1]
            + b',"'
            + key.encode()
            + b'":1.2345678901234567890123456789}\n'
        )
    original.write_bytes(raw)
    installed.write_bytes(raw)
    with pytest.raises(FormatError, match="identity"), verifier(original, installed):
        pytest.fail("floating identity was admitted")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ordinal", True),
        ("ordinal", 2),
        ("image_offset", -1),
        ("image_offset", True),
        ("sha256", "0" * 64),
        ("source_sha256", "0" * 64),
        ("session_id", "other"),
        ("event_id", "forged"),
        ("bytes", 1),
        ("timestamp", None),
    ],
)
def test_forged_snapshot_refuses(
    captures: tuple[Path, Path, OwnerSpy],
    field: str,
    value: Any,
) -> None:
    original, installed, owner = captures
    with verifier(original, installed) as read_selected:
        with pytest.raises(FormatError):
            read_selected({**owner.origins[0], field: value})


def test_extra_missing_and_protected_vendor_positions_are_not_authority(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    with verifier(original, installed) as read_selected:
        for wrong in (
            {**owner.origins[0], "path": ["data", "raw"]},
            {key: value for key, value in owner.origins[0].items() if key != "id"},
            {**owner.origins[0], "image_offset": original.read_bytes().index(b'"choices"')},
        ):
            with pytest.raises(FormatError):
                read_selected(wrong)


@pytest.mark.parametrize("fault", ["source", "installed", "session", "digest-format", "parse"])
def test_bad_generation_or_parser_refuses_before_yield(
    captures: tuple[Path, Path, OwnerSpy],
    fault: str,
) -> None:
    original, installed, _ = captures
    kwargs = {}
    if fault == "source":
        kwargs["source_sha256"] = "0" * 64
    elif fault == "installed":
        kwargs["installed_sha256"] = "0" * 64
    elif fault == "session":
        kwargs["session_id"] = "wrong"
    elif fault == "digest-format":
        kwargs["installed_sha256"] = "A" * 64
    else:
        installed.write_bytes(b'{"event":"tool:post","data":{},"a":1,"a":2}\n')
    with pytest.raises(FormatError), verifier(original, installed, **kwargs):
        pytest.fail("bad generation was admitted")


@pytest.mark.parametrize("which,when", [(0, "lookup"), (1, "lookup"), (0, "exit"), (1, "exit")])
def test_hash_drift_after_indexing_is_detected(
    captures: tuple[Path, Path, OwnerSpy],
    which: int,
    when: str,
) -> None:
    original, installed, owner = captures
    with pytest.raises(FormatError, match="generation changed"):
        with verifier(original, installed) as read_selected:
            with (original, installed)[which].open("ab") as changed:
                changed.write(b" ")
            if when == "lookup":
                read_selected(owner.origins[0])


def test_drift_during_lookup_and_poll_reentrancy_are_detected(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    callback: Callable[[], None] | None = None
    changed = False

    def poll() -> None:
        nonlocal changed
        if callback:
            callback()
        # Change installed generation at a poll boundary during lookup.
        if changed:
            with installed.open("ab") as target:
                target.write(b" ")
            changed = False

    with verifier(original, installed, poll=poll) as read_selected:

        def reenter() -> None:
            read_selected(owner.origins[0])

        callback = reenter
        with pytest.raises(FormatError, match="already in use"):
            read_selected(owner.origins[0])
        callback = None
        assert read_selected(owner.origins[0])["status"] == "present"
    # A poll-observed mutation is never authorized or silently tolerated.
    with pytest.raises(FormatError, match="generation changed"):
        with verifier(original, installed, poll=poll) as read_selected:
            changed = True
            read_selected(owner.origins[0])


def test_installed_value_budget_refuses_without_whole_image_read(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    rows = read_rows(installed)
    rows[0]["data"]["result"]["content"][0] = {"type": "text", "text": "x" * 16384}
    write_rows(installed, rows)
    with verifier(original, installed) as read_selected:
        with pytest.raises(FormatError, match="oversized"):
            read_selected(owner.origins[0])


def test_exact_budget_and_extreme_forged_positions(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    rows = read_rows(installed)
    # Quotes count toward the serialized-node budget, not just string contents.
    rows[0]["data"]["result"]["content"][0] = "x" * (16384 - 2)
    write_rows(installed, rows)
    with verifier(original, installed) as read_selected:
        assert read_selected(owner.origins[0])["value"] == "x" * (16384 - 2)
        for key in ("ordinal", "image_offset", "bytes"):
            with pytest.raises(FormatError, match="exact CI position"):
                read_selected({**owner.origins[0], key: 2**64})


def test_post_read_generation_check_prevents_returning_stale_node(
    captures: tuple[Path, Path, OwnerSpy],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from context_intelligence.optimize import positions

    original, installed, owner = captures
    read_value = positions._installed_value

    def mutate_after_read(*args: Any) -> dict[str, Any]:
        value = read_value(*args)
        with installed.open("ab") as target:
            target.write(b" ")
        return value

    monkeypatch.setattr(positions, "_installed_value", mutate_after_read)
    with pytest.raises(FormatError, match="generation changed"):
        with verifier(original, installed) as read_selected:
            read_selected(owner.origins[0])


def test_indexes_open_once_per_context_and_borrowed_descriptors_remain_open(
    captures: tuple[Path, Path, OwnerSpy],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from context_intelligence.optimize import positions

    original, installed, owner = captures
    calls = []
    actual_index = positions.Index

    class CountingIndex(actual_index):
        def __init__(self, source: BinaryIO, poll: Callable[[], None] | None = None):
            calls.append(source)
            super().__init__(source, poll)

    monkeypatch.setattr(positions, "Index", CountingIndex)
    with original.open("rb") as before, installed.open("rb") as after:
        with capture_image_verifier(
            before,
            after,
            session_id="s",
            source_sha256=digest(original),
            installed_sha256=digest(installed),
        ) as read_selected:
            for snapshot in owner.origins:
                read_selected(snapshot)
        assert calls == [before, after]
        assert not before.closed and not after.closed


def test_huge_original_and_protected_string_peak_below_four_mib(tmp_path: Path) -> None:
    original, installed = tmp_path / "original", tmp_path / "installed"
    size = 12 * 1024 * 1024
    prefix = (
        b'{"event":"tool:post","session_id":"s","data":{"result":{"content":['
        b'{"type":"image","source":{"type":"base64","media_type":"image/png","data":"'
    )
    with original.open("wb") as output:
        output.write(prefix)
        for _ in range(size // CHUNK):
            output.write(b"QUFB" * (CHUNK // 4))
        output.write(b'"}}],"output":"')
        for _ in range(size // CHUNK):
            output.write(b"z" * (CHUNK - 2) + b'\\"')
        output.write(b'"}}}\n')
    # Actual CI path produces the exact snapshot and reference-only installed node.
    owner = stage(original, installed)
    assert len(owner.origins) == 1
    tracemalloc.start()
    try:
        with verifier(original, installed) as read_selected:
            assert read_selected(owner.origins[0])["value"] == owner.replacements[0]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(f"position verifier peak: {peak} bytes")
    assert peak < 4 * 1024 * 1024, peak


@pytest.mark.parametrize("count", [0, 1, 64])
def test_batch_hashes_constant_indexes_once_and_consumes_input_once(
    captures: tuple[Path, Path, OwnerSpy], monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    from context_intelligence.optimize import positions

    original, installed, owner = captures
    hashes, indexes = [], []
    actual_hash, actual_index = positions._check_hash, positions.Index

    def counted_hash(source: BinaryIO, *args: Any) -> None:
        hashes.append(source)
        actual_hash(source, *args)

    class CountingIndex(actual_index):
        def __init__(self, source: BinaryIO, poll: Callable[[], None] | None = None):
            indexes.append(source)
            super().__init__(source, poll)

    class Once:
        def __init__(self) -> None:
            self.iterations = 0
            self.produced = 0

        def __iter__(self) -> Iterator[dict[str, Any]]:
            self.iterations += 1
            assert self.iterations == 1
            return self.generate()

        def generate(self) -> Iterator[dict[str, Any]]:
            for number in range(count):
                # Each input is pulled only as the corresponding result is read.
                assert self.produced == number
                self.produced += 1
                yield owner.origins[number % len(owner.origins)]

    monkeypatch.setattr(positions, "_check_hash", counted_hash)
    monkeypatch.setattr(positions, "Index", CountingIndex)
    inputs = Once()
    with verifier(original, installed) as read_selected:
        assert isinstance(read_selected, CaptureImageVerifier)
        assert len(hashes) == 4  # before + after indexing, both descriptors
        with read_selected.read_many(inputs) as results:
            assert inputs.produced == 0
            assert len(hashes) == 6
            for number in range(count):
                result = next(results)
                assert inputs.produced == number + 1
                assert result["value"] == owner.replacements[number % len(owner.origins)]
                # Result mutation must not mutate the file or the next result.
                result["value"]["text"] = "caller-owned"
            with pytest.raises(StopIteration):
                next(results)
            assert len(hashes) == 6  # no per-row checks, including input EOF
        assert len(hashes) == 8  # final batch check, before any host ACK
    assert len(hashes) == 10  # usual outer check
    assert len(indexes) == 2
    assert inputs.iterations == 1 and inputs.produced == count
    assert hashes[::2] == [indexes[0]] * 5
    assert hashes[1::2] == [indexes[1]] * 5


def test_batch_reordered_duplicate_positions_null_absent_and_inline_are_not_ack_authority(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    rows, old = read_rows(installed), read_rows(original)
    rows[0]["data"]["result"]["content"][0] = None
    rows[0]["data"]["result"]["content"][2] = old[0]["data"]["result"]["content"][2]
    del rows[1]["data"]["raw_request"]
    write_rows(installed, rows)
    order = (3, 0, 2, 1, 0, 2)
    with verifier(original, installed) as read_selected:
        expected = [read_selected(owner.origins[position]) for position in order]
        with read_selected.read_many(owner.origins[position] for position in order) as results:
            for position, value in zip(order, expected, strict=True):
                selected = next(results)
                assert selected == value
                assert set(selected) == {"status", "value", "path"}  # no ACK/expiry seal
                if position == 3:
                    assert selected["status"] == "absent"
                elif position == 0:
                    assert selected["status"] == "present" and selected["value"] is None
                elif position == 1:
                    assert selected["status"] == "present"
                    assert selected["value"]["type"] == "image"  # not a diagnostic ref
            with pytest.raises(StopIteration):
                next(results)
        # Batch exit does not consume the callable or change single-call semantics.
        assert read_selected(owner.origins[0]) == expected[1]
        with pytest.raises(FormatError, match="inactive"):
            next(results)


@pytest.mark.parametrize(
    "when", ["unstarted", "partial", "last-without-eof", "close", "close-at-eof"]
)
def test_batch_early_normal_exit_or_close_never_certifies_exhaustion_and_clears_busy(
    captures: tuple[Path, Path, OwnerSpy], when: str
) -> None:
    original, installed, owner = captures
    with verifier(original, installed) as read_selected:
        with pytest.raises(FormatError, match="fully exhausted|closed"):
            with read_selected.read_many(iter(owner.origins)) as results:
                if when == "partial":
                    next(results)
                elif when == "last-without-eof":
                    for _ in owner.origins:
                        next(results)
                elif when == "close-at-eof":
                    for _ in results:
                        pass
                    getattr(results, "close")()
                elif when == "close":
                    next(results)
                    getattr(results, "close")()
                    with pytest.raises(FormatError, match="closed"):
                        next(results)
        with pytest.raises(FormatError, match="inactive"):
            next(results)
        assert read_selected(owner.origins[0])["status"] == "present"
        with read_selected.read_many(owner.origins[:1]) as again:
            assert len(list(again)) == 1


@pytest.mark.parametrize("fault", ["snapshot", "generator", "poll"])
def test_batch_caught_late_failure_poisons_normal_exit(
    captures: tuple[Path, Path, OwnerSpy], fault: str
) -> None:
    original, installed, owner = captures
    armed = False

    def poll() -> None:
        if armed and fault == "poll":
            raise RuntimeError("synthetic cancellation")

    def snapshots() -> Iterator[dict[str, Any]]:
        yield owner.origins[0]
        if fault == "generator":
            raise RuntimeError("synthetic input failure")
        yield {**owner.origins[1], "sha256": "0" * 64} if fault == "snapshot" else owner.origins[1]

    with verifier(original, installed, poll=poll) as read_selected:
        with pytest.raises(FormatError, match="failed"):
            with read_selected.read_many(snapshots()) as results:
                assert next(results)["status"] == "present"
                armed = True
                with pytest.raises((FormatError, RuntimeError)):
                    next(results)
                armed = False
                # A closed/failed input generator must not look like successful EOF.
                with pytest.raises(FormatError, match="failed"):
                    next(results)
        armed = False
        assert read_selected(owner.origins[0])["status"] == "present"


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("when", ["entry", "iter", "middle", "last-read", "exhausted", "empty"])
def test_batch_generation_drift_refuses_before_acceptance(
    captures: tuple[Path, Path, OwnerSpy], which: int, when: str
) -> None:
    original, installed, owner = captures

    def mutate() -> None:
        with (original, installed)[which].open("ab") as changed:
            changed.write(b" ")

    class Inputs:
        def __iter__(self) -> Iterator[dict[str, Any]]:
            if when == "iter":
                mutate()
            return iter([] if when == "empty" else owner.origins)

    accepted = False
    with pytest.raises(FormatError, match="generation changed"):
        with verifier(original, installed) as read_selected:
            if when == "entry":
                mutate()
            with read_selected.read_many(Inputs()) as results:
                if when == "middle":
                    next(results)
                    mutate()
                if when == "last-read":
                    for _ in owner.origins:
                        next(results)
                    mutate()
                list(results)
                if when in ("exhausted", "empty"):
                    mutate()
            accepted = True  # host may start considering ACK only here
    assert not accepted


@pytest.mark.parametrize("operation", ["single", "batch"])
@pytest.mark.parametrize("boundary", ["iter", "next", "poll", "exit-poll"])
def test_batch_reentrancy_refuses_and_cleanup_allows_reuse(
    captures: tuple[Path, Path, OwnerSpy], operation: str, boundary: str
) -> None:
    original, installed, owner = captures
    callback: Callable[[], None] | None = None

    def poll() -> None:
        if callback:
            callback()

    with verifier(original, installed, poll=poll) as read_selected:

        def reenter() -> None:
            if operation == "single":
                read_selected(owner.origins[0])
            else:
                with read_selected.read_many([]):
                    pytest.fail("reentrant batch admitted")

        class Inputs:
            def __iter__(self) -> Iterator[dict[str, Any]]:
                if boundary == "iter":
                    reenter()
                return self.generate()

            def generate(self) -> Iterator[dict[str, Any]]:
                if boundary == "next":
                    reenter()
                yield owner.origins[0]

        if boundary == "poll":
            callback = reenter
        with pytest.raises(FormatError, match="already in use"):
            with read_selected.read_many(Inputs()) as results:
                list(results)
                if boundary == "exit-poll":
                    callback = reenter
        callback = None
        assert read_selected(owner.origins[0])["status"] == "present"
        with read_selected.read_many([]) as empty:
            assert list(empty) == []


@pytest.mark.parametrize("boundary", ["next", "poll"])
def test_recursive_batch_next_caught_in_callback_still_poisons(
    captures: tuple[Path, Path, OwnerSpy], boundary: str
) -> None:
    original, installed, owner = captures
    callback: Callable[[], None] | None = None

    def poll() -> None:
        if callback:
            callback()

    def snapshots() -> Iterator[dict[str, Any]]:
        if boundary == "next":
            reenter()
        yield owner.origins[0]

    def reenter() -> None:
        with pytest.raises(FormatError, match="already in use"):
            next(results)

    with verifier(original, installed, poll=poll) as read_selected:
        with pytest.raises(FormatError, match="failed"):
            with read_selected.read_many(snapshots()) as results:
                if boundary == "poll":
                    callback = reenter
                with pytest.raises(FormatError, match="failed"):
                    next(results)
                callback = None
        assert read_selected(owner.origins[0])["status"] == "present"


def test_snapshot_is_canonical_before_poll_mutates_caller_dict_in_single_and_batch(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    selected: dict[str, Any] | None = None

    def poll() -> None:
        if selected is not None:
            selected.update(owner.origins[1])

    with verifier(original, installed, poll=poll) as read_selected:
        selected = dict(owner.origins[0])
        assert read_selected(selected)["value"] == owner.replacements[0]
        selected = dict(owner.origins[0])
        with read_selected.read_many([selected]) as results:
            # Entry hashing can mutate the dict: reset before the iterator reads it.
            selected.update(owner.origins[0])
            assert next(results)["value"] == owner.replacements[0]
            with pytest.raises(StopIteration):
                next(results)
        selected = None


def test_outer_exit_invalidates_escaped_batch_and_refuses_active_batch(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    outer = verifier(original, installed)
    read_selected = outer.__enter__()
    scope = read_selected.read_many(owner.origins)
    results = scope.__enter__()
    assert next(results)["status"] == "present"
    with pytest.raises(FormatError, match="already in use"):
        outer.__exit__(None, None, None)
    with pytest.raises(FormatError, match="inactive"):
        next(results)
    with pytest.raises(FormatError, match="inactive"):
        scope.__exit__(None, None, None)
    with pytest.raises(FormatError, match="inactive"):
        with read_selected.read_many([]):
            pytest.fail("closed outer verifier admitted batch")


def test_batch_exception_exit_preserves_original_error_and_expires_iterator(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    with verifier(original, installed) as read_selected:
        with pytest.raises(RuntimeError, match="caller failure"):
            with read_selected.read_many(owner.origins) as results:
                next(results)
                raise RuntimeError("caller failure")
        with pytest.raises(FormatError, match="inactive"):
            next(results)
        assert read_selected(owner.origins[0])["status"] == "present"


def test_single_lookup_poll_cannot_enter_batch_and_busy_clears(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    callback: Callable[[], None] | None = None

    def poll() -> None:
        if callback:
            callback()

    with verifier(original, installed, poll=poll) as read_selected:

        def reenter() -> None:
            with read_selected.read_many([]):
                pytest.fail("single lookup admitted a batch")

        callback = reenter
        with pytest.raises(FormatError, match="already in use"):
            read_selected(owner.origins[0])
        callback = None
        with read_selected.read_many(owner.origins) as results:
            assert len(list(results)) == len(owner.origins)


def test_callback_stop_iteration_is_not_input_exhaustion(
    captures: tuple[Path, Path, OwnerSpy],
) -> None:
    original, installed, owner = captures
    armed = False

    def poll() -> None:
        if armed:
            raise StopIteration

    with verifier(original, installed, poll=poll) as read_selected:
        with pytest.raises(FormatError, match="failed"):
            with read_selected.read_many(owner.origins) as results:
                next(results)
                armed = True
                # A consuming for-loop sees StopIteration as EOF. It is still
                # not real input EOF and cannot validate the provisional batch.
                assert list(results) == []
                armed = False
        assert read_selected(owner.origins[0])["status"] == "present"


@pytest.mark.parametrize("hash_poll", [2, 3], ids=["original-hash", "installed-hash"])
@pytest.mark.parametrize("empty", [False, True], ids=["selected", "empty"])
def test_close_during_final_hash_poll_refuses_exhausted_batch(
    captures: tuple[Path, Path, OwnerSpy], hash_poll: int, empty: bool
) -> None:
    original, installed, owner = captures
    # Each actual descriptor hash has one chunk/poll, preceded by check()'s poll.
    assert 0 < original.stat().st_size < CHUNK
    assert 0 < installed.stat().st_size < CHUNK
    armed, accepted = False, False
    polls, closes = 0, 0

    def poll() -> None:
        nonlocal polls, closes
        if not armed:
            return
        polls += 1
        if polls == hash_poll:
            getattr(results, "close")()
            closes += 1
            # Final hashing must keep the iterator inactive and owner busy.
            with pytest.raises(FormatError, match="inactive"):
                next(results)
            with pytest.raises(FormatError, match="already in use"):
                read_selected(owner.origins[0])

    with verifier(original, installed, poll=poll) as read_selected:
        with pytest.raises(FormatError, match="failed|closed"):
            with read_selected.read_many([] if empty else owner.origins) as results:
                assert len(list(results)) == (0 if empty else len(owner.origins))
                armed = True
            accepted = True  # no host ACK may become eligible after cancellation
        armed = False
        assert closes == 1 and polls == 3
        assert not accepted
        with pytest.raises(FormatError, match="inactive"):
            next(results)
        assert read_selected(owner.origins[0])["status"] == "present"


def test_large_many_image_batch_streams_below_four_mib(tmp_path: Path) -> None:
    original, installed = tmp_path / "original", tmp_path / "installed"
    count = 1024
    # Many actual canonical positions plus large protected strings: neither the
    # batch nor either index may retain full records or all the returned nodes.
    with original.open("wb") as output:
        for number in range(count):
            row = record(image(png(number % 256)), event_id=str(number))
            row["data"]["result"]["output"] = "z" * 8192
            output.write(json.dumps(row).encode() + b"\n")
    owner = stage(original, installed)
    assert len(owner.origins) == count
    tracemalloc.start()
    try:
        with verifier(original, installed) as read_selected:
            with read_selected.read_many(snapshot for snapshot in owner.origins) as results:
                seen = 0
                for selected in results:
                    assert selected["value"] == owner.replacements[seen]
                    seen += 1
                assert seen == count
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(f"batch position verifier peak ({count} images): {peak} bytes")
    assert peak < 4 * 1024 * 1024, peak
