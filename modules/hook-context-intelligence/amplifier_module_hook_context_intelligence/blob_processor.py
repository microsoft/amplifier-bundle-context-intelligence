"""Blob processor — replaces large event fields with blob store references.

Provides a pure async function that deep-clones event data and offloads
known-large fields to a BlobStore, substituting them with ci-blob:// URIs.

Original data is NEVER modified; all mutations happen on the deep clone.
"""

from __future__ import annotations

import copy
from typing import Protocol

# Fields that contain large payloads and should be offloaded to blob storage.
BLOB_FIELDS: frozenset[str] = frozenset(
    {"raw", "result", "messages", "mount_plan", "context_snapshot", "debug"}
)


class _Writable(Protocol):
    """Minimal structural protocol: any object with an async write() method."""

    async def write(self, session_id: str, key: str, value: object) -> str: ...


def _lift_raw_fields(clone: dict) -> None:
    """Lift stop_reason, finish_reason, and usage from raw before offloading.

    Mutates *clone* in place.  Only called on the deep-cloned copy, never on
    the original event data.
    """
    raw = clone.get("raw")
    if not isinstance(raw, dict):
        return

    # Lift stop_reason (only if not already set at top level)
    if raw.get("stop_reason") is not None and clone.get("stop_reason") is None:
        clone["stop_reason"] = raw["stop_reason"]

    # Lift finish_reason (only if not already set at top level)
    if raw.get("finish_reason") is not None and clone.get("finish_reason") is None:
        clone["finish_reason"] = raw["finish_reason"]

    # Merge raw.usage into clone.usage (existing keys win on collision)
    raw_usage = raw.get("usage")
    if isinstance(raw_usage, dict):
        existing_usage = clone.get("usage")
        if isinstance(existing_usage, dict):
            clone["usage"] = {**raw_usage, **existing_usage}
        else:
            clone["usage"] = dict(raw_usage)


async def process_event_data(
    data: dict,
    blob_store: _Writable,
    session_id: str,
    node_id: str,
) -> dict:
    """Deep-clone *data* and replace known-large fields with blob references.

    Contract
    --------
    - ``data`` is **never** mutated — a :func:`copy.deepcopy` is taken first.
    - Every field from *data* is present in the returned clone.
    - For each field name in :data:`BLOB_FIELDS`:
        - If the field is absent from *data* or its value is ``None``, it is
          skipped (no blob write, no structural change).
        - Otherwise the clone's value is replaced with ``{"$blob_ref": uri}``
          where *uri* is returned by ``blob_store.write()``.
        - If ``blob_store.write()`` raises, the value is replaced with
          ``{"$blob_error": "write failed: <reason>"}`` and processing
          continues for the remaining fields.

    Parameters
    ----------
    data:       The raw event data dict to process.
    blob_store: A :class:`BlobStore`-compatible object used to persist blobs.
    session_id: The current session identifier (passed to ``blob_store.write``).
    node_id:    The graph node identifier; used to build the blob key
                ``<node_id>__<field_name>``.

    Returns
    -------
    dict
        A new dict that is a deep clone of *data* with blob fields substituted.
    """
    clone: dict = copy.deepcopy(data)
    _lift_raw_fields(clone)

    for field_name in BLOB_FIELDS:
        if field_name not in clone or clone[field_name] is None:
            continue

        key = f"{node_id}__{field_name}"
        try:
            uri = await blob_store.write(session_id, key, clone[field_name])
            clone[field_name] = {"$blob_ref": uri}
        except Exception as exc:  # noqa: BLE001
            clone[field_name] = {"$blob_error": f"write failed: {exc}"}

    return clone
