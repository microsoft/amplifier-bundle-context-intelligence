"""Bounded sanitized scheduled evidence, using synthetic captures only."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from context_intelligence.prune import scheduled
from context_intelligence.prune.storage import FileResult
import test_prune_inactive as fixtures


@pytest.fixture
def capture(monkeypatch):
    value = fixtures.InactiveTests()
    value.setUp()
    monkeypatch.setenv("HOME", str(value.root))
    try:
        yield value
    finally:
        value.doCleanups()


def save_config(capture, **options):
    config = scheduled.ScheduleConfig(
        root=capture.root / "projects", state_root=capture.root / "state", **options
    )
    path = capture.root / "config/config.json"
    scheduled.atomic_json(path, config.to_dict())
    return path


def receipt(path, result):
    return path.parent / result["run_receipt"]


def test_mixed_status_same_code_and_committed_warning(capture):
    path = save_config(capture)
    rows = [
        FileResult(capture.path, status="skipped", reason="identity mismatch"),
        FileResult(capture.path, status="error", reason="identity mismatch"),
        FileResult(capture.path, status="unchanged"),
        FileResult(capture.path, status="would-prune", payloads=1, bytes_before=10, bytes_after=3),
        FileResult(
            capture.path,
            status="pruned",
            payloads=2,
            bytes_before=20,
            bytes_after=9,
            reason="replacement completed, but postcommit guard cleanup failed",
        ),
    ]
    with patch.object(scheduled, "iter_prune_tree", return_value=iter(rows)):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "partial"
    assert result["exit_code"] == 1
    assert result["summary"] == {
        "type": "summary",
        "files": 5,
        "changed_files": 2,
        "skipped_files": 1,
        "errors": 2,
        "payloads": 3,
        "kept_undated": 0,
        "bytes_saved": 18,
        "asset_files_planned": 0,
        "asset_bytes_planned": 0,
        "asset_files_unlinked": 0,
        "asset_bytes_unlinked": 0,
    }
    assert result["reasons"] == {"identity_mismatch": 2, "safety_refusal": 1}
    assert result["status_by_reason"] == {
        "skipped": {"identity_mismatch": 1},
        "error": {"identity_mismatch": 1},
        "unchanged": {"none": 1},
        "would-prune": {"none": 1},
        "pruned": {"safety_refusal": 1},
    }
    assert [row["ordinal"] for row in result["error_details"]] == [2, 5]
    assert [row["status"] for row in result["error_details"]] == ["error", "pruned"]
    assert result["error_details_omitted"] == 0
    assert json.loads(receipt(path, result).read_text()) == result


def test_details_private_bounded_and_batch_continues(capture):
    path = save_config(capture)
    secret = "PRIVATE_PROMPT\nTOKEN=do-not-report/" + "x" * 100000
    private_path = capture.root / "PRIVATE_PATH" / "events.jsonl"
    count = scheduled.ERROR_DETAIL_LIMIT + 37
    rows = (FileResult(private_path, status="error", reason=secret) for _ in range(count))
    with patch.object(scheduled, "iter_prune_tree", return_value=rows):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["summary"]["files"] == count
    assert result["summary"]["errors"] == count
    assert len(result["error_details"]) == scheduled.ERROR_DETAIL_LIMIT
    assert result["error_details_omitted"] == count - scheduled.ERROR_DETAIL_LIMIT
    assert len(json.dumps(result["error_details"]).encode()) <= scheduled.ERROR_DETAIL_BYTES_LIMIT
    for row in result["error_details"]:
        assert row["path_sha256"] == hashlib.sha256(os.fsencode(private_path)).hexdigest()
        assert set(row) == {"ordinal", "path_sha256", "status", "code", "phase", "errno"}
        assert row["phase"] is None and row["errno"] is None
        assert row["code"] == "safety_refusal"
    for file in (path.parent / "last-run.json", receipt(path, result)):
        body = file.read_text()
        assert "PRIVATE" not in body and "TOKEN" not in body
        assert str(capture.root) not in body


def test_exact_byte_budget_and_omitted_count(capture, monkeypatch):
    path = save_config(capture)
    row = FileResult(capture.path, status="error")
    accumulator = scheduled._Attribution()
    accumulator.add(row, 1)
    exact = len(json.dumps(accumulator.error_details, sort_keys=True).encode())
    monkeypatch.setattr(scheduled, "ERROR_DETAIL_BYTES_LIMIT", exact)
    with patch.object(scheduled, "iter_prune_tree", return_value=iter([row, row, row])):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert len(result["error_details"]) == 1
    assert len(json.dumps(result["error_details"], sort_keys=True).encode()) == exact
    assert result["error_details_omitted"] == 2
    monkeypatch.setattr(scheduled, "ERROR_DETAIL_BYTES_LIMIT", exact - 1)
    with patch.object(scheduled, "iter_prune_tree", return_value=iter([row, row, row])):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["error_details"] == []
    assert result["error_details_omitted"] == 3


def test_held_source_sizes_never_read_bodies_or_follow_links(tmp_path):
    known = tmp_path / "known"
    known.write_bytes(b"SECRET" * 5)
    empty = tmp_path / "empty"
    empty.touch()
    linked = tmp_path / "linked"
    linked.symlink_to(known)
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "absent")
    directory_link = tmp_path / "directory-link"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    hard = tmp_path / "hard"
    hard.write_bytes(b"also private")
    os.link(hard, tmp_path / "hard-alias")
    accumulator = scheduled._Attribution()
    paths = [
        known,
        empty,
        linked,
        dangling,
        tmp_path,
        tmp_path / "absent",
        directory_link / "known",
        hard,
    ]
    with patch.object(Path, "open", side_effect=AssertionError("body access forbidden")):
        for ordinal, path in enumerate(paths, 1):
            accumulator.add(FileResult(path, status="skipped"), ordinal)
    held = accumulator.to_dict()["held_source_bytes"]
    assert held["known_bytes"] == 30
    assert held["known_files"] == 2
    assert held["unknown_files"] == 6
    assert held["reclaimable_bytes"] is None and held["sidecar_bytes"] is None


def test_two_runs_keep_immutable_started_and_final_receipts(capture):
    path = save_config(capture, older_than="12h", dry_run=True)
    first = scheduled.run_scheduled(path, now=fixtures.NOW)
    first_path = receipt(path, first)
    first_bytes = first_path.read_bytes()
    started = first_path.with_name("started.json")
    started_bytes = started.read_bytes()
    second = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert first["run_id"] != second["run_id"]
    assert first["exit_code"] == second["exit_code"] == 0
    assert first_path.read_bytes() == first_bytes
    assert started.read_bytes() == started_bytes
    assert json.loads(started_bytes)["state"] == "running"
    assert json.loads((path.parent / "last-run.json").read_text()) == second
    assert json.loads(receipt(path, second).read_text()) == second
    assert first_path.stat().st_mode & 0o077 == 0
    assert first_path.stat().st_nlink == 1
    assert not list(first_path.parent.glob(".local-prune-*"))
    assert capture.path.read_bytes() == capture.original


def test_busy_preserves_run_evidence(capture):
    path = save_config(capture, dry_run=True)
    first = scheduled.run_scheduled(path, now=fixtures.NOW)
    protected = {
        item: item.read_bytes()
        for item in (
            receipt(path, first),
            path.parent / "last-run.json",
            path.parent / "last-success.json",
        )
    }
    with scheduled.admission(path):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "skipped_busy" and result["exit_code"] == 0
    assert {item: item.read_bytes() for item in protected} == protected
    assert len(list((path.parent / "runs").iterdir())) == 1


def test_immutable_publication_refuses_existing_and_dangling(tmp_path):
    path = tmp_path / "result.json"
    scheduled._immutable_json(path, {"sentinel": "first"})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        scheduled._immutable_json(path, {"sentinel": "second"})
    assert path.read_bytes() == original
    link = tmp_path / "linked.json"
    link.symlink_to(tmp_path / "absent.json")
    with pytest.raises(scheduled.ScheduleError):
        scheduled._immutable_json(link, {"sentinel": "second"})
    assert link.is_symlink() and not link.exists()
    assert not list(tmp_path.glob(".local-prune-*"))


def test_last_run_write_failure_preserves_completed_run_receipt(capture):
    path = save_config(capture, dry_run=True)
    write = scheduled.atomic_json
    calls = 0

    def fail_final(destination, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "PRIVATE_EXCEPTION")
        write(destination, value)

    with patch.object(scheduled, "atomic_json", side_effect=fail_final):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result == {
        "state": "failed",
        "exit_code": 1,
        "error_code": "configuration_or_state_refused",
    }
    last = json.loads((path.parent / "last-run.json").read_text())
    assert last["state"] == "running"
    completed = json.loads(receipt(path, last).read_text())
    assert completed["state"] == "dry_run"
    assert not (path.parent / "last-success.json").exists()


def test_run_receipt_failure_keeps_started_and_restores_handlers(capture):
    path = save_config(capture, dry_run=True)
    write = scheduled._immutable_json
    handler = signal.getsignal(signal.SIGTERM)

    def fail_final(destination, value):
        if destination.name == "result.json":
            raise OSError(errno.EIO, "PRIVATE_EXCEPTION")
        write(destination, value)

    with patch.object(scheduled, "_immutable_json", side_effect=fail_final):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["exit_code"] == 1 and result["state"] == "failed"
    assert "PRIVATE" not in json.dumps(result)
    last = json.loads((path.parent / "last-run.json").read_text())
    assert receipt(path, last).with_name("started.json").is_file()
    assert not receipt(path, last).exists()
    assert last["state"] == "running"
    assert signal.getsignal(signal.SIGTERM) == handler


def test_uuid_collision_refuses_before_last_run_or_scan(capture):
    path = save_config(capture, dry_run=True)
    with patch.object(scheduled.uuid, "uuid4", return_value="00000000-0000-4000-8000-000000000000"):
        first = scheduled.run_scheduled(path, now=fixtures.NOW)
        saved = (path.parent / "last-run.json").read_bytes()
        with patch.object(scheduled, "iter_prune_tree", side_effect=AssertionError("no replay")):
            second = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert first["exit_code"] == 0
    assert second["exit_code"] == 1
    assert (path.parent / "last-run.json").read_bytes() == saved


@pytest.mark.parametrize("kind", ["symlink", "public", "file", "config_overlap"])
def test_unsafe_run_namespace_refuses_before_scan(capture, kind):
    path = save_config(capture, dry_run=True)
    runs = path.parent / "runs"
    if kind == "symlink":
        runs.symlink_to(capture.root, target_is_directory=True)
    elif kind == "public":
        runs.mkdir(mode=0o755)
    elif kind == "file":
        runs.write_bytes(b"PRIOR")
    else:
        path = runs
        scheduled.atomic_json(path, scheduled.ScheduleConfig().to_dict())
    with patch.object(scheduled, "iter_prune_tree", side_effect=AssertionError("no scan")):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["exit_code"] == 1
    assert not (path.parent / "last-run.json").exists()


def test_real_malformed_source_error_is_sanitized_and_retained(capture):
    capture.path.write_bytes(b'{"PRIVATE_PROMPT": BROKEN\n')
    capture.age(capture.path)
    path = save_config(capture, older_than="12h")
    result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "partial" and result["exit_code"] == 1
    assert result["summary"]["errors"] == 1
    assert result["error_details"][0]["code"] == "malformed_event"
    assert result["status_by_reason"] == {"error": {"malformed_event": 1}}
    assert "PRIVATE" not in receipt(path, result).read_text()
    assert capture.path.read_bytes() == b'{"PRIVATE_PROMPT": BROKEN\n'


def test_long_valid_policy_does_not_poison_last_run(capture):
    path = save_config(capture, older_than="0" * 60000 + "60d", dry_run=True)
    assert scheduled.ScheduleConfig.load(path).older_than.endswith("60d")
    for _ in range(2):
        rows = [FileResult(capture.path, status="error")] * scheduled.ERROR_DETAIL_LIMIT
        with patch.object(scheduled, "iter_prune_tree", return_value=iter(rows)):
            result = scheduled.run_scheduled(path, now=fixtures.NOW)
        assert result["state"] == "partial"
        assert result["effective_policy"]["older_than"] == "5184000s"
        assert len(result["error_details"]) == scheduled.ERROR_DETAIL_LIMIT
        assert scheduled.read_json(path.parent / "last-run.json") == result
        assert scheduled.read_json(receipt(path, result)) == result
        assert len(receipt(path, result).read_bytes()) <= 65536


def test_full_receipt_size_refused_before_publication(tmp_path):
    path = tmp_path / "result.json"
    with pytest.raises(scheduled.ScheduleError, match="run_receipt_size_limit"):
        scheduled._immutable_json(path, {"oversize": "x" * 65536})
    assert not list(tmp_path.iterdir())


def test_directory_sync_failure_retains_published_evidence(capture):
    path = save_config(capture, dry_run=True)
    sync = scheduled._sync_directory

    def fail_final_sync(directory):
        if (directory / "result.json").is_file():
            raise OSError(errno.EIO, "PRIVATE_EXCEPTION")
        sync(directory)

    with patch.object(scheduled, "_sync_directory", side_effect=fail_final_sync):
        result = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert result["exit_code"] == 1 and result["state"] == "failed"
    last = scheduled.read_json(path.parent / "last-run.json")
    assert last["state"] == "running"
    published = receipt(path, last)
    assert scheduled.read_json(published)["state"] == "dry_run"
    assert not (path.parent / "last-success.json").exists()
    saved = published.read_bytes()
    second = scheduled.run_scheduled(path, now=fixtures.NOW)
    assert second["exit_code"] == 0
    assert published.read_bytes() == saved  # No overwrite/retry of uncertain publication.


def test_hard_exit_after_link_keeps_unconfirmed_evidence(tmp_path):
    path = tmp_path / "result.json"
    script = """
import os
import sys
from pathlib import Path
from context_intelligence.prune import scheduled
link = os.link
def exit_after_link(*args, **kwargs):
    link(*args, **kwargs)
    os._exit(91)
scheduled.os.link = exit_after_link
scheduled._immutable_json(Path(sys.argv[1]), {"state": "synthetic-only"})
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True)
    assert result.returncode == 91
    assert result.stdout == result.stderr == b""
    assert json.loads(path.read_bytes()) == {"state": "synthetic-only"}
    assert path.stat().st_nlink == 2
    saved = {item: item.read_bytes() for item in tmp_path.iterdir()}
    with pytest.raises(scheduled.ScheduleError, match="unsafe_configuration_file"):
        scheduled.read_json(path)
    with pytest.raises(FileExistsError):
        scheduled._immutable_json(path, {"state": "do-not-overwrite"})
    assert {item: item.read_bytes() for item in tmp_path.iterdir()} == saved
