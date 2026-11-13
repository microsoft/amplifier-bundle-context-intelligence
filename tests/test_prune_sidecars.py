"""Synthetic exact-owner fixtures. No real captures or external resources."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from context_intelligence.prune import operations, storage
from context_intelligence.prune.policy import Policy, prune_record
from context_intelligence.prune.sidecars import Sidecars

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
OLD = "2026-11-01T00:00:00Z"
CUTOFF = NOW - timedelta(hours=12)


def asset(root: Path, value: bytes = b'{"synthetic":true}') -> tuple[Path, dict]:
    digest = hashlib.sha256(value).hexdigest()
    directory = root / "provider-payloads"
    directory.mkdir(exist_ok=True)
    path = directory / f"{digest}.json"
    path.write_bytes(value)
    return path, {"sha256": digest, "bytes": len(value), "redacted": True, "truncated": False}


def event(ref: dict, *, name="llm:request", stamp=OLD, key="raw") -> dict:
    return {
        "event": name,
        "timestamp": stamp,
        "event_id": "fixed",
        "session_id": "synthetic",
        "data": {
            "provider_payloads": {key: ref},
            "raw": None,
            "usage": {"cost": "0.123"},
            "status": "failed",
            "tool_call_id": "call-1",
            "nested": {"raw": "KEEP"},
        },
    }


def source(root: Path, records: list[dict]) -> Path:
    path = root / "events.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def request(path: Path, *, apply=True, responses=False, backup=False) -> operations.PruneRequest:
    return operations.PruneRequest(
        path,
        Policy(CUTOFF, responses, diagnostic_sidecars=True),
        apply=apply,
        writers_stopped=True,
        min_idle=timedelta(0),
        backup=backup,
    )


@pytest.fixture(autouse=True)
def isolated_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "recovery-state"
    )


def test_public_apply_metadata_first_actual_unlink_and_identity(tmp_path):
    pixel, ref = asset(tmp_path)
    record = event(ref)
    path = source(tmp_path, [record, event(ref)])
    unlink = os.unlink
    order = []

    def checked_unlink(target, *args, **kwargs):
        if Path(target) == pixel:
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            assert all("provider_payloads" not in row["data"] for row in rows)
            order.append("metadata-first")
        return unlink(target, *args, **kwargs)

    with patch("context_intelligence.prune.sidecars.os.unlink", side_effect=checked_unlink):
        result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "pruned", result.reason
    assert order == ["metadata-first"]
    assert result.asset_files_planned == result.asset_files_unlinked == 1
    assert result.asset_bytes_unlinked == ref["bytes"]
    assert not pixel.exists()
    for row in map(json.loads, path.read_text().splitlines()):
        assert row["event_id"] == record["event_id"]
        assert row["timestamp"] == record["timestamp"]
        assert row["data"]["usage"] == record["data"]["usage"]
        assert row["data"]["nested"] == record["data"]["nested"]
        assert row["data"]["status"] == "failed"
        assert row["data"]["tool_call_id"] == "call-1"
        assert row["data"]["provider_payloads_expired"]["raw"] == {
            "v": 1,
            "sha256": ref["sha256"],
            "bytes": ref["bytes"],
            "expired_at": NOW.isoformat(),
            "reason": "retention",
        }
    assert (tmp_path / result.diagnostic_manifest).is_file()
    assert operations.prune_file(path, request(path), now=NOW).status == "skipped"


@pytest.mark.parametrize(
    "held",
    [
        {"stamp": CUTOFF.isoformat()},
        {"stamp": NOW.isoformat()},
        {"stamp": None},
        {"name": "llm:response"},
        {"name": "tool:post"},
        {"key": "raw_response"},
    ],
)
def test_shared_recent_unknown_unselected_refs_pin(tmp_path, held):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref), event(ref, **held)])
    result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "pruned"
    assert pixel.exists()
    assert result.asset_files_unlinked == result.asset_files_planned == 0
    assert "provider_payloads" in json.loads(path.read_text().splitlines()[0])["data"]


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "../escape"},
        {"sha256": "A" * 64},
        {"bytes": True},
        {"bytes": 0},
        {"redacted": False},
        {"unknown": True},
        {"truncated": 1},
    ],
)
def test_unknown_descriptor_pins_owner(tmp_path, change):
    pixel, ref = asset(tmp_path)
    unknown = dict(ref, **change)
    path = source(tmp_path, [event(ref), event(unknown)])
    result = operations.prune_file(path, request(path), now=NOW)
    assert result.asset_files_unlinked == 0 and pixel.exists()


@pytest.mark.parametrize("protect", ["backup", "recovery", "events.jsonl.bak", "transcript.jsonl"])
def test_known_recovery_consumers_pin(tmp_path, protect):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    if protect != "backup":
        (tmp_path / protect).write_text("held")
    result = operations.prune_file(path, request(path, backup=protect == "backup"), now=NOW)
    assert result.asset_files_unlinked == 0 and pixel.exists()


def test_preview_no_unlink_no_journal_and_unreferenced_not_orphan(tmp_path):
    pixel, ref = asset(tmp_path)
    orphan, _ = asset(tmp_path, b'{"not_referenced":1}')
    path = source(tmp_path, [event(ref)])
    original = path.read_bytes()
    result = operations.prune_file(path, request(path, apply=False), now=NOW)
    assert result.status == "would-prune"
    assert result.asset_files_planned == 1 and result.asset_files_unlinked == 0
    assert path.read_bytes() == original and pixel.exists() and orphan.exists()
    assert not list(tmp_path.glob(".diagnostic-*"))


@pytest.mark.parametrize("fault", ["hash", "size", "symlink", "hardlink"])
def test_asset_integrity_failure_preserves_source_and_asset(tmp_path, fault):
    pixel, ref = asset(tmp_path)
    if fault == "hash":
        pixel.write_bytes(b"X" * ref["bytes"])
    elif fault == "size":
        pixel.write_bytes(b"short")
    elif fault == "symlink":
        pixel.unlink()
        pixel.symlink_to(tmp_path / "absent")
    else:
        os.link(pixel, tmp_path / "hardlink")
    path = source(tmp_path, [event(ref)])
    original = path.read_bytes()
    result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "error"
    assert path.read_bytes() == original and os.path.lexists(pixel)


def test_replace_failure_keeps_refs_and_pixels_with_intent(tmp_path):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    original = path.read_bytes()
    with patch.object(storage.os, "replace", side_effect=OSError("synthetic")):
        result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "error" and path.read_bytes() == original and pixel.exists()
    assert list(tmp_path.glob(".diagnostic-*.sqlite3"))
    assert operations.prune_file(path, request(path), now=NOW).status == "skipped"


def test_unlink_failure_keeps_committed_accounting_unknown_no_replay(tmp_path):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    with patch.object(Sidecars, "unlink", side_effect=OSError("synthetic-secret")):
        result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "pruned" and result.reason
    assert result.asset_durability_unknown and result.asset_files_unlinked == 0
    assert pixel.exists()
    assert "provider_payloads" not in json.loads(path.read_text())["data"]
    assert "synthetic-secret" not in result.reason
    assert operations.prune_file(path, request(path), now=NOW).status == "skipped"


def test_metadata_fsync_failure_never_unlinks(tmp_path):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    original_sync = storage._sync_directory
    calls = 0

    def sync(directory):
        nonlocal calls
        calls += 1
        if calls == 3:  # manifest, deny-replay receipt, replaced source
            raise OSError("synthetic")
        original_sync(directory)

    with patch.object(storage, "_sync_directory", side_effect=sync):
        result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "pruned" and result.reason
    assert pixel.exists() and result.asset_files_unlinked == 0


def test_response_age_is_independent_and_opt_in():
    row = event({}, name="llm:response", stamp="2026-11-13T00:00:00Z", key="raw_response")
    row["data"]["raw_response"] = "diagnostic"
    policy = Policy(CUTOFF, True, True, response_cutoff=NOW - timedelta(days=7))
    assert prune_record(row, policy)[0] is row
    with pytest.raises(ValueError):
        Policy(response_cutoff=CUTOFF)


def test_direct_raw_fields_are_directional_and_generic_blob_is_distinct():
    row = event({})
    row["data"].update(raw_request="drop", raw_response="keep", raw={"$blob_ref": "keep"})
    changed, _ = prune_record(row, Policy(CUTOFF, diagnostic_sidecars=True))
    assert changed["data"]["raw_response"] == "keep"
    assert changed["data"]["raw"] == {"$blob_ref": "keep"}
    assert "raw_request" not in changed["data"]


def test_noncanonical_direct_source_cannot_claim_complete_owner(tmp_path):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    duplicate = tmp_path / "copy.jsonl"
    duplicate.write_bytes(path.read_bytes())
    result = operations.prune_file(duplicate, request(duplicate), now=NOW)
    assert result.status == "error" and pixel.exists()
    assert "provider_payloads" in json.loads(path.read_text())["data"]


@pytest.mark.parametrize("phase", ["before", "after"])
def test_new_backup_pins_before_commit_or_unlink(tmp_path, phase):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    original = path.read_bytes()
    original_publish = storage._publish_receipt
    original_sync = storage._sync_directory
    calls = 0

    def publish(*args, **kwargs):
        original_publish(*args, **kwargs)
        if phase == "before":
            (tmp_path / "events.jsonl.bak").write_bytes(original)

    def sync(directory):
        nonlocal calls
        calls += 1
        original_sync(directory)
        if phase == "after" and calls == 3:
            (tmp_path / "events.jsonl.bak").write_bytes(original)

    with (
        patch.object(storage, "_publish_receipt", side_effect=publish),
        patch.object(storage, "_sync_directory", side_effect=sync),
    ):
        result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == ("error" if phase == "before" else "pruned")
    assert pixel.exists() and result.asset_files_unlinked == 0
    if phase == "before":
        assert path.read_bytes() == original


def test_postcommit_stat_failure_preserves_committed_effects(tmp_path):
    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    original_stat = Path.lstat
    original_sync = storage._sync_directory
    committed = False
    calls = 0

    def sync(directory):
        nonlocal committed, calls
        calls += 1
        original_sync(directory)
        committed = calls >= 3

    def lstat(target, *args, **kwargs):
        if committed and target == path:
            raise OSError("synthetic-stat")
        return original_stat(target, *args, **kwargs)

    with (
        patch.object(storage, "_sync_directory", side_effect=sync),
        patch.object(Path, "lstat", lstat),
    ):
        result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "pruned" and result.payloads == 1
    assert result.asset_durability_unknown and pixel.exists()


def test_real_capture_maintenance_busy_pins_assets(tmp_path):
    from context_intelligence.prune.coordination import capture_lock

    pixel, ref = asset(tmp_path)
    path = source(tmp_path, [event(ref)])
    original = path.read_bytes()
    with capture_lock(path, exclusive=True):
        result = operations.prune_file(path, request(path), now=NOW)
    assert result.status == "skipped" and pixel.exists() and path.read_bytes() == original


def test_distinct_exact_parents_do_not_share_digest_accounting(tmp_path):
    total = 0
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        pixel, ref = asset(root)
        path = source(root, [event(ref)])
        result = operations.prune_file(path, request(path), now=NOW)
        assert result.status == "pruned" and not pixel.exists()
        total += result.asset_bytes_unlinked
    assert total == 2 * ref["bytes"]
