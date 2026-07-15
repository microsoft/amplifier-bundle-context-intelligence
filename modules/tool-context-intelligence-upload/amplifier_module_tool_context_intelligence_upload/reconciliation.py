"""Operator reconciliation summary — independently-measured counts only.

The prior design computed ``already_present = max(0, read - ingested - skipped)``
while the CLI passed ``read = ingested + skipped``, so ``already_present`` was
identically 0 by algebra — a field that could never vary. ``unmapped`` was
hardcoded to 0, a constant wearing a variable's name.

This module reports only numbers that can actually vary and are measured
independently:

- ``read`` -- non-blank event-line count from the events files (independent
  of ingested/skipped, NOT a derived echo of ingested + skipped).
- ``ingested`` -- events accepted by the server.
- ``skipped`` -- events dropped as malformed, drifted, or missing a field.
- ``unmapped`` -- real runtime count of events that fell into the unmapped
  bucket (can be non-zero).
- ``live_sessions_skipped`` -- whole sessions dropped as live/in-progress.

``already_present`` is intentionally absent. Dedup safety is proven
behaviorally by the double-ingest test, not represented as a decorative
count -- ``read`` may legitimately exceed
``ingested + skipped + unmapped``.
"""

from __future__ import annotations


def reconciliation_summary(
    *,
    read: int,
    ingested: int,
    skipped: int,
    unmapped: int,
    live_sessions_skipped: int = 0,
) -> str:
    """Return the one-line operator reconciliation summary.

    Reports only independently-measured counts. No derived or
    algebraically-dead fields.
    """
    return (
        f"{read} read, {ingested} ingested, {skipped} skipped, "
        f"{unmapped} unmapped, {live_sessions_skipped} live-sessions-skipped"
    )
