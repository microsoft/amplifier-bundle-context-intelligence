"""CI-owned original-image positions, not a generic JSON lookup or ref parser.

The host retains both descriptors under its real stopped/flush boundary. This
module takes no capture lock (the host lock is not reentrant), installs nothing,
and grants no expiry, media-reference, mapping or checkpoint authority.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from itertools import zip_longest
from typing import Any, BinaryIO

from ._json import CHUNK, LABEL, FormatError, Index
from .capture import _decoded_image, _envelope, _image_origin, _images, _metadata, _source

_LIMIT = 16384
_RAW = ("raw", "raw_request", "raw_response")
_SNAPSHOT_FIELDS = {
    "session_id",
    "source_sha256",
    "ordinal",
    "event",
    "image_offset",
    "sha256",
    "bytes",
    "timestamp",
    "ts",
    "tool_call_id",
    "id",
    "event_id",
}


def _check_hash(source: BinaryIO, expected: str, poll: Callable[[], None] | None) -> None:
    source.seek(0)
    digest = hashlib.sha256()
    while block := source.read(CHUNK):
        if poll:
            poll()
        digest.update(block)
    if digest.hexdigest() != expected:
        raise FormatError("position verifier generation changed")


def _string_identity(index: Index, parent: int | None, key: str) -> tuple[str | None, str | None]:
    """Match the record-mapping string/null contract without float conversion.

    Unlike its legacy mapping representation, also retain missing versus null.
    Use this SAME helper on both sides, including status and call identity.
    """
    node = index.child(parent, key)
    kind = index.kind(node)
    value = index.small(node)
    if node is None or (kind == "scalar" and value == "null"):
        return kind, value
    if kind != "string" or value is None:
        raise FormatError("unsupported capture identity field type or size")
    return kind, value


def _record_identity(index: Index, root: int, session_id: str) -> tuple[Any, ...]:
    event, data = _envelope(index, root, session_id)
    fields = tuple(
        _string_identity(index, root, key)
        for key in ("session_id", "timestamp", "ts", "event_id", "id", "idempotency_key")
    )
    data_fields = tuple(
        _string_identity(index, data, key) for key in ("tool_call_id", "timestamp", "status")
    )
    # A changed tool success must not be laundered through a positional ACK.
    success = index.child(index.child(data, "result"), "success")
    success_fact = index.kind(success), index.small(success)
    if success is not None and success_fact not in (
        ("scalar", "true"),
        ("scalar", "false"),
        ("scalar", "null"),
    ):
        raise FormatError("unsupported capture result success type")
    return event, fields, data_fields, success_fact


def _path(index: Index, root: int, image: int) -> tuple[str | int, ...]:
    parts: list[str | int] = []  # JSON depth is independently bounded by Index.
    node = image
    while node != root:
        row = index.db.execute("SELECT parent,key FROM node WHERE id=?", (node,)).fetchone()
        if row is None or row[0] is None:
            raise FormatError("image position is outside its capture record")
        parent, key = row
        parts.append(int(key) if index.kind(parent) == "array" else key)
        node = parent
    return tuple(reversed(parts))


def _validate_snapshot(
    snapshot: dict[str, Any], session_id: str, source_sha256: str
) -> tuple[int, str]:
    if not isinstance(snapshot, dict):
        raise FormatError("selected image is not an exact CI snapshot")
    # Retain canonical scalar facts, not a caller-owned dict across poll callbacks.
    snapshot = dict(snapshot)
    if set(snapshot) != _SNAPSHOT_FIELDS:
        raise FormatError("selected image is not an exact CI snapshot")
    for key in ("ordinal", "image_offset", "bytes"):
        minimum = 0 if key == "image_offset" else 1
        if type(snapshot[key]) is not int or not minimum <= snapshot[key] <= 2**63 - 1:
            raise FormatError("selected image is not an exact CI position")
    for key in _SNAPSHOT_FIELDS - {"ordinal", "image_offset", "bytes"}:
        value = snapshot[key]
        if value is not None and (not isinstance(value, str) or len(value) > LABEL):
            raise FormatError("selected image is not an exact CI snapshot")
    if snapshot["session_id"] != session_id or snapshot["source_sha256"] != source_sha256:
        raise FormatError("selected image is not in this CI generation")
    return snapshot["image_offset"], _metadata(snapshot)


def _installed_value(
    before: Index, after: Index, root: int, new_root: int, path: tuple[str | int, ...]
) -> dict[str, Any]:
    old, new = root, new_root
    for depth, part in enumerate(path):
        kind = before.kind(old)
        if kind not in ("object", "array") or after.kind(new) != kind:
            raise FormatError("installed image ancestor type changed")
        if kind == "array":
            old_count = before.db.execute(
                "SELECT count(*) FROM node WHERE parent=?", (old,)
            ).fetchone()[0]
            new_count = after.db.execute(
                "SELECT count(*) FROM node WHERE parent=?", (new,)
            ).fetchone()[0]
            if old_count != new_count:
                raise FormatError("installed image array positions changed")
        # Canonical message role is part of its addressed ancestry, not a vendor search.
        if before.child(old, "role") is not None and _string_identity(
            before, old, "role"
        ) != _string_identity(after, new, "role"):
            raise FormatError("installed image message role changed")
        old_child = before.child(old, str(part))
        new_child = after.child(new, str(part))
        if old_child is None:
            raise FormatError("original image position disappeared")
        if new_child is None:
            if depth == 1 and path[0] == "data" and part in _RAW:
                return {"status": "absent", "value": None, "path": path}
            raise FormatError("installed image position is missing")
        old, new = old_child, new_child
    # A JSON null is PRESENT; it cannot be confused with a removed raw field.
    return {"status": "present", "value": after.bounded_value(new, _LIMIT), "path": path}


class _ImageBatch(Iterator[dict[str, Any]]):
    """One bounded provisional result at a time; only input EOF means exhaustion."""

    def __init__(self, owner: CaptureImageVerifier, snapshots: Iterator[dict[str, Any]]) -> None:
        self._owner = owner
        self._snapshots = snapshots
        self._active = True
        self._advancing = False
        self._exhausted = False
        self._failed = False

    def __iter__(self) -> _ImageBatch:
        return self

    def _healthy(self) -> None:
        if not self._active or not self._owner._active:
            raise FormatError("image batch context is inactive")
        if self._failed:
            raise FormatError("image batch failed or was closed")

    def __next__(self) -> dict[str, Any]:
        self._healthy()
        if self._advancing:
            self._failed = True
            raise FormatError("image batch iterator is already in use")
        if self._exhausted:
            raise StopIteration
        self._advancing = True
        try:
            try:
                snapshot = next(self._snapshots)
            except StopIteration:
                self._healthy()
                self._exhausted = True
            else:
                position, expected = _validate_snapshot(
                    snapshot, self._owner._session_id, self._owner._source_sha256
                )
                if self._owner._poll:
                    self._owner._poll()
                self._healthy()
                result = self._owner._lookup(position, expected)
                self._healthy()
                return result
        except BaseException:
            # Catching a failed next() in the caller cannot accept a partial batch.
            self._failed = True
            raise
        finally:
            self._advancing = False
        raise StopIteration

    def close(self) -> None:
        """Cancel, never certify exhaustion, even if the caller catches errors."""
        self._failed = True

    def _finish(self) -> None:
        self._healthy()
        if not self._exhausted:
            raise FormatError("image batch iterator was not fully exhausted")


class CaptureImageVerifier:
    """Scoped callable yielded by :func:`capture_image_verifier`.

    ``verifier(snapshot)`` retains per-lookup generation checks.
    ``with verifier.read_many(snapshots) as results`` amortizes those checks.
    Neither operation supplies diagnostic-reference, expiry or ACK authority.
    """

    def __init__(
        self,
        before: Index,
        after: Index,
        session_id: str,
        source_sha256: str,
        check: Callable[[], None],
        poll: Callable[[], None] | None,
    ) -> None:
        self._before = before
        self._after = after
        self._session_id = session_id
        self._source_sha256 = source_sha256
        self._check = check
        self._poll = poll
        self._active = True
        self._busy = False

    def _acquire(self) -> None:
        if not self._active or self._busy:
            raise FormatError("image verifier context is inactive or already in use")
        self._busy = True

    def _lookup(self, position: int, expected: str) -> dict[str, Any]:
        row = self._before.db.execute(
            "SELECT ordinal,root,image FROM image_position WHERE position=? AND snapshot=?",
            (position, expected),
        ).fetchone()
        if row is None:
            raise FormatError("selected image is not an exact admitted CI image")
        ordinal, root, image = row
        new_root = self._after.db.execute(
            "SELECT node FROM record WHERE ordinal=?", (ordinal,)
        ).fetchone()[0]
        return _installed_value(
            self._before, self._after, root, new_root, _path(self._before, root, image)
        )

    def __call__(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self._acquire()
        try:
            position, expected = _validate_snapshot(snapshot, self._session_id, self._source_sha256)
            self._check()
            result = self._lookup(position, expected)
            self._check()
            return result
        finally:
            self._busy = False

    @contextmanager
    def read_many(self, snapshots: Iterable[dict[str, Any]]) -> Iterator[Iterator[dict[str, Any]]]:
        """Stream ordered results, PROVISIONAL until this context exits normally.

        The host must retain its stopped/flush and FD/path custody, spool bounded
        facts and perform NO ACK until the iterator is fully exhausted AND this
        context exits normally after its final generation check. Both descriptors
        are hashed once on entry and once on successful exit, never per item.
        Empty input is valid only when exhausted; it supplies no expiry authority.

        A failed next(), even if caught, poisons the batch. Early normal exit or
        close() refuses rather than certifying exhaustion. Saved iterators expire
        on batch/outer exit. No input/result list or additional lock is retained.
        """
        self._acquire()
        batch = None
        try:
            self._check()
            # iter() itself can invoke caller code: it too runs under busy custody.
            batch = _ImageBatch(self, iter(snapshots))
            yield batch
            batch._finish()
            batch._active = False
            self._check()
            # A final hash poll may cancel through the public close() method.
            # Keep iteration inactive, but never accept that cancelled batch.
            if batch._failed:
                raise FormatError("image batch failed or was closed")
        finally:
            if batch is not None:
                batch._active = False
            self._busy = False


@contextmanager
def capture_image_verifier(
    original: BinaryIO,
    installed: BinaryIO,
    *,
    session_id: str,
    source_sha256: str,
    installed_sha256: str,
    poll: Callable[[], None] | None = None,
) -> Iterator[CaptureImageVerifier]:
    """Yield ``read_selected(exact_original_origin)`` for one fenced generation.

    An origin is the EXACT snapshot emitted by ``rewrite_capture`` /
    ``capture_group_sources``: all fields, original image offset, ordinal,
    decoded digest/size and session/source identity must match. No caller path
    is accepted. Only known structured canonical PNG/JPEG images are indexed;
    strings, vendor payloads, URL images and unknown image metadata are protected.

    Each result has ``status`` (``present`` or ``absent``), ``value`` (a JSON
    node bounded to 16 KiB), and ``path`` (a tuple of str keys / int indices).
    ``absent`` means ONLY that the entire addressed data.raw/raw_request/
    raw_response field is missing from the same llm record. It is NOT expiry
    evidence: the host must independently attest original policy and real expiry.
    JSON null is present, never absent. Other missing ancestors/positions, changed
    record identity/count, array cardinality, message role or types refuse.

    The caller must validate the returned node using its actual diagnostic-ref
    parser, exact selected manifest ref, media closure and record mapping. An
    unchanged inline image is present, NOT a committed reference. This is neither
    a general non-target diff proof nor a complete-group reachability claim.
    CI stage/apply recovery fences remain independent. No lock is acquired here.

    Two disk indexes are opened once per context; image bytes are hashed one at
    a time without full-record/string/pixel lists. Both descriptor hashes are
    checked before/after indexing, each single lookup and normal context exit.
    ``read_selected.read_many(snapshots)`` instead checks once at batch entry and
    once after full exhaustion on normal batch exit. Its ordered bounded results
    are PROVISIONAL until that exit succeeds; spool facts, never ACK while inside
    the batch. See ``CaptureImageVerifier.read_many`` for cancellation/failure
    semantics. The host owns FD/path binding and writer exclusion; hashes do not
    replace them.
    Descriptors are borrowed, seeked and never closed. The callable expires with
    this scope and is nonreentrant (including through ``poll``).
    """
    if not isinstance(session_id, str) or not session_id or len(session_id) > LABEL:
        raise FormatError("invalid position verifier session identity")
    for digest in (source_sha256, installed_sha256):
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise FormatError("invalid position verifier generation digest")

    def check() -> None:
        if poll:
            poll()
        _check_hash(original, source_sha256, poll)
        _check_hash(installed, installed_sha256, poll)

    check()
    original.seek(0)
    installed.seek(0)
    with Index(original, poll) as before, Index(installed, poll) as after:
        before.db.execute(
            "CREATE TABLE image_position(position INTEGER PRIMARY KEY,ordinal INTEGER,"
            "root INTEGER,image INTEGER,snapshot TEXT)"
        )
        for first, second in zip_longest(before.roots(), after.roots()):
            if first is None or second is None:
                raise FormatError("conversion changed capture record count")
            ordinal, root = first
            new_ordinal, new_root = second
            if ordinal != new_ordinal or _record_identity(
                before, root, session_id
            ) != _record_identity(after, new_root, session_id):
                raise FormatError("conversion changed record identity")
            for image in _images(before, root, session_id):
                selected = _source(before, image)
                if selected is None:
                    continue
                _, encoded = selected
                digest, size = _decoded_image(before, encoded, None)
                snapshot = _image_origin(
                    before, ordinal, root, image, session_id, source_sha256, digest, size
                )
                before.db.execute(
                    "INSERT INTO image_position VALUES(?,?,?,?,?)",
                    (snapshot["image_offset"], ordinal, root, image, _metadata(snapshot)),
                )
        check()
        read_selected = CaptureImageVerifier(before, after, session_id, source_sha256, check, poll)
        try:
            yield read_selected
            read_selected._acquire()
            try:
                check()
            finally:
                read_selected._busy = False
        finally:
            read_selected._active = False
