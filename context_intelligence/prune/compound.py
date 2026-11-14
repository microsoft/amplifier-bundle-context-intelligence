"""One exact verified Optimize generation may enter offline diagnostic retention.

This is not replay permission. The permanent fence remains untouched, a new
exclusive attempt fence precedes replacement, and recovery originals pin assets.
The caller must still own all existing capture/recovery/writer guards.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coordination import PruneError, receipt_path
from .storage import _RepeatFence, _signature, _sync_directory, _unique_object


def _regular(path: Path) -> os.stat_result:
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise PruneError("unsafe Optimize continuation ancestor")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise PruneError("unsafe Optimize continuation file")
    return info


def _hash(path: Path) -> str:
    info = _regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        if _signature(os.fstat(source.fileno())) != _signature(info):
            raise PruneError("Optimize continuation changed while opening")
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        if _signature(os.fstat(source.fileno())) != _signature(info):
            raise PruneError("Optimize continuation changed while hashing")
    if _signature(path.lstat()) != _signature(info):
        raise PruneError("Optimize continuation entry changed")
    return digest


def _json(path: Path) -> dict[str, Any]:
    _regular(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        raw = source.read(32769)
    if len(raw) > 32768:
        raise PruneError("oversized Optimize continuation metadata")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, RecursionError) as exc:
        raise PruneError("invalid Optimize continuation metadata") from exc


@dataclass(frozen=True)
class VerifiedOptimize:
    fence: _RepeatFence
    directory: Path
    stamps: tuple[tuple[Path, tuple[int, ...]], ...]
    metadata: tuple[int, ...] | None

    @property
    def attempt(self) -> Path:
        return optimize_retention_receipt(
            self.fence.path.with_name("events.jsonl"), self.fence.sha256
        )

    def check(self) -> None:
        self.fence.check()
        for path, signature in self.stamps:
            if _signature(_regular(path)) != signature:
                raise PruneError("verified Optimize generation changed")
        metadata = self.fence.path.with_name("metadata.json")
        try:
            current = _signature(metadata.lstat())
        except FileNotFoundError:
            current = None
        if current != self.metadata:
            raise PruneError("verified Optimize metadata changed")
        session = (
            self.fence.path.parent.parent
            if self.fence.path.parent.name == "context-intelligence"
            else self.fence.path.parent
        )
        if os.path.lexists(session / "transfer-fence.json"):
            raise PruneError("Optimize continuation is transfer-fenced")


def verified_optimize(path: Path, directory: Path) -> VerifiedOptimize:
    directory = directory.expanduser().absolute()
    if directory.parent != path.parent or not stat.S_ISDIR(directory.lstat().st_mode):
        raise PruneError("Optimize continuation requires exact sibling operation")
    journal_path = directory / "journal.json"
    journal = _json(journal_path)
    if (
        journal.get("format") != "context-intelligence-optimize"
        or type(journal.get("version")) is not int
        or journal["version"] != 1
        or journal.get("adapter") != "ci-canonical-rich-content-v1"
        or journal.get("network_backfill") is not False
        or journal.get("state") != "verified"
        or journal.get("source") != str(path)
    ):
        raise PruneError("Optimize continuation requires exact verified generation")
    marker = receipt_path(path)
    expected = {
        "format": "context-intelligence-optimize",
        "version": 1,
        "operation_id": journal.get("operation_id"),
        "source_sha256": journal.get("source_sha256"),
        "candidate_sha256": journal.get("candidate_sha256"),
        "notice": "Local historical conversion; network replay remains refused.",
    }
    if _json(marker) != expected or _hash(path) != journal.get("candidate_sha256"):
        raise PruneError("Optimize continuation source/fence binding mismatch")
    companions = [(journal_path, _signature(_regular(journal_path)))]
    for name, field in (
        ("original", "source_sha256"),
        ("assets.jsonl", "manifest_sha256"),
        ("records.jsonl", "mapping_sha256"),
    ):
        artifact = directory / name
        if _hash(artifact) != journal.get(field):
            raise PruneError("Optimize continuation recovery/mapping integrity mismatch")
        companions.append((artifact, _signature(_regular(artifact))))
    metadata = path.with_name("metadata.json")
    try:
        meta = _signature(metadata.lstat())
    except FileNotFoundError:
        meta = None
    if (list(meta) if meta is not None else None) != journal.get("metadata_signature"):
        raise PruneError("Optimize continuation metadata mismatch")
    fence = _RepeatFence(marker, _signature(_regular(marker)), _hash(marker))
    result = VerifiedOptimize(fence, directory, tuple(companions), meta)
    if os.path.lexists(result.attempt):
        raise PruneError("Optimize retention attempt exists; reconcile, do not replay")
    result.check()
    _sync_directory(path.parent)  # Presence after lost acknowledgement is not durability.
    return result


def optimize_retention_receipt(path: Path, fence_sha256: str) -> Path:
    """Deterministic generation receipt, independent of operation-directory copies."""
    if (
        not isinstance(fence_sha256, str)
        or len(fence_sha256) != 64
        or any(character not in "0123456789abcdef" for character in fence_sha256)
    ):
        raise ValueError("requires exact lowercase permanent-fence SHA256")
    return path.with_name(f"{path.name}.optimize-retention-{fence_sha256}.json")
