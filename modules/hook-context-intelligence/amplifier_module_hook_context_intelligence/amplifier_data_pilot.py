"""amplifier-data floor pilot — fail-safe dual-write of CI session events.

PILOT, OFF BY DEFAULT. This mirrors each ``events.jsonl`` line into a local
``amplifier-data`` ``AmplifierStore`` as a content-addressed Cell, then verifies
that the store regenerates the line **byte-for-byte** (amplifier-data's E1
guarantee) against the canonical JSONL that CI already writes.

Why this is the right first step (per amplifier-data CONSUMER_INTEGRATION.md):
  * §7 rates CI's session-event log + ``ci-blob://`` blobs a **Strong** fit for
    the append-only event log + content-addressed ``CellWriteEvent`` floor.
  * §10 / §9 green-light a **dual-write pilot** and note the **pure-Python
    fallback** means ``pip install -e .`` needs **no maturin/Rust** — so this
    never adds a Rust toolchain to CI's hot write path.

Design invariants (non-negotiable for a pilot in a hot write path):
  1. NEVER affect the primary disk write. Every operation here is guarded; any
     failure disables the pilot for the session and is logged at DEBUG only.
  2. amplifier-data is imported lazily and optionally. If it is not installed,
     the pilot is a silent no-op — production installs are unaffected.
  3. Byte-identical guarantee by construction: the pilot stores the EXACT
     canonical bytes the LoggingHandler writes (same ``_canonical_json(record)``,
     newline excluded), so any regeneration mismatch is a real substrate signal,
     not an encoding artifact.
  4. In the hot path the store is **in-memory** (no ``path=``) — zero new files,
     zero Rust requirement. Durable restart/E1 verification against real
     ``events.jsonl`` files is done offline by ``verify_events_jsonl`` /
     ``pilot_verify.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EquivalenceReport:
    """Result of a regeneration-equivalence comparison.

    ``byte_identical`` is the headline signal: every recorded line regenerated
    from the store equals the original bytes, with no read/regenerate errors.
    """

    total: int = 0
    matched: int = 0
    mismatched: int = 0
    errors: int = 0
    distinct_refs: int = 0
    mismatch_samples: list[str] = field(default_factory=list)

    @property
    def byte_identical(self) -> bool:
        return (
            self.total > 0
            and self.matched == self.total
            and self.mismatched == 0
            and self.errors == 0
        )

    @property
    def dedup_ratio(self) -> float:
        """Fraction of lines that collapsed to a shared content-addressed ref."""
        if self.total == 0:
            return 0.0
        return 1.0 - (self.distinct_refs / self.total)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "errors": self.errors,
            "distinct_refs": self.distinct_refs,
            "dedup_ratio": round(self.dedup_ratio, 4),
            "byte_identical": self.byte_identical,
            "mismatch_samples": self.mismatch_samples,
        }


def _import_store() -> Any | None:
    """Lazily import AmplifierStore. Returns the class or None if unavailable."""
    try:
        from amplifier_data import AmplifierStore

        return AmplifierStore
    except Exception:  # pragma: no cover - exercised only when dep absent
        logger.debug("amplifier-data not importable; floor pilot disabled", exc_info=True)
        return None


class DualWriteStore:
    """In-memory, fail-safe mirror of CI session events into amplifier-data.

    One instance is held by a ``LoggingHandler`` (shared across the sessions that
    handler serves). It records canonical JSONL lines as content-addressed Cells
    and can verify byte-identical regeneration at session end.
    """

    def __init__(self, *, enabled: bool = False, path: str | Path | None = None) -> None:
        self._enabled = False
        self._store: Any | None = None
        # (ref, original_bytes) for every line recorded this process lifetime.
        self._refs: list[tuple[str, bytes]] = []

        if not enabled:
            return

        store_cls = _import_store()
        if store_cls is None:
            return

        try:
            # Default in-memory: no path => no new files, no Rust requirement.
            self._store = store_cls(path=str(path)) if path else store_cls()
            self._enabled = True
            logger.debug("amplifier-data floor pilot ENABLED (in-memory=%s)", path is None)
        except Exception:  # pragma: no cover - defensive
            logger.debug("amplifier-data store init failed; floor pilot disabled", exc_info=True)
            self._store = None

    @property
    def enabled(self) -> bool:
        return self._enabled and self._store is not None

    def record_line(self, line: str) -> str | None:
        """Mirror one canonical JSONL line (no trailing newline) into the store.

        Never raises. On any failure the pilot disables itself for the rest of
        the session and the primary disk write is untouched.
        """
        if not self.enabled:
            return None
        try:
            payload = line.encode("utf-8")
            ref = self._store.write_cell(payload)  # type: ignore[union-attr]
            self._refs.append((ref, payload))
            return ref
        except Exception:
            logger.debug("floor pilot dual-write failed; disabling for session", exc_info=True)
            self._enabled = False
            return None

    def verify_recorded(self) -> EquivalenceReport:
        """Regenerate every recorded cell and compare to the original bytes."""
        report = EquivalenceReport()
        if self._store is None:
            return report
        seen: set[str] = set()
        for ref, original in self._refs:
            report.total += 1
            seen.add(ref)
            try:
                regen = self._store.regenerate(ref).payload
            except Exception:
                report.errors += 1
                continue
            if regen == original:
                report.matched += 1
            else:
                report.mismatched += 1
                if len(report.mismatch_samples) < 5:
                    report.mismatch_samples.append(ref)
        report.distinct_refs = len(seen)
        return report

    def close(self) -> None:
        if self._store is not None:
            try:
                self._store.close()
            except Exception:  # pragma: no cover - defensive
                logger.debug("floor pilot store close failed", exc_info=True)
            self._store = None
            self._enabled = False


def verify_events_jsonl(
    events_path: str | Path,
    *,
    store_path: str | Path | None = None,
    test_restart: bool = False,
) -> EquivalenceReport:
    """Standalone regeneration-equivalence check over an existing events.jsonl.

    Reads each line, writes it to an AmplifierStore as a Cell, optionally closes
    and reopens the store (durable E1 / torn-tail-survival check when
    ``store_path`` is given and ``test_restart`` is True), then regenerates every
    cell and compares bytes. Never mutates the source file.

    Returns an :class:`EquivalenceReport`. ``total == 0`` means the source was
    empty or amplifier-data was unavailable (see logs).
    """
    report = EquivalenceReport()
    store_cls = _import_store()
    if store_cls is None:
        logger.warning("amplifier-data not importable; cannot run regeneration-equivalence check")
        return report

    events_path = Path(events_path)
    if events_path.is_dir():
        events_path = events_path / "events.jsonl"
    if not events_path.exists():
        logger.warning("events.jsonl not found at %s", events_path)
        return report

    lines = [ln for ln in events_path.read_text(encoding="utf-8").splitlines() if ln]

    store = store_cls(path=str(store_path)) if store_path else store_cls()
    try:
        refs: list[tuple[str, bytes]] = []
        for line in lines:
            payload = line.encode("utf-8")
            refs.append((store.write_cell(payload), payload))

        if test_restart and store_path:
            store.close()
            store = store_cls(path=str(store_path))  # reopen — regenerate from the log alone

        seen: set[str] = set()
        for ref, original in refs:
            report.total += 1
            seen.add(ref)
            try:
                regen = store.regenerate(ref).payload
            except Exception:
                report.errors += 1
                continue
            if regen == original:
                report.matched += 1
            else:
                report.mismatched += 1
                if len(report.mismatch_samples) < 5:
                    report.mismatch_samples.append(ref)
        report.distinct_refs = len(seen)
    finally:
        try:
            store.close()
        except Exception:  # pragma: no cover - defensive
            pass

    return report
