"""Tests for cli.py — argparse CLI with two custom help levels."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# -h compact help
# ---------------------------------------------------------------------------


class TestCompactHelp:
    """The -h flag must print compact help to stdout and exit 0."""

    def test_minus_h_exits_zero(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["-h"])
        assert exc_info.value.code == 0

    def test_minus_h_stdout_contains_required_strings(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["-h"])
        captured = capsys.readouterr()
        assert "context-intelligence-upload" in captured.out
        assert "--path" in captured.out
        assert "--server-url" in captured.out
        assert "--api-key" in captured.out


# ---------------------------------------------------------------------------
# --help detailed help
# ---------------------------------------------------------------------------


class TestDetailedHelp:
    """The --help flag must print detailed help to stdout and exit 0."""

    def test_double_dash_help_exits_zero(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_double_dash_help_contains_examples(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "Replay a single session directory" in captured.out
        assert "Replay an entire project tree" in captured.out
        assert "Target a recovery server" in captured.out

    def test_double_dash_help_contains_progress_schema_fields(self, capsys):
        """Progress schema section must contain 'failed_at' and 'sessions_total' fields."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "failed_at" in captured.out
        assert "sessions_total" in captured.out

    def test_double_dash_help_contains_exit_codes(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "EXIT CODES" in captured.out

    def test_double_dash_help_contains_idempotency(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "IDEMPOTENCY" in captured.out

    def test_double_dash_help_contains_workspace_and_metadata_validation(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "WORKSPACE" in captured.out
        assert "METADATA VALIDATION" in captured.out


# ---------------------------------------------------------------------------
# Missing required arguments → exit 2
# ---------------------------------------------------------------------------


class TestMissingRequiredArgs:
    """Missing required args should exit with code 2."""

    def test_missing_path_exits_2(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--server-url", "http://localhost", "--api-key", "key"])
        assert exc_info.value.code == 2

    def test_missing_server_url_exits_2(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--path", "/some/path", "--api-key", "key"])
        assert exc_info.value.code == 2

    def test_missing_api_key_exits_2(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--path", "/some/path", "--server-url", "http://localhost"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# main() — auto-generated job_id
# ---------------------------------------------------------------------------


class TestJobIdAutoGeneration:
    """When --job-id is not provided, a UUID4 is auto-generated and printed to stderr."""

    def test_auto_generates_job_id_when_not_provided(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=[],
            ),
            pytest.raises(SystemExit),
        ):
            main()
        captured = capsys.readouterr()
        # A UUID4 should have appeared on stderr
        uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        assert re.search(uuid_pattern, captured.err, re.IGNORECASE), (
            f"No UUID4 found in stderr: {captured.err!r}"
        )
        assert "progress=" in captured.err, f"'progress=' not found in stderr: {captured.err!r}"

    def test_provided_job_id_not_auto_generated(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                    "--job-id",
                    "my-custom-job",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=[],
            ),
            pytest.raises(SystemExit),
        ):
            main()
        captured = capsys.readouterr()
        # When job_id is explicitly provided, it should NOT be printed to stderr
        assert "my-custom-job" not in captured.err


# ---------------------------------------------------------------------------
# main() — end-to-end CLI flows
# ---------------------------------------------------------------------------


class TestCliEndToEnd:
    """End-to-end CLI flows: nonexistent path, no sessions, successful upload."""

    def test_nonexistent_path_exits_2(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        nonexistent = tmp_path / "does_not_exist"
        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(nonexistent),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_no_sessions_found(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=[],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "no sessions found" in captured.err.lower()
        result = json.loads(captured.out)
        assert result["status"] == "completed"
        assert result["sessions_uploaded"] == 0

    def test_successful_upload(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 5,
        }

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["status"] == "completed"
        assert result["sessions_uploaded"] == 1
