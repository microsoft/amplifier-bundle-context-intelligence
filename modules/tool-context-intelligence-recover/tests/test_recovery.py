"""Contract tests for compact, native-only recovery V2."""

from __future__ import annotations

import fcntl
import hashlib
import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import tracemalloc
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from multiprocessing.connection import Connection
from pathlib import Path

import httpx
import pytest
from amplifier_module_hook_context_intelligence.config_resolver import Destination
from context_intelligence.recovery_retirement import RetirementError, retire_job

from amplifier_module_tool_context_intelligence_recover import cli, recovery
from amplifier_module_tool_context_intelligence_recover.recovery import (
    DestinationConfig,
    RecoveryRunner,
    discover_native_artifacts,
    resolve_scan_roots,
)


def _event(session_id: str | None, timestamp: str = "2026-09-15T00:00:00Z") -> bytes:
    data: dict[str, str] = {"timestamp": timestamp}
    if session_id is not None:
        data["session_id"] = session_id
    return (
        json.dumps({"event": "session:start", "workspace": "project", "data": data}).encode()
        + b"\n"
    )


def _capture(
    root: Path,
    session_id: str = "session-1",
    *,
    working_dir: str = "/workspace/app",
    parent_id: str = "",
    version: str = "1.0.0",
    body: bytes | None = None,
) -> Path:
    capture = root / "project" / "sessions" / session_id / "context-intelligence"
    capture.mkdir(parents=True)
    (capture / "metadata.json").write_text(
        json.dumps(
            {
                "format": "context-intelligence",
                "version": version,
                "session_id": session_id,
                "workspace": "project",
                "parent_id": parent_id,
                "working_dir": working_dir,
                "status": "completed",
                "ended_at": "2026-09-15T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    events = capture / "events.jsonl"
    events.write_bytes(body if body is not None else _event(session_id))
    return events


def _config(*destinations: Destination) -> DestinationConfig:
    return DestinationConfig(
        destinations={destination.name: destination for destination in destinations},
        invalid_names=(),
        base_path=Path("/not-used"),
    )


@pytest.fixture
def configured_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        recovery,
        "resolve_scan_roots",
        lambda _config, explicit: [Path(path).absolute() for path in explicit],
    )
    destination = Destination("default", "https://default.example", "key", ("**",), ())
    monkeypatch.setattr(recovery, "load_destination_config", lambda _path: _config(destination))
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, settings_path: (recovery.load_destination_config(settings_path), None),
    )


def _server(requests: list[httpx.Request], status: int = 202) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(status, json={"status": "queued"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _assert_indexed_next_cursor_plan(runner: RecoveryRunner) -> None:
    target = runner.checkpoint.connection.execute(
        "SELECT target_fingerprint FROM cursor LIMIT 1"
    ).fetchone()["target_fingerprint"]
    plan = list(
        runner.checkpoint.connection.execute(
            f"EXPLAIN QUERY PLAN {recovery._NEXT_CURSOR_SQL}", (target,)
        )
    )
    details = [str(row["detail"]) for row in plan]
    assert any("cursor_target_pending_order" in detail for detail in details)
    assert not any("USE TEMP B-TREE" in detail for detail in details)


def _fresh_cli(args: list[str], *, home: Path) -> subprocess.CompletedProcess[str]:
    """Run the candidate package, without inherited destinations or credentials."""
    repo = Path(__file__).resolve().parents[3]
    source_paths = [
        str(repo),
        str(repo / "modules/tool-context-intelligence-recover"),
        str(repo / "modules/hook-context-intelligence"),
        str(repo / "modules/tool-context-intelligence-query"),
        str(repo / "modules/tool-context-intelligence-prune"),
        *filter(None, os.environ.get("PYTHONPATH", "").split(os.pathsep)),
    ]
    folders = {
        "HOME": home,
        "XDG_CONFIG_HOME": home / "config",
        "XDG_STATE_HOME": home / "xdg-state",
        "XDG_CACHE_HOME": home / "cache",
        "XDG_DATA_HOME": home / "data",
        "AMPLIFIER_HOME": home / ".amplifier",
        "TMPDIR": home / "tmp",
    }
    for path in folders.values():
        path.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, "-m", "amplifier_module_tool_context_intelligence_recover", *args],
        cwd=home,
        env={
            **{name: str(path) for name, path in folders.items()},
            "PATH": os.defpath,
            "PYTHONPATH": os.pathsep.join(source_paths),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "LANG": "C.UTF-8",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    "reason_args",
    [["--reason", "forgot retirement"], ["--reason", ""], ["--reason="]],
    ids=["nonempty", "empty-token", "empty-equals"],
)
@pytest.mark.parametrize("mode", ["--once", "--watch"])
def test_reason_without_retire_refuses_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reason_args: list[str],
    mode: str,
) -> None:
    source = _capture(tmp_path / "captures")
    metadata = source.parent / "metadata.json"
    before = (source.read_bytes(), metadata.read_bytes())
    state = tmp_path / "state"

    def unexpected_runner(*_args: object, **_kwargs: object) -> None:
        pytest.fail("RecoveryRunner reached before invalid --reason was rejected")

    def unexpected_retirement(*_args: object, **_kwargs: object) -> None:
        pytest.fail("retire_job reached before invalid --reason was rejected")

    monkeypatch.setattr(cli, "RecoveryRunner", unexpected_runner)
    monkeypatch.setattr(cli, "retire_job", unexpected_retirement)
    with pytest.raises(SystemExit) as exited:
        cli.main(
            [
                mode,
                "--state-dir",
                str(state),
                "--job-id",
                "invalid-reason",
                "--path",
                str(tmp_path / "captures"),
                *reason_args,
            ]
        )
    assert exited.value.code == 2
    output = capsys.readouterr()
    assert "--reason requires --retire-job" in output.err
    assert output.out == ""
    assert not state.exists()
    assert (source.read_bytes(), metadata.read_bytes()) == before


@pytest.mark.parametrize(
    "reason_args", [["--reason", ""], ["--reason="]], ids=["empty-token", "empty-equals"]
)
@pytest.mark.parametrize("mode", ["--once", "--watch"])
def test_reason_without_retire_refuses_in_fresh_entrypoint(
    tmp_path: Path, configured_roots: None, reason_args: list[str], mode: str
) -> None:
    source = _capture(tmp_path / "captures", working_dir=str(tmp_path))
    state = tmp_path / "state"
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=state, job_id="invalid-reason", client=client)
        try:
            runner.run([tmp_path / "captures"], dry_run=True)
            before_rows = _checkpoint_rows(runner)
            before = {
                path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in [source, source.parent / "metadata.json", *state.iterdir()]
                if path.is_file()
            }
            # A real held lock bounds even the broken watch path. On the old
            # guard, the child reaches RecoveryRunner and reports "already locked",
            # rather than rejecting the administrative option during parsing.
            process = _fresh_cli(
                [
                    mode,
                    "--state-dir",
                    str(state),
                    "--job-id",
                    "invalid-reason",
                    "--path",
                    str(tmp_path / "captures"),
                    *reason_args,
                ],
                home=tmp_path,
            )
            assert process.returncode == 2, process.stderr
            assert "--reason requires --retire-job" in process.stderr
            assert process.stdout == ""
            assert _checkpoint_rows(runner) == before_rows
            assert {
                path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in before
            } == before
            assert {path for path in state.iterdir() if path.is_file()} == {
                path for path in before if path.parent == state
            }
            # Only the parent's dry-run traffic is observed by this mock.
            # The fresh child's refusal is proven by its stderr and unchanged state.
            assert not requests
        finally:
            runner.close()


def test_retired_job_refuses_same_process_and_restarted_entrypoint(
    tmp_path: Path, configured_roots: None
) -> None:
    import sqlite3
    from contextlib import closing

    _capture(tmp_path)
    state = tmp_path / "state"
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=state, job_id="retire-me", client=client)
        runner.run([tmp_path], dry_run=True)
        ledger = runner.checkpoint.path
        with pytest.raises(RetirementError, match="running"):
            retire_job(ledger, reason="Attempt while runner owns the lock")
        runner.close()
        assert not requests
        with closing(sqlite3.connect(ledger)) as db:
            before = list(db.execute("SELECT * FROM cursor"))
        cli.main(
            [
                "--state-dir",
                str(state),
                "--job-id",
                "retire-me",
                "--retire-job",
                "--reason",
                "Operator retired old launcher and abandoned catch-up",
            ]
        )
        for _ in range(2):
            with pytest.raises(RetirementError, match="abandoned"):
                RecoveryRunner(state_dir=state, job_id="retire-me", client=client)
        process = _fresh_cli(
            [
                "--state-dir",
                str(state),
                "--job-id",
                "retire-me",
                "--once",
            ],
            home=tmp_path,
        )
        assert process.returncode != 0
        assert "recovery job is abandoned" in process.stderr
        assert not requests
        with closing(sqlite3.connect(ledger)) as db:
            assert list(db.execute("SELECT * FROM cursor")) == before
        # An unrelated, ordinary job still starts and performs its normal work.
        ordinary = RecoveryRunner(state_dir=state, job_id="ordinary", client=client)
        try:
            assert ordinary.run([tmp_path]).delivered == 1
            assert requests
        finally:
            ordinary.close()


