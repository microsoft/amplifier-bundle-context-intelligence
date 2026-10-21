"""Public pruning contract. All state and captures are synthetic and task-owned."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from context_intelligence.native_transcript import CaptureLocator, read_native_transcript
from context_intelligence.prune import (
    Policy,
    PruneRequest,
    PruneSummary,
    iter_prune_tree,
    prune_file,
)
from context_intelligence.prune import storage
from context_intelligence.prune.coordination import (
    CaptureBusy,
    PrunedCapture,
    capture_lock,
    receipt_path,
    replay_guard,
)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
OLD = "2026-01-01T00:00:00.123456789Z"


def line(event: str = "llm:request", **data: object) -> bytes:
    return (
        json.dumps(
            {"event": event, "timestamp": OLD, "workspace": "synthetic", "data": data}
        ).encode()
        + b"\n"
    )


@pytest.fixture(autouse=True)
def isolated_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "context_intelligence.prune.recovery.default_recovery_root", lambda: tmp_path / "recovery"
    )


def request(root: Path, **kwargs: Any) -> PruneRequest:
    return PruneRequest(
        root=root,
        policy=Policy(NOW - timedelta(days=14)),
        apply=True,
        writers_stopped=True,
        min_idle=timedelta(0),
        **kwargs,
    )


def test_public_api_enforces_authority_and_summary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires"):
        PruneRequest(tmp_path, apply=True)
    path = tmp_path / "events.jsonl"
    original = line(raw={"messages": ["synthetic"]}, usage={"tokens": 4})
    path.write_bytes(original)
    preview = PruneRequest.from_options(tmp_path, older_than="14d", min_idle="0s", now=NOW)
    summary = PruneSummary()
    for result in iter_prune_tree(preview, now=NOW):
        summary.add(result)
    assert summary.changed_files == 1 and summary.payloads == 1
    assert path.read_bytes() == original and not receipt_path(path).exists()
    result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "pruned"
    assert json.loads(path.read_bytes())["data"] == {"usage": {"tokens": 4}}
    receipt = json.loads(receipt_path(path).read_bytes())
    assert receipt["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["expected"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["state"] == "prepared"  # not a promise about commit after a crash
    assert prune_file(path, request(tmp_path), now=NOW).status == "skipped"


def test_native_transcript_identical_and_other_files_untouched(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    (tmp_path / "metadata.json").write_text(
        json.dumps(
            {
                "format": "context-intelligence",
                "version": "1.0.0",
                "session_id": "synthetic",
                "workspace": "synthetic",
            }
        )
    )
    body = (
        line("prompt:submit", prompt="Synthetic user")
        + line(raw={"input": "large synthetic"})
        + line("llm:response", raw={"output": "response retained"})
        + line("prompt:complete", response="Synthetic assistant")
    )
    path.write_bytes(body)
    saved = tmp_path / "transcript.jsonl"
    saved.write_bytes(b"separate saved transcript\n")
    locator = CaptureLocator.from_session_dir(tmp_path)
    before = read_native_transcript(locator).as_dict()
    assert prune_file(path, request(tmp_path), now=NOW).status == "pruned"
    assert read_native_transcript(locator).as_dict() == before
    assert saved.read_bytes() == b"separate saved transcript\n"
    assert (
        json.loads(path.read_bytes().splitlines()[2])["data"]["raw"]["output"]
        == "response retained"
    )


@pytest.mark.parametrize("value", [b"", b"invalid", b"{}"])
def test_any_receipt_blocks_every_consumer_even_when_source_unmodified(
    tmp_path: Path, value: bytes
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(line(raw="synthetic"))
    receipt_path(path).write_bytes(value)
    with pytest.raises(PrunedCapture):
        with replay_guard(path):
            pytest.fail("must not enter guarded consumer")
    assert prune_file(path, request(tmp_path), now=NOW).status == "skipped"


def test_shared_reader_blocks_prune_and_exclusive_prune_blocks_reader(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original = line(raw="synthetic")
    path.write_bytes(original)
    with replay_guard(path):
        result = prune_file(path, request(tmp_path), now=NOW)
        assert result.status == "skipped" and "busy" in (result.reason or "")
    with capture_lock(path, exclusive=True):
        with pytest.raises(CaptureBusy):
            with replay_guard(path):
                pytest.fail("must not read source while prune owns lock")
    assert path.read_bytes() == original
    assert prune_file(path, request(tmp_path), now=NOW).status == "pruned"


def test_replace_failure_keeps_original_and_durable_deny_marker(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original = line(raw="synthetic")
    path.write_bytes(original)
    with patch.object(storage.os, "replace", side_effect=OSError("synthetic fault")):
        result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "error" and path.read_bytes() == original
    assert receipt_path(path).exists()
    assert not list(tmp_path.glob(".events-prune-*"))
    assert not list(tmp_path.glob(".prune-receipt-*"))
    with pytest.raises(PrunedCapture):
        with replay_guard(path):
            pytest.fail("prepared receipt is a permanent deny fence")


def test_fsync_order_and_marker_durable_before_source_replace(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(line(raw="synthetic"))
    real_sync, real_link, real_replace = os.fsync, os.link, os.replace
    calls: list[str] = []

    def sync(fd: int) -> None:
        name = os.readlink(f"/proc/self/fd/{fd}")
        calls.append(
            "directory"
            if Path(name) == tmp_path
            else ("receipt" if ".prune-receipt-" in name else "output")
        )
        real_sync(fd)

    def link(*args: Any, **kwargs: Any) -> None:
        assert calls[-1] == "receipt"
        calls.append("publish-receipt")
        real_link(*args, **kwargs)

    def replace(*args: Any, **kwargs: Any) -> None:
        assert receipt_path(path).exists() and calls[-1] == "directory"
        calls.append("replace-source")
        real_replace(*args, **kwargs)

    with (
        patch.object(storage.os, "fsync", sync),
        patch.object(storage.os, "link", link),
        patch.object(storage.os, "replace", replace),
    ):
        assert prune_file(path, request(tmp_path), now=NOW).status == "pruned"
    assert calls == [
        "output",
        "receipt",
        "publish-receipt",
        "directory",
        "replace-source",
        "directory",
    ]


def make_ledger(root: Path, source: Path) -> tuple[Path, Path]:
    root.mkdir()
    db = root / "job.v2.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE artifact(artifact_id TEXT, path TEXT, source_sha256 TEXT, state TEXT)"
        )
        connection.execute(
            "CREATE TABLE cursor(artifact_id TEXT, ordinal INTEGER, byte_offset INTEGER)"
        )
        connection.execute(
            "INSERT INTO artifact VALUES('a', ?, 'synthetic', 'eligible')", (str(source),)
        )
        connection.execute("INSERT INTO cursor VALUES('a', 12, 345)")
    lock = Path(str(db) + ".lock")
    lock.touch(mode=0o600)
    return db, lock


def test_registered_source_never_pruned_or_checkpoint_reset(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original = line(raw="synthetic")
    path.write_bytes(original)
    db, _ = make_ledger(tmp_path / "recovery", path)
    before = db.read_bytes()
    result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert path.read_bytes() == original and db.read_bytes() == before
    assert not receipt_path(path).exists()


def test_busy_or_unknown_ledger_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(line(raw="synthetic"))
    db, lock = make_ledger(tmp_path / "recovery", tmp_path / "other-events.jsonl")
    with lock.open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert prune_file(path, request(tmp_path), now=NOW).status == "error"
    db.write_bytes(b"not sqlite")
    assert prune_file(path, request(tmp_path), now=NOW).status == "error"
    assert not receipt_path(path).exists()


@pytest.mark.parametrize(
    "schema", [{"name": "amplifier.log", "ver": "2.0.0"}, {"name": "other", "ver": "1.0.0"}]
)
def test_unknown_schema_refuses_whole_source(tmp_path: Path, schema: dict[str, str]) -> None:
    path = tmp_path / "events.jsonl"
    record = json.loads(line(raw="synthetic"))
    record["schema"] = schema
    body = line(raw="earlier valid line") + json.dumps(record).encode() + b"\n"
    path.write_bytes(body)
    assert prune_file(path, request(tmp_path), now=NOW).status == "error"
    assert path.read_bytes() == body and not receipt_path(path).exists()


def test_unsupported_native_metadata_refuses(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(line(raw="synthetic"))
    (tmp_path / "metadata.json").write_text('{"format":"context-intelligence","version":"9.0.0"}')
    assert prune_file(path, request(tmp_path), now=NOW).status == "error"
    assert not receipt_path(path).exists()


def test_linked_alias_cannot_bypass_target_receipt(tmp_path: Path) -> None:
    from context_intelligence.prune.coordination import PruneError, open_capture

    target = tmp_path / "target.jsonl"
    target.write_bytes(line(raw="synthetic"))
    receipt_path(target).write_bytes(b"{}")
    alias = tmp_path / "events.jsonl"
    alias.symlink_to(target)
    with pytest.raises(PruneError, match="unsafe capture source"):
        with open_capture(alias):
            pytest.fail("alias must never read marked target")


def test_missing_source_marker_is_still_checked(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    receipt_path(path).symlink_to(tmp_path / "absent")
    with pytest.raises(PrunedCapture):
        with replay_guard(path):
            pytest.fail("dangling receipt must deny even when source is missing")


def test_conflicting_nested_age_is_retained_without_receipt(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original = line(raw="synthetic", timestamp=NOW.isoformat())
    path.write_bytes(original)
    result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "unchanged" and result.missing_timestamps == 1
    assert path.read_bytes() == original and not receipt_path(path).exists()


def test_dangling_standard_recovery_root_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original = line(raw="synthetic")
    path.write_bytes(original)
    (tmp_path / "recovery").symlink_to(tmp_path / "absent")
    result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "error" and "recovery-state-unsafe:" in (result.reason or "")
    assert path.read_bytes() == original and not receipt_path(path).exists()


@pytest.mark.parametrize("name", ["apply", "writers_stopped", "inactive_only", "backup"])
def test_python_safety_flags_reject_truthy_non_booleans(tmp_path: Path, name: str) -> None:
    options: dict[str, Any] = {name: "false"}
    with pytest.raises(ValueError, match="must be a boolean"):
        PruneRequest(tmp_path, **options)
    with pytest.raises(ValueError, match="must be a boolean"):
        PruneRequest.from_options(tmp_path, **options)
    assert list(tmp_path.iterdir()) == []


def test_python_response_selection_rejects_truthy_non_boolean() -> None:
    invalid: Any = "false"
    with pytest.raises(ValueError, match="must be a boolean"):
        Policy(include_responses=invalid)


def test_recovery_registration_immediately_before_capture_fence_is_skipped(tmp_path: Path) -> None:
    from contextlib import contextmanager
    from context_intelligence.prune import operations

    path = tmp_path / "events.jsonl"
    original = line(raw="synthetic")
    path.write_bytes(original)
    registered = []
    real_lock = capture_lock

    @contextmanager
    def register_then_lock(source: Path, *, exclusive: bool = False):
        # Reproduce registration just before pruning acquires the capture fence.
        # The old ledger-before-capture ordering would already have missed this job.
        db, _ = make_ledger(tmp_path / "recovery", source)
        registered.append((db, db.read_bytes()))
        with real_lock(source, exclusive=exclusive):
            yield

    with patch.object(operations, "capture_lock", register_then_lock):
        result = prune_file(path, request(tmp_path), now=NOW)
    assert result.status == "skipped" and "skipped-ledger:" in (result.reason or "")
    assert path.read_bytes() == original and not receipt_path(path).exists()
    assert registered[0][0].read_bytes() == registered[0][1]


def test_capture_is_exclusively_fenced_during_first_recovery_inventory(tmp_path: Path) -> None:
    from context_intelligence.prune import recovery

    path = tmp_path / "events.jsonl"
    path.write_bytes(line(raw="synthetic"))

    def observe_root() -> Path:
        with pytest.raises(CaptureBusy):
            with replay_guard(path):
                pytest.fail("new recovery scan must not register during inventory")
        return tmp_path / "recovery"

    with patch.object(recovery, "default_recovery_root", observe_root):
        assert prune_file(path, request(tmp_path), now=NOW).status == "pruned"
