"""Level 1: pure retention policy; no filesystem, network, Click or runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ](?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:[.,]([0-9]+))?(?:Z|[+-](?:[01][0-9]|2[0-3]):?[0-5][0-9])"
)


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h|d|w)", value)
    if not match:
        raise ValueError("use a nonnegative duration with s, m, h, d or w (e.g. 14d)")
    try:
        return timedelta(
            seconds=float(match[1])
            * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match[2]]
        )
    except (OverflowError, ValueError) as exc:
        raise ValueError("duration is too large") from exc


def parse_timestamp(value: Any) -> datetime | None:
    # fromisoformat normalizes invalid offset minutes (e.g. +00:60).
    # Validate the accepted spelling before letting it construct the instant.
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _timestamp_identity(value: Any) -> tuple[datetime, str] | None:
    """Compare submicrosecond digits without datetime's silent truncation.

    Calendar timestamps with seconds and whole-minute UTC offsets cover both
    supported logger formats. Retain other spellings rather than guess precision.
    The datetime floor is conservative for comparison with a datetime cutoff.
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        return None
    return parsed, (match[1] or "")[6:].rstrip("0")


def event_timestamp(record: dict[str, Any]) -> datetime | None:
    values = [record[key] for key in ("timestamp", "ts") if key in record]
    if isinstance(record.get("data"), dict) and "timestamp" in record["data"]:
        values.append(record["data"]["timestamp"])
    parsed = [_timestamp_identity(value) for value in values]
    first = parsed[0] if parsed else None
    if first is None or any(value is None or value != first for value in parsed):
        return None
    return first[0]


@dataclass(frozen=True)
class Policy:
    cutoff: datetime | None = None
    include_responses: bool = False

    def __post_init__(self) -> None:
        if type(self.include_responses) is not bool:
            raise ValueError("include_responses must be a boolean")
        if self.cutoff is not None and self.cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")


def prune_record(record: dict[str, Any], policy: Policy) -> tuple[dict[str, Any], bool]:
    """Return unchanged identity or a detached replacement; never mutate input.

    A raw dictionary with ANY $blob_ref key is retained, even if unrecognized:
    deleting a retrieval reference saves negligible bytes, not its remote blob.
    """
    targets = ("llm:request", "llm:response") if policy.include_responses else ("llm:request",)
    data = record.get("data")
    if record.get("event") not in targets or not isinstance(data, dict) or "raw" not in data:
        return record, False
    if isinstance(data["raw"], dict) and "$blob_ref" in data["raw"]:
        return record, False
    if policy.cutoff is not None:
        timestamp = event_timestamp(record)
        if timestamp is None:
            return record, True
        if timestamp >= policy.cutoff:
            return record, False
    replacement = dict(record)
    replacement["data"] = {key: value for key, value in data.items() if key != "raw"}
    return replacement, False
