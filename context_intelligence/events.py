"""Level 1: pure single-event envelopes compatible with the ingestion protocol."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def build_event_payload(
    event: str, workspace: str, data: dict[str, Any], working_dir: str | None = None
) -> dict[str, Any]:
    """Build a detached, JSON-safe envelope with the hook's v1 idempotency key.

    Callers must sanitize content before calling this function. Include a stable
    event identity in ``data`` when otherwise-identical occurrences are distinct.
    Persist the returned envelope unchanged for retries. ``working_dir`` is
    envelope metadata and intentionally excluded from the existing v1 key.
    An unknown working directory is omitted, never encoded as a blank string.
    """
    if not isinstance(event, str) or not event.strip():
        raise ValueError("event must be a nonblank string")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("workspace must be a nonblank string")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    if working_dir is not None and (not isinstance(working_dir, str) or not working_dir.strip()):
        raise ValueError("working_dir must be nonblank when supplied")
    canonical = json.dumps(
        {"event": event, "workspace": workspace, "data": data},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload = json.loads(canonical)
    payload["idempotency_key"] = (
        "aci-event-v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    if working_dir is not None:
        payload["working_dir"] = working_dir
    return payload
