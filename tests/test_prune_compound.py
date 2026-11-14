"""Synthetic exact-generation composition; never clears a replay/recovery fence."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import patch

import pytest

from context_intelligence.optimize import apply, stage
from context_intelligence.prune import operations, storage, optimize_retention_receipt
from context_intelligence.prune.coordination import assert_replayable, PrunedCapture, receipt_path
from context_intelligence.prune.policy import Policy
from test_optimize_operations import SyntheticMediaOwner
from test_optimize_stream import image

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


@pytest.fixture
def converted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard"
    )
    monkeypatch.setattr(
        "context_intelligence.prune.recovery.default_recovery_root", lambda: tmp_path / "recovery"
    )
    path = tmp_path / "events.jsonl"
    pixels = b'{"synthetic_diagnostic":1}'
    digest = hashlib.sha256(pixels).hexdigest()
    assets = tmp_path / "provider-payloads"
    assets.mkdir()
    asset = assets / f"{digest}.json"
    asset.write_bytes(pixels)
    ref = dict(sha256=digest, bytes=len(pixels), redacted=True, truncated=False)
    record = dict(
        event="llm:request",
        session_id="s1",
        timestamp="2026-01-01T00:00:00+00:00",
        data=dict(
            raw=dict(messages=[dict(role="user", content=[image()])]),
            provider_payloads=dict(raw=ref),
            usage=dict(tokens=7),
        ),
    )
    path.write_text(json.dumps(record) + "\n")
    owner = SyntheticMediaOwner(tmp_path / "media")
    directory = tmp_path / "conversion"
    stage(path, directory, session_id="s1", media=owner, writers_stopped=True)
    apply(directory, media=owner, writers_stopped=True)
    return path, directory, asset


def request(path, directory):
    return operations.PruneRequest(
        path,
        Policy(NOW - timedelta(hours=12), diagnostic_sidecars=True),
        apply=True,
        writers_stopped=True,
        verified_optimize=directory,
        min_idle=timedelta(0),
    )


def attempt(path):
    return optimize_retention_receipt(
        path, hashlib.sha256(receipt_path(path).read_bytes()).hexdigest()
    )


def test_exact_verified_generation_can_expire_without_clear_or_replay(converted):
    path, directory, asset = converted
    fence = receipt_path(path).read_bytes()
    optimized = path.read_bytes()
    original = (directory / "original").read_bytes()
    preview = operations.prune_file(path, replace(request(path, directory), apply=False), now=NOW)
    assert preview.status == "would-prune" and path.read_bytes() == optimized
    assert not attempt(path).exists()
    result = operations.prune_file(path, request(path, directory), now=NOW)
    assert result.status == "pruned", result.reason
    row = json.loads(path.read_bytes())
    assert "raw" not in row["data"] and "provider_payloads" not in row["data"]
    assert row["data"]["provider_payloads_expired"]["raw"]["reason"] == "retention"
    assert row["data"]["usage"] == {"tokens": 7}
    assert asset.exists()  # the preserved exact original remains a consumer
    assert result.asset_bytes_unlinked == result.asset_files_unlinked == 0
    assert result.asset_files_planned == result.asset_bytes_planned == 0
    assert result.asset_files_held_recovery == 1
    assert result.asset_bytes_held_recovery == asset.stat().st_size
    assert receipt_path(path).read_bytes() == fence
    assert (directory / "original").read_bytes() == original
    assert attempt(path).is_file()
    assert result.diagnostic_manifest
    with pytest.raises(PrunedCapture):
        assert_replayable(path)
    second = operations.prune_file(path, request(path, directory), now=NOW)
    assert second.status == "error"  # never replay the previous retention effect


@pytest.mark.parametrize("artifact", ["journal.json", "original", "assets.jsonl", "records.jsonl"])
def test_changed_verified_generation_refuses(converted, artifact):
    path, directory, asset = converted
    original = path.read_bytes()
    if artifact == "journal.json":
        value = json.loads((directory / artifact).read_bytes())
        value["candidate_sha256"] = "0" * 64
        (directory / artifact).write_text(json.dumps(value))
    else:
        with (directory / artifact).open("ab") as target:
            target.write(b" ")
    result = operations.prune_file(path, request(path, directory), now=NOW)
    assert result.status == "error" and path.read_bytes() == original and asset.exists()


def test_incomplete_generation_and_transfer_fence_refuse(converted):
    path, directory, asset = converted
    journal = directory / "journal.json"
    value = json.loads(journal.read_bytes())
    value["state"] = "replace-pending"
    journal.write_text(json.dumps(value))
    assert operations.prune_file(path, request(path, directory), now=NOW).status == "error"
    value["state"] = "verified"
    journal.write_text(json.dumps(value))
    path.with_name("transfer-fence.json").symlink_to(path.with_name("missing"))
    assert operations.prune_file(path, request(path, directory), now=NOW).status == "error"
    assert asset.exists()


def test_unknown_after_attempt_is_preserved_not_replayed(converted):
    path, directory, asset = converted
    original = path.read_bytes()
    with patch.object(storage.os, "replace", side_effect=OSError("synthetic")):
        result = operations.prune_file(path, request(path, directory), now=NOW)
    assert result.status == "error" and path.read_bytes() == original and asset.exists()
    assert attempt(path).is_file()
    assert operations.prune_file(path, request(path, directory), now=NOW).status == "error"


def test_compound_requires_explicit_offline_qualified_policy(converted):
    path, directory, _ = converted
    for options in (
        dict(writers_stopped=False),
        dict(inactive_only=True),
        dict(repeat_pruned=True),
        dict(policy=Policy()),
    ):
        with pytest.raises(ValueError):
            replace(request(path, directory), **options)


def test_stale_operation_copy_cannot_replay_unknown_attempt(converted):
    import shutil

    path, directory, _ = converted
    stale = directory.with_name("stale-copy")
    shutil.copytree(directory, stale)
    with patch.object(storage.os, "replace", side_effect=OSError("synthetic")):
        assert operations.prune_file(path, request(path, directory), now=NOW).status == "error"
    assert attempt(path).exists()
    original = path.read_bytes()
    assert operations.prune_file(path, request(path, stale), now=NOW).status == "error"
    assert path.read_bytes() == original


def test_attempt_sync_failure_preserves_fence_and_source(converted):
    path, directory, _ = converted
    original = path.read_bytes()
    sync = storage._sync_directory

    def fail_after_publication(parent):
        if attempt(path).exists():
            raise OSError("synthetic")
        sync(parent)

    with patch.object(storage, "_sync_directory", side_effect=fail_after_publication):
        result = operations.prune_file(path, request(path, directory), now=NOW)
    assert result.status == "error" and attempt(path).exists() and path.read_bytes() == original
    assert operations.prune_file(path, request(path, directory), now=NOW).status == "error"


def test_general_receipt_destination_directory_is_synced(tmp_path):
    path = tmp_path / "events.jsonl"
    directory = tmp_path / "operation"
    directory.mkdir()
    observed = []
    with patch.object(
        storage, "_sync_directory", side_effect=lambda parent: observed.append(parent)
    ):
        storage._publish_receipt(path, {"synthetic": True}, destination=directory / "intent.json")
    assert observed == [tmp_path, directory]
