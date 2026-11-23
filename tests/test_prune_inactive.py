"""Synthetic execution ownership fixtures. Never touches real coordination."""

import hashlib
import json
import os
import socket
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from context_intelligence.prune import PruneRequest, prune_file
from context_intelligence.prune.coordination import receipt_path
from context_intelligence.prune import storage as core
from context_intelligence.prune.storage import Policy
from context_intelligence.prune.inactive import InactiveInventory


NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=30)


class InactiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        # Scheduled API tests reuse this fixture. HOME changes alone cannot
        # isolate recovery defaults that config.py resolved at import time.
        isolation = patch(
            "context_intelligence.prune.recovery.default_recovery_root",
            return_value=self.root / "synthetic-recovery",
        )
        isolation.start()
        self.addCleanup(isolation.stop)
        standard = patch(
            "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT",
            self.root / "synthetic-standard-recovery",
        )
        standard.start()
        self.addCleanup(standard.stop)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.sid = "synthetic-session"
        slug = str(self.workspace).replace("/", "-")
        self.session = self.root / "projects" / slug / "sessions" / self.sid
        self.ci = self.session / "context-intelligence"
        self.ci.mkdir(parents=True)
        self.path = self.ci / "events.jsonl"
        self.owner_dir = (
            self.root
            / "state/v1"
            / hashlib.sha256(str(self.workspace).encode()).hexdigest()
            / self.sid
        )
        self.owner_dir.mkdir(parents=True)
        self.lock = self.owner_dir / "session.lock"
        self.lock.touch(mode=0o600)
        self.owner_path = self.owner_dir / "owner.json"
        self.owner = dict(
            app="amplifier-cli",
            hostname=socket.gethostname(),
            session_id=self.sid,
            workspace=str(self.workspace),
            coordination_root=str(self.root / "state"),
            active=False,
            released_at=OLD.isoformat(),
        )
        self.metadata_path = self.ci / "metadata.json"
        self.metadata = dict(
            session_id=self.sid,
            parent_id="",
            status="completed",
            working_dir=str(self.workspace),
            ended_at=OLD.isoformat(),
            last_event_at=OLD.isoformat(),
        )
        self.write(self.owner_path, self.owner)
        self.write(self.metadata_path, self.metadata)
        self.write(self.session / "metadata.json", dict(session_id=self.sid))
        self.original = (
            json.dumps(
                dict(
                    event="llm:request",
                    timestamp=OLD.isoformat(),
                    data=dict(raw={"input": "SYNTHETIC ONLY"}, usage={"tokens": 12}),
                )
            ).encode()
            + b"\n"
        )
        self.path.write_bytes(self.original)
        self.age(self.path)
        self.inventory = InactiveInventory.load(self.root / "state")
        self.policy = Policy(NOW - timedelta(days=14))

    def age(self, path):
        os.utime(path, (OLD.timestamp(), OLD.timestamp()))

    def write(self, path, value):
        path.write_text(json.dumps(value))
        self.age(path)

    def prune(self, **kwargs):
        return self.inventory.prune(self.path, self.policy, now=NOW, **kwargs)

    def test_prunes_released_old_root_and_preserves_owner(self):
        owner_before = self.owner_path.read_bytes()
        inode = self.lock.stat().st_ino
        preview = self.prune()
        self.assertEqual(preview.status, "would-prune")
        self.assertEqual(self.path.read_bytes(), self.original)
        result = self.prune(apply=True)
        self.assertEqual(result.status, "pruned", result.reason)
        self.assertNotIn("raw", json.loads(self.path.read_bytes())["data"])
        self.assertEqual(self.owner_path.read_bytes(), owner_before)
        self.assertEqual(self.lock.stat().st_ino, inode)
        self.assertFalse(self.path.with_name("events.jsonl.bak").exists())

    def test_busy_execution_lock_is_skipped(self):
        import fcntl

        with self.lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.prune(apply=True)
            self.assertEqual(result.status, "skipped")
            self.assertIn("live", result.reason or "")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_lock_held_through_atomic_replace_then_released(self):
        import fcntl

        replace = core.os.replace
        observed = []

        def checked_replace(*args):
            with self.lock.open("rb") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed.append(True)
            replace(*args)

        with patch.object(core.os, "replace", checked_replace):
            self.assertEqual(self.prune(apply=True).status, "pruned")
        self.assertEqual(observed, [True])
        with self.lock.open("rb") as contender:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_no_execution_lock_is_created(self):
        self.lock.unlink()
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_no_owner_legacy_session_is_skipped(self):
        self.inventory.owners.clear()
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_duplicate_owner_is_skipped(self):
        self.inventory.owners[self.sid].append(self.owner_path)
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_owner_session_id_mismatch_is_skipped(self):
        self.write(self.owner_path, self.owner | {"session_id": "other-session"})
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_wrong_workspace_key_directory_is_skipped(self):
        wrong = self.owner_dir.parent.with_name("0" * 64) / self.sid
        wrong.parent.mkdir()
        self.owner_dir.rename(wrong)
        self.inventory.owners[self.sid] = [wrong / "owner.json"]
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_wrong_native_slug_is_skipped(self):
        wrong = self.session.parent.parent.with_name("-wrong-slug")
        self.session.parent.parent.rename(wrong)
        path = wrong / "sessions" / self.sid / "context-intelligence/events.jsonl"
        result = self.inventory.prune(path, self.policy, now=NOW, apply=True)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(path.read_bytes(), self.original)

    def test_child_running_unknown_and_identity_conflict_skipped(self):
        for changes in (
            {"parent_id": "parent"},
            {"status": "running"},
            {"status": "unknown"},
            {"session_id": "another"},
            {"working_dir": "/another"},
            {"ended_at": NOW.isoformat()},
            {"last_event_at": NOW.isoformat()},
        ):
            with self.subTest(changes=changes):
                self.write(self.metadata_path, self.metadata | changes)
                self.assertEqual(self.prune(apply=True).status, "skipped")
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_active_foreign_recent_or_wrong_owner_skipped(self):
        for changes in (
            {"active": True},
            {"hostname": "another-host"},
            {"app": "unknown"},
            {"released_at": NOW.isoformat()},
            {"workspace": "/wrong"},
            {"coordination_root": "/wrong"},
        ):
            with self.subTest(changes=changes):
                self.write(self.owner_path, self.owner | changes)
                self.assertEqual(self.prune(apply=True).status, "skipped")
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_recent_native_history_skips(self):
        transcript = self.session / "transcript.jsonl"
        transcript.write_text("synthetic")
        os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))
        self.assertEqual(self.prune(apply=True).status, "skipped")

    def test_metadata_change_during_scan_aborts_without_replacement(self):
        real_stream = core._stream

        def stream(*args):
            digest = real_stream(*args)
            self.write(self.metadata_path, self.metadata | {"status": "running"})
            return digest

        with patch.object(core, "_stream", stream):
            result = self.prune(apply=True)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.ci.glob(".events-prune-*")), [])

    def test_transfer_fence_skipped(self):
        self.write(self.session / "transfer-fence.json", {"synthetic": True})
        self.assertEqual(self.prune(apply=True).status, "skipped")

    def test_owner_and_lock_replacement_abort_and_release_lock(self):
        import fcntl

        real_stream = core._stream
        for target in (self.owner_path, self.lock):
            with self.subTest(target=target):
                # Replacing owner.json in the first case refreshes its mtime.
                # Keep both cases eligible so each reaches the stream callback.
                self.age(self.owner_path)

                def stream(*args, target=target):
                    digest = real_stream(*args)
                    replacement = target.with_name("replacement")
                    replacement.write_bytes(target.read_bytes())
                    os.replace(replacement, target)
                    return digest

                with patch.object(core, "_stream", wraps=stream) as streamed:
                    result = self.prune(apply=True)
                streamed.assert_called_once()
                self.assertEqual(result.status, "skipped")
                expected = (
                    "execution-lock identity changed"
                    if target == self.lock
                    else "session metadata/history changed"
                )
                self.assertIn(expected, result.reason or "")
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(list(self.ci.glob(".events-prune-*")), [])
                with self.lock.open("rb") as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_stricter_idle_is_honored(self):
        self.assertEqual(
            self.prune(apply=True, min_idle=timedelta(days=60)).status,
            "skipped",
        )
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_cannot_shorten_14_day_inactivity_with_age_filter(self):
        self.owner["released_at"] = (NOW - timedelta(days=7)).isoformat()
        self.write(self.owner_path, self.owner)
        result = self.inventory.prune(
            self.path,
            Policy(NOW - timedelta(hours=1)),
            now=NOW,
            apply=True,
        )
        self.assertEqual(result.status, "skipped")


