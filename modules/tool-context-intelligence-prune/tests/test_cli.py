"""Synthetic fixtures only. Never opens the user's Amplifier event logs."""

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from amplifier_module_tool_context_intelligence_prune.cli import main

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)
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
        isolation = patch(
            "context_intelligence.prune.recovery.default_recovery_root",
            return_value=self.root / "synthetic-recovery",
        )
        isolation.start()
        self.addCleanup(isolation.stop)

    def put(self, content, path=None):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        stamp = (NOW - timedelta(days=1)).timestamp()
        os.utime(path, (stamp, stamp))
        return path


class CliTests(Fixture):
    def run_cli(self, *args):
        with patch("context_intelligence.prune.operations.datetime") as clock:
            clock.now.return_value = NOW
            return CliRunner().invoke(main, [str(self.root), "--min-idle", "0s", *args])

    def test_preview_apply_and_json_summary(self):
        original = encoded(event())
        self.put(original)
        dry = self.run_cli("--older-than", "14d", "--json")
        self.assertEqual(dry.exit_code, 0, dry.output)
        rows = [json.loads(line) for line in dry.output.splitlines()]
        self.assertEqual(rows[-1]["payloads"], 1)
        self.assertEqual(rows[-1]["mode"], "dry-run")
        self.assertEqual(self.path.read_bytes(), original)
        result = self.run_cli("--older-than", "2w", "--apply", "--writers-stopped", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output.splitlines()[-1])["mode"], "apply")
        self.assertNotIn("raw", json.loads(self.path.read_bytes())["data"])

    def test_invalid_options_cannot_write(self):
        original = encoded(event())
        self.put(original)
        for args in (
            ("--apply",),
            ("--backup",),
            ("--before", "2026-01-01"),
            ("--older-than", "-2d"),
            ("--older-than", "999999999999999d"),
            ("--apply", "--writers-stopped", "--dry-run"),
            ("--before", OLD, "--older-than", "1w"),
        ):
            result = self.run_cli(*args)
            self.assertEqual(result.exit_code, 2, result.output)
            self.assertEqual(self.path.read_bytes(), original)

    def test_missing_timestamp_report_and_exit(self):
        self.put(encoded(event(stamp=None)))
        result = self.run_cli("--older-than", "14d", "--json")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(json.loads(result.output.splitlines()[-1])["kept_undated"], 1)

    def test_text_reports_unchanged_file_with_only_undated_payloads(self):
        original = encoded(event(stamp=None)) + encoded(event(stamp="invalid"))
        self.put(original)
        for options in ((), ("--apply", "--writers-stopped")):
            with self.subTest(options=options):
                result = self.run_cli("--older-than", "14d", *options)
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertIn(f"unchanged: {self.path}", result.output)
                self.assertIn("2 kept undated", result.output)
                self.assertIn("missing or ambiguous timestamps", result.output)
                self.assertIn("0 changed; 0 skipped; 0 errors; 2 kept undated", result.output)
                self.assertEqual(self.path.read_bytes(), original)

    def test_text_reports_mixed_dated_and_undated_payloads(self):
        undated = encoded(event(stamp=None))
        self.put(encoded(event()) + undated)
        result = self.run_cli("--older-than", "14d", "--apply", "--writers-stopped")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"pruned: {self.path}", result.output)
        self.assertIn("1 payloads", result.output)
        self.assertIn("1 kept undated", result.output)
        self.assertIn("missing or ambiguous timestamps", result.output)
        self.assertIn("1 changed; 0 skipped; 0 errors; 1 kept undated", result.output)
        lines = self.path.read_bytes().splitlines(keepends=True)
        self.assertNotIn("raw", json.loads(lines[0])["data"])
        self.assertEqual(lines[1], undated)

    def test_bad_file_does_not_hide_other_successes(self):
        self.put(b'{"truncated"', self.root / "a" / "events.jsonl")
        good = self.put(encoded(event()), self.root / "b" / "events.jsonl")
        result = self.run_cli("--apply", "--writers-stopped", "--json")
        self.assertEqual(result.exit_code, 1, result.output)
        summary = json.loads(result.output.splitlines()[-1])
        self.assertEqual(summary["changed_files"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertNotIn("raw", json.loads(good.read_bytes())["data"])

    def test_empty_tree_clear_summary(self):
        result = self.run_cli("--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["files"], 0)

    def test_help_explains_limits(self):
        result = CliRunner().invoke(main, ["--help"])
        self.assertEqual(result.exit_code, 0)
        for text in ("data.raw", "--older-than", "--include-responses", "--writers-stopped"):
            self.assertIn(text, result.output)


if __name__ == "__main__":
    unittest.main()
