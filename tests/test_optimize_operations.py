"""No live captures: exercise the real local file/guard/journal path."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from context_intelligence.optimize import StagedImage, apply, preview, resume, stage
from context_intelligence.optimize import rewrite_capture
from context_intelligence.optimize._json import FormatError
from context_intelligence.optimize import operations
from context_intelligence.prune.coordination import (
    PruneError,
    capture_lock,
    receipt_path,
    replay_guard,
)

from test_optimize_stream import event, image


class SyntheticMediaOwner:
    """Injected boundary only; not evidence about Foundation's production API."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir()
        self.published = False
        self.acknowledged = False
        self.origins: list[dict[str, Any]] = []

    def stage_image(self, image: BinaryIO, *, mime: str, origin: dict[str, Any]) -> StagedImage:
        data = image.read()  # tiny synthetic fixtures only
        digest = hashlib.sha256(data).hexdigest()
        assert digest == origin["sha256"] and len(data) == origin["bytes"]
        path = self.root / digest
        added = 0
        if not path.exists():
            path.write_bytes(data)
            added = len(data)
        self.origins.append(origin)
        return StagedImage(
            {"type": "synthetic_reference_only", "sha256": digest},
            {"asset": digest, "mime": mime},
            added,
        )

    def publish(self, manifest: Path) -> None:
        with manifest.open("rb") as rows:
            for line in rows:
                path = self.root / json.loads(line)["owner"]["asset"]
                with path.open("rb") as asset:
                    os.fsync(asset.fileno())
        operations._sync_directory(self.root)
        self.published = True

    def verify(self, manifest: Path) -> None:
        assert self.published
        with manifest.open("rb") as rows:
            for line in rows:
                entry = json.loads(line)
                path = self.root / entry["owner"]["asset"]
                assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["origin"]["sha256"]

    def acknowledge(self, manifest: Path, *, source: Path, sha256: str) -> None:
        assert self.published
        assert hashlib.sha256(source.read_bytes()).hexdigest() == sha256
        self.acknowledged = True


@pytest.fixture(autouse=True)
def isolated_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    monkeypatch.setattr(
        "context_intelligence.prune.recovery.default_recovery_root", lambda: tmp_path / "recovery"
    )


def setup(tmp_path: Path) -> tuple[Path, Path, SyntheticMediaOwner]:
    path = tmp_path / "events.jsonl"
    path.write_bytes(event([{"type": "text", "text": "keep"}, image(), image()]))
    return path, tmp_path / "conversion-1", SyntheticMediaOwner(tmp_path / "media")


def prepare(path: Path, operation: Path, owner: SyntheticMediaOwner) -> dict[str, Any]:
    return stage(path, operation, session_id="s1", media=owner, writers_stopped=True)


def test_preview_stage_apply_resume_preserve_identity_recovery_and_pairing(tmp_path: Path) -> None:
    path, operation, owner = setup(tmp_path)
    original = path.read_bytes()
    view = preview(path, session_id="s1")
    assert view["images"] == 2 and view["writer_exclusion"] == "not-established"
    staged = prepare(path, operation, owner)
    assert path.read_bytes() == original and not owner.published
    assert staged["counts"]["new_asset_bytes"] == len(b"synthetic-png")
    result = apply(operation, media=owner, writers_stopped=True)
    assert result["state"] == "verified" and owner.acknowledged
    assert (operation / "original").read_bytes() == original
    before, after = json.loads(original), json.loads(path.read_bytes())
    before_content = before["data"]["result"].pop("content")
    after_content = after["data"]["result"].pop("content")
    assert before == after
    assert before_content[0] == after_content[0]
    assert result["accounting"]["reclaimed_bytes"] == 0
    assert result["accounting"]["retained_recovery_bytes"] == len(original)
    converted = path.read_bytes()
    assert resume(operation, media=owner, writers_stopped=True)["state"] == "verified"
    assert path.read_bytes() == converted
    with pytest.raises(PruneError):
        with replay_guard(path):
            pass


def test_authority_and_existing_guards_are_mandatory(tmp_path: Path) -> None:
    path, operation, owner = setup(tmp_path)
    with pytest.raises(PruneError, match="offline"):
        stage(path, operation, session_id="s1", media=owner, writers_stopped=False)
    with capture_lock(path, exclusive=True), pytest.raises(PruneError, match="busy"):
        prepare(path, operation, owner)
    assert not operation.exists()
    receipt_path(path).write_text("{}")
    with pytest.raises(PruneError, match="replay refused"):
        prepare(path, operation, owner)