def _checkpoint_rows_from_path(path: Path) -> dict[str, list[tuple]]:
    """Read committed outcomes and any retirement marker without starting a job."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return {
            table: list(db.execute(f"SELECT * FROM {table} ORDER BY rowid"))
            for table in ("artifact", "cursor", "recovery_job_retirement")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
        }


def _assert_job_lock_available(ledger: Path) -> None:
    with Path(str(ledger) + ".lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize(
    ("marker", "refusal"),
    [
        ("valid-retired", "recovery job is abandoned"),
        ("copied-inode", "recovery job retirement identity is invalid"),
        ("wrong-job", "recovery job retirement identity is invalid"),
        ("empty-marker", "invalid recovery job retirement record"),
        ("extra-column", "invalid recovery job retirement schema"),
    ],
)
def test_retirement_markers_refuse_fresh_start_without_changing_checkpoint(
    tmp_path: Path, configured_roots: None, marker: str, refusal: str
) -> None:
    """Missing marker validation must not resume even partially acknowledged work."""
    root, state, job = tmp_path / "captures", tmp_path / "state", "marker"
    source = _capture(root, working_dir=str(tmp_path), body=_event("session-1") * 2)
    metadata = source.parent / "metadata.json"
    before_source = (source.read_bytes(), metadata.read_bytes())
    with (
        _server([]) as client,
        closing(RecoveryRunner(state_dir=state, job_id=job, client=client)) as runner,
    ):
        assert runner.run([root]).delivered == 1
        ledger = runner.checkpoint.path
    outcomes = _checkpoint_rows_from_path(ledger)
    assert len(outcomes["artifact"]) == len(outcomes["cursor"]) == 1
    retire_job(ledger, reason="Synthetic catch-up abandoned")
    assert {
        table: rows
        for table, rows in _checkpoint_rows_from_path(ledger).items()
        if table != "recovery_job_retirement"
    } == outcomes
    if marker == "copied-inode":
        original_inode = ledger.stat().st_ino
        copy = ledger.with_name(ledger.name + ".copy")
        copy.write_bytes(ledger.read_bytes())
        copy.chmod(0o600)
        copy.replace(ledger)
        assert ledger.stat().st_ino != original_inode
    elif marker != "valid-retired":
        with closing(sqlite3.connect(ledger)) as db:
            if marker == "wrong-job":
                db.execute("UPDATE recovery_job_retirement SET job_id='some-other-job'")
            elif marker == "empty-marker":
                db.execute("DELETE FROM recovery_job_retirement")
            else:
                assert marker == "extra-column"
                db.execute("ALTER TABLE recovery_job_retirement ADD COLUMN unexpected TEXT")
            db.commit()
    before = _checkpoint_rows_from_path(ledger)
    # Repeated constructor refusal must also release the in-process lock registry.
    for _ in range(2):
        with pytest.raises(RetirementError, match=refusal):
            RecoveryRunner(state_dir=state, job_id=job)
        _assert_job_lock_available(ledger)
        assert _checkpoint_rows_from_path(ledger) == before
    process = _fresh_cli(
        ["--once", "--state-dir", str(state), "--job-id", job, "--path", str(root)],
        home=tmp_path,
    )
    assert process.returncode != 0, process.stdout
    assert f"RetirementError: {refusal}" in process.stderr, process.stderr
    assert process.stdout == ""
    assert _checkpoint_rows_from_path(ledger) == before
    assert (source.read_bytes(), metadata.read_bytes()) == before_source
    _assert_job_lock_available(ledger)


def _hold_runner(state: Path, job: str, pipe: Connection) -> None:
    """Own the actual runner lock in a spawned interpreter, not the parent's registry."""
    try:
        with closing(RecoveryRunner(state_dir=state, job_id=job)):
            pipe.send("READY")
            assert pipe.poll(10), "Parent did not release the runner"
            assert pipe.recv() == "RELEASE"
        pipe.send("DONE")
    finally:
        pipe.close()


def _pause_retirement_after_begin(state: Path, job: str, pipe: Connection) -> None:
    """Pause only after SQLite has executed BEGIN IMMEDIATE under retire_job's flock."""
    real_connect = sqlite3.connect

    class PausingConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=(), /) -> sqlite3.Cursor:
            result = super().execute(sql, parameters)
            if sql == "BEGIN IMMEDIATE":
                assert self.in_transaction
                pipe.send("READY")
                assert pipe.poll(10), "Parent did not release the retirement transaction"
                assert pipe.recv() == "RELEASE"
            return result

    def connect(*args, **kwargs) -> sqlite3.Connection:
        return real_connect(*args, **kwargs, factory=PausingConnection)

    try:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(sqlite3, "connect", connect)
            receipt = retire_job(
                state / f"{job}.v2.sqlite3", reason="Synthetic retirement-first catch-up abandoned"
            )
        pipe.send(receipt)
    finally:
        pipe.close()


@contextmanager
def _spawn_job_process(
    target: Callable[[Path, str, Connection], None], state: Path, job: str
) -> Iterator[Connection]:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=target, args=(state, job, child))
    try:
        process.start()
        child.close()
        assert parent.poll(10), "Child did not reach its real lock/transaction boundary"
        assert parent.recv() == "READY"
        yield parent
        process.join(10)
        assert not process.is_alive(), "Child did not finish after RELEASE"
        assert process.exitcode == 0
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(10)
            if process.is_alive():
                process.kill()
                process.join(10)
            assert not process.is_alive(), "Owned child survived cleanup"
            process.close()
        parent.close()
        child.close()


def test_runner_first_blocks_retirement_then_succeeds_after_release(
    tmp_path: Path, configured_roots: None
) -> None:
    """Retirement must take the same OS lock as a runner in another process."""
    source = _capture(tmp_path / "captures", working_dir=str(tmp_path))
    before_source = source.read_bytes()
    state, job = tmp_path / "state", "runner-first"
    with closing(RecoveryRunner(state_dir=state, job_id=job)) as runner:
        runner.run([tmp_path / "captures"], dry_run=True)
        ledger = runner.checkpoint.path
    before = _checkpoint_rows_from_path(ledger)
    with _spawn_job_process(_hold_runner, state, job) as pipe:
        with pytest.raises(RetirementError, match="recovery job is running"):
            retire_job(ledger, reason="Cannot abandon a live runner")
        assert _checkpoint_rows_from_path(ledger) == before
        pipe.send("RELEASE")
        assert pipe.poll(10), "Runner did not release its lock"
        assert pipe.recv() == "DONE"
    _assert_job_lock_available(ledger)
    receipt = retire_job(ledger, reason="Runner released; synthetic catch-up abandoned")
    assert receipt["disposition"] == "abandoned"
    after = _checkpoint_rows_from_path(ledger)
    assert after["artifact"] == before["artifact"]
    assert after["cursor"] == before["cursor"]
    assert len(after["recovery_job_retirement"]) == 1
    assert source.read_bytes() == before_source
    _assert_job_lock_available(ledger)


def test_retirement_first_blocks_fresh_start_then_reports_abandoned_after_commit(
    tmp_path: Path, configured_roots: None
) -> None:
    """Startup must not slip between retirement's real BEGIN and committed marker."""
    root, state, job = tmp_path / "captures", tmp_path / "state", "retirement-first"
    source = _capture(root, working_dir=str(tmp_path))
    before_source = source.read_bytes()
    with closing(RecoveryRunner(state_dir=state, job_id=job)) as runner:
        runner.run([root], dry_run=True)
        ledger = runner.checkpoint.path
    before = _checkpoint_rows_from_path(ledger)
    args = ["--once", "--state-dir", str(state), "--job-id", job, "--path", str(root)]
    with _spawn_job_process(_pause_retirement_after_begin, state, job) as pipe:
        # Verify the pause is after a REAL write transaction acquired SQLite's lock.
        with (
            closing(sqlite3.connect(ledger, timeout=0)) as db,
            pytest.raises(sqlite3.OperationalError, match="locked"),
        ):
            db.execute("BEGIN IMMEDIATE")
        process = _fresh_cli(args, home=tmp_path)
        assert process.returncode != 0, process.stdout
        assert "recovery job is already locked" in process.stderr, process.stderr
        assert process.stdout == ""
        assert _checkpoint_rows_from_path(ledger) == before
        pipe.send("RELEASE")
        assert pipe.poll(10), "Retirement did not commit after RELEASE"
        receipt = pipe.recv()
        assert receipt["job_id"] == job
        assert receipt["disposition"] == "abandoned"
    after = _checkpoint_rows_from_path(ledger)
    assert after["artifact"] == before["artifact"]
    assert after["cursor"] == before["cursor"]
    assert len(after["recovery_job_retirement"]) == 1
    process = _fresh_cli(args, home=tmp_path)
    assert process.returncode != 0, process.stdout
    assert "RetirementError: recovery job is abandoned" in process.stderr, process.stderr
    assert process.stdout == ""
    assert _checkpoint_rows_from_path(ledger) == after
    assert source.read_bytes() == before_source
    _assert_job_lock_available(ledger)


@pytest.mark.parametrize(
    "args",
    [
        ["--retire-job"],
        ["--reason", "missing retirement command"],
        ["--retire-job", "--state-dir", "/unused", "--job-id", "../job", "--reason", "bad id"],
        ["--retire-job", "--state-dir", "/unused", "--job-id", "job", "--reason", "x", "--dry-run"],
    ],
)
def test_retirement_cli_requires_explicit_existing_job(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2


def test_v2_checkpoint_is_compact_locked_and_leaves_v1_untouched(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path)
    legacy = tmp_path / "state" / "job.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"v1 must remain untouched")
    runner = RecoveryRunner(state_dir=legacy.parent, job_id="job")
    try:
        summary = runner.run([tmp_path], dry_run=True)
        assert summary.legacy_checkpoint_present is True
        assert runner.checkpoint.path.name == "job.v2.sqlite3"
        assert legacy.read_bytes() == b"v1 must remain untouched"
        with pytest.raises(RuntimeError, match="already locked"):
            RecoveryRunner(state_dir=legacy.parent, job_id="job")
        tables = {
            row[0]
            for row in runner.checkpoint.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"artifact", "cursor"}
        cursor_indexes = {
            row[1] for row in runner.checkpoint.connection.execute("PRAGMA index_list(cursor)")
        }
        assert "cursor_artifact_target_state" in cursor_indexes
        assert runner.checkpoint.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        stat_facts = runner.checkpoint.connection.execute(
            """
            SELECT source_size,source_mtime_ns,source_inode,
                   metadata_size,metadata_mtime_ns,metadata_inode
            FROM artifact
            """
        ).fetchone()
        assert all(value > 0 for value in stat_facts)
    finally:
        runner.close()


def test_discovery_is_file_led_exact_and_has_no_rglob_or_backlog_fetchall(tmp_path: Path) -> None:
    good = _capture(tmp_path)
    lookalike = tmp_path / "not-a-project" / "events.jsonl"
    lookalike.parent.mkdir(parents=True)
    lookalike.write_bytes(b"{}\n")

    assert list(discover_native_artifacts([tmp_path])) == [good]
    source = Path(recovery.__file__).read_text(encoding="utf-8")
    assert ".rglob(" not in source
    assert ".fetchall(" not in source
    assert "LIMIT 1" in source


def test_source_symlink_outside_owner_home_is_quarantined(
    tmp_path: Path, configured_roots: None
) -> None:
    events = _capture(tmp_path)
    outside = tmp_path.parent / "outside-events.jsonl"
    outside.write_bytes(events.read_bytes())
    events.unlink()
    events.symlink_to(outside)

    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="outside")
    try:
        summary = runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert summary.inventory == {"quarantined": 1}


def test_100k_line_plan_has_only_compact_tables_and_bounded_peak_memory(
    tmp_path: Path, configured_roots: None
) -> None:
    events = _capture(tmp_path)
    events.write_bytes(_event("session-1") * 100_001)
    tracemalloc.start()
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="large")
    try:
        summary = runner.run([tmp_path], dry_run=True)
        _, peak = tracemalloc.get_traced_memory()
        db_bytes = runner.checkpoint.path.stat().st_size
        rows = runner.checkpoint.connection.execute(
            "SELECT (SELECT COUNT(*) FROM artifact), (SELECT COUNT(*) FROM cursor)"
        ).fetchone()
    finally:
        runner.close()
        tracemalloc.stop()

    assert summary.inventory == {"eligible": 1}
    assert tuple(rows) == (1, 1)
    assert db_bytes < 200_000
    assert peak < 8 * 1024 * 1024


def test_flat_1000_artifact_selection_uses_index_without_temp_sort(
    tmp_path: Path, configured_roots: None
) -> None:
    for number in range(1000):
        _capture(tmp_path, f"flat-{number:04}")

    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="flat-plan")
    try:
        runner.run([tmp_path], dry_run=True)
        _assert_indexed_next_cursor_plan(runner)
    finally:
        runner.close()


