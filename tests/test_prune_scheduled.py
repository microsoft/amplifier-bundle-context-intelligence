"""Synthetic retention cycles and scheduling configuration; no user captures."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import pytest

from context_intelligence.prune import Policy, PruneRequest, prune_file, replay_guard
from context_intelligence.prune.coordination import PrunedCapture, receipt_path
from context_intelligence.prune.schedule import _argument, unit_text
from context_intelligence.prune.scheduled import (
    JobBusy,
    ScheduleConfig,
    ScheduleError,
    admission,
    atomic_json,
    run_scheduled,
)
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


def config_for(value, **options):
    return ScheduleConfig(root=value.root / "projects", state_root=value.root / "state", **options)


def save_config(value, config):
    path = value.root / "config/config.json"
    atomic_json(path, config.to_dict())
    return path


def test_two_cycles_prune_newly_aged_request_without_touching_metadata(capture):
    """Actual transaction result of cycle 1 feeds cycle 2; injected clocks only."""
    now = fixtures.NOW
    newer = now - timedelta(days=14) + timedelta(seconds=60)
    idle = now - timedelta(days=8)
    capture.owner["released_at"] = idle.isoformat()
    capture.metadata.update(ended_at=idle.isoformat(), last_event_at=idle.isoformat())
    capture.write(capture.owner_path, capture.owner)
    capture.write(capture.metadata_path, capture.metadata)
    lines = [
        json.loads(capture.original),
        {"event": "llm:request", "timestamp": newer.isoformat(), "data": {"raw": "LATER"}},
        {"event": "llm:response", "timestamp": fixtures.OLD.isoformat(), "data": {"raw": "KEEP"}},
        {
            "event": "llm:request",
            "timestamp": fixtures.OLD.isoformat(),
            "data": {"raw": {"$blob_ref": "ci-blob://synthetic"}},
        },
    ]
    capture.path.write_text("".join(json.dumps(row) + "\n" for row in lines))
    os.utime(capture.path, (idle.timestamp(), idle.timestamp()))
    initial_mtime = capture.path.stat().st_mtime_ns
    config = config_for(capture, older_than="14d")
    path = save_config(capture, config)
    first = run_scheduled(path, now=now)
    assert first["state"] == "completed", first
    assert first["summary"]["payloads"] == 1
    body = [json.loads(line) for line in capture.path.read_text().splitlines()]
    assert body[1]["data"]["raw"] == "LATER"
    fence = receipt_path(capture.path).read_bytes()
    metadata = capture.metadata_path.read_bytes()
    second = run_scheduled(path, now=now + timedelta(seconds=120))
    assert second["state"] == "completed", second
    assert second["summary"]["payloads"] == 1
    body = [json.loads(line) for line in capture.path.read_text().splitlines()]
    assert "raw" not in body[1]["data"]
    assert body[2:] == lines[2:]
    assert len(body) == len(lines)  # No records removed, only selected raw fields.
    assert capture.path.stat().st_mtime_ns == initial_mtime
    assert receipt_path(capture.path).read_bytes() == fence
    assert capture.metadata_path.read_bytes() == metadata
    attempts = list(capture.ci.glob("events.jsonl.prune-attempt-*.json"))
    assert len(attempts) == 1
    assert json.loads(attempts[0].read_text())["repeat_pruned"] is True
    with pytest.raises(PrunedCapture), replay_guard(capture.path):
        pass
    third = run_scheduled(path, now=now + timedelta(seconds=180))
    assert third["summary"]["payloads"] == 0
    assert third["fenced_captures"] == 1
    assert third["prior_attempts_unconfirmed"] == 1
    assert len(list(capture.ci.glob("events.jsonl.prune-attempt-*.json"))) == 1


def test_legacy_none_preserves_coupled_cutoff(capture):
    now = fixtures.NOW
    idle = now - timedelta(days=8)
    capture.owner["released_at"] = idle.isoformat()
    capture.metadata.update(ended_at=idle.isoformat(), last_event_at=idle.isoformat())
    capture.write(capture.owner_path, capture.owner)
    capture.write(capture.metadata_path, capture.metadata)
    request = PruneRequest(
        capture.root / "projects",
        Policy(now - timedelta(days=14)),
        inactive_only=True,
        state_root=capture.root / "state",
    )
    assert prune_file(capture.path, request, now=now).status == "skipped"
    assert (
        prune_file(capture.path, replace(request, min_inactive=timedelta(days=7)), now=now).status
        == "would-prune"
    )
    assert capture.path.read_bytes() == capture.original


def test_resumed_capture_must_be_inactive_again(capture):
    config = config_for(capture)
    path = save_config(capture, config)
    assert run_scheduled(path, now=fixtures.NOW)["summary"]["payloads"] == 1
    base = receipt_path(capture.path).read_bytes()
    with capture.path.open("ab") as stream:
        stream.write(
            json.dumps(
                {
                    "event": "llm:request",
                    "timestamp": fixtures.NOW.isoformat(),
                    "data": {"raw": "RESUMED"},
                }
            ).encode()
            + b"\n"
        )
    stamp = fixtures.NOW.timestamp()
    os.utime(capture.path, (stamp, stamp))
    capture.owner["released_at"] = fixtures.NOW.isoformat()
    capture.metadata.update(
        ended_at=fixtures.NOW.isoformat(), last_event_at=fixtures.NOW.isoformat()
    )
    capture.write(capture.owner_path, capture.owner)
    capture.write(capture.metadata_path, capture.metadata)
    assert run_scheduled(path, now=fixtures.NOW + timedelta(hours=1))["summary"]["payloads"] == 0
    assert run_scheduled(path, now=fixtures.NOW + timedelta(days=8))["summary"]["payloads"] == 1
    assert receipt_path(capture.path).read_bytes() == base


@pytest.mark.parametrize(
    "options",
    [
        {"at": "02:00\nExecStart=evil"},
        {"at": "2:00"},
        {"at": "24:00"},
        {"min_inactive": "1h"},
        {"min_idle": "8d"},
        {"older_than": "0s"},
        {"max_runtime": True},
        {"max_runtime": 0},
        {"max_runtime": 86401},
        {"dry_run": "false"},
        {"version": True},
        {"root": Path("relative")},
    ],
)
def test_invalid_config_rejected_before_effects(options):
    with pytest.raises((ScheduleError, ValueError)):
        ScheduleConfig(**options)


def test_config_duplicates_and_unknown_values_sanitized(capture):
    path = capture.root / "bad/config.json"
    path.parent.mkdir()
    path.write_text('{"version":1,"version":1,"SENTINEL":"RAW"}')
    path.chmod(0o600)
    result = run_scheduled(path)
    assert result["exit_code"] == 1
    assert "SENTINEL" not in json.dumps(result)
    assert not (path.parent / "last-run.json").exists()


@pytest.mark.parametrize(
    "older_than,min_inactive,now",
    [
        ("1000000d", "7d", fixtures.NOW),
        ("7d", "1000000d", fixtures.NOW),
        ("14d", "7d", datetime(1, 1, 10, tzinfo=timezone.utc)),
        ("7d", "14d", datetime(1, 1, 10, tzinfo=timezone.utc)),
        ("7d", "7d", fixtures.NOW.replace(tzinfo=None)),
    ],
    ids=["age-overflow", "inactivity-overflow", "ancient-age", "ancient-inactivity", "naive"],
)
def test_duration_refusal_precedes_scan_and_preserves_prior_reports(
    capture, older_than, min_inactive, now
):
    path = save_config(
        capture, config_for(capture, older_than=older_than, min_inactive=min_inactive)
    )
    reports = [path.parent / name for name in ("last-run.json", "last-success.json")]
    for report in reports:
        atomic_json(report, {"sentinel": "PRIOR"})
    protected = {
        item: item.read_bytes()
        for item in (path, capture.path, capture.owner_path, capture.metadata_path, *reports)
    }
    with patch(
        "context_intelligence.prune.scheduled.iter_prune_tree",
        side_effect=AssertionError("configuration refusal must precede capture traversal"),
    ):
        try:
            result = run_scheduled(path, now=now)
        finally:
            assert {item: item.read_bytes() for item in protected} == protected
            assert not receipt_path(capture.path).exists()
    assert result == {
        "state": "failed",
        "exit_code": 1,
        "error_code": "configuration_or_state_refused",
    }


@pytest.mark.parametrize("field", ["older_than", "min_inactive"])
def test_saved_extreme_duration_cli_emits_one_refusal_json(capture, monkeypatch, field):
    from click.testing import CliRunner

    repo = Path(__file__).resolve().parents[1]
    module = repo / "modules/tool-context-intelligence-prune"
    monkeypatch.syspath_prepend(str(module))
    cli = import_module("amplifier_module_tool_context_intelligence_prune.scheduled_cli")
    module_path = cli.__file__
    assert module_path is not None
    assert Path(module_path).resolve() == (
        module / "amplifier_module_tool_context_intelligence_prune/scheduled_cli.py"
    )
    assert cli.run_scheduled is run_scheduled
    path = save_config(capture, config_for(capture, **{field: "1000000d"}))
    marker = receipt_path(capture.path)
    marker.write_bytes(b"SENTINEL-RECEIPT")
    reports = [path.parent / name for name in ("last-run.json", "last-success.json")]
    for report in reports:
        atomic_json(report, {"sentinel": "PRIOR"})
    protected = {item: item.read_bytes() for item in (path, capture.path, marker, *reports)}
    with patch(
        "context_intelligence.prune.scheduled.iter_prune_tree",
        side_effect=AssertionError("configuration refusal must precede capture traversal"),
    ):
        result = CliRunner().invoke(cli.main, ["run", "--config", str(path)])
    assert {item: item.read_bytes() for item in protected} == protected
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout) == {
        "state": "failed",
        "exit_code": 1,
        "error_code": "configuration_or_state_refused",
    }


def test_busy_does_not_overwrite_last_result(capture):
    path = save_config(capture, config_for(capture))
    last = path.parent / "last-run.json"
    atomic_json(last, {"sentinel": "PRIOR"})
    with admission(path):
        assert run_scheduled(path)["state"] == "skipped_busy"
        with pytest.raises(JobBusy), admission(path):
            pass
    assert json.loads(last.read_text()) == {"sentinel": "PRIOR"}


def test_run_malformed_source_does_not_leak_raw(capture, capsys):
    capture.path.write_bytes(b'{"event":"SENTINEL-PRIVATE-RAW" BROKEN\n')
    capture.age(capture.path)
    path = save_config(capture, config_for(capture))
    result = run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "partial"
    assert result["reasons"] == {"malformed_event": 1}
    assert "SENTINEL" not in json.dumps(result)
    assert "SENTINEL" not in (path.parent / "last-run.json").read_text()
    assert "SENTINEL" not in capsys.readouterr().out
    assert not receipt_path(capture.path).exists()


def test_unknown_fence_is_never_cleared(capture):
    marker = receipt_path(capture.path)
    marker.write_text('{"SENTINEL":"unknown"}')
    path = save_config(capture, config_for(capture))
    result = run_scheduled(path, now=fixtures.NOW)
    assert result["exit_code"] == 1
    assert result["fenced_captures"] == 1
    assert capture.path.read_bytes() == capture.original
    assert marker.read_text() == '{"SENTINEL":"unknown"}'


def test_unit_quotes_expansion_without_shell(tmp_path):
    config_path = tmp_path / 'a% b$".json'
    units = unit_text(ScheduleConfig(), config_path, Path(sys.executable))
    service = units["context-intelligence-local-prune.service"]
    assert "%%" in service and "$$" in service and '\\"' in service
    assert "-m" in service and "scheduled_cli" in service
    assert "bash" not in service
    timer = units["context-intelligence-local-prune.timer"]
    assert "OnCalendar=*-*-* 02:00:00" in timer
    assert "Persistent=false" in timer
    assert "Restart=no" in service
    with pytest.raises(ScheduleError):
        _argument("bad\narg")


def test_public_repeat_requires_verified_inactive_path(capture):
    with pytest.raises(ValueError):
        PruneRequest(capture.path, repeat_pruned=True, writers_stopped=True)
    with pytest.raises(ValueError):
        PruneRequest.from_options(capture.path, repeat_pruned="false")


def test_precommit_deadline_prevents_publication(capture):
    path = save_config(capture, config_for(capture, max_runtime=1))
    with patch("context_intelligence.prune.scheduled.time.monotonic", side_effect=[0, 2, 2, 2]):
        result = run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "interrupted"
    assert capture.path.read_bytes() == capture.original
    assert not receipt_path(capture.path).exists()


def test_sigterm_during_record_stops_before_replacement(capture):
    import signal
    from context_intelligence.prune import storage

    path = save_config(capture, config_for(capture))
    transform = storage.prune_record
    previous_handler = signal.getsignal(signal.SIGTERM)

    def stop(record, policy):
        signal.raise_signal(signal.SIGTERM)
        return transform(record, policy)

    with patch.object(storage, "prune_record", stop):
        result = run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "interrupted"
    assert capture.path.read_bytes() == capture.original
    assert not receipt_path(capture.path).exists()
    assert signal.getsignal(signal.SIGTERM) == previous_handler


def test_only_safety_skips_is_success_without_claiming_deletion(capture):
    capture.owner["active"] = True
    capture.write(capture.owner_path, capture.owner)
    path = save_config(capture, config_for(capture))
    result = run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "completed" and result["exit_code"] == 0
    assert result["summary"]["skipped_files"] == 1
    assert result["summary"]["payloads"] == 0
    assert result["reasons"] == {"owner_not_released": 1}
    assert capture.path.read_bytes() == capture.original


def test_incomplete_installation_refuses_run_before_capture_scan(capture):
    path = save_config(capture, config_for(capture))
    atomic_json(
        path.parent / "installation.json",
        {
            "format": "context-intelligence-local-prune-installation",
            "version": 1,
            "config": str(path),
            "state": "installing",
            "config_sha256": config_for(capture).sha256,
        },
    )
    result = run_scheduled(path, now=fixtures.NOW)
    assert result["state"] == "failed" and result["exit_code"] == 1
    assert capture.path.read_bytes() == capture.original
    assert not receipt_path(capture.path).exists()
