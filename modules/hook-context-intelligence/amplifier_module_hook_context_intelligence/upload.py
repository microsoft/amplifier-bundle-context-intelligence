"""Upload helpers — canonical JSON, idempotency key, and payload builder."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(data: dict[str, Any]) -> str:
    """Serialize *data* to a stable compact JSON string."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _compute_idempotency_key(event: str, workspace: str | None, data: dict[str, Any]) -> str:
    """Build a deterministic request id from the sanitized event envelope."""
    canonical = _canonical_json(
        {
            "event": event,
            "workspace": workspace or "",
            "data": data,
        }
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"aci-event-v1:{digest}"


def build_payload(
    event: str,
    workspace: str | None,
    data: dict[str, Any],
    working_dir: str | None = None,
) -> dict[str, Any]:
    """Build the HTTP request payload for a single event.

    ``working_dir`` is a top-level envelope field (alongside ``workspace``), NOT
    part of ``data`` -- it is a session/envelope attribute, not event content.

    Deliberately NOT part of the idempotency key: the key is computed from
    exactly {event, workspace, data} (see ``_compute_idempotency_key``) so
    existing HTTP dedup behavior is unchanged. ``working_dir`` is additive
    envelope metadata only; including it in the dedup key would change the
    idempotency key for every existing event stream and could produce
    duplicate deliveries for in-flight/retried events across the rollout.
    """
    return {
        "event": event,
        "workspace": workspace or "",
        "working_dir": working_dir or "",
        "idempotency_key": _compute_idempotency_key(event, workspace, data),
        "data": data,
    }
