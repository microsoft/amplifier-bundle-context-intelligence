"""Per-file, POSIX CI historical conversion with assets-first recovery.

Offline authority is explicit. Capture maintenance/recovery guards do NOT stop
an existing writer with an open descriptor. Originals are never auto-deleted.
Every file is an independent transaction; this API has no corpus atomicity.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from context_intelligence.prune.coordination import (
    PruneError,
    assert_replayable,
    capture_lock,
    receipt_path,
)
from context_intelligence.prune.recovery import recovery_guard
from context_intelligence.prune.storage import (
    _capture_schema,
    _publish_receipt,
    _signature,
    _sync_directory,
)

from ._json import CHUNK, FormatError
from .capture import ImageOwner, rewrite_capture, write_record_mapping

JOURNAL = "journal.json"


def _safe(path: Path) -> os.stat_result:
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise PruneError("unsafe optimize ancestor")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise PruneError("optimize requires owned regular single-link files")
    return info


@contextmanager
def _open(path: Path) -> Iterator[BinaryIO]:
    initial = _safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if _signature(initial) != _signature(os.fstat(handle.fileno())):
            raise PruneError("optimize source identity changed")
        yield handle


def _hash(path: Path, poll: Callable[[], None] | None = None) -> str:
    with _open(path) as source:
        digest = hashlib.sha256()
        while chunk := source.read(CHUNK):
            if poll:
                poll()
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    with _open(path) as source:
        raw = source.read(32769)
    if len(raw) > 32768:
        raise PruneError("oversized optimize journal")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise PruneError("invalid optimize journal") from exc
    if not isinstance(value, dict):
        raise PruneError("invalid optimize journal")
    return value


def _save(directory: Path, value: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix=".journal-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=True, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / JOURNAL)
        _sync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _authority(writers_stopped: bool) -> None:
    if writers_stopped is not True:
        raise PruneError("optimize requires explicit offline/writers-stopped authority")
    if os.name != "posix":
        raise PruneError("optimize replacement durability is qualified only on POSIX")


def _path(path: Path) -> Path:
    path = Path(path).expanduser().absolute()
    if path.name != "events.jsonl" or ".." in path.parts:
        raise PruneError("optimize accepts an explicitly owned events.jsonl only")
    _safe(path)
    return path


@contextmanager
def _guard(
    path: Path, roots: tuple[Path, ...], *, replay: bool = True
) -> Iterator[Callable[[], None]]:
    with capture_lock(path, exclusive=True), recovery_guard(path, roots) as recovery:
        if replay:
            assert_replayable(path)
        schema = _capture_schema(path)
        parent_identity = (path.parent.stat().st_dev, path.parent.stat().st_ino)

        def check() -> None:
            recovery()
            if (path.parent.stat().st_dev, path.parent.stat().st_ino) != parent_identity:
                raise PruneError("optimize capture parent changed")
            if schema and _signature(schema[0].lstat()) != schema[1]:
                raise PruneError("optimize capture metadata changed")
            if schema is None and os.path.lexists(path.with_name("metadata.json")):
                raise PruneError("optimize capture metadata appeared")

        check()
        yield check
        check()


def preview(
    path: Path,
    *,
    session_id: str,
    recovery_roots: tuple[Path, ...] = (),
    poll: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Inventory one explicit capture without staging assets or changing it.

    The established maintenance API may create its stable lock. Preview does not
    grant apply authority, readers compatibility, or writer exclusion.
    """
    path = _path(path)
    with _guard(path, recovery_roots) as check:
        initial = _signature(_safe(path))
        digest = _hash(path, poll)
        with _open(path) as source:
            counts = rewrite_capture(
                source, None, session_id=session_id, source_sha256=digest, poll=poll
            )
        check()
        if _signature(_safe(path)) != initial or _hash(path, poll) != digest:
            raise PruneError("optimize source drift during preview")
        return {
            "format": "context-intelligence-optimize-preview",
            "version": 1,
            "path": str(path),
            "source_sha256": digest,
            "bytes": path.stat().st_size,
            "session_id": session_id,
            **counts,
            "writer_exclusion": "not-established",
        }