def test_progress_cycle_uses_cached_targets_without_checkpoint_aggregates(
    tmp_path: Path, configured_roots: None
) -> None:
    body = _event("session-1") + _event("session-1", "2026-09-15T00:00:01Z")
    for number in range(1000):
        _capture(
            tmp_path,
            f"watch-{number:04}",
            body=body.replace(b"session-1", f"watch-{number:04}".encode()),
        )
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="watch-sql", client=_server(requests)
    )
    try:
        runner.run([tmp_path], dry_run=True)
        traced: list[str] = []
        runner.checkpoint.connection.set_trace_callback(traced.append)
        summary = runner.run(refresh=False)
    finally:
        runner.close()

    assert summary.delivered == 1
    assert len([request for request in requests if request.url.path == "/recovery/events"]) == 1
    progress_sql = "\n".join(traced).upper()
    assert "SELECT DISTINCT" not in progress_sql
    assert "FROM ARTIFACT GROUP BY STATE" not in progress_sql


def test_unchanged_artifact_plan_reuses_durable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    original = recovery._stream_fingerprint
    calls = 0

    def counted(path: Path, metadata: dict[str, object]) -> tuple[int, str, str, str | None]:
        nonlocal calls
        calls += 1
        return original(path, metadata)

    monkeypatch.setattr(recovery, "_stream_fingerprint", counted)
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="fast-path")
    try:
        runner.run([tmp_path], dry_run=True)
        runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert calls == 1


def test_accepted_artifact_plan_reuses_durable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path, body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"))
    original = recovery._stream_fingerprint
    calls = 0

    def counted(path: Path, metadata: dict[str, object]) -> tuple[int, str, str, str | None]:
        nonlocal calls
        calls += 1
        return original(path, metadata)

    monkeypatch.setattr(recovery, "_stream_fingerprint", counted)
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="progress", client=_server(requests)
    )
    try:
        assert runner.run([tmp_path]).delivered == 1
        assert runner.run([tmp_path]).delivered == 1
        assert runner.run([tmp_path], dry_run=True).inventory == {"accepted": 1}
    finally:
        runner.close()

    assert calls == 1


def test_incremental_delivery_refreshes_only_touched_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    """One cursor advance must not re-derive state for every artifact in the plan."""
    _capture(tmp_path, "first")
    _capture(tmp_path, "second")
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="incremental-state", client=_server([])
    )
    try:
        runner.run([tmp_path], dry_run=True)
        original = runner.checkpoint.artifact_state_from_cursors
        refreshed: list[str] = []

        def counted(artifact_id: str) -> tuple[str, str]:
            refreshed.append(artifact_id)
            return original(artifact_id)

        monkeypatch.setattr(runner.checkpoint, "artifact_state_from_cursors", counted)
        assert runner.run(refresh=False).delivered == 1
    finally:
        runner.close()

    assert len(refreshed) == 1


def test_invalid_destination_remains_blocked_and_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    invalid = DestinationConfig({}, ("invalid",), Path("/not-used"))
    monkeypatch.setattr(
        recovery, "load_effective_destination_config", lambda *_args: (invalid, None)
    )
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="invalid-destination")
    try:
        summary = runner.run([tmp_path], dry_run=True)
        cursor = runner.checkpoint.connection.execute(
            "SELECT state,error FROM cursor WHERE destination='invalid'"
        ).fetchone()
    finally:
        runner.close()

    assert summary.inventory == {"blocked": 1}
    assert summary.pending is True
    assert tuple(cursor) == ("blocked", "invalid-destination")


def test_refresh_caches_effective_configuration_per_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path, "one")
    _capture(tmp_path, "two")
    original = recovery.load_effective_destination_config
    calls = 0

    def counted(
        working_dir: str, settings_path: Path
    ) -> tuple[DestinationConfig | None, str | None]:
        nonlocal calls
        calls += 1
        return original(working_dir, settings_path)

    monkeypatch.setattr(recovery, "load_effective_destination_config", counted)
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="route-cache")
    try:
        runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert calls == 1


def test_progress_reuses_client_and_capability_until_the_next_plan_refresh(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(
        tmp_path,
        body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"),
    )
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="capability-cache", client=_server(requests)
    )
    try:
        assert runner.run([tmp_path]).delivered == 1
        assert runner.run(refresh=False).delivered == 1
    finally:
        runner.close()

    assert len([request for request in requests if request.url.path == "/version"]) == 1


def test_oversize_source_line_is_quarantined_without_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    events = _capture(tmp_path)
    monkeypatch.setattr(recovery, "_MAX_SOURCE_LINE_BYTES", 32)
    events.write_bytes(b"{" + b"x" * 64 + b"}\n")

    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="oversize")
    try:
        summary = runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert summary.inventory == {"quarantined": 1}


def test_metadata_version_and_same_sized_middle_mutation_are_quarantined(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "bad-version", version="2.0.0")
    events = _capture(
        tmp_path,
        "changed",
        body=_event("changed") + _event("changed", "2026-09-15T00:00:01Z"),
    )
    first = RecoveryRunner(state_dir=tmp_path / "state", job_id="source")
    try:
        assert first.run([tmp_path], dry_run=True).inventory == {"eligible": 1, "quarantined": 1}
    finally:
        first.close()

    original = events.read_bytes()
    events.write_bytes(original.replace(b"00:00:01Z", b"00:00:02Z"))
    requests: list[httpx.Request] = []
    second = RecoveryRunner(state_dir=tmp_path / "state", job_id="source", client=_server(requests))
    try:
        summary = second.run([tmp_path])
    finally:
        second.close()

    assert summary.inventory == {"quarantined": 2}
    assert requests == []


def test_mutation_after_plan_before_delivery_never_posts_stale_record(
    tmp_path: Path, configured_roots: None
) -> None:
    events = _capture(
        tmp_path,
        body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"),
    )
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="post-plan-mutation",
        client=_server(requests),
    )
    try:
        runner.run([tmp_path], dry_run=True)
        original = events.read_bytes()
        events.write_bytes(original.replace(b"00:00:01Z", b"00:00:02Z"))
        summary = runner.run(refresh=False)
    finally:
        runner.close()

    assert summary.inventory == {"quarantined": 1}
    assert [request for request in requests if request.url.path == "/recovery/events"] == []


def test_missing_line_session_id_is_injected_but_conflicting_id_is_not_sent(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "injected", body=_event(None))
    _capture(tmp_path, "conflict", body=_event("not-conflict"))
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="session", client=_server(requests)
    )
    try:
        summary = runner.run([tmp_path])
    finally:
        runner.close()

    posted = [request for request in requests if request.url.path == "/recovery/events"]
    assert summary.inventory == {"accepted": 1, "quarantined": 1}
    assert json.loads(posted[0].content)["data"]["session_id"] == "injected"


def test_destinations_have_independent_cursors_and_one_post_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path, body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"))
    one = Destination("one", "https://one.example", "key", ("**",), ())
    two = Destination("two", "https://two.example", "key", ("**",), ())
    monkeypatch.setattr(recovery, "load_destination_config", lambda _path: _config(one, two))
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="fanout", client=_server(requests))
    try:
        summary = runner.run([tmp_path])
        rows = runner.checkpoint.connection.execute(
            "SELECT destination,ordinal,state FROM cursor ORDER BY destination"
        ).fetchall()
    finally:
        runner.close()

    assert summary.delivered == 2
    assert [(row["destination"], row["ordinal"], row["state"]) for row in rows] == [
        ("one", 1, "pending"),
        ("two", 1, "pending"),
    ]


def test_same_url_destinations_isolate_bad_and_good_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    bad = Destination("bad", "https://shared.example", "bad-key", ("**",), ())
    good = Destination("good", "https://shared.example", "good-key", ("**",), ())
    monkeypatch.setattr(recovery, "load_destination_config", lambda _path: _config(bad, good))
    monkeypatch.setattr(
        RecoveryRunner,
        "_destination_headers",
        staticmethod(lambda destination: {"Authorization": destination.api_key}),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "bad-key":
            return httpx.Response(401)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(202)

    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="same-url",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        summary = runner.run([tmp_path])
        states = {
            row["destination"]: row["state"]
            for row in runner.checkpoint.connection.execute("SELECT destination,state FROM cursor")
        }
    finally:
        runner.close()

    assert summary.delivered == 1
    assert states == {"bad": "blocked", "good": "complete"}
    assert any(request.url.path == "/recovery/events" for request in requests)


def test_credential_repair_reprobes_unchanged_logical_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    current = {"destination": Destination("target", "https://same.example", "bad", ("**",), ())}
    monkeypatch.setattr(
        recovery, "load_destination_config", lambda _path: _config(current["destination"])
    )
    monkeypatch.setattr(
        RecoveryRunner,
        "_destination_headers",
        staticmethod(lambda destination: {"Authorization": destination.api_key}),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "bad":
            return httpx.Response(401)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(202)

    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="credential-repair",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        assert runner.run([tmp_path]).inventory == {"blocked": 1}
        assert runner.run(refresh=False).delivered == 0
        current["destination"] = Destination("target", "https://same.example", "good", ("**",), ())
        repaired = runner.run([tmp_path])
        cursor = runner.checkpoint.connection.execute("SELECT state FROM cursor").fetchone()
    finally:
        runner.close()

    assert repaired.delivered == 1
    assert cursor["state"] == "complete"
    assert [request.headers.get("Authorization") for request in requests].count("bad") == 1
    assert [request.headers.get("Authorization") for request in requests].count("good") == 2


def test_plan_refresh_reloads_runner_injected_keys_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    key_name = "RECOVERY_ROTATED_TEST_KEY"
    monkeypatch.delenv(key_name, raising=False)
    keys_path = tmp_path / ".amplifier" / "keys.env"
    keys_path.parent.mkdir()
    keys_path.write_text(f"{key_name}=bad\n", encoding="utf-8")
    monkeypatch.setattr(recovery.Path, "home", classmethod(lambda _cls: tmp_path))
    hook_config = {
        "destinations": {
            "target": {
                "url": "https://keys.example",
                "api_key": "${RECOVERY_ROTATED_TEST_KEY}",
                "include": ["**"],
            }
        }
    }
    monkeypatch.setattr(
        recovery,
        "load_destination_config",
        lambda _path: recovery._destination_config_from_hook_config(hook_config),
    )
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, _settings: (
            recovery._destination_config_from_hook_config(hook_config),
            None,
        ),
    )
    monkeypatch.setattr(
        RecoveryRunner,
        "_destination_headers",
        staticmethod(lambda destination: {"Authorization": destination.api_key}),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "bad":
            return httpx.Response(401)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(202)

    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="keys-rotation",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        assert runner.run([tmp_path]).inventory == {"blocked": 1}
        keys_path.write_text(f"{key_name}=good\n", encoding="utf-8")
        assert runner.run([tmp_path]).delivered == 1
    finally:
        runner.close()

    assert [request.headers.get("Authorization") for request in requests].count("bad") == 1
    assert [request.headers.get("Authorization") for request in requests].count("good") == 2


def test_target_or_active_policy_change_resets_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    effective = {
        "config": _config(Destination("target", "https://old.example", "key", ("**",), ()))
    }
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, _settings: (effective["config"], None),
    )
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="policy")
    try:
        runner.run([tmp_path], dry_run=True)
        runner.checkpoint.connection.execute(
            "UPDATE cursor SET ordinal=7,byte_offset=99,state='pending'"
        )
        effective["config"] = _config(
            Destination("target", "https://new.example", "key", ("**",), ())
        )
        runner.run([tmp_path], dry_run=True)
        target_reset = runner.checkpoint.connection.execute(
            "SELECT ordinal,byte_offset FROM cursor"
        ).fetchone()
        runner.checkpoint.connection.execute(
            "UPDATE cursor SET ordinal=3,byte_offset=33,state='pending'"
        )
        effective["config"] = _config(
            Destination("target", "https://new.example", "key", ("**", "**/team/"), ())
        )
        runner.run([tmp_path], dry_run=True)
        policy_reset = runner.checkpoint.connection.execute(
            "SELECT ordinal,byte_offset FROM cursor"
        ).fetchone()
    finally:
        runner.close()

    assert tuple(target_reset) == (0, 0)
    assert tuple(policy_reset) == (0, 0)


