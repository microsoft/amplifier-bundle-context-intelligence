"""Current RecoveryRunner/prune ordering, using real SQLite and separate processes.

Run with the current repo root, recover, hook and upload directories on PYTHONPATH.
No retained sourcecap driver, fake HTTP response, user capture or user DB is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from amplifier_module_tool_context_intelligence_recover import recovery as producer
from context_intelligence.prune import Policy, PruneRequest, prune_file, recovery
from context_intelligence.prune import coordination

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 1, tzinfo=UTC)
WAIT = 20


def _emit(value: dict) -> None:
    print(json.dumps(value), flush=True)


def _command() -> str:
    assert select.select([sys.stdin], [], [], WAIT)[0], "parent command timed out"
    return sys.stdin.readline().strip()


def _receive(process: subprocess.Popen) -> dict:
    assert process.stdout is not None
    assert select.select([process.stdout], [], [], WAIT)[0], "runner response timed out"
    line = process.stdout.readline()
    if not line:
        process.wait(timeout=5)
        assert process.stderr is not None
        pytest.fail(f"runner exited {process.returncode}: {process.stderr.read().decode()}")
    return json.loads(line)


def _send(process: subprocess.Popen, command: str) -> None:
    assert process.stdin is not None
    process.stdin.write((command + "\n").encode())
    process.stdin.flush()


@contextmanager
def _child(home: Path, source: Path, mode: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPLIFIER_")}
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / ".local/state"))
    # Keep current-source packages first even when an installed module exists.
    paths = [
        REPO,
        REPO / "modules/tool-context-intelligence-recover",
        REPO / "modules/hook-context-intelligence",
        REPO / "modules/tool-context-intelligence-upload",
    ]
    env["PYTHONPATH"] = os.pathsep.join(map(str, paths)) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(Path(__file__).resolve()),
            "--runner-child",
            str(home),
            str(source),
            mode,
        ],
        cwd=home,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield process
    finally:
        # Only the PID created above is signalled/reaped. Never broad process cleanup.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


def _runner_child(home: Path, source: Path, mode: str) -> None:
    assert Path.home() == home
    assert Path(producer.__file__).resolve() == (
        REPO
        / "modules/tool-context-intelligence-recover"
        / "amplifier_module_tool_context_intelligence_recover/recovery.py"
    )
    state = home / "state"
    reads: list[str] = []
    sends: list[str] = []
    refusals: list[str] = []
    real_fingerprint = producer._stream_fingerprint
    real_capture_lock = coordination.capture_lock

    def read_spy(path, metadata):
        reads.append(str(path))
        return real_fingerprint(path, metadata)

    def forbid_send(_client, request, *args, **kwargs):
        # Outbound spy: fail before any network operation, never fabricate a response.
        sends.append(str(request.url))
        raise AssertionError("dry-run lock test attempted an HTTP request")

    @contextmanager
    def observe_source_lock(path, *, exclusive=False):
        try:
            with real_capture_lock(path, exclusive=exclusive):
                yield
        except coordination.CaptureBusy:
            refusals.append(str(path))
            raise

    with (
        patch.object(producer, "_stream_fingerprint", read_spy),
        patch.object(producer.httpx.Client, "send", forbid_send),
        patch.object(coordination, "capture_lock", observe_source_lock),
    ):
        if mode == "blocked":
            _emit({"phase": "waiting"})
            assert _command() == "scan"
        runner = producer.RecoveryRunner(
            state_dir=state,
            job_id="staged" if mode == "staged" else "a",
            settings_path=home / "settings.yaml",
        )
        try:
            if mode == "staged":
                real_commit = runner.checkpoint.commit

                def pause_before_commit():
                    db = runner.checkpoint.connection
                    assert db.in_transaction
                    row = db.execute(
                        "SELECT state,source_sha256,record_count FROM artifact"
                    ).fetchone()
                    assert tuple(row) == (
                        "eligible",
                        hashlib.sha256(source.read_bytes()).hexdigest(),
                        1,
                    ), tuple(row)
                    assert db.execute("SELECT COUNT(*) FROM cursor").fetchone()[0] == 1
                    # This succeeds only AFTER the real scan's capture SH exited.
                    # The runner still owns job EX and its membership is uncommitted.
                    with real_capture_lock(source, exclusive=True):
                        pass
                    _emit({"phase": "staged", "capture_ex_available": True, "reads": len(reads)})
                    assert _command() == "commit"
                    real_commit()

                with patch.object(runner.checkpoint, "commit", pause_before_commit):
                    summary = runner.run(dry_run=True)
            else:
                summary = runner.run(dry_run=True)
            artifact_rows = runner.checkpoint.connection.execute(
                "SELECT COUNT(*) FROM artifact"
            ).fetchone()[0]
            cursor_rows = runner.checkpoint.connection.execute(
                "SELECT COUNT(*) FROM cursor"
            ).fetchone()[0]
        finally:
            runner.close()
        _emit(
            {
                "phase": "closed",
                "job_ex_acquired": True,  # Real constructor above acquired nonblocking flock.
                "reads": len(reads),
                "sends": sends,
                "refusals": refusals,
                "artifact_rows": artifact_rows,
                "cursor_rows": cursor_rows,
                "summary": summary.to_dict(),
            }
        )


@pytest.fixture
def synthetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    work = home / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: home / "state")
    source = home / "captures/project/sessions/synthetic/context-intelligence/events.jsonl"
    source.parent.mkdir(parents=True)
    metadata = {
        "format": "context-intelligence",
        "version": "1.0.0",
        "session_id": "synthetic",
        "workspace": "project",
        "parent_id": "",
        "working_dir": str(work),
        "status": "completed",
        "ended_at": "2026-01-01T01:00:00Z",
    }
    (source.parent / "metadata.json").write_text(json.dumps(metadata))
    source.write_text(
        json.dumps(
            {
                "event": "llm:request",
                "workspace": "project",
                "timestamp": "2026-01-01T00:00:00Z",
                "data": {
                    "session_id": "synthetic",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "raw": {"messages": ["synthetic"]},
                },
            }
        )
        + "\n"
    )
    (home / "settings.yaml").write_text(
        json.dumps(
            {
                "overrides": {
                    "hook-context-intelligence": {
                        "config": {
                            "base_path": str(home / "captures"),
                            "destinations": {
                                "synthetic": {
                                    "url": "https://unused.invalid",
                                    "api_key": "synthetic",
                                    "include": ["**"],
                                }
                            },
                        }
                    }
                },
            }
        )
    )
    return home, source


def _request(home: Path) -> PruneRequest:
    return PruneRequest(
        home,
        Policy(NOW - timedelta(days=14)),
        apply=True,
        writers_stopped=True,
        min_idle=timedelta(0),
    )


def _rows(db: Path) -> tuple[list[tuple], list[tuple]]:
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0)) as reader:
        return (
            list(reader.execute("SELECT * FROM artifact")),
            list(reader.execute("SELECT * FROM cursor")),
        )


def _exited_successfully(process: subprocess.Popen) -> None:
    assert process.wait(timeout=WAIT) == 0
    assert process.stderr is not None
    assert process.stderr.read() == b""


def test_pending_real_scan_commit_is_fenced_by_job_ex_then_membership(synthetic_home) -> None:
    home, source = synthetic_home
    original = source.read_bytes()
    db = home / "state/staged.v2.sqlite3"
    with _child(home, source, "staged") as process:
        staged = _receive(process)
        assert staged == {"phase": "staged", "capture_ex_available": True, "reads": 1}
        # SQLite itself confirms the staged registration is invisible to another reader.
        assert _rows(db) == ([], [])
        with coordination.capture_lock(source, exclusive=True):
            pass
        result = prune_file(source, _request(home), now=NOW)
        assert result.status == "error"
        assert (result.reason or "").startswith("recovery-state-busy-or-unreadable:")
        assert source.read_bytes() == original
        assert not coordination.receipt_path(source).exists()
        _send(process, "commit")
        closed = _receive(process)
        assert closed["phase"] == "closed" and closed["artifact_rows"] == closed["cursor_rows"] == 1
        assert closed["reads"] == 1 and closed["sends"] == [] and closed["refusals"] == []
        _exited_successfully(process)

    before = _rows(db)
    assert len(before[0]) == len(before[1]) == 1
    db_bytes = db.read_bytes()
    result = prune_file(source, _request(home), now=NOW)
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert source.read_bytes() == original and not coordination.receipt_path(source).exists()
    assert _rows(db) == before and db.read_bytes() == db_bytes


def test_early_job_release_allows_runner_but_capture_ex_prevents_registration(
    synthetic_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, source = synthetic_home
    original = source.read_bytes()
    for job in ("a", "z"):
        checkpoint = producer.RecoveryCheckpoint(home / "state", job)
        checkpoint.close()
    visits: list[str] = []
    observations: list[dict] = []
    real_exclude = recovery._exclude_registered
    with _child(home, source, "blocked") as process:
        assert _receive(process) == {"phase": "waiting"}

        def observe(connection: sqlite3.Connection, path: Path) -> None:
            name = Path(connection.execute("PRAGMA database_list").fetchone()[2]).name
            real_exclude(connection, path)
            visits.append(name)
            if name == "z.v2.sqlite3":
                # Job a's check has finished, but prune still holds capture EX.
                # A real runner must get a's EX and fail its real source SH guard.
                _send(process, "scan")
                report = _receive(process)
                observations.append(report)
                assert report["phase"] == "closed" and report["job_ex_acquired"]
                assert report["reads"] == 0 and report["sends"] == []
                assert report["refusals"] == [str(source)]
                assert report["artifact_rows"] == report["cursor_rows"] == 0
                assert report["summary"]["pending"] and report["summary"]["retry_after_s"] == 1
                assert _rows(home / "state/a.v2.sqlite3") == ([], [])
                assert source.read_bytes() == original
                assert not coordination.receipt_path(source).exists()
                _exited_successfully(process)

        monkeypatch.setattr(recovery, "_exclude_registered", observe)
        result = prune_file(source, _request(home), now=NOW)

    assert visits == ["a.v2.sqlite3", "z.v2.sqlite3"] and len(observations) == 1
    assert result.status == "pruned", result
    assert coordination.receipt_path(source).is_file()
    assert json.loads(source.read_bytes())["data"] == {
        "session_id": "synthetic",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    assert _rows(home / "state/a.v2.sqlite3") == ([], [])


if __name__ == "__main__":
    assert sys.argv[1] == "--runner-child"
    _runner_child(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
