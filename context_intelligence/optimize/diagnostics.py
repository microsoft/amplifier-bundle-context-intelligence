"""Bounded, CI-owner diagnostic expiration, not a generic JSON transformer.

Policy/sidecar reachability/ownership stay with prune.operations and the host.
This helper neither opens a path nor deletes an asset. It rewrites only the exact
direct raw fields and owner-local provider_payloads descriptors selected by that
policy. No Foundation or application imports, blob/network/pixel hydration.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, BinaryIO

from ._json import FormatError, Index
from .capture import _copy, _projection

FIELDS = frozenset(("raw", "raw_request", "raw_response"))


def _markers(projection: dict[str, Any], value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value.keys() <= FIELDS:
        raise FormatError("expiration callback must select known diagnostic fields")
    descriptors = projection["provider_payloads"]
    if not isinstance(descriptors, dict):
        if value:
            raise FormatError("diagnostic expiration lacks owned descriptors")
        return {}
    result = {}
    for field, marker in value.items():
        descriptor = descriptors.get(field)
        if (
            not isinstance(descriptor, dict)
            or not isinstance(marker, dict)
            or set(marker) != {"v", "sha256", "bytes", "expired_at", "reason"}
            or type(marker.get("v")) is not int
            or marker["v"] != 1
            or marker.get("reason") != "retention"
            or not isinstance(marker.get("sha256"), str)
            or not re.fullmatch("[0-9a-f]{64}", marker["sha256"])
            or type(marker.get("bytes")) is not int
            or marker["bytes"] < 0
            or marker["sha256"] != descriptor.get("sha256")
            or marker["bytes"] != descriptor.get("bytes")
            or type(descriptor.get("bytes")) is not int
            or descriptor.get("redacted") is not True
            or field in projection["blob_ref_fields"]
        ):
            raise FormatError("expiration marker does not match owned descriptor")
        expired_at = marker.get("expired_at")
        try:
            if not isinstance(expired_at, str):
                raise ValueError
            when = datetime.fromisoformat(expired_at)
            if not expired_at.endswith("+00:00") or when.utcoffset() != timezone.utc.utcoffset(
                when
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise FormatError("expiration requires an explicit UTC timestamp") from exc
        result[field] = dict(marker)
    expired = projection["provider_payloads_expired"]
    if result and expired is not None:
        if not isinstance(expired, dict):
            raise FormatError("unsupported diagnostic expiration metadata")
        for field, marker in result.items():
            if field in expired and expired[field] != marker:
                raise FormatError("conflicting prior diagnostic expiration marker")
    return result


def _data(index: Index, data: int, output: BinaryIO, selected: dict[str, dict[str, Any]]) -> None:
    """Copy unknown key/value spans unchanged; reconstruct only punctuation."""
    expired_node = index.child(data, "provider_payloads_expired")
    expired = index.bounded_value(expired_node, 16384)
    if expired is not None and not isinstance(expired, dict):
        raise FormatError("unsupported diagnostic expiration metadata")
    expired = expired or {}
    for field in selected:
        if field in expired and expired[field] != selected[field]:
            raise FormatError("conflicting prior diagnostic expiration marker")
    expired = {**expired, **selected}
    encoded = json.dumps(expired, ensure_ascii=True, allow_nan=False).encode()
    if len(encoded) > 16384:
        raise FormatError("oversized diagnostic expiration metadata")
    output.write(b"{")
    comma = False
    for key, start, end, node in index.db.execute(
        "SELECT key,member_start,end,id FROM node WHERE parent=? ORDER BY start", (data,)
    ):
        if key in selected or key == "provider_payloads_expired":
            continue
        if comma:
            output.write(b",")
        comma = True
        if key == "provider_payloads":
            output.write(b'"provider_payloads":{')
            inner_comma = False
            for field, member, finish in index.db.execute(
                "SELECT key,member_start,end FROM node WHERE parent=? ORDER BY start", (node,)
            ):
                if field in selected:
                    continue
                if inner_comma:
                    output.write(b",")
                _copy(index.source, output, member, finish)
                inner_comma = True
            output.write(b"}")
        else:
            _copy(index.source, output, start, end)
    if comma:
        output.write(b",")
    output.write(b'"provider_payloads_expired":' + encoded + b"}")


def rewrite_diagnostics(
    source: BinaryIO,
    output: BinaryIO | None,
    *,
    session_id: str,
    expire: Callable[[dict[str, Any]], dict[str, dict[str, Any]]],
    poll: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Expire exact selected diagnostics using the frozen shared tombstone schema.

    ``expire`` receives the bounded projection, never raw bodies. Return a map
    of field -> {v:1,sha256,bytes,expired_at:UTC+00:00,reason:'retention'} matching
    the live descriptor. Empty map copies the event unchanged. Caller MUST own
    maintenance/recovery/writer exclusion, reference closure and durable receipt;
    output here is only a candidate stream. Use write_record_mapping before any
    later replacement/index UPDATE. Unknown/malformed descriptors fail closed.
    """
    counts = {"records": 0, "changed_records": 0, "expired_fields": 0}
    with Index(source, poll) as index:
        at = 0
        for ordinal, root in index.roots():
            projection = _projection(index, ordinal, root, session_id)
            selected = _markers(projection, expire(copy.deepcopy(projection)))
            counts["records"] += 1
            if not selected:
                continue
            counts["changed_records"] += 1
            counts["expired_fields"] += len(selected)
            data = index.child(root, "data")
            assert data is not None
            if output is not None:
                start, end = index.span(data)
                _copy(source, output, at, start)
                _data(index, data, output, selected)
                at = end
        if output is not None:
            source.seek(0, 2)
            _copy(source, output, at, source.tell())
    return counts