def test_filtered_and_removed_destinations_are_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    active = Destination("active", "https://active.example", "key", ("**",), ())
    filtered = Destination("filtered", "https://filtered.example", "key", ("never/",), ())
    effective = {"config": _config(active, filtered)}
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, _settings: (effective["config"], None),
    )
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="filters")
    try:
        runner.run([tmp_path], dry_run=True)
        initial = list(
            runner.checkpoint.connection.execute(
                "SELECT destination,state FROM cursor ORDER BY destination"
            )
        )
        effective["config"] = _config(active)
        runner.run([tmp_path], dry_run=True)
        retired = runner.checkpoint.connection.execute(
            "SELECT state FROM cursor WHERE destination='filtered'"
        ).fetchone()
    finally:
        runner.close()

    assert [(row["destination"], row["state"]) for row in initial] == [
        ("active", "pending"),
        ("filtered", "filtered"),
    ]
    assert retired["state"] == "not-required-by-current-policy"


def test_unreadable_effective_config_preserves_pending_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    destination = Destination("target", "https://target.example", "key", ("**",), ())
    available = {"value": True}

    def effective(
        _working_dir: str, _settings: Path
    ) -> tuple[DestinationConfig | None, str | None]:
        return (
            (_config(destination), None)
            if available["value"]
            else (None, "global-settings-unreadable")
        )

    monkeypatch.setattr(recovery, "load_effective_destination_config", effective)
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="unreadable")
    try:
        runner.run([tmp_path], dry_run=True)
        available["value"] = False
        summary = runner.run([tmp_path], dry_run=True)
        cursor = runner.checkpoint.connection.execute("SELECT ordinal,state FROM cursor").fetchone()
    finally:
        runner.close()

    assert summary.inventory == {"blocked": 1}
    assert tuple(cursor) == (0, "pending")


def test_parent_is_delivered_before_child_and_orphan_is_eligible(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "z-parent")
    _capture(tmp_path, "a-child", parent_id="z-parent")
    _capture(tmp_path, "orphan", parent_id="absent")
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="parent", client=_server(requests))
    try:
        runner.run([tmp_path])
        runner.run([tmp_path])
        runner.run([tmp_path])
    finally:
        runner.close()

    sessions = [
        json.loads(request.content)["origin"]["session_id"]
        for request in requests
        if request.url.path == "/recovery/events"
    ]
    assert sessions.index("z-parent") < sessions.index("a-child")
    assert "orphan" in sessions


def test_deep_parent_chain_stays_schedulable_without_recursive_cycle_cte(
    tmp_path: Path, configured_roots: None
) -> None:
    parent_id = ""
    for number in range(32):
        session_id = f"session-{number:03}"
        _capture(tmp_path, session_id, parent_id=parent_id)
        parent_id = session_id
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="deep", client=_server(requests))
    try:
        summaries = [runner.run([tmp_path])]
        _assert_indexed_next_cursor_plan(runner)
        summaries.extend(runner.run(refresh=False) for _ in range(31))
    finally:
        runner.close()

    source = Path(recovery.__file__).read_text(encoding="utf-8")
    assert "WITH RECURSIVE" not in source
    assert sum(summary.delivered for summary in summaries) == 32
    assert summaries[-1].pending is False


def test_lost_ack_retries_identical_canonical_bytes_without_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    artifact = _capture(tmp_path)
    first_payloads: list[bytes] = []

    def lost_ack(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        first_payloads.append(request.content)
        raise httpx.RemoteProtocolError("lost acknowledgement")

    first = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="retry",
        client=httpx.Client(transport=httpx.MockTransport(lost_ack)),
    )
    try:
        first_summary = first.run([tmp_path])
    finally:
        first.close()
    assert first_summary.pending is True
    assert first_summary.retry_after_s is not None

    second_payloads: list[bytes] = []

    def duplicate(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        second_payloads.append(request.content)
        return httpx.Response(202)

    second = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="retry",
        client=httpx.Client(transport=httpx.MockTransport(duplicate)),
    )
    try:
        now = recovery.time.time
        monkeypatch.setattr(recovery.time, "time", lambda: now() + 10)
        assert second.run([tmp_path]).pending is False
    finally:
        second.close()

    assert first_payloads == second_payloads
    assert str(artifact) not in second_payloads[0].decode()


def test_429_defers_only_attempted_cursor_and_stops_after_one_post(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "first")
    _capture(tmp_path, "second")
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="throttle", client=_server(requests, 429)
    )
    try:
        summary = runner.run([tmp_path])
        states = [
            row["state"]
            for row in runner.checkpoint.connection.execute(
                "SELECT state FROM cursor ORDER BY artifact_id"
            )
        ]
    finally:
        runner.close()

    assert len([request for request in requests if request.url.path == "/recovery/events"]) == 1
    assert sorted(states) == ["deferred", "pending"]
    assert summary.retry_after_s is not None


def test_command_preserves_once_nonzero_and_rejects_server_override(
    tmp_path: Path, configured_roots: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture(tmp_path)
    with pytest.raises(SystemExit) as exited:
        cli.main(["--once", "--dry-run", "--path", str(tmp_path)])
    assert exited.value.code == 1
    assert json.loads(capsys.readouterr().out)["inventory"] == {"eligible": 1}
    with pytest.raises(SystemExit) as rejected:
        cli.main(["--server-url", "https://not-accepted.example"])
    assert rejected.value.code == 2


def test_scan_roots_deduplicate_and_reject_other_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "owner"
    home.mkdir(parents=True)
    monkeypatch.setattr(recovery.Path, "home", classmethod(lambda _cls: home))
    config = DestinationConfig({}, (), home / ".amplifier" / "projects")
    monkeypatch.setattr(recovery, "DEFAULT_BASE_PATH", config.base_path)
    expected = [home / ".amplifier" / "projects", home / "captures"]
    explicit_paths = [home / ".amplifier" / "projects", home.parent, home / "captures"]
    assert resolve_scan_roots(config, explicit_paths) == expected


def test_prune_receipt_blocks_new_job_before_any_network(
    tmp_path: Path, configured_roots: None
) -> None:
    from context_intelligence.prune.coordination import receipt_path

    path = _capture(tmp_path)
    original = path.read_bytes()
    receipt_path(path).write_bytes(b"prepared or invalid")
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="pruned", client=_server(requests))
    try:
        summary = runner.run([tmp_path])
        row = runner.checkpoint.connection.execute("SELECT * FROM artifact").fetchone()
        assert row["state"] == "quarantined"
        assert "pruned-capture:" in row["reason"]
        assert summary.delivered == 0
        assert list(runner.checkpoint.connection.execute("SELECT * FROM cursor")) == []
    finally:
        runner.close()
    assert requests == [] and path.read_bytes() == original