def prepare_presence_capture(value):
    """Eligible for the real scheduled 60d/7d/1h policy, without stopped writers."""
    idle = NOW - timedelta(days=9)
    released = NOW - timedelta(days=8)
    value.owner["released_at"] = released.isoformat()
    value.metadata.update(ended_at=released.isoformat(), last_event_at=released.isoformat())
    value.write(value.owner_path, value.owner)
    value.write(value.metadata_path, value.metadata)
    row = json.loads(value.original)
    row["timestamp"] = (NOW - timedelta(days=90)).isoformat()
    value.original = (json.dumps(row) + "\n").encode()
    value.path.write_bytes(value.original)
    for name in ("transcript.jsonl", "events.jsonl"):
        (value.session / name).write_bytes(b'{"synthetic_native_history":"preserve"}\n')
    for path in (
        value.path,
        value.owner_path,
        value.metadata_path,
        value.session / "metadata.json",
        value.session / "transcript.jsonl",
        value.session / "events.jsonl",
    ):
        os.utime(path, (idle.timestamp(), idle.timestamp()))
    assert not os.path.lexists(receipt_path(value.path))


def presence_entry(path):
    """Read bytes or link text without treating a dangling link as absent."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    identity = (
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
    return identity, os.readlink(path) if path.is_symlink() else path.read_bytes()


def presence_snapshot(value):
    return {
        path: presence_entry(path)
        for path in (
            value.path,
            value.owner_path,
            value.lock,
            value.metadata_path,
            value.session / "metadata.json",
            value.session / "transcript.jsonl",
            value.session / "events.jsonl",
            value.session / "transfer-fence.json",
            value.root / "presence-target",
        )
    }


def prepare_presence_history(value, basename, kind):
    prepare_presence_capture(value)
    history = value.session / basename
    target = value.root / "presence-target"
    if kind != "regular":
        history.unlink()
    if kind == "existing":
        target.write_bytes(b"SYNTHETIC TARGET MUST SURVIVE\n")
        value.age(target)
    if kind in ("dangling", "existing"):
        history.symlink_to(target)
    return presence_snapshot(value)


def prune_presence_capture(value):
    request = PruneRequest.from_options(
        value.path,
        older_than="60d",
        min_idle="1h",
        now=NOW,
        apply=True,
        inactive_only=True,
        writers_stopped=False,
        min_inactive=timedelta(days=7),
        repeat_pruned=True,
        state_root=value.root / "state",
    )
    return prune_file(value.path, request, now=NOW)


def assert_presence_effects(value, before, *, pruned):
    after = presence_snapshot(value)
    if pruned:
        before = {path: entry for path, entry in before.items() if path != value.path}
        after.pop(value.path)
        expected = json.loads(value.original)
        del expected["data"]["raw"]
        assert json.loads(value.path.read_bytes()) == expected
        marker = json.loads(receipt_path(value.path).read_bytes())
        assert marker["source"]["sha256"] == hashlib.sha256(value.original).hexdigest()
        assert marker["expected"]["sha256"] == hashlib.sha256(value.path.read_bytes()).hexdigest()
        assert marker["effective_policy"]["inactive_only"] is True
        assert marker["effective_policy"]["writers_stopped"] is False
        assert marker["effective_policy"]["repeat_pruned"] is True
        assert marker["effective_policy"]["min_inactive_seconds"] == 7 * 86400
    else:
        assert value.path.read_bytes() == value.original
        assert not os.path.lexists(receipt_path(value.path))
    assert after == before
    for pattern in ("events.jsonl.prune-attempt-*", "*.bak", ".events-prune-*", ".prune-receipt-*"):
        assert list(value.ci.glob(pattern)) == []


@pytest.fixture
def presence_capture():
    value = InactiveTests()
    value.setUp()
    try:
        yield value
    finally:
        value.doCleanups()


@pytest.mark.parametrize("basename", ["transcript.jsonl", "events.jsonl"])
@pytest.mark.parametrize("kind", ["dangling", "existing", "regular", "absent"])
def test_presence_history_public_api(presence_capture, basename, kind):
    before = prepare_presence_history(presence_capture, basename, kind)
    result = prune_presence_capture(presence_capture)
    pruned = kind in ("regular", "absent")
    assert result.status == ("pruned" if pruned else "skipped"), result.reason
    assert result.payloads == (1 if pruned else 0)
    assert_presence_effects(presence_capture, before, pruned=pruned)


@contextmanager
def presence_fence_scan(value, kind, timing):
    """Inject only after the real CI stream, leaving native traversal real too."""
    prepare_presence_capture(value)
    marker = value.session / "transfer-fence.json"
    target = value.root / "presence-target"
    if kind == "existing":
        target.write_bytes(b"SYNTHETIC TARGET MUST SURVIVE\n")
        value.age(target)

    def create_fence():
        assert not os.path.lexists(marker)
        if kind == "regular":
            value.write(marker, {"synthetic_transfer": True})
        elif kind in ("dangling", "existing"):
            marker.symlink_to(target)

    if timing == "entry":
        create_fence()
    before = presence_snapshot(value)
    real_stream = core._stream
    scans = []

    def after_scan(*args):
        digest = real_stream(*args)
        if os.path.samestat(os.fstat(args[0].fileno()), value.path.stat()):
            scans.append(True)
            assert value.path.read_bytes() == value.original
            assert not os.path.lexists(receipt_path(value.path))
            if timing == "after-scan":
                assert before[marker] is None
                create_fence()
                # This entry is the fixture's intentional write, not a pruner
                # side effect. Its link text/identity/target must now survive.
                before[marker] = presence_entry(marker)
        return digest

    with patch.object(core, "_stream", after_scan):
        yield before
    if timing == "after-scan" or kind == "absent":
        assert len(scans) == 1


@pytest.mark.parametrize("timing", ["entry", "after-scan"])
@pytest.mark.parametrize("kind", ["dangling", "existing", "regular", "absent"])
def test_presence_fence_public_api(presence_capture, kind, timing):
    with presence_fence_scan(presence_capture, kind, timing) as before:
        result = prune_presence_capture(presence_capture)
    pruned = kind == "absent"
    assert result.status == ("pruned" if pruned else "skipped"), result.reason
    if not pruned:
        assert result.reason is not None
        assert "transfer" in result.reason
    assert_presence_effects(presence_capture, before, pruned=pruned)


if __name__ == "__main__":
    unittest.main()
