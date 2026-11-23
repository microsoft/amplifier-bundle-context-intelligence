"""Maintenance refusals must not erase capture discovery or delivery results.

All captures are synthetic. HTTP is stubbed; these are local regressions, not
evidence of server ingestion.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from amplifier_module_tool_context_intelligence_upload import cli
from amplifier_module_tool_context_intelligence_upload.logging_hook_format import discover_legacy
from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult
from context_intelligence.prune.coordination import PruneError, capture_lock, receipt_path

from ._legacy_fixtures import build_ci_session, build_legacy_session


@pytest.fixture(autouse=True)
def local_cli(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Use no real settings, credentials, captures, or network connections."""
    monkeypatch.setattr(
        cli,
        "_resolve_connection",
        lambda _: cli._Connection("https://upload.invalid", "synthetic", "static", "", None),
    )

    def no_connect(*args: Any, **kwargs: Any) -> None:
        pytest.fail("network access is forbidden in these regressions")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", no_connect)
    post = MagicMock(return_value=httpx.Response(200))
    monkeypatch.setattr(httpx.Client, "post", post)
    return post


def _invoke(monkeypatch: pytest.MonkeyPatch, root: Path, source_format: str) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "context-intelligence-upload",
            "--path",
            str(root),
            "--format",
            source_format,
            "--job-id",
            "synthetic-maintenance",
            "--progress",
            str(root / "progress.json"),
            "--max-retries",
            "0",
        ],
    )
    cli.main()


def _deny_sidecar(events: Path, failure: str, monkeypatch: pytest.MonkeyPatch) -> int:
    lock = events.with_name(events.name + ".maintenance.lock")
    if failure == "symlink":
        lock.symlink_to(events)
        return errno.ELOOP

    real_open = os.open

    def read_only_sidecar(path: Any, *args: Any, **kwargs: Any) -> int:
        if path == lock:
            raise OSError(errno.EROFS, "private OS detail", str(lock))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", read_only_sidecar)
    return errno.EROFS


@pytest.mark.parametrize("failure", ["symlink", "read-only"])
def test_legacy_discovery_propagates_sidecar_open_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    events = build_legacy_session(tmp_path) / "events.jsonl"
    expected_errno = _deny_sidecar(events, failure, monkeypatch)

    with pytest.raises(PruneError) as refused:
        discover_legacy(tmp_path)

    assert str(refused.value) == "cannot open capture maintenance lock"
    assert isinstance(refused.value.__cause__, OSError)
    assert refused.value.__cause__.errno == expected_errno
    assert str(tmp_path) not in str(refused.value)
    assert "private OS detail" not in str(refused.value)


@pytest.mark.parametrize("failure", ["symlink", "read-only"])
def test_legacy_cli_sidecar_refusal_is_not_empty_upload_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    local_cli: MagicMock,
    failure: str,
) -> None:
    events = build_legacy_session(tmp_path) / "events.jsonl"
    _deny_sidecar(events, failure, monkeypatch)

    with pytest.raises(SystemExit) as stopped:
        _invoke(monkeypatch, tmp_path, "logging-hook")

    output = capsys.readouterr()
    assert stopped.value.code == 1
    assert output.out == ""
    assert output.err == "error: cannot open capture maintenance lock\n"
    assert not (tmp_path / "progress.json").exists()
    local_cli.assert_not_called()


@pytest.mark.parametrize("refusal", ["busy", "marked"])
def test_initial_count_refusal_fails_closed_before_upload_or_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    local_cli: MagicMock,
    refusal: str,
) -> None:
    events = build_ci_session(tmp_path) / "context-intelligence" / "events.jsonl"
    upload = MagicMock(wraps=cli.run_upload)
    count = MagicMock(wraps=cli._count_total_events)
    monkeypatch.setattr(cli, "run_upload", upload)
    monkeypatch.setattr(cli, "_count_total_events", count)

    with ExitStack() as held:
        if refusal == "busy":
            held.enter_context(capture_lock(events, exclusive=True))
        else:
            receipt_path(events).write_bytes(b"synthetic deny marker")
        with pytest.raises(SystemExit) as stopped:
            _invoke(monkeypatch, tmp_path, "context-intelligence")

    output = capsys.readouterr()
    assert stopped.value.code == 1
    assert output.out == ""
    assert ("capture-maintenance-busy" if refusal == "busy" else "pruned-capture") in output.err
    count.assert_called_once()
    upload.assert_not_called()
    local_cli.assert_not_called()
    assert not (tmp_path / "progress.json").exists()