def _checkpoint_rows(
    runner: RecoveryRunner,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    connection = runner.checkpoint.connection
    return (
        dict(connection.execute("SELECT * FROM artifact").fetchone()),
        [dict(row) for row in connection.execute("SELECT * FROM cursor")],
    )


def _seed_source_changed(runner: RecoveryRunner, root: Path, mutation: str) -> Path:
    """Create real completed cursors with a synthetic ACK, then detect changed bytes."""
    body = (
        json.dumps(
            {
                "event": "llm:request",
                "workspace": "project",
                "data": {
                    "timestamp": "2026-09-15T00:00:00Z",
                    "session_id": "session-1",
                    "raw": "SYNTHETIC-ORIGINAL",
                },
            }
        ).encode("utf-8")
        + b"\n"
    )
    path = _capture(root, body=body)
    metadata = path.parent / "metadata.json"
    original_metadata = metadata.read_bytes()
    seeded = runner.run([root])
    artifact, cursors = _checkpoint_rows(runner)
    assert seeded.inventory == {"accepted": 1}
    assert seeded.delivered == 1 and seeded.pending is False
    assert artifact["source_sha256"] == hashlib.sha256(body).hexdigest()
    assert artifact["metadata_sha256"] == hashlib.sha256(original_metadata).hexdigest()
    assert len(cursors) == 1
    assert (cursors[0]["state"], cursors[0]["ordinal"], cursors[0]["byte_offset"]) == (
        "complete",
        1,
        len(body),
    )
    if mutation == "events":
        changed = body.replace(b"SYNTHETIC-ORIGINAL", b"SYNTHETIC-MODIFIED")
        assert changed != body and len(changed) == len(body)
        path.write_bytes(changed)
    else:
        assert mutation == "metadata"
        changed = original_metadata.replace(b"01:00:00Z", b"02:00:00Z")
        assert changed != original_metadata and len(changed) == len(original_metadata)
        metadata.write_bytes(changed)
    detected = runner.run([root])
    changed_artifact, changed_cursors = _checkpoint_rows(runner)
    assert detected.inventory == {"quarantined": 1}
    assert detected.delivered == 0 and detected.pending is True
    assert changed_artifact["reason"] == "source-changed"
    assert changed_artifact["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert changed_artifact["metadata_sha256"] == hashlib.sha256(metadata.read_bytes()).hexdigest()
    changed_column = "source_sha256" if mutation == "events" else "metadata_sha256"
    assert changed_artifact[changed_column] != artifact[changed_column]
    assert changed_cursors == cursors
    return path


@contextmanager
def _maintenance_refusal(path: Path, refusal: str) -> Iterator[str]:
    """Repair only this synthetic sidecar; never unlink or move the stable lock."""
    from context_intelligence.prune.coordination import receipt_path

    lock = path.with_name(path.name + ".maintenance.lock")
    inode = lock.stat().st_ino
    if refusal == "receipt":
        sidecar = receipt_path(path)
        sidecar.write_bytes(b"synthetic refusal marker; source has NOT been pruned")
        reason = "pruned-capture: raw retention is irreversible; replay refused"
    else:
        assert refusal == "unsafe-lock"
        sidecar = path.with_name(path.name + ".extra-maintenance-link")
        os.link(lock, sidecar)
        assert lock.stat().st_nlink == 2
        reason = "unsafe capture maintenance lock"
    try:
        yield reason
    finally:
        sidecar.unlink()
        assert lock.stat().st_ino == inode and lock.stat().st_nlink == 1


def _seed_only_server(requests: list[httpx.Request]) -> httpx.Client:
    client = _server(requests)

    def reject_after_seed(request: httpx.Request) -> None:
        if len(requests) >= 2:
            pytest.fail(f"Unexpected request after seed: {request.method} {request.url.path}")

    client.event_hooks["request"].append(reject_after_seed)
    return client


@pytest.mark.parametrize("mutation", ["events", "metadata"])
@pytest.mark.parametrize("refusal", ["receipt", "unsafe-lock"])
def test_source_changed_quarantine_survives_maintenance_refusal_and_repair(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    refusal: str,
) -> None:
    requests: list[httpx.Request] = []
    root, state, job = tmp_path / "captures", tmp_path / "state", "permanent"
    with _seed_only_server(requests) as client:
        runner = RecoveryRunner(state_dir=state, job_id=job, client=client)
        try:
            path = _seed_source_changed(runner, root, mutation)
            before = _checkpoint_rows(runner)
            source = path.read_bytes()
            metadata = (path.parent / "metadata.json").read_bytes()
            with _maintenance_refusal(path, refusal):
                refused = runner.run([root])
                after_refusal = _checkpoint_rows(runner)
        finally:
            runner.close()

        # Exercise the public non-dry-run CLI after reopening the same real job.
        # The double supplies no further ACKs, including capability requests.
        client_type = httpx.Client

        def unexpected(request: httpx.Request) -> httpx.Response:
            pytest.fail(f"Unexpected CLI request: {request.method} {request.url.path}")

        monkeypatch.setattr(
            recovery.httpx,
            "Client",
            lambda **_kwargs: client_type(transport=httpx.MockTransport(unexpected)),
        )
        with pytest.raises(SystemExit) as exited:
            cli.main(["--path", str(root), "--state-dir", str(state), "--job-id", job, "--once"])
        report = json.loads(capsys.readouterr().out)
        assert exited.value.code == 1, f"Changed source falsely completed: {report}"
        assert report["inventory"] == {"quarantined": 1}
        assert report["delivered"] == 0 and report["pending"] is True
        assert refused.inventory == {"quarantined": 1}
        assert refused.delivered == 0 and refused.pending is True
        assert after_refusal == before

        reopened = RecoveryRunner(state_dir=state, job_id=job, client=client)
        try:
            assert _checkpoint_rows(reopened) == before
            subsequent = reopened.run([root])
            assert subsequent.inventory == {"quarantined": 1}
            assert subsequent.delivered == 0 and subsequent.pending is True
            assert _checkpoint_rows(reopened) == before
        finally:
            reopened.close()
        assert path.read_bytes() == source
        assert (path.parent / "metadata.json").read_bytes() == metadata
        assert [(r.method, r.url.path) for r in requests] == [
            ("GET", "/version"),
            ("POST", "/recovery/events"),
        ]
        assert json.loads(requests[1].content)["data"]["raw"] == "SYNTHETIC-ORIGINAL"


def test_busy_scan_preserves_source_changed_quarantine(
    tmp_path: Path, configured_roots: None
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    root = tmp_path / "captures"
    requests: list[httpx.Request] = []
    with _seed_only_server(requests) as client:
        runner = RecoveryRunner(
            state_dir=tmp_path / "state", job_id="permanent-busy", client=client
        )
        try:
            path = _seed_source_changed(runner, root, "events")
            before = _checkpoint_rows(runner)
            with capture_lock(path, exclusive=True):
                busy = runner.run([root])
                assert _checkpoint_rows(runner) == before
            assert busy.inventory == {"quarantined": 1}
            assert busy.delivered == 0 and busy.pending is True
            assert busy.retry_after_s is not None and 0 < busy.retry_after_s <= 1.0
            released = runner.run([root])
            assert released.inventory == {"quarantined": 1}
            assert released.delivered == 0 and released.pending is True
            assert released.retry_after_s is None
            assert _checkpoint_rows(runner) == before
        finally:
            runner.close()
    assert len(requests) == 2


@pytest.mark.parametrize("refusal", ["receipt", "unsafe-lock"])
def test_maintenance_refusal_replaces_retryable_reason_and_recovers(
    tmp_path: Path, configured_roots: None, refusal: str
) -> None:
    root = tmp_path / "captures"
    path = _capture(root)
    source = path.read_bytes()
    metadata = path.parent / "metadata.json"
    completed = metadata.read_bytes()
    incomplete = json.loads(completed)
    incomplete["status"] = "running"
    incomplete.pop("ended_at")
    metadata.write_text(json.dumps(incomplete), encoding="utf-8")
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="repairable", client=client)
        try:
            # Observe the scan's repairable diagnosis before run() derives
            # aggregate state from the (not-yet-created) delivery cursors.
            runner._scan_artifact(path)
            runner.checkpoint.commit()
            artifact, cursors = _checkpoint_rows(runner)
            assert artifact["state"] == "deferred"
            assert artifact["reason"] == "session-incomplete" and cursors == []
            with _maintenance_refusal(path, refusal) as reason:
                refused = runner.run([root])
                artifact, cursors = _checkpoint_rows(runner)
                assert refused.inventory == {"quarantined": 1}
                assert refused.delivered == 0 and refused.pending is True
                assert artifact["reason"] == reason and cursors == []
                assert requests == []
            metadata.write_bytes(completed)
            repaired = runner.run([root])
            artifact, cursors = _checkpoint_rows(runner)
            assert repaired.inventory == {"accepted": 1}
            assert repaired.delivered == 1 and repaired.pending is False
            assert artifact["reason"] == ""
            assert len(cursors) == 1
            assert (cursors[0]["state"], cursors[0]["ordinal"], cursors[0]["byte_offset"]) == (
                "complete",
                1,
                len(source),
            )
        finally:
            runner.close()
    assert path.read_bytes() == source and metadata.read_bytes() == completed
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/version"),
        ("POST", "/recovery/events"),
    ]


def test_busy_scan_preserves_existing_identity_routes_and_acknowledged_offsets(
    tmp_path: Path, configured_roots: None
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    path = _capture(tmp_path, body=_event("session-1") + _event("session-1"))
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="busy", client=_server(requests))
    try:
        runner.run([tmp_path])
        artifact = dict(runner.checkpoint.connection.execute("SELECT * FROM artifact").fetchone())
        cursor = dict(runner.checkpoint.connection.execute("SELECT * FROM cursor").fetchone())
        assert cursor["ordinal"] == 1 and cursor["byte_offset"] > 0
        with capture_lock(path, exclusive=True):
            summary = runner.run([tmp_path], dry_run=True)
            assert summary.pending
            assert summary.retry_after_s is not None and 0 < summary.retry_after_s <= 1.0
            assert (
                dict(runner.checkpoint.connection.execute("SELECT * FROM artifact").fetchone())
                == artifact
            )
            assert (
                dict(runner.checkpoint.connection.execute("SELECT * FROM cursor").fetchone())
                == cursor
            )
        runner.run([tmp_path])
        after = runner.checkpoint.connection.execute("SELECT * FROM cursor").fetchone()
        assert after["ordinal"] == 2 and after["byte_offset"] > cursor["byte_offset"]
    finally:
        runner.close()


def test_prune_refuses_registered_real_v2_checkpoint_without_changing_cursors(
    tmp_path: Path, configured_roots: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from context_intelligence.prune import PruneRequest, prune_file

    path = _capture(tmp_path)
    state = tmp_path / "state"
    runner = RecoveryRunner(state_dir=state, job_id="registered", client=_server([]))
    try:
        runner.run([tmp_path], dry_run=True)
        cursor_before = [
            dict(row) for row in runner.checkpoint.connection.execute("SELECT * FROM cursor")
        ]
    finally:
        runner.close()
    monkeypatch.setattr("context_intelligence.prune.recovery.default_recovery_root", lambda: state)
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    before = path.read_bytes()
    req = PruneRequest(tmp_path, apply=True, writers_stopped=True, min_idle=timedelta(0))
    result = prune_file(path, req, now=datetime.now(UTC))
    assert result.status == "skipped" and "skipped-ledger:" in (result.reason or "")
    runner = RecoveryRunner(state_dir=state, job_id="registered", client=_server([]))
    try:
        assert [
            dict(row) for row in runner.checkpoint.connection.execute("SELECT * FROM cursor")
        ] == cursor_before
    finally:
        runner.close()
    assert path.read_bytes() == before


def test_prune_keeps_current_cli_default_job_with_alternate_authority(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hashlib
    import sqlite3
    from contextlib import closing
    from datetime import UTC, datetime, timedelta

    from context_intelligence import config
    from context_intelligence.prune import PruneRequest, prune_file
    from context_intelligence.prune.coordination import receipt_path

    standard = tmp_path / ".local/state/context-intelligence/recovery"
    alternate = tmp_path / "alternate-authority"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config, "DEFAULT_RECOVERY_STATE_ROOT", standard)
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / ".amplifier/settings.yaml")
    config.SETTINGS_PATH.parent.mkdir()
    config.SETTINGS_PATH.write_text(
        "overrides:\n  hook-context-intelligence:\n    config:\n"
        f"      recovery_state_root: {json.dumps(str(alternate))}\n",
        encoding="utf-8",
    )
    work = tmp_path / "workspace"
    work.mkdir()
    body = (
        json.dumps(
            {
                "event": "llm:request",
                "workspace": "project",
                "timestamp": "2026-01-01T00:00:00Z",
                "data": {
                    "session_id": "session-1",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "raw": "SYNTHETIC ONLY",
                },
            }
        ).encode()
        + b"\n"
    )
    root = tmp_path / "captures"
    path = _capture(root, working_dir=str(work), body=body)
    with pytest.raises(SystemExit) as exited:
        cli.main(["--path", str(root), "--dry-run"])
    assert exited.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["inventory"]["eligible"] == 1
    assert report["delivered"] == 0 and report["pending"] is True
    db = standard / "default/default.v2.sqlite3"

    def rows():
        with closing(sqlite3.connect(db)) as connection:
            return (
                list(connection.execute("SELECT * FROM artifact")),
                list(connection.execute("SELECT * FROM cursor")),
            )

    before_rows = rows()
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT artifact_id FROM artifact").fetchone() == (
            hashlib.sha256(str(path.absolute()).encode()).hexdigest(),
        )
        assert connection.execute("SELECT source_sha256 FROM artifact").fetchone() == (
            hashlib.sha256(body).hexdigest(),
        )
        assert connection.execute("SELECT ordinal,byte_offset,state FROM cursor").fetchone() == (
            0,
            0,
            "pending",
        )
    lock = Path(str(db) + ".lock")
    before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)]
    request = PruneRequest(root, apply=True, writers_stopped=True, min_idle=timedelta(0))
    result = prune_file(path, request, now=datetime.now(UTC))
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert path.read_bytes() == body and not receipt_path(path).exists()
    assert rows() == before_rows
    assert [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)] == before