def stage(
    path: Path,
    operation_dir: Path,
    *,
    session_id: str,
    media: ImageOwner,
    writers_stopped: bool,
    recovery_roots: tuple[Path, ...] = (),
    poll: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Stage a candidate and preserve an exact original generation.

    ``operation_dir`` is a NEW private sibling directory beside events.jsonl.
    Its journal plus paged asset manifest is the durable host operation identity.
    A failed/incomplete staging generation is kept, never overwritten/replayed.
    """
    _authority(writers_stopped)
    path = _path(path)
    directory = Path(operation_dir).expanduser().absolute()
    if directory.parent != path.parent or directory == path:
        raise PruneError("operation directory must be a new capture sibling")
    with _guard(path, recovery_roots) as check:
        initial = _signature(_safe(path))
        digest = _hash(path, poll)
        directory.mkdir(mode=0o700)
        _sync_directory(directory.parent)
        journal: dict[str, Any] = {
            "format": "context-intelligence-optimize",
            "version": 1,
            "adapter": "ci-canonical-rich-content-v1",
            "operation_id": str(uuid.uuid4()),
            "state": "staging",
            "source": str(path),
            "source_sha256": digest,
            "source_signature": list(initial),
            "source_bytes": initial[2],
            "session_id": session_id,
            "network_backfill": False,
            "metadata_signature": _metadata_signature(path),
        }
        _save(directory, journal)
        with _open(path) as source, (directory / "original").open("xb") as original:
            os.chmod(directory / "original", 0o600)
            while block := source.read(CHUNK):
                if poll:
                    poll()
                original.write(block)
            original.flush()
            os.fsync(original.fileno())
        if _hash(directory / "original", poll) != digest:
            raise PruneError("source changed while preserving recovery generation")
        with (
            _open(path) as source,
            (directory / "candidate").open("xb") as output,
            (directory / "assets.jsonl").open("xb") as manifest,
        ):
            os.chmod(directory / "candidate", 0o600)
            os.chmod(directory / "assets.jsonl", 0o600)
            counts = rewrite_capture(
                source,
                output,
                session_id=session_id,
                source_sha256=digest,
                media=media,
                manifest=manifest,
                poll=poll,
            )
            # Preserve the source mode/timestamps/xattrs for the candidate.
            shutil.copystat(path, directory / "candidate", follow_symlinks=False)
            output.flush()
            os.fsync(output.fileno())
            manifest.flush()
            os.fsync(manifest.fileno())
        check()
        if _signature(_safe(path)) != initial or _hash(path, poll) != digest:
            raise PruneError("optimize source drift; stage not accepted")
        with (
            _open(directory / "original") as old,
            _open(directory / "candidate") as new,
            (directory / "records.jsonl").open("xb") as mapping,
        ):
            os.chmod(directory / "records.jsonl", 0o600)
            records = write_record_mapping(old, new, mapping, session_id=session_id, poll=poll)
            if records != counts["records"]:
                raise FormatError("record mapping coverage mismatch")
            mapping.flush()
            os.fsync(mapping.fileno())
        journal.update(
            state="staged",
            counts=counts,
            candidate_sha256=_hash(directory / "candidate", poll),
            candidate_bytes=(directory / "candidate").stat().st_size,
            manifest_sha256=_hash(directory / "assets.jsonl", poll),
            mapping_sha256=_hash(directory / "records.jsonl", poll),
            candidate_signature=list(_signature(_safe(directory / "candidate"))),
        )
        _sync_directory(directory)
        _save(directory, journal)
        return journal


def _load(directory: Path) -> tuple[Path, dict[str, Any]]:
    if not stat.S_ISDIR(directory.lstat().st_mode) or directory.stat().st_uid != os.getuid():
        raise PruneError("unsafe optimize operation directory")
    value = _json(directory / JOURNAL)
    if (
        value.get("format") != "context-intelligence-optimize"
        or type(value.get("version")) is not int
        or value["version"] != 1
        or value.get("network_backfill") is not False
        or value.get("adapter") != "ci-canonical-rich-content-v1"
    ):
        raise PruneError("unsupported optimize journal")
    path = _path(Path(value["source"]))
    if path.parent != directory.parent:
        raise PruneError("optimize journal source binding mismatch")
    return path, value


def _metadata_signature(path: Path) -> list[int] | None:
    metadata = path.with_name("metadata.json")
    try:
        info = metadata.lstat()
    except FileNotFoundError:
        return None
    return list(_signature(info))


def _replace_candidate(directory: Path, path: Path, journal: dict[str, Any]) -> None:
    """Pin both rename directories; no owner/poll callbacks past final checks.

    Existing maintenance/offline ownership excludes cooperating writers. This
    does not claim isolation from a hostile process with the same OS identity.
    """
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    operation = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        candidate = directory / "candidate"
        with _open(candidate) as prepared:
            digest = hashlib.sha256()
            while block := prepared.read(CHUNK):
                digest.update(block)
            if digest.hexdigest() != journal["candidate_sha256"]:
                raise PruneError("optimize candidate changed at commit")
            if list(_signature(os.fstat(prepared.fileno()))) != journal["candidate_signature"]:
                raise PruneError("optimize candidate identity changed")
            current = os.stat("candidate", dir_fd=operation, follow_symlinks=False)
            if list(_signature(current)) != journal["candidate_signature"]:
                raise PruneError("optimize candidate entry changed")
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if list(_signature(current)) != journal["source_signature"]:
                raise PruneError("optimize source entry changed")
            if directory.lstat().st_ino != os.fstat(operation).st_ino:
                raise PruneError("optimize operation directory changed")
            if path.parent.lstat().st_ino != os.fstat(parent).st_ino:
                raise PruneError("optimize parent directory changed")
            os.replace("candidate", path.name, src_dir_fd=operation, dst_dir_fd=parent)
        os.fsync(parent)
        os.fsync(operation)
    finally:
        os.close(operation)
        os.close(parent)


def _fence(path: Path, journal: dict[str, Any]) -> None:
    expected = {
        "format": "context-intelligence-optimize",
        "version": 1,
        "operation_id": journal["operation_id"],
        "source_sha256": journal["source_sha256"],
        "candidate_sha256": journal["candidate_sha256"],
        "notice": "Local historical conversion; network replay remains refused.",
    }
    marker = receipt_path(path)
    if os.path.lexists(marker):
        if _json(marker) != expected:
            raise PruneError("foreign capture replay fence; optimize refused")
        # An earlier publication may have linked the receipt and then failed
        # before syncing its parent. Presence is not durability. Establish it
        # again BEFORE any source replacement, including old-source resume.
        _sync_directory(path.parent)
    else:
        _publish_receipt(path, expected)


def apply(
    operation_dir: Path,
    *,
    media: ImageOwner,
    writers_stopped: bool,
    recovery_roots: tuple[Path, ...] = (),
    poll: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Publish or reconcile ONE file. Unknown replacements are hash-reconciled."""
    _authority(writers_stopped)
    directory = Path(operation_dir).expanduser().absolute()
    path, journal = _load(directory)
    with _guard(path, recovery_roots, replay=False) as check:
        if _metadata_signature(path) != journal["metadata_signature"]:
            raise PruneError("optimize capture metadata changed since staging")
        state = journal["state"]
        if state == "staging":
            raise PruneError("incomplete staging preserved; media-owner reconciliation required")
        if state not in (
            "staged",
            "publishing-assets",
            "assets-published",
            "replace-pending",
            "replaced",
            "verified",
        ):
            raise PruneError("unsupported optimize operation state")
        if _hash(directory / "original", poll) != journal["source_sha256"]:
            raise PruneError("optimize recovery generation changed")
        manifest = directory / "assets.jsonl"
        if _hash(manifest, poll) != journal["manifest_sha256"]:
            raise PruneError("optimize asset manifest changed")
        if _hash(directory / "records.jsonl", poll) != journal["mapping_sha256"]:
            raise PruneError("optimize record mapping changed")
        current = _hash(path, poll)
        old, new = journal["source_sha256"], journal["candidate_sha256"]
        changed = old != new
        if current not in (old, new):
            raise PruneError("optimize source drift; no replacement replay")
        # Candidate hash without a prior replacement intent is not our commit.
        if changed and current == new and state not in ("replace-pending", "replaced", "verified"):
            raise PruneError("unexpected candidate source without commit intent")
        if current == old and list(_signature(_safe(path))) != journal["source_signature"]:
            raise PruneError("optimize original identity changed")
        if state in ("staged", "publishing-assets"):
            assert_replayable(path)
            journal["state"] = "publishing-assets"
            _save(directory, journal)
            media.publish(manifest)  # owner contract reconciles its saved receipts
            media.verify(manifest)
            journal["state"] = "assets-published"
            _save(directory, journal)
        media.verify(manifest)
        if current == old and changed:
            candidate = directory / "candidate"
            if _hash(candidate, poll) != new:
                raise PruneError("optimize candidate changed")
            check()
            if poll:
                poll()
            if _hash(path, poll) != old:
                raise PruneError("optimize source drift before replacement")
            _fence(path, journal)
            journal["state"] = "replace-pending"
            _save(directory, journal)  # durable intent BEFORE uncertain replacement
            check()
            if list(_signature(_safe(path))) != journal["source_signature"]:
                raise PruneError("optimize source identity changed before replacement")
            _replace_candidate(directory, path, journal)
            journal["state"] = "replaced"
            _save(directory, journal)
        elif changed:
            _fence(path, journal)
            _sync_directory(path.parent)
            _sync_directory(directory)
        check()
        if _hash(path, poll) != new:
            raise PruneError("optimize replacement readback mismatch")
        # Real bounded reader verification, not merely a successful rename.
        with _open(path) as source:
            readback = rewrite_capture(
                source, None, session_id=journal["session_id"], source_sha256=new, poll=poll
            )
        if readback["records"] != journal["counts"]["records"]:
            raise FormatError("optimize record identity verification failed")
        media.verify(manifest)
        media.acknowledge(manifest, source=path, sha256=new)
        journal["state"] = "verified"
        journal["accounting"] = {
            "inline_bytes_removed": journal["counts"]["inline_bytes"],
            "new_asset_bytes": journal["counts"]["new_asset_bytes"],
            "reference_file_delta": journal["source_bytes"] - journal["candidate_bytes"],
            "retained_recovery_bytes": (directory / "original").stat().st_size,
            "manifest_bytes": manifest.stat().st_size,
            # Not claimed reclaimed: original recovery generation remains.
            "reclaimed_bytes": 0,
        }
        _save(directory, journal)
        return journal


def resume(
    operation_dir: Path,
    *,
    media: ImageOwner,
    writers_stopped: bool,
    recovery_roots: tuple[Path, ...] = (),
    poll: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Same guarded state machine; never re-run parsing or an unknown replacement."""
    return apply(
        operation_dir,
        media=media,
        writers_stopped=writers_stopped,
        recovery_roots=recovery_roots,
        poll=poll,
    )