@pytest.mark.parametrize("source_format", ["context-intelligence", "logging-hook"])
@pytest.mark.parametrize("refusal", ["busy", "marked"])
@pytest.mark.parametrize("http_status", [200, 403])
def test_post_upload_refusal_preserves_actual_delivery_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    local_cli: MagicMock,
    source_format: str,
    refusal: str,
    http_status: int,
) -> None:
    if source_format == "logging-hook":
        events = build_legacy_session(tmp_path) / "events.jsonl"
        expected_events = 2
    else:
        events = build_ci_session(tmp_path) / "context-intelligence" / "events.jsonl"
        expected_events = 1
    local_cli.return_value = httpx.Response(http_status)
    real_upload = cli.run_upload
    results: list[UploadResult] = []
    count = MagicMock(wraps=cli._count_total_events)
    monkeypatch.setattr(cli, "_count_total_events", count)

    with ExitStack() as held:

        def deliver_then_refuse_count(**kwargs: Any) -> UploadResult:
            result = real_upload(**kwargs)
            results.append(result)
            # The real upload has finished and released its shared guard.
            if refusal == "busy":
                held.enter_context(capture_lock(events, exclusive=True))
            else:
                receipt_path(events).write_bytes(b"synthetic deny marker")
            return result

        monkeypatch.setattr(cli, "run_upload", deliver_then_refuse_count)
        with pytest.raises(SystemExit) as stopped:
            _invoke(monkeypatch, tmp_path, source_format)

    output = capsys.readouterr()
    result = results[0]
    assert json.loads(output.out) == result.to_dict()
    assert stopped.value.code == (0 if http_status == 200 else 1)
    progress = json.loads((tmp_path / "progress.json").read_text())
    assert progress["status"] == ("completed" if result.success else "failed")
    if result.success:
        assert result.events_uploaded == expected_events
        assert result.sessions_uploaded == 1
        assert local_cli.call_count == expected_events
        assert count.call_count == 2
        assert "warning: reconciliation unavailable" in output.err
        assert "source counts not verified" in output.err
        assert ("capture-maintenance-busy" if refusal == "busy" else "pruned-capture") in output.err
        assert "reconciliation mismatch" not in output.err
        assert "upload complete" in output.err
        assert f"1 sessions  ({expected_events} events)" in output.err
    else:
        assert result.events_uploaded == result.sessions_uploaded == 0
        local_cli.assert_called_once()
        count.assert_called_once()  # No diagnostic reread on a failed upload.
        assert "reconciliation" not in output.err
        assert "HTTP 403" in output.err


def test_post_count_io_failure_warns_without_hiding_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    local_cli: MagicMock,
) -> None:
    build_ci_session(tmp_path)
    monkeypatch.setattr(
        cli, "_count_total_events", MagicMock(side_effect=[1, OSError("synthetic read failure")])
    )

    with pytest.raises(SystemExit) as stopped:
        _invoke(monkeypatch, tmp_path, "context-intelligence")

    output = capsys.readouterr()
    assert stopped.value.code == 0
    assert json.loads(output.out) == {
        "status": "completed",
        "sessions_uploaded": 1,
        "events_uploaded": 1,
    }
    assert "warning: reconciliation unavailable -- synthetic read failure" in output.err
    assert "source counts not verified" in output.err
    assert "upload complete" in output.err
    local_cli.assert_called_once()


def test_post_count_does_not_swallow_programming_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_cli: MagicMock
) -> None:
    build_ci_session(tmp_path)
    monkeypatch.setattr(
        cli, "_count_total_events", MagicMock(side_effect=[1, RuntimeError("synthetic bug")])
    )

    with pytest.raises(RuntimeError, match="synthetic bug"):
        _invoke(monkeypatch, tmp_path, "context-intelligence")

    local_cli.assert_called_once()
