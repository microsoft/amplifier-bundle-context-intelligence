"""Synthetic execution ownership fixtures. Never touches real coordination."""

import hashlib
import json
import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
