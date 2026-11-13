"""Fixed CI diagnostic-retention adapter over the owner's bounded span reader.

No raw values or arbitrary JSON-looking strings are materialized. Non-target
key/value spans are copied exactly. This is not a configurable transformation
framework, and it grants neither writer exclusion nor deletion authority.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from typing import Any, BinaryIO, Callable, Iterator

from context_intelligence.optimize._json import CHUNK, FormatError, Index
from context_intelligence.optimize.capture import _copy

from .coordination import PruneError
from .policy import Policy, prune_record
from .sidecars import Sidecars


def _projection(index: Index, root: int) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in ("event", "timestamp", "ts"):
        node = index.child(root, field)
        if node is not None:
            record[field] = index.bounded_value(node)
    schema = index.child(root, "schema")
    if schema is not None and index.bounded_value(schema) != {
        "name": "amplifier.log",
        "ver": "1.0.0",
    }:
        raise FormatError("unsupported event schema")
    data = index.child(root, "data")
    if index.kind(data) != "object":
        return record
    projected: dict[str, Any] = {}
    for field in ("timestamp", "provider_payloads", "provider_payloads_expired"):
        node = index.child(data, field)
        if node is not None:
            projected[field] = index.bounded_value(node, 16384)
    for field in ("raw", "raw_request", "raw_response"):
        node = index.child(data, field)
        if node is not None:
            projected[field] = (
                {"$blob_ref": True} if index.child(node, "$blob_ref") is not None else None
            )
    record["data"] = projected
    return record


def _data(
    index: Index, node: int, output: BinaryIO, old: dict[str, Any], new: dict[str, Any]
) -> None:
    output.write(b"{")
    comma = False
    emitted = set()
    for key, member, finish in index.db.execute(
        "SELECT key,member_start,end FROM node WHERE parent=? ORDER BY start", (node,)
    ):
        if key in old and key not in new:
            continue
        if comma:
            output.write(b",")
        comma = True
        emitted.add(key)
        if key in ("provider_payloads", "provider_payloads_expired") and new.get(key) != old.get(
            key
        ):
            output.write(json.dumps(key).encode() + b":")
            output.write(json.dumps(new[key], ensure_ascii=True, allow_nan=False).encode())
        else:
            _copy(index.source, output, member, finish)
    for key in ("provider_payloads", "provider_payloads_expired"):
        if key not in emitted and key in new:
            if comma:
                output.write(b",")
            comma = True
            output.write(json.dumps(key).encode() + b":")
            output.write(json.dumps(new[key], ensure_ascii=True, allow_nan=False).encode())
    output.write(b"}")


def line_facts(
    source: BinaryIO, poll: Callable[[], None] | None = None
) -> Iterator[tuple[int, int, str]]:
    """Offset/size/hash per physical line, with bounded enormous-line memory."""
    source.seek(0)
    offset = 0
    size = 0
    digest = hashlib.sha256()
    while block := source.read(CHUNK):
        if poll:
            poll()
        at = 0
        while at < len(block):
            newline = block.find(b"\n", at)
            end = newline + 1 if newline >= 0 else len(block)
            piece = block[at:end]
            digest.update(piece)
            size += len(piece)
            at = end
            if newline >= 0:
                yield offset, size, digest.hexdigest()
                offset += size
                size = 0
                digest = hashlib.sha256()
    if size:
        yield offset, size, digest.hexdigest()


def stream(
    source: BinaryIO,
    output: BinaryIO | None,
    policy: Policy,
    result: Any,
    poll: Callable[[], None] | None,
    sidecars: Sidecars,
    observe: bool,
) -> str:
    """Inventory or rewrite one fenced capture; caller owns durable commit."""
    source.seek(0)
    digest = hashlib.sha256()
    while block := source.read(CHUNK):
        if poll:
            poll()
        digest.update(block)
    source.seek(0)
    try:
        with Index(source, poll) as index, tempfile.TemporaryFile() as candidate:
            at = 0
            for _, root in index.roots():
                if poll:
                    poll()
                record = _projection(index, root)
                if observe:
                    sidecars.observe(record)
                    continue
                replacement, undated = prune_record(record, policy, sidecars=sidecars)
                result.missing_timestamps += int(undated)
                if replacement is record:
                    continue
                result.payloads += 1
                data = index.child(root, "data")
                assert data is not None
                start, end = index.span(data)
                _copy(source, candidate, at, start)
                _data(index, data, candidate, record["data"], replacement["data"])
                at = end
            if observe:
                return digest.hexdigest()
            source.seek(0, 2)
            _copy(source, candidate, at, source.tell())
            candidate.flush()
            result.bytes_after = candidate.tell()
            old_lines = line_facts(source, poll)
            new_lines = line_facts(candidate, poll)
            for ordinal, old in enumerate(old_lines, 1):
                new = next(new_lines, None)
                if new is None:
                    raise FormatError("diagnostic rewrite lost physical records")
                sidecars.record_fact(ordinal, old, new)
                result.lines = ordinal
            if next(new_lines, None) is not None:
                raise FormatError("diagnostic rewrite added physical records")
            if output is not None:
                candidate.seek(0)
                while block := candidate.read(CHUNK):
                    if poll:
                        poll()
                    output.write(block)
        return digest.hexdigest()
    except FormatError as exc:
        raise PruneError("invalid JSON or unsupported diagnostic event; source unchanged") from exc