def test_explicit_missing_recovery_root_refuses(tmp_path: Path) -> None:
    path, operation, owner = setup(tmp_path)
    with pytest.raises(PruneError, match="recovery-state-missing"):
        stage(
            path,
            operation,
            session_id="s1",
            media=owner,
            writers_stopped=True,
            recovery_roots=(tmp_path / "missing",),
        )
    assert not operation.exists()


def test_drift_or_damaged_candidate_refuses_without_replacement(tmp_path: Path) -> None:
    path, operation, owner = setup(tmp_path)
    prepare(path, operation, owner)
    original = path.read_bytes()
    path.write_bytes(original + b"\n")
    with pytest.raises(PruneError, match="drift"):
        apply(operation, media=owner, writers_stopped=True)
    path.write_bytes(original)
    # Even exact restored bytes do not restore the original inode/ctime witness.
    with pytest.raises(PruneError, match="identity"):
        resume(operation, media=owner, writers_stopped=True)


@pytest.mark.parametrize("point", ["assets-published", "replace-pending", "replaced", "verified"])
def test_resume_reconciles_each_durable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    path, operation, owner = setup(tmp_path)
    prepare(path, operation, owner)
    save = operations._save
    fired = False

    def interrupted(directory: Path, journal: dict[str, Any]) -> None:
        nonlocal fired
        save(directory, journal)
        if journal["state"] == point and not fired:
            fired = True
            raise RuntimeError("synthetic crash")

    monkeypatch.setattr(operations, "_save", interrupted)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        apply(operation, media=owner, writers_stopped=True)
    monkeypatch.setattr(operations, "_save", save)
    if point in ("replaced", "verified"):
        os_replace = os.replace

        # _save itself atomically replaces journals; permit ONLY those updates.
        def journal_only(source: Path, destination: Path, **kwargs: Any) -> None:
            if str(destination) == path.name or destination == path:
                raise AssertionError("must reconcile committed replacement, not replay")
            os_replace(source, destination, **kwargs)

        monkeypatch.setattr(operations.os, "replace", journal_only)
    assert resume(operation, media=owner, writers_stopped=True)["state"] == "verified"


def test_lost_replace_acknowledgment_is_reconciled_by_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, operation, owner = setup(tmp_path)
    prepare(path, operation, owner)
    replace = os.replace

    def lost(source: Path, destination: Path, **kwargs: Any) -> None:
        replace(source, destination, **kwargs)
        if str(destination) == path.name or destination == path:
            raise OSError("synthetic lost replacement acknowledgment")

    monkeypatch.setattr(operations.os, "replace", lost)
    with pytest.raises(OSError, match="lost"):
        apply(operation, media=owner, writers_stopped=True)
    converted = path.read_bytes()
    monkeypatch.setattr(operations.os, "replace", replace)
    assert resume(operation, media=owner, writers_stopped=True)["state"] == "verified"
    assert path.read_bytes() == converted


def test_mapping_binds_every_record_without_event_id_assumption(tmp_path: Path) -> None:
    path, operation, owner = setup(tmp_path)
    # Blank physical lines, missing event_id, repeated id, CRLF and no final newline.
    original = b"\n  " + event() + b"\n\t" + event().rstrip(b"\r\n")
    path.write_bytes(original)
    prepare(path, operation, owner)
    mapped = [json.loads(line) for line in (operation / "records.jsonl").read_bytes().splitlines()]
    assert len(mapped) == 3 and mapped[0]["old"]["sha256"] == hashlib.sha256(original).hexdigest()
    candidate = (operation / "candidate").read_bytes()
    assert mapped[0]["new"]["sha256"] == hashlib.sha256(candidate).hexdigest()
    for ordinal, row in enumerate(mapped[1:], 1):
        assert row["ordinal"] == ordinal and row["eventId"] is None
        for name, content in (("old", original), ("new", candidate)):
            fact = row[name]
            part = content[fact["offset"] : fact["offset"] + fact["bytes"]]
            assert hashlib.sha256(part).hexdigest() == fact["sha256"]
            assert json.loads(part)["id"] == "event-1"


def test_lost_rename_reply_resyncs_parent_before_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, operation, owner = setup(tmp_path)
    prepare(path, operation, owner)
    replace = operations._replace_candidate

    def lost(*args: Any) -> None:
        replace(*args)
        raise OSError("lost acknowledgment")

    monkeypatch.setattr(operations, "_replace_candidate", lost)
    with pytest.raises(OSError):
        apply(operation, media=owner, writers_stopped=True)
    sync = operations._sync_directory
    seen: list[Path] = []

    def track(directory: Path) -> None:
        sync(directory)
        seen.append(directory)

    monkeypatch.setattr(operations, "_sync_directory", track)
    resume(operation, media=owner, writers_stopped=True)
    assert path.parent in seen and operation in seen


