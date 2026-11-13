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
    diagnostic_sidecars: bool = False
    response_cutoff: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.include_responses) is not bool:
            raise ValueError("include_responses must be a boolean")
        if type(self.diagnostic_sidecars) is not bool:
            raise ValueError("diagnostic_sidecars must be a boolean")
        if self.cutoff is not None and self.cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        if self.response_cutoff is not None:
            if not self.include_responses:
                raise ValueError("response_cutoff requires include_responses")
            if self.response_cutoff.utcoffset() is None:
                raise ValueError("response_cutoff must be timezone-aware")


def diagnostic_fields(record: dict[str, Any], policy: Policy) -> tuple[tuple[str, ...], bool]:
    """Exact event/direction adapter. Never infer direction from arbitrary fields."""
    event = record.get("event")
    if event == "llm:request":
        fields = ("raw", "raw_request") if policy.diagnostic_sidecars else ("raw",)
        cutoff = policy.cutoff
    elif event == "llm:response" and policy.include_responses:
        fields = ("raw", "raw_response") if policy.diagnostic_sidecars else ("raw",)
        cutoff = policy.response_cutoff if policy.response_cutoff is not None else policy.cutoff
    else:
        return (), False
    if cutoff is not None:
        timestamp = event_timestamp(record)
        if timestamp is None:
            return (), True
        if timestamp >= cutoff:
            return (), False
    return fields, False


def prune_record(
    record: dict[str, Any], policy: Policy, *, sidecars: Any = None
) -> tuple[dict[str, Any], bool]:
    """Return unchanged identity or a detached replacement; never mutate input.

    A raw dictionary with ANY $blob_ref key is retained, even if unrecognized:
    deleting a retrieval reference saves negligible bytes, not its remote blob.
    """
    data = record.get("data")
    if not isinstance(data, dict):
        return record, False
    if not policy.diagnostic_sidecars and (
        "raw" not in data or (isinstance(data["raw"], dict) and "$blob_ref" in data["raw"])
    ):
        return record, False
    # Preserve the original undated accounting contract for ordinary raw-only work.
    keys = ("raw", "raw_request", "raw_response") if policy.diagnostic_sidecars else ("raw",)
    if not any(key in data for key in keys) and not (
        sidecars is not None and "provider_payloads" in data
    ):
        return record, False
    fields, undated = diagnostic_fields(record, policy)
    if not fields:
        return record, undated
    removed = {
        key
        for key in fields
        if key in data and not (isinstance(data[key], dict) and "$blob_ref" in data[key])
    }
    new_data = {key: value for key, value in data.items() if key not in removed}
    refs = data.get("provider_payloads")
    if sidecars is not None and isinstance(refs, dict):
        remaining = {
            key: value
            for key, value in refs.items()
            if key not in fields or not sidecars.expires(value)
        }
        if remaining != refs:
            expired = dict(data.get("provider_payloads_expired", {}))
            for key, value in refs.items():
                if key not in remaining:
                    expired[key] = sidecars.tombstone(value)
            new_data["provider_payloads_expired"] = expired
            if remaining:
                new_data["provider_payloads"] = remaining
            else:
                new_data.pop("provider_payloads", None)
    if new_data == data:
        return record, False
    replacement = dict(record)
    replacement["data"] = new_data
    return replacement, False
