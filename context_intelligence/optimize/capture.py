"""CI envelope / canonical rich-content historical adapter, with no network I/O.

Only actual structured Core content arrays are visited. JSON-looking human text,
tool output strings, vendor wire schemas and $blob_ref values are never promoted.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from itertools import zip_longest
from typing import Any, BinaryIO, Protocol

from ._json import CHUNK, FormatError, Index


@dataclass(frozen=True)
class StagedImage:
    """Owner-supplied reference-only diagnostic replacement and recovery token.

    The actual media owner validates pixels/provenance/expiry and owns the
    replacement reader contract. CI does not manufacture SessionBlobRefV1.
    """

    replacement: dict[str, Any]
    receipt: dict[str, Any]
    new_asset_bytes: int


class ImageOwner(Protocol):
    def stage_image(self, image: BinaryIO, *, mime: str, origin: dict[str, Any]) -> StagedImage: ...

    def publish(self, manifest: Path) -> None:
        """Idempotently fsync assets first, reconciling saved owner receipts."""
        ...

    def verify(self, manifest: Path) -> None:
        """Verify exact accepted refs, bytes and reference closure without hydration."""
        ...

    def acknowledge(self, manifest: Path, *, source: Path, sha256: str) -> None:
        """Reconcile local historical-file commit; NOT historical ingestion replay."""
        ...


def _envelope(index: Index, root: int, session_id: str) -> tuple[str, int]:
    event = index.bounded_value(index.child(root, "event"))
    data = index.child(root, "data")
    if not isinstance(event, str) or not event or index.kind(data) != "object":
        raise FormatError("unsupported CI event envelope")
    schema = index.child(root, "schema")
    if schema is not None and index.bounded_value(schema) != {
        "name": "amplifier.log",
        "ver": "1.0.0",
    }:
        raise FormatError("unsupported CI event schema")
    sid = index.bounded_value(index.child(root, "session_id"))
    if sid is not None and sid != session_id:
        raise FormatError("capture session identity mismatch")
    assert data is not None
    return event, data


def _images(index: Index, root: int, session_id: str) -> Iterator[int]:
    event, data = _envelope(index, root, session_id)
    if event == "tool:post":
        result = index.child(data, "result")
        # A string result is not a producer-proven rich-content envelope.
        if index.kind(result) == "object":
            content = index.child(result, "content")
            if index.kind(content) == "array":
                yield from _image_blocks(index, content)
    elif event in ("llm:request", "llm:response"):
        for field in ("raw", "raw_request", "raw_response"):
            raw = index.child(data, field)
            if index.kind(raw) != "object":
                continue
            messages = index.child(raw, "messages")
            if index.kind(messages) != "array":
                continue
            for message in index.children(messages):
                # Canonical roles only; not a recursive vendor-payload search.
                if index.bounded_value(index.child(message, "role")) not in (
                    "user",
                    "assistant",
                    "tool",
                    "system",
                    "developer",
                ):
                    continue
                content = index.child(message, "content")
                if index.kind(content) == "array":
                    yield from _image_blocks(index, content)


def _image_blocks(index: Index, content: int | None) -> Iterator[int]:
    for block in index.children(content):
        if index.small(index.child(block, "type")) == "image":
            yield block


def _source(index: Index, image: int) -> tuple[str, int] | None:
    if index.db.execute(
        "SELECT 1 FROM node WHERE parent=? AND key NOT IN ('type','source','visibility') LIMIT 1",
        (image,),
    ).fetchone():
        return None  # unknown image owner metadata is protected, never discarded
    source = index.child(image, "source")
    if index.kind(source) != "object":
        return None
    fields = index.db.execute("SELECT count(*) FROM node WHERE parent=?", (source,)).fetchone()[0]
    if (
        fields != 3
        or index.db.execute(
            "SELECT 1 FROM node WHERE parent=? AND key NOT IN ('type','media_type','data') LIMIT 1",
            (source,),
        ).fetchone()
    ):
        return None
    mime = index.small(index.child(source, "media_type"))
    data = index.child(source, "data")
    if (
        index.small(index.child(source, "type")) != "base64"
        or mime not in ("image/png", "image/jpeg")
        or index.kind(data) != "string"
    ):
        return None
    assert data is not None and mime is not None
    return mime, data


def _decoded_image(index: Index, node: int, output: BinaryIO | None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    pending = b""
    ended = False
    try:
        for part in index.decoded(node):
            if ended and part:
                raise FormatError("base64 data after padding")
            pending += part
            # Keep the last quartet for strict final padding validation.
            count = max(0, ((len(pending) - 4) // 4) * 4)
            if count:
                block, pending = pending[:count], pending[count:]
                if b"=" in block:
                    raise FormatError("base64 data after padding")
                decoded = base64.b64decode(block, validate=True)
                digest.update(decoded)
                size += len(decoded)
                if output:
                    output.write(decoded)
            ended = b"=" in pending and len(pending) == 4
        decoded = base64.b64decode(pending, validate=True)
        if not pending or len(pending) % 4 or base64.b64encode(decoded) != pending:
            raise FormatError("noncanonical or empty base64 image")
        digest.update(decoded)
        size += len(decoded)
        if output:
            output.write(decoded)
    except (binascii.Error, ValueError) as exc:
        raise FormatError("invalid canonical image base64") from exc
    return digest.hexdigest(), size


def iter_capture_projection(
    source: BinaryIO, *, session_id: str, poll: Callable[[], None] | None = None
) -> Iterator[dict[str, Any]]:
    """Read-only bounded CI fields for owner-local sidecar/reference inventory.

    The caller owns opening/fencing the source. No raw/result bodies, pixels,
    blob fetches or inferred canonical references are returned. Oversized
    metadata fails closed instead of silently claiming complete coverage.
    """
    with Index(source, poll) as index:
        for ordinal, root in index.roots():
            yield _projection(index, ordinal, root, session_id)


def _projection(index: Index, ordinal: int, root: int, session_id: str) -> dict[str, Any]:
    event, data = _envelope(index, root, session_id)
    result: dict[str, Any] = {"ordinal": ordinal, "event": event}
    for field in ("id", "event_id", "ts", "timestamp", "session_id", "idempotency_key"):
        result[field] = index.bounded_value(index.child(root, field))
    result["schema"] = index.bounded_value(index.child(root, "schema"))
    result["data_timestamp"] = index.bounded_value(index.child(data, "timestamp"))
    result["expired"] = index.bounded_value(index.child(data, "expired"), 16384)
    result["provider_payloads"] = index.bounded_value(index.child(data, "provider_payloads"), 16384)
    result["provider_payloads_expired"] = index.bounded_value(
        index.child(data, "provider_payloads_expired"), 16384
    )
    result["opaque_fields"] = {
        field: index.kind(index.child(data, field))
        for field in ("raw", "raw_request", "raw_response", "result")
    }
    result["blob_ref_fields"] = [
        field
        for field in ("raw", "raw_request", "raw_response")
        if index.child(index.child(data, field), "$blob_ref") is not None
    ]
    return result


def _copy(source: BinaryIO, output: BinaryIO, start: int, end: int) -> None:
    source.seek(start)
    remaining = end - start
    while remaining:
        block = source.read(min(CHUNK, remaining))
        if not block:
            raise FormatError("source changed during bounded copy")
        output.write(block)
        remaining -= len(block)


def _range_fact(
    source: BinaryIO, start: int, end: int, poll: Callable[[], None] | None
) -> dict[str, Any]:
    digest = hashlib.sha256()
    source.seek(start)
    remaining = end - start
    while remaining:
        if poll:
            poll()
        block = source.read(min(CHUNK, remaining))
        if not block:
            raise FormatError("source changed while mapping records")
        digest.update(block)
        remaining -= len(block)
    return {"offset": start, "bytes": end - start, "sha256": digest.hexdigest()}


def write_record_mapping(
    old: BinaryIO,
    new: BinaryIO,
    output: BinaryIO,
    *,
    session_id: str,
    poll: Callable[[], None] | None = None,
) -> int:
    """Persist all record addresses for index UPDATE, never delete/re-enroll.

    Header binds complete old/new files; each row binds one physical nonblank
    line INCLUDING its newline and surrounding horizontal whitespace. JSON
    token spans are supplied separately. Blank lines stay in the file and hash
    but are not fictitious events. No corpus-sized Python collections.
    """

    def emit(value: dict[str, Any]) -> None:
        output.write(json.dumps(value, ensure_ascii=True, allow_nan=False).encode() + b"\n")

    def identity(index: Index, root: int, key: str) -> str | None:
        node = index.child(root, key)
        if node is None or (index.kind(node) == "scalar" and index.small(node) == "null"):
            return None
        if index.kind(node) != "string":
            # Unknown producer types stay protected. json.loads on a floating
            # identity would round its lexeme before facts reached an index.
            raise FormatError("unsupported capture identity field type")
        return index.bounded_value(node)

    old.seek(0, 2)
    old_size = old.tell()
    new.seek(0, 2)
    new_size = new.tell()
    emit(
        {
            "type": "header",
            "format": "ci-optimize-record-mapping",
            "version": 1,
            "span": "physical-line-including-newline",
            "old": _range_fact(old, 0, old_size, poll),
            "new": _range_fact(new, 0, new_size, poll),
        }
    )
    old.seek(0)
    new.seek(0)
    count = 0
    with Index(old, poll) as before, Index(new, poll) as after:
        for pair in zip_longest(before.roots(), after.roots()):
            first, second = pair
            if first is None or second is None:
                raise FormatError("conversion changed capture record count")
            ordinal, root = first
            new_ordinal, new_root = second
            event, _ = _envelope(before, root, session_id)
            new_event, _ = _envelope(after, new_root, session_id)
            facts: dict[str, Any] = {"ordinal": ordinal, "event": event}
            if ordinal != new_ordinal or event != new_event:
                raise FormatError("conversion changed record identity")
            for output_key, key in (
                ("timestamp", "timestamp"),
                ("ts", "ts"),
                ("eventId", "event_id"),
                ("id", "id"),
                ("idempotency_key", "idempotency_key"),
            ):
                value = identity(before, root, key)
                if value != identity(after, new_root, key):
                    raise FormatError("conversion changed event identity fields")
                facts[output_key] = value
            facts["old"] = _range_fact(old, *before.record_span(ordinal), poll)
            facts["new"] = _range_fact(new, *after.record_span(ordinal), poll)
            facts["old_json"] = _range_fact(old, *before.span(root), poll)
            facts["new_json"] = _range_fact(new, *after.span(new_root), poll)
            emit(facts)
            count += 1
    return count


def rewrite_capture(
    source: BinaryIO,
    output: BinaryIO | None,
    *,
    session_id: str,
    source_sha256: str,
    media: ImageOwner | None = None,
    manifest: BinaryIO | None = None,
    poll: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Preview or stage known images, copying every non-target span verbatim.

    No producer-proven JSON-in-string schema is enabled yet. That coverage is
    explicit in ``protected_strings``; the incremental decoder itself handles
    huge escaped strings without materializing them.
    """
    if media is not None and (manifest is None or output is None):
        raise ValueError("staging requires an output and manifest")
    counts = dict(
        records=0,
        images=0,
        unsupported=0,
        protected_strings=0,
        inline_bytes=0,
        decoded_bytes=0,
        new_asset_bytes=0,
    )
    with Index(source, poll) as index:
        index.db.execute("CREATE TABLE replacement(start INTEGER,end INTEGER,value BLOB)")
        for ordinal, root in index.roots():
            event, data = _envelope(index, root, session_id)
            counts["records"] += 1
            for field in ("raw", "raw_request", "raw_response", "result"):
                if index.kind(index.child(data, field)) == "string":
                    counts["protected_strings"] += 1
            for image in _images(index, root, session_id):
                selected = _source(index, image)
                if selected is None:
                    counts["unsupported"] += 1
                    continue
                mime, encoded = selected
                with tempfile.TemporaryFile() as decoded:
                    digest, size = _decoded_image(index, encoded, decoded if media else None)
                    counts["images"] += 1
                    counts["decoded_bytes"] += size
                    start, end = index.span(image)
                    counts["inline_bytes"] += end - start
                    if media is None:
                        continue
                    decoded.seek(0)
                    origin = {
                        "session_id": session_id,
                        "source_sha256": source_sha256,
                        "ordinal": ordinal,
                        "event": event,
                        "image_offset": start,
                        "sha256": digest,
                        "bytes": size,
                        "timestamp": index.bounded_value(index.child(root, "timestamp")),
                        "ts": index.bounded_value(index.child(root, "ts")),
                        "tool_call_id": index.bounded_value(index.child(data, "tool_call_id")),
                        "id": index.bounded_value(index.child(root, "id")),
                        "event_id": index.bounded_value(index.child(root, "event_id")),
                    }
                    visibility = index.child(image, "visibility")
                    visibility_value = index.bounded_value(visibility)
                    staged = media.stage_image(decoded, mime=mime, origin=copy.deepcopy(origin))
                    replacement_value = dict(staged.replacement)
                    if visibility is not None:
                        replacement_value["visibility"] = visibility_value
                    replacement = json.dumps(
                        replacement_value, ensure_ascii=True, allow_nan=False, separators=(",", ":")
                    ).encode()
                    receipt = json.dumps(
                        {"origin": origin, "owner": staged.receipt},
                        ensure_ascii=True,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode()
                    if len(replacement) > 16384 or len(receipt) > 16384:
                        raise FormatError("oversized media owner receipt")
                    if type(staged.new_asset_bytes) is not int or staged.new_asset_bytes < 0:
                        raise FormatError("invalid media owner accounting")
                    counts["new_asset_bytes"] += staged.new_asset_bytes
                    index.db.execute(
                        "INSERT INTO replacement VALUES(?,?,?)", (start, end, replacement)
                    )
                    if manifest is None:
                        raise ValueError("staging requires a manifest")
                    manifest.write(receipt + b"\n")
        if output is not None:
            at = 0
            for start, end, value in index.db.execute(
                "SELECT start,end,value FROM replacement ORDER BY start"
            ):
                if start < at:
                    raise FormatError("overlapping canonical image spans")
                _copy(source, output, at, start)
                output.write(value)
                at = end
            source.seek(0, 2)
            _copy(source, output, at, source.tell())
    return counts
