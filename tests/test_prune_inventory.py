"""Traversal inventory is advisory; all captures and ownership here are synthetic."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from context_intelligence.prune import Policy, PruneRequest, iter_prune_tree, operations, prune_file
from context_intelligence.prune.coordination import capture_lock, receipt_path

NOW = datetime(2026, 10, 20, tzinfo=UTC)
OLD = NOW - timedelta(days=30)
ORIGINAL = (
    json.dumps(
        {
            "event": "llm:request",
            "timestamp": OLD.isoformat(),
            "data": {"raw": "SYNTHETIC ONLY", "usage": {"tokens": 12}},
        }
    ).encode()
    + b"\n"
)


def write_old(path: Path, body: bytes) -> None:
    path.write_bytes(body)
    os.utime(path, (OLD.timestamp(), OLD.timestamp()))


def write_json(path: Path, value: dict[str, object]) -> None:
    write_old(path, json.dumps(value).encode())


@dataclass
class Session:
    path: Path
    owner: Path
    lock: Path
    metadata: Path


def session(root: Path, sid: str) -> Session:
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    state = root / "state"
    slug = str(workspace).replace("/", "-")
    native = root / "projects" / slug / "sessions" / sid
    ci = native / "context-intelligence"
    ci.mkdir(parents=True)
    path = ci / "events.jsonl"
    write_old(path, ORIGINAL)
    write_json(native / "metadata.json", {"session_id": sid})
    metadata = ci / "metadata.json"
    write_json(
        metadata,
        {
            "session_id": sid,
            "parent_id": "",
            "status": "completed",
            "working_dir": str(workspace),
            "ended_at": OLD.isoformat(),
            "last_event_at": OLD.isoformat(),
        },
    )
    owner_dir = state / "v1" / hashlib.sha256(str(workspace).encode()).hexdigest() / sid
    owner_dir.mkdir(parents=True)
    lock = owner_dir / "session.lock"
    lock.touch(mode=0o600)
    owner = owner_dir / "owner.json"
    write_json(
        owner,
        {
            "app": "amplifier-cli",
            "hostname": socket.gethostname(),
            "session_id": sid,
            "workspace": str(workspace),
            "coordination_root": str(state),
            "active": False,
            "released_at": OLD.isoformat(),
        },
    )
    return Session(path, owner, lock, metadata)


def request(root: Path, *, recovery_roots: tuple[Path, ...] = ()) -> PruneRequest:
    return PruneRequest(
        root / "projects",
        policy=Policy(NOW - timedelta(days=14)),
        apply=True,
        inactive_only=True,
        state_root=root / "state",
        recovery_roots=recovery_roots,
    )


@pytest.fixture(autouse=True)
def isolated_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    monkeypatch.setattr(
        "context_intelligence.prune.recovery.default_recovery_root",
        lambda: tmp_path / "synthetic-recovery",
    )


@pytest.mark.parametrize("owners_present", [False, True])
def test_tree_loads_one_inventory_even_when_owner_index_is_empty(
    tmp_path: Path, owners_present: bool
) -> None:
    sessions = [session(tmp_path, sid) for sid in ("a", "b", "c")]
    if not owners_present:
        for candidate in sessions:
            candidate.owner.unlink()
    with patch.object(
        operations.InactiveInventory, "load", wraps=operations.InactiveInventory.load
    ) as loaded:
        results = list(iter_prune_tree(request(tmp_path), now=NOW))
    loaded.assert_called_once_with(tmp_path / "state")
    assert [result.path for result in results] == [candidate.path for candidate in sessions]
    assert [result.status for result in results] == [
        "pruned" if owners_present else "skipped"
    ] * len(sessions)
    for candidate in sessions:
        if owners_present:
            assert json.loads(candidate.path.read_bytes())["data"] == {"usage": {"tokens": 12}}
            assert receipt_path(candidate.path).exists()
        else:
            assert candidate.path.read_bytes() == ORIGINAL
            assert not receipt_path(candidate.path).exists()


def test_empty_tree_does_not_require_an_ownership_inventory(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()
    with patch.object(operations.InactiveInventory, "load") as loaded:
        assert list(iter_prune_tree(request(tmp_path), now=NOW)) == []
    loaded.assert_not_called()


def test_non_inactive_tree_does_not_load_inventory(tmp_path: Path) -> None:
    candidate = session(tmp_path, "a")
    with patch.object(operations.InactiveInventory, "load") as loaded:
        results = list(
            iter_prune_tree(PruneRequest(tmp_path / "projects", min_idle=timedelta(0)), now=NOW)
        )
    loaded.assert_not_called()
    assert [result.status for result in results] == ["would-prune"]
    assert candidate.path.read_bytes() == ORIGINAL


def test_public_file_calls_load_fresh_inventory_after_owner_appears(tmp_path: Path) -> None:
    candidate = session(tmp_path, "a")
    owner = candidate.owner.read_bytes()
    candidate.owner.unlink()
    with patch.object(
        operations.InactiveInventory, "load", wraps=operations.InactiveInventory.load
    ) as loaded:
        assert prune_file(candidate.path, request(tmp_path), now=NOW).status == "skipped"
        write_old(candidate.owner, owner)
        assert prune_file(candidate.path, request(tmp_path), now=NOW).status == "pruned"
    assert loaded.call_count == 2


@pytest.mark.parametrize("change", ["active", "missing"])
def test_public_file_calls_recheck_ownership_after_preview(tmp_path: Path, change: str) -> None:
    candidate = session(tmp_path, "a")
    with patch.object(
        operations.InactiveInventory, "load", wraps=operations.InactiveInventory.load
    ) as loaded:
        preview = prune_file(candidate.path, replace(request(tmp_path), apply=False), now=NOW)
        assert preview.status == "would-prune"
        if change == "missing":
            candidate.owner.unlink()
        else:
            owner = json.loads(candidate.owner.read_bytes())
            write_json(candidate.owner, owner | {"active": True})
        result = prune_file(candidate.path, request(tmp_path), now=NOW)
    assert loaded.call_count == 2
    assert result.status == "skipped"
    assert candidate.path.read_bytes() == ORIGINAL
    assert not receipt_path(candidate.path).exists()


def test_each_tree_call_gets_a_fresh_inventory(tmp_path: Path) -> None:
    candidate = session(tmp_path, "a")
    owner = candidate.owner.read_bytes()
    candidate.owner.unlink()
    with patch.object(
        operations.InactiveInventory, "load", wraps=operations.InactiveInventory.load
    ) as loaded:
        assert [r.status for r in iter_prune_tree(request(tmp_path), now=NOW)] == ["skipped"]
        write_old(candidate.owner, owner)
        assert [r.status for r in iter_prune_tree(request(tmp_path), now=NOW)] == ["pruned"]
    assert loaded.call_count == 2


@pytest.mark.parametrize("change", ["owner-active", "owner-missing", "metadata", "lock"])
def test_shared_index_never_caches_per_file_eligibility(tmp_path: Path, change: str) -> None:
    first = session(tmp_path, "a")
    second = session(tmp_path, "b")
    with patch.object(
        operations.InactiveInventory, "load", wraps=operations.InactiveInventory.load
    ) as loaded:
        results = iter_prune_tree(request(tmp_path), now=NOW)
        result = next(results)
        assert result.path == first.path and result.status == "pruned"
        if change == "owner-active":
            owner = json.loads(second.owner.read_bytes())
            write_json(second.owner, owner | {"active": True})
        elif change == "owner-missing":
            second.owner.unlink()
        elif change == "metadata":
            metadata = json.loads(second.metadata.read_bytes())
            write_json(second.metadata, metadata | {"status": "running"})
        with second.lock.open("rb") as held:
            if change == "lock":
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            remaining = list(results)
    loaded.assert_called_once()
    assert len(remaining) == 1
    assert remaining[0].path == second.path and remaining[0].status == "skipped"
    reason = remaining[0].reason or ""
    if change == "owner-active":
        assert "clean release" in reason
    elif change == "metadata":
        assert "completed root" in reason
    elif change == "lock":
        assert "session is live" in reason
    else:
        assert reason  # The indexed path vanished; it must not authorize mutation.
    assert second.path.read_bytes() == ORIGINAL
    assert not receipt_path(second.path).exists()
    with second.lock.open("rb") as released:
        fcntl.flock(released, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("fence", ["receipt", "capture-lock", "recovery-root"])
def test_tree_keeps_per_file_capture_and_recovery_guards(tmp_path: Path, fence: str) -> None:
    first = session(tmp_path, "a")
    second = session(tmp_path, "b")
    recovery = tmp_path / "custom-recovery"
    recovery.mkdir()
    prune_request = request(tmp_path, recovery_roots=(recovery,))
    results = iter_prune_tree(prune_request, now=NOW)
    result = next(results)
    assert result.path == first.path and result.status == "pruned"
    if fence == "receipt":
        receipt_path(second.path).write_bytes(b"{}")
    elif fence == "recovery-root":
        recovery.rmdir()
    if fence == "capture-lock":
        with capture_lock(second.path, exclusive=True):
            remaining = list(results)
    else:
        remaining = list(results)
    assert len(remaining) == 1
    result = remaining[0]
    assert result.path == second.path
    assert result.status == ("error" if fence == "recovery-root" else "skipped")
    assert {
        "receipt": "pruned-capture:",
        "capture-lock": "capture-maintenance-busy:",
        "recovery-root": "recovery-state-missing:",
    }[fence] in (result.reason or "")
    assert second.path.read_bytes() == ORIGINAL
    if fence != "receipt":
        assert not receipt_path(second.path).exists()