@pytest.fixture
def watch_maintenance_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Advance local retry time without waiting or changing persisted HTTP time."""
    clock = [1000.0]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock[0])
    return clock


def _watch_no_discovery(*_args: object) -> Iterator[Path]:
    pytest.fail("Watch progress must not rediscover the capture tree")


def _watch_posts(requests: list[httpx.Request]) -> list[dict]:
    return [
        json.loads(request.content)
        for request in requests
        if request.url.path == "/recovery/events"
    ]


def test_watch_maintenance_shared_capture_lock_does_not_defer_registration(
    tmp_path: Path, configured_roots: None, watch_maintenance_clock: list[float]
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    path = _capture(tmp_path)
    source = path.read_bytes()
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-shared", client=client)
        try:
            with capture_lock(path):
                summary = runner.run([tmp_path])
            assert summary.delivered == 1 and summary.pending is False
            assert summary.retry_after_s is None and not runner._maintenance_busy
            assert _checkpoint_rows(runner)[1][0]["byte_offset"] == len(source)
            assert path.read_bytes() == source
        finally:
            runner.close()


@pytest.mark.parametrize("append", [False, True], ids=["unchanged", "appended"])
def test_watch_maintenance_new_capture_retries_without_discovery(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    watch_maintenance_clock: list[float],
    append: bool,
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    path = _capture(tmp_path)
    original = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    metadata = (path.parent / "metadata.json").read_bytes()
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-new", client=client)
        try:
            with capture_lock(path, exclusive=True):
                initial = runner.run([tmp_path])
                assert initial.pending and initial.delivered == 0
                assert list(runner.checkpoint.connection.execute("SELECT * FROM artifact")) == []
                assert list(runner.checkpoint.connection.execute("SELECT * FROM cursor")) == []
                assert requests == []
                if append:
                    with path.open("ab") as handle:
                        handle.write(_event("session-1", "2026-09-15T00:00:01Z"))
            expected = path.read_bytes()
            if not append:
                assert path.stat().st_mtime_ns == stamp and expected == original
            monkeypatch.setattr(recovery, "discover_native_artifacts", _watch_no_discovery)
            watch_maintenance_clock[0] += 1.0
            resumed = runner.run(refresh=False)
            assert resumed.delivered == 1, (
                "Released, previously unregistered capture was not scanned"
            )
            if append:
                resumed = runner.run(refresh=False)
                assert resumed.delivered == 1
            assert resumed.pending is False and resumed.retry_after_s is None
            artifact, cursors = _checkpoint_rows(runner)
            assert artifact["state"] == "accepted"
            assert artifact["record_count"] == (2 if append else 1)
            assert cursors[0]["byte_offset"] == len(expected)
            assert [post["origin"]["ordinal"] for post in _watch_posts(requests)] == (
                [0, 1] if append else [0]
            )
            assert path.read_bytes() == expected
            assert (path.parent / "metadata.json").read_bytes() == metadata
        finally:
            runner.close()


@pytest.mark.parametrize("accepted", [False, True], ids=["partial-ack", "already-accepted"])
def test_watch_maintenance_registered_capture_clears_pending_without_reposting(
    tmp_path: Path,
    configured_roots: None,
    watch_maintenance_clock: list[float],
    accepted: bool,
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    first = _event("session-1")
    body = first if accepted else first + _event("session-1", "2026-09-15T00:00:01Z")
    path = _capture(tmp_path, body=body)
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-ack", client=client)
        try:
            assert runner.run([tmp_path]).delivered == 1
            before_artifact, before_cursors = _checkpoint_rows(runner)
            assert before_cursors[0]["ordinal"] == 1
            assert before_cursors[0]["byte_offset"] == len(first)
            with capture_lock(path, exclusive=True):
                assert runner.run([tmp_path], dry_run=True).pending
                assert _checkpoint_rows(runner) == (before_artifact, before_cursors)
            watch_maintenance_clock[0] += 1
            resumed = runner.run(refresh=False)
            after_artifact, after_cursors = _checkpoint_rows(runner)
            assert resumed.delivered == (0 if accepted else 1)
            assert after_cursors[0]["byte_offset"] == len(body)
            for field in ("artifact_id", "session_id", "working_dir", "source_sha256"):
                assert after_artifact[field] == before_artifact[field]
            for field in ("destination", "target_fingerprint", "policy_fingerprint"):
                assert after_cursors[0][field] == before_cursors[0][field]
            assert [post["origin"]["ordinal"] for post in _watch_posts(requests)] == (
                [0] if accepted else [0, 1]
            )
            assert resumed.pending is False, "Accepted cursor still has stale maintenance pending"
            assert resumed.retry_after_s is None
            assert path.read_bytes() == body
        finally:
            runner.close()


def test_watch_maintenance_due_scans_are_targeted_and_healthy_work_is_not_starved(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    watch_maintenance_clock: list[float],
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    busy = _capture(tmp_path, "busy")
    healthy = _capture(tmp_path, "healthy", body=_event("healthy") * 3)
    requests: list[httpx.Request] = []
    scans: list[tuple[Path, float]] = []
    hashes: list[Path] = []
    busy_reads: list[Path] = []
    real_hash = recovery._stream_fingerprint
    real_open = Path.open

    def open_file(path: Path, mode: str = "r", *args, **kwargs):
        if path in (busy, busy.parent / "metadata.json") and "r" in mode:
            busy_reads.append(path)
        return real_open(path, mode, *args, **kwargs)

    def fingerprint(path: Path, metadata: dict[str, object]) -> tuple[int, str, str, str | None]:
        hashes.append(path)
        return real_hash(path, metadata)

    monkeypatch.setattr(recovery, "_stream_fingerprint", fingerprint)
    monkeypatch.setattr(Path, "open", open_file)
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-due", client=client)
        real_scan = runner._scan_artifact

        def scan(path: Path) -> None:
            scans.append((path, watch_maintenance_clock[0]))
            real_scan(path)

        monkeypatch.setattr(runner, "_scan_artifact", scan)
        try:
            with capture_lock(busy, exclusive=True):
                assert runner.run([tmp_path]).delivered == 1
                monkeypatch.setattr(recovery, "discover_native_artifacts", _watch_no_discovery)
                watch_maintenance_clock[0] = 1000.25
                assert runner.run(refresh=False).delivered == 1
                assert [t for p, t in scans if p == busy] == [1000.0]
                watch_maintenance_clock[0] = 1001.0
                assert runner.run(refresh=False).delivered == 1
                watch_maintenance_clock[0] = 1001.25
                assert runner.run(refresh=False).delivered == 0
                watch_maintenance_clock[0] = 1002.0
                assert runner.run(refresh=False).delivered == 0
                assert hashes == [healthy], "Locked capture bytes must not be fingerprinted"
                assert busy_reads == [], "No source or metadata read is allowed under capture EX"
                assert [post["origin"]["session_id"] for post in _watch_posts(requests)] == [
                    "healthy",
                    "healthy",
                    "healthy",
                ]
                assert [t for p, t in scans if p == busy] == [1000.0, 1001.0, 1002.0]
            watch_maintenance_clock[0] = 1003.0
            done = runner.run(refresh=False)
            assert done.delivered == 1 and done.pending is False
            assert hashes == [healthy, busy]
            assert [p for p, _t in scans].count(healthy) == 1
        finally:
            runner.close()


@pytest.mark.parametrize("mutation", ["events", "metadata"])
def test_watch_maintenance_changed_registered_snapshot_is_quarantined_before_post(
    tmp_path: Path,
    configured_roots: None,
    watch_maintenance_clock: list[float],
    mutation: str,
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    path = _capture(tmp_path, body=_event("session-1") * 2)
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-change", client=client)
        try:
            assert runner.run([tmp_path]).delivered == 1
            _, cursors = _checkpoint_rows(runner)
            with capture_lock(path, exclusive=True):
                runner.run([tmp_path], dry_run=True)
                changed_path = path if mutation == "events" else path.parent / "metadata.json"
                original = changed_path.read_bytes()
                changed_path.write_bytes(original.replace(b"00:00", b"00:01"))
                assert changed_path.read_bytes() != original
            watch_maintenance_clock[0] += 1
            # A scan must diagnose even an accepted or dry-run cursor, not rely on POST revalidation.
            summary = runner.run(refresh=False, dry_run=True)
            artifact, after = _checkpoint_rows(runner)
            assert summary.inventory == {"quarantined": 1}
            assert artifact["reason"] == "source-changed"
            assert (after[0]["ordinal"], after[0]["byte_offset"]) == (
                cursors[0]["ordinal"],
                cursors[0]["byte_offset"],
            )
            assert summary.pending is True and summary.retry_after_s is None
            assert runner.run(refresh=False).delivered == 0
            assert len(_watch_posts(requests)) == 1
        finally:
            runner.close()


def test_watch_maintenance_permanent_quarantine_survives_retry_and_full_refresh(
    tmp_path: Path,
    configured_roots: None,
    watch_maintenance_clock: list[float],
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    requests: list[httpx.Request] = []
    with _seed_only_server(requests) as client:
        runner = RecoveryRunner(
            state_dir=tmp_path / "state", job_id="watch-permanent", client=client
        )
        try:
            path = _seed_source_changed(runner, tmp_path, "events")
            before = _checkpoint_rows(runner)
            with capture_lock(path, exclusive=True):
                assert runner.run([tmp_path]).pending
            watch_maintenance_clock[0] += 1
            summary = runner.run(refresh=False)
            assert summary.inventory == {"quarantined": 1} and summary.pending
            assert _checkpoint_rows(runner) == before
            assert summary.retry_after_s is None, "Permanent refusal retained a transient busy wake"
            assert runner.run([tmp_path]).inventory == {"quarantined": 1}
            assert _checkpoint_rows(runner) == before
        finally:
            runner.close()


@pytest.mark.parametrize("registered", [False, True], ids=["new", "registered"])
@pytest.mark.parametrize("marker", ["malformed", "dangling"])
def test_watch_maintenance_receipt_during_contention_refuses_replay(
    tmp_path: Path,
    configured_roots: None,
    watch_maintenance_clock: list[float],
    registered: bool,
    marker: str,
) -> None:
    from context_intelligence.prune.coordination import capture_lock, receipt_path

    path = _capture(tmp_path, body=_event("session-1") * 2)
    source = path.read_bytes()
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-receipt", client=client)
        try:
            if registered:
                runner.run([tmp_path])
            before_posts = len(_watch_posts(requests))
            before_cursors = [
                (row["ordinal"], row["byte_offset"])
                for row in runner.checkpoint.connection.execute("SELECT * FROM cursor")
            ]
            with capture_lock(path, exclusive=True):
                runner.run([tmp_path], dry_run=True)
                receipt = receipt_path(path)
                if marker == "malformed":
                    receipt.write_bytes(b"not-json")
                else:
                    receipt.symlink_to(path.parent / "absent-receipt-target")
                    assert not receipt.exists() and receipt.is_symlink()
            watch_maintenance_clock[0] += 1
            summary = runner.run(refresh=False, dry_run=True)
            assert summary.inventory == {"quarantined": 1}
            artifact, cursors = _checkpoint_rows(runner)
            assert str(artifact["reason"]).startswith("pruned-capture:")
            assert [(row["ordinal"], row["byte_offset"]) for row in cursors] == before_cursors
            assert summary.pending and summary.retry_after_s is None
            assert not runner._maintenance_busy
            assert runner.run(refresh=False).delivered == 0
            assert len(_watch_posts(requests)) == before_posts
            assert path.read_bytes() == source
        finally:
            runner.close()


def test_watch_maintenance_resolved_parent_is_planned_before_child_delivery(
    tmp_path: Path,
    configured_roots: None,
    watch_maintenance_clock: list[float],
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    parent = _capture(tmp_path, "z-parent")
    child = _capture(tmp_path, "a-child", parent_id="z-parent")
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-parent", client=client)
        try:
            with ExitStack() as locks:
                locks.enter_context(capture_lock(parent, exclusive=True))
                locks.enter_context(capture_lock(child, exclusive=True))
                assert runner.run([tmp_path]).delivered == 0
            watch_maintenance_clock[0] += 1
            first = runner.run(refresh=False)
            second = runner.run(refresh=False)
            assert (first.delivered, second.delivered) == (1, 1)
            assert [post["origin"]["session_id"] for post in _watch_posts(requests)] == [
                "z-parent",
                "a-child",
            ]
            assert second.inventory == {"accepted": 2} and second.pending is False
        finally:
            runner.close()


def test_watch_maintenance_resolved_collision_is_quarantined_before_any_post(
    tmp_path: Path,
    configured_roots: None,
    watch_maintenance_clock: list[float],
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    paths = [_capture(tmp_path / root, "same") for root in ("one", "two")]
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(
            state_dir=tmp_path / "state", job_id="watch-collision", client=client
        )
        try:
            with ExitStack() as locks:
                for path in paths:
                    locks.enter_context(capture_lock(path, exclusive=True))
                runner.run([tmp_path / "one", tmp_path / "two"])
            watch_maintenance_clock[0] += 1
            summary = runner.run(refresh=False)
            assert summary.inventory == {"quarantined": 2}
            assert summary.pending and summary.retry_after_s is None
            assert summary.delivered == 0 and requests == []
            assert {
                row["reason"]
                for row in runner.checkpoint.connection.execute("SELECT * FROM artifact")
            } == {"session-id-collision"}
        finally:
            runner.close()


def test_watch_maintenance_new_destination_rebuilds_delivery_and_inventory_caches(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    watch_maintenance_clock: list[float],
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    _capture(tmp_path, "healthy", working_dir="/workspace/healthy")
    busy = _capture(tmp_path, "busy", working_dir="/workspace/busy")
    one = Destination("one", "https://one.example", "key", ("**",), ())
    two = Destination("two", "https://two.example", "key", ("**",), ())
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda working_dir, _settings: (_config(two if "busy" in working_dir else one), None),
    )
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-route", client=client)
        try:
            with capture_lock(busy, exclusive=True):
                assert runner.run([tmp_path]).delivered == 1
            watch_maintenance_clock[0] += 1
            summary = runner.run(refresh=False)
            assert summary.delivered == 1
            assert summary.inventory == {"accepted": 2} and summary.pending is False
            assert [
                (request.url.host, json.loads(request.content)["origin"]["session_id"])
                for request in requests
                if request.url.path == "/recovery/events"
            ] == [("one.example", "healthy"), ("two.example", "busy")]
        finally:
            runner.close()


def test_watch_maintenance_retry_delay_uses_monotonic_not_http_wall_clock(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    watch_maintenance_clock: list[float],
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    busy = _capture(tmp_path, "busy")
    _capture(tmp_path, "healthy")
    wall = [5000.0]
    monkeypatch.setattr(recovery.time, "time", lambda: wall[0])
    requests: list[httpx.Request] = []
    throttle = [True]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        if json.loads(request.content)["origin"]["session_id"] == "healthy" and throttle[0]:
            throttle[0] = False
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(202)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="watch-clocks", client=client)
        try:
            with capture_lock(busy, exclusive=True):
                assert runner.run([tmp_path]).retry_after_s == 1
                deferred = runner.checkpoint.connection.execute(
                    "SELECT retry_at FROM cursor"
                ).fetchone()
                assert deferred["retry_at"] == 5007.0
                watch_maintenance_clock[0] += 0.25
                wait = runner.run(refresh=False)
                assert wait.retry_after_s == pytest.approx(0.75)
            watch_maintenance_clock[0] = 1001.0
            assert runner.run(refresh=False).delivered == 1
            assert (
                runner.checkpoint.connection.execute(
                    "SELECT retry_at FROM cursor WHERE state='deferred'"
                ).fetchone()["retry_at"]
                == 5007.0
            )
            watch_maintenance_clock[0] = 9000.0
            assert runner.run(refresh=False).delivered == 0
            wall[0] = 5007.0
            done = runner.run(refresh=False)
            assert done.delivered == 1 and done.pending is False
            assert [post["origin"]["session_id"] for post in _watch_posts(requests)] == [
                "healthy",
                "busy",
                "healthy",
            ]
        finally:
            runner.close()


@pytest.mark.parametrize("accepted", [False, True], ids=["new", "already-accepted"])
def test_watch_maintenance_cli_finishes_before_full_refresh_and_releases_job_lock(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    watch_maintenance_clock: list[float],
    accepted: bool,
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    path = _capture(tmp_path)
    requests: list[httpx.Request] = []
    client = _server(requests)
    state, job = tmp_path / "state", "watch-cli"
    if accepted:
        seed = RecoveryRunner(state_dir=state, job_id=job, client=client)
        try:
            assert seed.run([tmp_path]).delivered == 1
        finally:
            seed.close()
    # Only HTTP construction is replaced; CLI constructs and closes the real runner.
    monkeypatch.setattr(recovery.httpx, "Client", lambda **_kwargs: client)
    refreshes: list[bool] = []
    real_run = RecoveryRunner.run

    def run(self: RecoveryRunner, *args, **kwargs):
        refreshes.append(kwargs.get("refresh", True))
        return real_run(self, *args, **kwargs)

    monkeypatch.setattr(RecoveryRunner, "run", run)
    sleeps: list[float] = []
    with ExitStack() as locks:
        locks.enter_context(capture_lock(path, exclusive=True))

        def sleep(delay: float) -> None:
            sleeps.append(delay)
            locks.close()
            watch_maintenance_clock[0] += delay
            assert len(sleeps) <= 4, (
                "Watch remained pending after lock release without a full refresh"
            )

        monkeypatch.setattr(cli.time, "sleep", sleep)
        try:
            with pytest.raises(SystemExit) as exited:
                cli.main(
                    ["--watch", "--path", str(tmp_path), "--state-dir", str(state), "--job-id", job]
                )
            assert exited.value.code == 0
            reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
            assert reports[0]["pending"] is True and reports[-1]["pending"] is False
            assert refreshes == [True, False]
            assert watch_maintenance_clock[0] < 1300.0
            assert [post["origin"]["ordinal"] for post in _watch_posts(requests)] == [0]
        finally:
            assert client.is_closed
            reopened = RecoveryRunner(state_dir=state, job_id=job, client=client)
            reopened.close()


def _source_observation(path: Path) -> tuple[bytes, tuple[int, ...]]:
    body, info = path.read_bytes(), path.stat()
    # Reading may update atime; inode/content/ownership and modification facts
    # must not change on refusal.
    return body, (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _damage_native_capture(path: Path, damage: str) -> None:
    """Write malformed source bytes, never constructing an oversized Python int."""
    metadata = path.parent / "metadata.json"
    if damage.startswith("status-"):
        value = {
            "list": [],
            "dict": {},
            "number": 3,
            "bool": True,
            "null": None,
            "running": "running",
        }
        document = json.loads(metadata.read_bytes())
        if damage == "status-missing":
            document.pop("status")
        else:
            document["status"] = value[damage.removeprefix("status-")]
        metadata.write_text(json.dumps(document), encoding="utf-8")
    else:
        target = metadata if damage.startswith("metadata-") else path
        if damage.endswith("-integer"):
            assert sys.get_int_max_str_digits() == 4300
            original = target.read_bytes().rstrip()
            raw = original[:-1] + b',"oversized":' + b"1" * 4301 + b"}\n"
            assert len(raw) < recovery._MAX_SOURCE_LINE_BYTES
            target.write_bytes(raw)
        elif damage.endswith("-utf8"):
            target.write_bytes(b"\xff\n")
        else:
            assert damage.endswith("-json")
            target.write_bytes(b"{not-json}\n")


@pytest.mark.parametrize("healthy_first", [False, True])
@pytest.mark.parametrize(
    ("damage", "state", "reason"),
    [
        ("status-list", "quarantined", "invalid-native-metadata"),
        ("status-dict", "quarantined", "invalid-native-metadata"),
        ("status-number", "quarantined", "invalid-native-metadata"),
        ("status-bool", "quarantined", "invalid-native-metadata"),
        ("status-null", "blocked", "no-valid-destinations"),
        ("status-missing", "blocked", "no-valid-destinations"),
        ("status-running", "blocked", "no-valid-destinations"),
        ("metadata-integer", "quarantined", "corrupt-metadata"),
        ("metadata-json", "quarantined", "corrupt-metadata"),
        ("metadata-utf8", "quarantined", "corrupt-metadata"),
        ("events-integer", "quarantined", "malformed-line"),
        ("events-json", "quarantined", "malformed-line"),
        ("events-utf8", "quarantined", "malformed-line"),
    ],
)
def test_bad_capture_is_local_and_healthy_sibling_is_inventoried(
    tmp_path: Path,
    configured_roots: None,
    damage: str,
    state: str,
    reason: str,
    healthy_first: bool,
) -> None:
    bad = _capture(tmp_path / "captures", "bad")
    healthy = _capture(tmp_path / "captures", "healthy")
    _damage_native_capture(bad, damage)
    if damage in {"status-null", "status-missing", "status-running"}:
        # Preserve the existing parser classification, not the unrelated
        # reconciliation diagnostic priority for an artifact without cursors.
        assert recovery._read_metadata(bad.parent / "metadata.json", "bad", "project")[3] == (
            "session-incomplete"
        )
    paths = [healthy, bad] if healthy_first else [bad, healthy]
    before = {
        p: _source_observation(p)
        for source in paths
        for p in (source, source.parent / "metadata.json")
    }
    requests: list[httpx.Request] = []
    with (
        _server(requests) as client,
        closing(
            RecoveryRunner(state_dir=tmp_path / "state", job_id="local", client=client)
        ) as runner,
    ):
        summary = runner.run(paths, dry_run=True)
        assert summary.delivered == 0
        assert summary.inventory == {state: 1, "eligible": 1}
        rows = {
            row["path"]: dict(row)
            for row in runner.checkpoint.connection.execute("SELECT * FROM artifact")
        }
        assert rows[str(bad)]["reason"] == reason
        assert rows[str(healthy)]["state"] == "eligible"
        assert not requests
    assert {p: _source_observation(p) for p in before} == before


@pytest.mark.parametrize("mode", ["refresh-pending", "refresh-complete", "delivery"])
@pytest.mark.parametrize(
    "damage",
    [
        "status-list",
        "status-dict",
        "metadata-integer",
        "events-integer",
        "metadata-json",
        "metadata-utf8",
        "events-json",
        "events-utf8",
    ],
)
def test_malformed_changed_snapshot_preserves_ack_and_facts_after_repair_and_reopen(
    tmp_path: Path,
    configured_roots: None,
    mode: str,
    damage: str,
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    root, state, job = tmp_path / "captures", tmp_path / "state", "snapshot"
    first = _event("bad")
    bad = _capture(root, "bad", body=first if mode == "refresh-complete" else first * 2)
    metadata = bad.parent / "metadata.json"
    original = {bad: bad.read_bytes(), metadata: metadata.read_bytes()}
    requests: list[httpx.Request] = []
    fd_root = Path("/proc/self/fd")
    with _server(requests) as client:
        closed_baseline = len(list(fd_root.iterdir())) if fd_root.is_dir() else None
        with closing(RecoveryRunner(state_dir=state, job_id=job, client=client)) as runner:
            assert runner.run([bad]).delivered == 1
            fact, cursors = _checkpoint_rows(runner)
            assert (cursors[0]["ordinal"], cursors[0]["byte_offset"]) == (1, len(first))
            healthy = _capture(root, "healthy")
            runner.run([bad, healthy], dry_run=True)
            _damage_native_capture(bad, damage)
            damaged = {p: _source_observation(p) for p in original}
            requests.clear()
            baseline = len(list(fd_root.iterdir())) if fd_root.is_dir() else None
            for cycle in range(4):
                runner.run([bad, healthy], refresh=mode != "delivery")
                row = dict(
                    runner.checkpoint.connection.execute(
                        "SELECT * FROM artifact WHERE path=?", (str(bad),)
                    ).fetchone()
                )
                if row["state"] == "quarantined":
                    break
            assert (row["state"], row["reason"]) == ("quarantined", "source-changed")
            assert {key: row[key] for key in fact if key not in {"state", "reason"}} == {
                key: value for key, value in fact.items() if key not in {"state", "reason"}
            }
            cursor = dict(
                runner.checkpoint.connection.execute(
                    "SELECT * FROM cursor WHERE artifact_id=?", (fact["artifact_id"],)
                ).fetchone()
            )
            assert (cursor["ordinal"], cursor["byte_offset"]) == (1, len(first))
            if mode == "refresh-complete":
                assert cursor == cursors[0]
            assert {p: _source_observation(p) for p in damaged} == damaged
            assert not [
                r
                for r in requests
                if r.method == "POST" and json.loads(r.content)["origin"]["session_id"] == "bad"
            ]
            ledger = runner.checkpoint.path
            for _ in range(3):
                runner.run([bad, healthy])
                current = dict(
                    runner.checkpoint.connection.execute(
                        "SELECT * FROM artifact WHERE path=?", (str(bad),)
                    ).fetchone()
                )
                assert current == row
                with capture_lock(bad, exclusive=True):
                    pass
                if baseline is not None:
                    assert len(list(fd_root.iterdir())) == baseline
            assert {p: _source_observation(p) for p in damaged} == damaged
            assert any(
                r.method == "POST" and json.loads(r.content)["origin"]["session_id"] == "healthy"
                for r in requests
            )
        _assert_job_lock_available(ledger)
        if closed_baseline is not None:
            assert len(list(fd_root.iterdir())) == closed_baseline
        for _ in range(2):
            with closing(RecoveryRunner(state_dir=state, job_id=job, client=client)) as reopened:
                reopened.run([bad, healthy])
                assert (
                    dict(
                        reopened.checkpoint.connection.execute(
                            "SELECT * FROM artifact WHERE path=?", (str(bad),)
                        ).fetchone()
                    )
                    == row
                )
                assert (
                    dict(
                        reopened.checkpoint.connection.execute(
                            "SELECT * FROM cursor WHERE artifact_id=?", (fact["artifact_id"],)
                        ).fetchone()
                    )
                    == cursor
                )
                # First reopen still sees malformed source bytes. Repair only
                # after proving the committed quarantine survives that reopen.
                for p, body in original.items():
                    p.write_bytes(body)
                reopened.run([bad, healthy])
                assert (
                    dict(
                        reopened.checkpoint.connection.execute(
                            "SELECT * FROM artifact WHERE path=?", (str(bad),)
                        ).fetchone()
                    )
                    == row
                )
            _assert_job_lock_available(ledger)
            if closed_baseline is not None:
                assert len(list(fd_root.iterdir())) == closed_baseline
        assert not [
            r
            for r in requests
            if r.method == "POST" and json.loads(r.content)["origin"]["session_id"] == "bad"
        ]


@pytest.mark.parametrize(
    "damage", ["status-list", "metadata-integer", "events-integer", "events-json"]
)
def test_permanent_source_changed_is_not_replaced_by_parser_refusal(
    tmp_path: Path,
    configured_roots: None,
    damage: str,
) -> None:
    requests: list[httpx.Request] = []
    with (
        _server(requests) as client,
        closing(
            RecoveryRunner(state_dir=tmp_path / "state", job_id="permanent", client=client)
        ) as runner,
    ):
        path = _seed_source_changed(runner, tmp_path, "events")
        before = _checkpoint_rows(runner)
        _damage_native_capture(path, damage)
        requests.clear()
        for _ in range(3):
            runner.run([path])
            assert _checkpoint_rows(runner) == before
        assert not requests


def test_producer_foreign_keys_prevent_orphan_but_controlled_damage_still_refuses_prune(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta
    from context_intelligence.prune import PruneRequest, prune_file
    from context_intelligence.prune.coordination import receipt_path

    path = _capture(
        tmp_path,
        body=(
            b'{"event":"llm:request","workspace":"project","data":{"session_id":"session-1",'
            b'"timestamp":"2026-01-01T00:00:00Z","raw":"synthetic"}}\n'
        ),
    )
    state = tmp_path / "state"
    with (
        _server([]) as client,
        closing(RecoveryRunner(state_dir=state, job_id="damage", client=client)) as runner,
    ):
        runner.run([path], dry_run=True)
        db = runner.checkpoint.connection
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM artifact")
        db.rollback()
        ledger = runner.checkpoint.path
    monkeypatch.setattr("context_intelligence.prune.recovery.default_recovery_root", lambda: state)
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    before = {p: _source_observation(p) for p in (path, path.parent / "metadata.json")}
    request = PruneRequest(tmp_path, apply=True, writers_stopped=True, min_idle=timedelta(0))
    assert prune_file(path, request, now=datetime.now(UTC)).status == "skipped"
    with closing(sqlite3.connect(ledger)) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DELETE FROM artifact")
        db.commit()
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
    rows = _checkpoint_rows_from_path(ledger)
    db_before = _source_observation(ledger)
    result = prune_file(path, request, now=datetime.now(UTC))
    assert result.status == "error"
    assert (result.reason or "").startswith("recovery-state-unknown: orphan recovery cursor")
    assert result.payloads == 0 and result.bytes_before == result.bytes_after
    assert {p: _source_observation(p) for p in before} == before
    assert _source_observation(ledger) == db_before
    assert _checkpoint_rows_from_path(ledger) == rows
    assert not receipt_path(path).exists()
    assert not list(tmp_path.rglob("*prune*"))


def test_unrelated_real_producer_early_refusal_defaults_do_not_block_prune(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta
    from context_intelligence.prune import PruneRequest, prune_file

    unrelated = _capture(tmp_path, "unrelated")
    (unrelated.parent / "metadata.json").unlink()
    state = tmp_path / "state"
    requests: list[httpx.Request] = []
    with (
        _server(requests) as client,
        closing(RecoveryRunner(state_dir=state, job_id="defaults", client=client)) as runner,
    ):
        runner.run([unrelated], dry_run=True)
        fact, cursors = _checkpoint_rows(runner)
        assert not cursors
        assert all(
            fact[key] == ""
            for key in (
                "session_id",
                "workspace",
                "source_sha256",
                "source_stream_sha256",
                "metadata_sha256",
            )
        )
        assert fact["source_inode"] == fact["source_size"] == 0
        ledger = runner.checkpoint.path
    source = _capture(
        tmp_path,
        "prunable",
        body=(
            b'{"event":"llm:request","timestamp":"2026-01-01T00:00:00Z",'
            b'"data":{"raw":"synthetic"}}\n'
        ),
    )
    monkeypatch.setattr("context_intelligence.prune.recovery.default_recovery_root", lambda: state)
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    before = _checkpoint_rows_from_path(ledger)
    result = prune_file(
        source,
        PruneRequest(tmp_path, apply=True, writers_stopped=True, min_idle=timedelta(0)),
        now=datetime.now(UTC),
    )
    assert result.status == "pruned" and result.payloads == 1
    assert _checkpoint_rows_from_path(ledger) == before
    assert not requests


def test_second_decode_refusal_quarantines_without_post_or_ack_advance(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _capture(tmp_path)
    raw = source.read_bytes().rstrip(b"\r\n").decode("utf-8")
    real_loads = json.loads
    decodes = 0

    def loads(value, **kwargs):
        nonlocal decodes
        if value == raw:
            decodes += 1
            if decodes == 2:
                # The decoder can refuse at either boundary. Trigger an actual
                # integer-limit ValueError only at the second boundary.
                assert sys.get_int_max_str_digits() == 4300
                return real_loads(b'{"number":' + b"1" * 4301 + b"}")
        return real_loads(value, **kwargs)

    requests: list[httpx.Request] = []
    with (
        _server(requests) as client,
        closing(
            RecoveryRunner(state_dir=tmp_path / "state", job_id="decode", client=client)
        ) as runner,
    ):
        runner.run([source], dry_run=True)
        before_fact, before_cursors = _checkpoint_rows(runner)
        monkeypatch.setattr(recovery.json, "loads", loads)
        summary = runner.run(refresh=False)
        assert decodes == 2 and summary.delivered == 0
        fact, cursors = _checkpoint_rows(runner)
        assert (fact["state"], fact["reason"]) == ("quarantined", "malformed-line")
        assert fact["source_sha256"] == before_fact["source_sha256"]
        assert (cursors[0]["ordinal"], cursors[0]["byte_offset"]) == (
            before_cursors[0]["ordinal"],
            before_cursors[0]["byte_offset"],
        )
        assert not [r for r in requests if r.method == "POST"]