def test_existing_fence_after_interrupted_publication_is_synced_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from context_intelligence.prune import storage

    path, operation, owner = setup(tmp_path)
    prepare(path, operation, owner)
    original = path.read_bytes()
    storage_sync = storage._sync_directory

    def fail_publication_sync(directory: Path) -> None:
        if directory == path.parent:
            raise OSError("synthetic receipt link published, directory sync interrupted")
        storage_sync(directory)

    monkeypatch.setattr(storage, "_sync_directory", fail_publication_sync)
    with pytest.raises(OSError, match="receipt link"):
        apply(operation, media=owner, writers_stopped=True)
    assert receipt_path(path).is_file() and path.read_bytes() == original
    assert json.loads((operation / "journal.json").read_bytes())["state"] == "assets-published"
    monkeypatch.setattr(storage, "_sync_directory", storage_sync)
    seen: list[Path] = []
    sync = operations._sync_directory
    replace = operations._replace_candidate

    def track(directory: Path) -> None:
        sync(directory)
        seen.append(directory)

    def checked_replace(*args: Any) -> None:
        assert path.parent in seen, "matching fence must be durable BEFORE source replacement"
        replace(*args)

    monkeypatch.setattr(operations, "_sync_directory", track)
    monkeypatch.setattr(operations, "_replace_candidate", checked_replace)
    assert resume(operation, media=owner, writers_stopped=True)["state"] == "verified"


@pytest.mark.parametrize("kind", ["candidate", "metadata", "metadata-link"])
def test_owner_callback_races_refuse_before_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path, operation, owner = setup(tmp_path)
    prepare(path, operation, owner)
    original = path.read_bytes()
    verify = owner.verify
    fired = False

    def race(manifest: Path) -> None:
        nonlocal fired
        verify(manifest)
        if not fired:
            fired = True
            if kind == "candidate":
                candidate = operation / "candidate"
                candidate.unlink()
                candidate.symlink_to(path)
            elif kind == "metadata":
                path.with_name("metadata.json").write_text("{}")
            else:
                path.with_name("metadata.json").symlink_to(tmp_path / "absent")

    monkeypatch.setattr(owner, "verify", race)
    with pytest.raises(PruneError):
        apply(operation, media=owner, writers_stopped=True)
    assert path.read_bytes() == original


def test_symlinks_and_foreign_fences_never_replaced(tmp_path: Path) -> None:
    path, operation, owner = setup(tmp_path)
    prepare(path, operation, owner)
    receipt_path(path).symlink_to(tmp_path / "absent")
    with pytest.raises((PruneError, FileNotFoundError)):
        apply(operation, media=owner, writers_stopped=True)
    assert json.loads(path.read_bytes())["data"]["result"]["content"][1]["type"] == "image"


def test_incomplete_stage_preserved_no_unknown_owner_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, operation, owner = setup(tmp_path)

    def fail(*args: Any, **kwargs: Any) -> StagedImage:
        raise RuntimeError("owner unavailable")

    monkeypatch.setattr(owner, "stage_image", fail)
    with pytest.raises(RuntimeError):
        prepare(path, operation, owner)
    assert (operation / "original").read_bytes() == path.read_bytes()
    with pytest.raises(PruneError, match="incomplete staging"):
        resume(operation, media=owner, writers_stopped=True)


def test_callback_cannot_mutate_ci_manifest_provenance(tmp_path: Path) -> None:
    path, operation, owner = setup(tmp_path)
    value = json.loads(path.read_bytes())
    value["event_id"] = "producer-id"
    value["id"] = "envelope-id"
    path.write_text(json.dumps(value) + "\n")
    original_stage = owner.stage_image

    def mutate(image: BinaryIO, *, mime: str, origin: dict[str, Any]) -> StagedImage:
        result = original_stage(image, mime=mime, origin=origin)
        origin["session_id"] = "foreign"
        origin["event_id"] = "wrong"
        return result

    owner.stage_image = mutate
    prepare(path, operation, owner)
    rows = [json.loads(line) for line in (operation / "assets.jsonl").read_bytes().splitlines()]
    assert rows[0]["origin"]["event_id"] == "producer-id"
    assert rows[0]["origin"]["id"] == "envelope-id"
    assert rows[0]["origin"]["session_id"] == "s1"


def test_invalid_input_refuses_before_owner_effect(tmp_path: Path) -> None:
    import io

    _, _, owner = setup(tmp_path)
    block = image()
    block["visibility"] = "x" * 4097
    for manifest in (None, io.BytesIO()):
        with pytest.raises((FormatError, ValueError)):
            rewrite_capture(
                io.BytesIO(event([block])),
                io.BytesIO(),
                session_id="s1",
                source_sha256="a" * 64,
                media=owner,
                manifest=manifest,
            )
    assert not owner.origins
