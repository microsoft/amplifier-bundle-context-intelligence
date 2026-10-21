"""Synthetic fixtures only. Never opens the user's Amplifier event logs."""

import copy
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from context_intelligence.prune import storage as core
from context_intelligence.prune.storage import PruneError, discover_events, prune_file
from context_intelligence.prune.policy import (
    Policy,
    event_timestamp,
    parse_duration,
    parse_timestamp,
    prune_record,
)


NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=14)
OLD = "2026-07-01T12:00:00.123456789Z"


def event(name="llm:request", stamp: object = OLD, *, field="timestamp", raw=True):
    result = {
        "event": name,
        field: stamp,
        "session_id": "synthetic",
        "request_id": "request-1",
        "workspace": "test",
        "data": {
            "provider": "synthetic",
            "model": "test",
            "message_count": 1,
            "usage": {"input_tokens": 234, "output_tokens": 7, "cost_usd": "0.001"},
            "extension": {"raw": "MUST STAY"},
            "messages": ["MUST STAY"],
        },
    }
    if raw:
        result["data"]["raw"] = {"messages": [{"role": "user", "content": "SYNTHETIC ONLY"}]}
    return result


def encoded(record):
    return json.dumps(record, ensure_ascii=False).encode() + b"\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "events.jsonl"

    def put(self, content, path=None):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        stamp = (NOW - timedelta(days=1)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def prune(self, **kwargs):
        return prune_file(self.path, Policy(CUTOFF), now=NOW, **kwargs)


class RecordTests(unittest.TestCase):
    def test_only_direct_raw_is_removed(self):
        record = event()
        original = copy.deepcopy(record)
        changed, missing = prune_record(record, Policy(CUTOFF))
        expected = copy.deepcopy(record)
        del expected["data"]["raw"]
        self.assertEqual(changed, expected)
        self.assertEqual(record, original)
        self.assertFalse(missing)

    def test_both_logger_envelopes_and_nested_fallback(self):
        for field in ("timestamp", "ts"):
            with self.subTest(field=field):
                changed, _ = prune_record(event(field=field), Policy(CUTOFF))
                self.assertNotIn("raw", changed["data"])
        nested = event()
        nested["data"]["timestamp"] = nested.pop("timestamp")
        self.assertIsNotNone(event_timestamp(nested))

    def test_timestamp_offsets_and_fractional_seconds(self):
        a = parse_timestamp("2026-07-01T05:00:00.123456789-07:00")
        b = parse_timestamp(OLD)
        self.assertEqual(a, b)

    def test_submicrosecond_timestamp_conflicts_are_not_rounded_away(self):
        for field in ("timestamp", "ts"):
            record = event(field=field)
            record["data"]["timestamp"] = "2026-07-01T12:00:00.123456790Z"
            changed, undated = prune_record(record, Policy(CUTOFF))
            self.assertIs(changed, record)
            self.assertTrue(undated)

    def test_submicrosecond_equivalence_accepts_offsets_and_trailing_zeroes(self):
        for nested in (
            "2026-07-01T05:00:00.123456789-07:00",
            "2026-07-01T12:00:00.123456789000+00:00",
            "2026-07-01T12:00:00,123456789+0000",
        ):
            record = event()
            record["data"]["timestamp"] = nested
            changed, undated = prune_record(record, Policy(CUTOFF))
            self.assertNotIn("raw", changed["data"])
            self.assertFalse(undated)

    def test_unrecognized_precision_spelling_is_retained(self):
        record = event(stamp="2026-07-01T12:00:00+00:00:00.000000001")
        self.assertEqual(prune_record(record, Policy(CUTOFF)), (record, True))

    def test_invalid_offset_fields_are_not_normalized_into_valid_times(self):
        for offset in ("+00:60", "-00:60", "+01:99", "+0060", "+24:00"):
            stamp = f"2026-07-01T12:00:00{offset}"
            record = event(stamp=stamp)
            self.assertIsNone(parse_timestamp(stamp))
            self.assertEqual(prune_record(record, Policy(CUTOFF)), (record, True))

    def test_nanosecond_cutoff_ties_conservatively_retain(self):
        cutoff = datetime(2026, 7, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
        for fraction in ("123456000", "123456001", "123456999"):
            record = event(stamp=f"2026-07-01T12:00:00.{fraction}Z")
            self.assertIs(prune_record(record, Policy(cutoff))[0], record)

    def test_before_is_strict(self):
        for stamp in (CUTOFF.isoformat(), NOW.isoformat(), (NOW + timedelta(days=1)).isoformat()):
            record = event(stamp=stamp)
            self.assertIs(prune_record(record, Policy(CUTOFF))[0], record)

    def test_missing_invalid_naive_conflicting_times_kept(self):
        for stamp in (None, "", "broken", "2026-01-01", "2026-01-01T00:00:00", 1):
            record = event(stamp=stamp)
            self.assertEqual(prune_record(record, Policy(CUTOFF)), (record, True))
        record = event()
        record["ts"] = NOW.isoformat()
        self.assertEqual(prune_record(record, Policy(CUTOFF)), (record, True))
        record = event()
        record["ts"] = OLD
        self.assertNotIn("raw", prune_record(record, Policy(CUTOFF))[0]["data"])

    def test_no_age_filter_includes_undated(self):
        changed, missing = prune_record(event(stamp=None), Policy())
        self.assertNotIn("raw", changed["data"])
        self.assertFalse(missing)

    def test_nested_timestamp_must_agree_with_each_envelope_field(self):
        for field in ("timestamp", "ts"):
            for nested in (NOW.isoformat(), "invalid", None):
                record = event(field=field)
                record["data"]["timestamp"] = nested
                replacement, undated = prune_record(record, Policy(CUTOFF))
                self.assertIs(replacement, record)
                self.assertTrue(undated)
            agreed = event(field=field)
            agreed["data"]["timestamp"] = OLD
            self.assertNotIn("raw", prune_record(agreed, Policy(CUTOFF))[0]["data"])

    def test_response_opt_in(self):
        record = event("llm:response")
        self.assertIs(prune_record(record, Policy(CUTOFF))[0], record)
        changed, _ = prune_record(record, Policy(CUTOFF, include_responses=True))
        self.assertNotIn("raw", changed["data"])
        self.assertEqual(changed["data"]["usage"], record["data"]["usage"])

    def test_unrelated_event_names_and_unknown_structures_retained(self):
        for name in (
            "provider:request",
            "provider:response",
            "tool:post",
            "llm:request:raw",
            "unknown",
        ):
            record = event(name)
            self.assertIs(prune_record(record, Policy())[0], record)
        for data in (None, [], "opaque", {"nested": {"raw": "keep"}}):
            record = event()
            record["data"] = data
            self.assertIs(prune_record(record, Policy())[0], record)

    def test_raw_can_be_null_string_or_blob_reference(self):
        for raw in (None, "text", [], {}):
            record = event()
            record["data"]["raw"] = raw
            self.assertNotIn("raw", prune_record(record, Policy())[0]["data"])

    def test_blob_reference_is_preserved_even_if_unrecognized(self):
        for raw in ({"$blob_ref": "ci-blob://test/key"}, {"$blob_ref": None}):
            record = event()
            record["data"]["raw"] = raw
            self.assertIs(prune_record(record, Policy())[0], record)

    def test_duration_validation(self):
        self.assertEqual(parse_duration("2w"), timedelta(days=14))
        self.assertEqual(parse_duration("1.5h"), timedelta(minutes=90))
        for invalid in ("-1d", "2 weeks", "14", "NaNd", "infd", "1e9s"):
            with self.assertRaises(ValueError):
                parse_duration(invalid)
        with self.assertRaises(ValueError):
            Policy(datetime(2026, 1, 1))


class FileTests(Fixture):
    def test_preview_is_read_only_and_reports_exact_savings(self):
        original = encoded(event()) + encoded(event("tool:post"))
        self.put(original)
        before = self.path.stat()
        result = self.prune()
        self.assertEqual(result.status, "would-prune")
        self.assertEqual(result.payloads, 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.stat().st_ino, before.st_ino)
        applied = self.prune(apply=True)
        self.assertEqual(applied.bytes_saved, result.bytes_saved)
        self.assertEqual(len(self.path.read_bytes()), result.bytes_after)
        self.assertEqual(self.path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_apply_retains_records_usage_unicode_and_other_files(self):
        old = event()
        old["data"]["messages"] = ["\u03bb\u96ea"]
        recent = event(stamp=NOW.isoformat())
        response = event("llm:response")
        tool = b' { "event":"tool:post", "data":{"raw":"keep"} }\r\n'
        original = encoded(old) + encoded(recent) + encoded(response) + tool
        self.put(original)
        transcript = self.root / "transcript.jsonl"
        transcript.write_bytes(b"KEEP TRANSCRIPT EXACTLY")
        self.path.chmod(0o640)
        result = self.prune(apply=True)
        lines = self.path.read_bytes().splitlines(keepends=True)
        self.assertEqual(result.status, "pruned")
        self.assertEqual(len(lines), 4)
        self.assertNotIn("raw", json.loads(lines[0])["data"])
        self.assertEqual(lines[1:], [encoded(recent), encoded(response), tool])
        self.assertEqual(json.loads(lines[0])["data"]["messages"], ["\u03bb\u96ea"])
        self.assertEqual(transcript.read_bytes(), b"KEEP TRANSCRIPT EXACTLY")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_idempotent_second_apply_does_not_replace_file(self):
        self.put(encoded(event()))
        self.prune(apply=True)
        before = self.path.stat()
        with self.assertRaises(PruneError):
            self.prune(apply=True)
        self.assertEqual(self.path.stat().st_ino, before.st_ino)

    def test_large_event(self):
        record = event()
        record["data"]["raw"] = {"input": "x" * 5_000_000}
        self.put(encoded(record))
        result = self.prune(apply=True)
        self.assertGreater(result.bytes_saved, 4_999_000)
        self.assertLess(self.path.stat().st_size, 1000)

    def test_retained_numeric_tokens_are_not_rounded_or_overflowed(self):
        original = (
            b'{"event":"llm:request","timestamp":"2026-01-01T00:00:00Z",'
            b'"data":{"raw":{},"precise":0.123456789012345678901234567890,'
            b'"huge":1e999,"small":1e-999,"signed":-0,"integer":1234567890123456789012345}}\n'
        )
        self.put(original)
        self.prune(apply=True)
        result = self.path.read_bytes()
        for token in (
            b"0.123456789012345678901234567890",
            b"1e999",
            b"1e-999",
            b'"signed":-0',
            b"1234567890123456789012345",
        ):
            self.assertIn(token, result)
        self.assertNotIn(b'"raw"', result)

    def test_invalid_json_aborts_entire_file_and_cleans_temp(self):
        for tail in (
            b'{"secret":"DO NOT PRINT"',
            b"\xff\n",
            b"[]\n",
            b'{"event":"x","event":"y"}\n',
            b'{"x":NaN}\n',
        ):
            original = encoded(event()) + tail
            self.put(original)
            with self.assertRaises(PruneError) as caught:
                self.prune(apply=True)
            self.assertNotIn("DO NOT PRINT", str(caught.exception))
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(list(self.root.glob(".events-prune-*")), [])

    def test_blank_lines_crlf_and_missing_final_newline(self):
        original = (
            b"\n \t\r\n" + encoded(event()).rstrip(b"\n") + b"\r\n" + encoded(event()).rstrip(b"\n")
        )
        self.put(original)
        self.prune(apply=True)
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(b"\n \t\r\n"))
        self.assertIn(b"\r\n", data)
        self.assertFalse(data.endswith(b"\n"))

    def test_backup_exact_private_and_never_overwritten(self):
        original = encoded(event())
        self.put(original)
        result = self.prune(apply=True, backup=True)
        backup = self.path.with_name("events.jsonl.bak")
        self.assertEqual(result.backup, backup)
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.put(original)
        core.receipt_path(self.path).unlink()  # explicit fixture reset, not product recovery
        with self.assertRaises(FileExistsError):
            self.prune(apply=True, backup=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(backup.read_bytes(), original)

    def test_recent_file_safety_guard(self):
        self.put(encoded(event()))
        os.utime(self.path, (NOW.timestamp(), NOW.timestamp()))
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertEqual(self.prune(apply=True, min_idle=timedelta(0)).status, "pruned")

    def test_zero_idle_disables_guard_even_for_future_mtime(self):
        self.put(encoded(event()))
        future = (NOW + timedelta(days=2)).timestamp()
        os.utime(self.path, (future, future))
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertEqual(self.prune(apply=True, min_idle=timedelta(0)).status, "pruned")

    def test_symlink_and_hardlink_skipped(self):
        target = self.root / "target"
        self.put(encoded(event()), target)
        self.path.symlink_to(target)
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.path.unlink()
        os.link(target, self.path)
        self.assertEqual(self.prune(apply=True).status, "skipped")
        self.assertIn("raw", json.loads(target.read_bytes())["data"])

    def test_append_or_same_size_edit_detected(self):
        real_stream = core._stream
        for append in (True, False):
            original = encoded(event())
            self.put(original)

            def racing_stream(*args):
                digest = real_stream(*args)
                if append:
                    with self.path.open("ab") as writer:
                        writer.write(b'{"event":"new"}\n')
                else:
                    self.path.write_bytes(original.replace(b"SYNTHETIC ONLY", b"SYNTHETIC EDIT"))
                return digest

            with patch.object(core, "_stream", racing_stream):
                with self.assertRaises(PruneError):
                    self.prune(apply=True)
            self.assertIn(b'"raw"', self.path.read_bytes())
            if append:
                self.assertTrue(self.path.read_bytes().endswith(b'{"event":"new"}\n'))

    def test_atomic_replace_failure_keeps_original(self):
        original = encoded(event())
        self.put(original)
        with patch.object(core.os, "replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaises(OSError):
                self.prune(apply=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".events-prune-*")), [])

    def test_lock_refuses_competing_pruner(self):
        import fcntl

        self.put(encoded(event()))
        with self.path.open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(PruneError):
                self.prune(apply=True)

    def test_discovery_recursive_exact_name_and_no_link_traversal(self):
        self.put(encoded(event()), self.root / "a" / "context-intelligence" / "events.jsonl")
        self.put(encoded(event()), self.root / "b" / "events.jsonl")
        self.put(encoded(event()), self.root / "b" / "events.jsonl.bak")
        (self.root / "alias").symlink_to(self.root / "b", target_is_directory=True)
        self.assertEqual(len(list(discover_events(self.root))), 2)
        with self.assertRaises(PruneError):
            list(discover_events(self.root / "absent"))


if __name__ == "__main__":
    unittest.main()
