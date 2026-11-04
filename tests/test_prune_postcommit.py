"""Public accounting regressions using real capture replacement and SQLite."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_prune_inactive as inactive_tests
from test_prune_ledger_resources import _SCHEMA

from context_intelligence import config
from context_intelligence.prune import PruneRequest, PruneSummary, prune_file, result_dict
from context_intelligence.prune import operations, storage
from context_intelligence.prune.coordination import (
    CaptureBusy,
    PruneError,
    PrunedCapture,
    receipt_path,
    replay_guard,
)
from context_intelligence.prune.inactive import InactiveInventory

WARNING = "replacement completed, but postcommit guard cleanup failed"
DURABILITY_WARNING = "replacement completed, but directory fsync failed"
REQUEST = (
    b'{"event":"llm:request","timestamp":"2026-01-01T00:00:00Z",'
    b'"data":{"raw":"SYNTHETIC PAYLOAD","usage":{"tokens":7}}}\n'
)
PRUNED_REQUEST = (
    b'{"event":"llm:request","timestamp":"2026-01-01T00:00:00Z","data":{"usage":{"tokens":7}}}\n'
)
RESPONSE = b'{"event":"llm:response","data":{"raw":"RESPONSE STAYS","usage":{"tokens":2}}}\n'


@pytest.fixture
def capture(monkeypatch):
    # Reuse the existing real, released execution-owner fixture without editing it.
    fixture = inactive_tests.InactiveTests()
    fixture.setUp()
    try:
        monkeypatch.setattr(config, "SETTINGS_PATH", fixture.root / "absent-settings.yaml")
        monkeypatch.setattr(
            config, "DEFAULT_RECOVERY_STATE_ROOT", fixture.root / "absent-standard-recovery"
        )
        fixture.metadata["workspace"] = str(fixture.workspace)
        fixture.metadata_path.write_text(json.dumps(fixture.metadata), encoding="utf-8")
        fixture.age(fixture.metadata_path)
        fixture.original = REQUEST + RESPONSE
        fixture.path.write_bytes(fixture.original)
        fixture.age(fixture.path)
        ledger_root = fixture.root / "recovery"
        ledger_root.mkdir()
        db_path = ledger_root / "empty.v2.sqlite3"
        with closing(sqlite3.connect(db_path)) as db:
            db.executescript(_SCHEMA)
            db.commit()
            assert db.execute("SELECT COUNT(*) FROM artifact").fetchone() == (0,)
        ledger_lock = Path(str(db_path) + ".lock")
        ledger_lock.touch(mode=0o600)
        yield SimpleNamespace(
            **vars(fixture), db_path=db_path, ledger_lock=ledger_lock, age=fixture.age
        )
    finally:
        fixture.doCleanups()


def _protected(capture):
    return {
        path: (path.read_bytes(), storage._signature(path.stat()))
        for path in (capture.db_path, capture.ledger_lock, capture.owner_path, capture.lock)
    }


def _run(
    capture,
    monkeypatch,
    *,
    inactive=False,
    fault=None,
    directory_fault=False,
    apply=True,
    backup=False,
):
    """Delegate every operation; faults are armed only at the named boundary."""
    trace = []
    inner = None
    injected = 0
    replaced = False
    protected = _protected(capture)
    real_lstat, real_replace = Path.lstat, os.replace
    real_sync = storage._sync_directory
    real_inner = InactiveInventory.prune if inactive else operations._transaction

    def lstat(path, *args, **kwargs):
        nonlocal injected
        if path == capture.ledger_lock:
            if not injected and (
                (fault == "early" and inner is None)
                or (fault in ("final", "final-refusal") and inner is not None)
            ):
                injected += 1
                trace.append("guard-fault")
                if fault == "final-refusal":
                    raise PruneError("skipped-ledger: SYNTHETIC PRIVATE DETAIL")
                raise OSError(errno.EIO, "SYNTHETIC PRIVATE DETAIL")
            result = real_lstat(path, *args, **kwargs)
            trace.append("ledger-lstat-pass")
            return result
        return real_lstat(path, *args, **kwargs)

    def replace(source, destination, *args, **kwargs):
        nonlocal replaced
        assert Path(destination) == capture.path
        assert receipt_path(capture.path).is_file()
        # The public capture fence must still exclude readers at replacement.
        with pytest.raises(CaptureBusy):
            with replay_guard(capture.path):
                pytest.fail("capture fence was released before replacement")
        result = real_replace(source, destination, *args, **kwargs)
        replaced = True
        trace.append("source-replaced")
        return result

    def sync(directory):
        if directory_fault and replaced:
            trace.append("directory-fsync-fault")
            raise OSError(errno.EIO, "SYNTHETIC PRIVATE DETAIL")
        return real_sync(directory)

    def observed_inner(*args, **kwargs):
        nonlocal inner
        trace.append("inner-enter")
        result = real_inner(*args, **kwargs)
        inner = asdict(result)
        trace.append("inner-return")
        return result

    def before_commit():
        assert not replaced
        trace.append("before-commit")

    request = PruneRequest(
        capture.root,
        capture.policy,
        apply=apply,
        writers_stopped=not inactive,
        inactive_only=inactive,
        state_root=capture.root / "state",
        recovery_roots=(capture.db_path.parent,),
        min_idle=timedelta(0),
        before_commit=before_commit,
        backup=backup,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "lstat", lstat)
        scoped.setattr(storage.os, "replace", replace)
        scoped.setattr(storage, "_sync_directory", sync)
        if inactive:
            scoped.setattr(InactiveInventory, "prune", observed_inner)
        else:
            scoped.setattr(operations, "_transaction", observed_inner)
        result = prune_file(capture.path, request, now=inactive_tests.NOW)
    assert _protected(capture) == protected
    assert trace.count("source-replaced") == int(replaced)
    assert trace.count("inner-enter") <= 1
    assert injected == int(fault is not None)
    summary = PruneSummary()
    summary.add(result)
    print(
        json.dumps(
            {
                "inactive": inactive,
                "fault": fault,
                "directory_fault": directory_fault,
                "trace": trace,
                "inner": inner,
                "result": result_dict(result),
                "summary": summary.to_dict(),
                "exit_code": summary.exit_code,
            },
            default=str,
        )
    )
    return result, inner, trace, summary


def _assert_committed(capture, result, inner, trace, summary, warning):
    assert trace.count("source-replaced") == trace.count("inner-enter") == 1
    assert trace.index("source-replaced") < trace.index("inner-return")
    assert trace.count("before-commit") >= 4
    assert all(
        index < trace.index("source-replaced")
        for index, event in enumerate(trace)
        if event == "before-commit"
    )
    assert capture.path.read_bytes() == PRUNED_REQUEST + RESPONSE
    receipt = json.loads(receipt_path(capture.path).read_text(encoding="utf-8"))
    assert receipt["source"]["sha256"] == hashlib.sha256(capture.original).hexdigest()
    assert receipt["expected"]["sha256"] == hashlib.sha256(PRUNED_REQUEST + RESPONSE).hexdigest()
    with pytest.raises(PrunedCapture):
        with replay_guard(capture.path):
            pytest.fail("a committed capture must remain replay-denied")
    assert result.status == "pruned"
    assert asdict(result) == {**inner, "reason": warning}
    assert result.reason == warning
    assert "SYNTHETIC PRIVATE DETAIL" not in (result.reason or "")
    assert result.lines == 2
    assert result.payloads == 1
    assert result.bytes_before == len(capture.original)
    assert result.bytes_after == len(PRUNED_REQUEST + RESPONSE)
    assert result.bytes_saved == 26
    assert result_dict(result)["bytes_saved"] == 26
    assert summary.changed_files == summary.payloads == 1
    assert summary.bytes_saved == 26
    assert summary.errors == summary.exit_code == int(bool(warning))
    if result.backup is not None:
        assert result.backup.read_bytes() == capture.original


@pytest.mark.parametrize("inactive", [False, True], ids=["stopped-writers", "inactive-only"])
@pytest.mark.parametrize("fault", ["final", "final-refusal"])
def test_final_recovery_guard_eio_preserves_committed_accounting(
    capture, monkeypatch, inactive, fault
):
    result, inner, trace, summary = _run(
        capture, monkeypatch, inactive=inactive, fault=fault, backup=True
    )
    assert trace.index("inner-return") < trace.index("guard-fault")
    assert trace.count("guard-fault") == 1
    _assert_committed(capture, result, inner, trace, summary, WARNING)


@pytest.mark.parametrize("inactive", [False, True], ids=["stopped-writers", "inactive-only"])
@pytest.mark.parametrize(
    ("directory_fault", "fault"),
    [(False, None), (True, None), (True, "final")],
    ids=["success", "directory-warning", "combined-warnings"],
)
def test_committed_success_and_durability_warning_controls(
    capture, monkeypatch, inactive, directory_fault, fault
):
    result, inner, trace, summary = _run(
        capture, monkeypatch, inactive=inactive, fault=fault, directory_fault=directory_fault
    )
    warning = DURABILITY_WARNING if directory_fault else None
    if fault:
        warning = f"{warning}; {WARNING}"
    _assert_committed(capture, result, inner, trace, summary, warning)


def test_early_guard_error_has_no_transaction_or_receipt(capture, monkeypatch):
    result, inner, trace, summary = _run(capture, monkeypatch, fault="early")
    assert inner is None
    assert "inner-enter" not in trace
    assert capture.path.read_bytes() == capture.original
    assert not receipt_path(capture.path).exists()
    assert result.status == "error"
    assert result.lines == result.payloads == result.bytes_before == result.bytes_after == 0
    assert summary.changed_files == summary.payloads == summary.bytes_saved == 0
    assert summary.errors == summary.exit_code == 1
    assert WARNING not in (result.reason or "")


@pytest.mark.parametrize("preview", [True, False], ids=["preview", "no-op"])
def test_noncommitting_result_is_not_claimed_as_committed(capture, monkeypatch, preview):
    if not preview:
        capture.original = PRUNED_REQUEST + RESPONSE
        capture.path.write_bytes(capture.original)
        capture.age(capture.path)
    result, inner, trace, summary = _run(capture, monkeypatch, fault="final", apply=not preview)
    assert inner is not None
    assert inner["status"] == ("would-prune" if preview else "unchanged")
    assert "source-replaced" not in trace
    assert capture.path.read_bytes() == capture.original
    assert not receipt_path(capture.path).exists()
    assert result.status == "error"
    assert summary.changed_files == summary.payloads == summary.bytes_saved == 0
    assert summary.errors == summary.exit_code == 1
    assert WARNING not in (result.reason or "")
