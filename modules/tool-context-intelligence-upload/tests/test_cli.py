"""Tests for cli.py — argparse CLI with two custom help levels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_cli(*args: str) -> tuple[int, str, str]:
    """Run the CLI with *args and capture stdout, stderr, and exit code.

    Returns (exit_code, stdout, stderr).
    """
    from amplifier_module_tool_context_intelligence_upload import cli

    with (
        patch("sys.argv", ["context-intelligence-upload", *args]),
        patch("sys.stdout") as mock_stdout,
        patch("sys.stderr") as mock_stderr,
    ):
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        mock_stdout.write = lambda s: stdout_buf.append(s)
        mock_stderr.write = lambda s: stderr_buf.append(s)

        try:
            cli.main()
            exit_code = 0
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

        return exit_code, "".join(stdout_buf), "".join(stderr_buf)


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------


class TestModuleStructure:
    """Verify the module exports the required symbols."""

    def test_main_exists(self):
        from amplifier_module_tool_context_intelligence_upload import cli

        assert callable(cli.main)

    def test_build_parser_exists(self):
        from amplifier_module_tool_context_intelligence_upload import cli

        assert callable(cli._build_parser)

    def test_compact_help_action_exists(self):
        from amplifier_module_tool_context_intelligence_upload import cli

        assert cli._CompactHelpAction is not None

    def test_detailed_help_action_exists(self):
        from amplifier_module_tool_context_intelligence_upload import cli

        assert cli._DetailedHelpAction is not None


# ---------------------------------------------------------------------------
# _build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    """Verify the parser has all required arguments."""

    def get_parser(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        return _build_parser()

    def test_parser_has_path_argument(self):
        parser = self.get_parser()
        # Should be able to parse --path
        args = parser.parse_args(
            ["--path", "/some/path", "--server-url", "http://localhost", "--api-key", "key"]
        )
        assert args.path == "/some/path"

    def test_parser_has_server_url_argument(self):
        parser = self.get_parser()
        args = parser.parse_args(
            ["--path", "/some/path", "--server-url", "http://localhost", "--api-key", "key"]
        )
        assert args.server_url == "http://localhost"

    def test_parser_has_api_key_argument(self):
        parser = self.get_parser()
        args = parser.parse_args(
            ["--path", "/some/path", "--server-url", "http://localhost", "--api-key", "mykey"]
        )
        assert args.api_key == "mykey"

    def test_parser_has_optional_job_id(self):
        parser = self.get_parser()
        args = parser.parse_args(
            [
                "--path",
                "/some/path",
                "--server-url",
                "http://localhost",
                "--api-key",
                "key",
                "--job-id",
                "custom-id",
            ]
        )
        assert args.job_id == "custom-id"

    def test_parser_job_id_default_is_none(self):
        parser = self.get_parser()
        args = parser.parse_args(
            ["--path", "/some/path", "--server-url", "http://localhost", "--api-key", "key"]
        )
        # When --job-id is not provided, it should default to None so main() can auto-generate
        assert args.job_id is None

    def test_parser_has_optional_progress_argument(self):
        parser = self.get_parser()
        args = parser.parse_args(
            [
                "--path",
                "/some/path",
                "--server-url",
                "http://localhost",
                "--api-key",
                "key",
                "--progress",
                "/tmp/custom.json",
            ]
        )
        assert args.progress == "/tmp/custom.json"

    def test_parser_progress_default_is_none(self):
        parser = self.get_parser()
        args = parser.parse_args(
            ["--path", "/some/path", "--server-url", "http://localhost", "--api-key", "key"]
        )
        # When --progress not provided, default is None (main() computes the path based on job_id)
        assert args.progress is None

    def test_parser_add_help_false(self):
        """Parser must use add_help=False (no automatic -h/--help)."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        parser = _build_parser()
        # add_help=False means we've managed help ourselves
        assert parser.add_help is False


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

    def test_minus_h_writes_to_stdout(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["-h"])
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_minus_h_contains_usage(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["-h"])
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "--path" in captured.out

    def test_minus_h_contains_flag_list(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["-h"])
        captured = capsys.readouterr()
        assert "--path" in captured.out
        assert "--server-url" in captured.out
        assert "--api-key" in captured.out

    def test_minus_h_nothing_on_stderr(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["-h"])
        captured = capsys.readouterr()
        assert captured.err == ""


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

    def test_double_dash_help_writes_to_stdout(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_double_dash_help_contains_what_it_does(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "WHAT IT DOES" in captured.out

    def test_double_dash_help_contains_parameters(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "PARAMETERS" in captured.out

    def test_double_dash_help_contains_metadata_validation(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "METADATA VALIDATION" in captured.out

    def test_double_dash_help_contains_topological_ordering(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "TOPOLOGICAL ORDERING" in captured.out

    def test_double_dash_help_contains_idempotency_guarantee(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "IDEMPOTENCY GUARANTEE" in captured.out

    def test_double_dash_help_contains_workspace_behaviour(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "WORKSPACE BEHAVIOUR" in captured.out

    def test_double_dash_help_contains_progress_file(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "PROGRESS FILE" in captured.out

    def test_double_dash_help_contains_exit_codes(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "EXIT CODES" in captured.out

    def test_double_dash_help_contains_examples(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert "EXAMPLES" in captured.out

    def test_double_dash_help_exit_codes_include_0_1_2(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        # All three exit codes should appear in the output
        assert "0" in captured.out
        assert "1" in captured.out
        assert "2" in captured.out

    def test_double_dash_help_nothing_on_stderr(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        captured = capsys.readouterr()
        assert captured.err == ""


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

    def test_no_args_exits_2(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args([])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# main() — path validation
# ---------------------------------------------------------------------------


class TestMainPathValidation:
    """Nonexistent path exits with code 2."""

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

    def test_existing_path_does_not_exit_2_on_path_check(self, tmp_path, capsys):
        """An existing path should not fail the path check (may still fail for other reasons)."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        # We mock discover_and_sort to return empty list so we don't need a server
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
        # Should exit 0 (no sessions found)
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# main() — auto-generated job_id
# ---------------------------------------------------------------------------


class TestMainAutoJobId:
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
        assert len(captured.err) > 0
        # Check that a UUID-like string (with dashes) is in stderr
        import re

        uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        assert re.search(uuid_pattern, captured.err, re.IGNORECASE), (
            f"No UUID4 found in stderr: {captured.err!r}"
        )

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
        # (The auto-generation message should only appear when auto-generating)
        assert "my-custom-job" not in captured.err


# ---------------------------------------------------------------------------
# main() — no sessions found
# ---------------------------------------------------------------------------


class TestMainNoSessions:
    """When discover_and_sort returns empty list, print to stderr and exit 0 with JSON stdout."""

    def test_no_sessions_exits_0(self, tmp_path, capsys):
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

    def test_no_sessions_outputs_json_on_stdout(self, tmp_path, capsys):
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
        result = json.loads(captured.out)
        assert result["status"] == "completed"
        assert result["sessions_uploaded"] == 0
        assert result["events_uploaded"] == 0

    def test_no_sessions_prints_to_stderr(self, tmp_path, capsys):
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
        assert len(captured.err) > 0


# ---------------------------------------------------------------------------
# main() — successful upload exits 0 with JSON
# ---------------------------------------------------------------------------


class TestMainSuccessfulUpload:
    """Successful upload exits 0 with result JSON on stdout."""

    def test_successful_upload_exits_0(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        fake_sessions: list[Any] = [(tmp_path, {"session_id": "s1"})]
        fake_result = UploadResult(success=True, sessions_uploaded=1, events_uploaded=5)

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
                return_value=fake_result,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0

    def test_successful_upload_outputs_json_on_stdout(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        fake_sessions: list[Any] = [(tmp_path, {"session_id": "s1"})]
        fake_result = UploadResult(success=True, sessions_uploaded=1, events_uploaded=5)

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
                return_value=fake_result,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            main()
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["status"] == "completed"
        assert result["sessions_uploaded"] == 1
        assert result["events_uploaded"] == 5


# ---------------------------------------------------------------------------
# main() — failed upload exits 1
# ---------------------------------------------------------------------------


class TestMainFailedUpload:
    """Failed upload exits 1."""

    def test_failed_upload_exits_1(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        fake_sessions: list[Any] = [(tmp_path, {"session_id": "s1"})]
        fake_result = UploadResult(
            success=False,
            sessions_uploaded=0,
            events_uploaded=0,
            error="HTTP 500",
        )

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
                return_value=fake_result,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1

    def test_failed_upload_outputs_json_on_stdout(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        fake_sessions: list[Any] = [(tmp_path, {"session_id": "s1"})]
        fake_result = UploadResult(
            success=False,
            sessions_uploaded=0,
            events_uploaded=0,
            error="HTTP 500",
        )

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
                return_value=fake_result,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            main()
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# main() — progress tracker receives correct arguments
# ---------------------------------------------------------------------------


class TestMainProgressTracker:
    """Verify ProgressTracker is created with correct arguments."""

    def test_progress_tracker_created_with_job_id(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        fake_sessions: list[Any] = [(tmp_path, {"session_id": "s1"})]
        fake_result = UploadResult(success=True, sessions_uploaded=1, events_uploaded=0)

        tracker_kwargs: dict[str, Any] = {}

        def capture_tracker(*args: Any, **kwargs: Any) -> MagicMock:
            tracker_kwargs.update(kwargs)
            tracker_kwargs["args"] = args
            return MagicMock()

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
                    "explicit-job-id",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=fake_result,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker",
                side_effect=capture_tracker,
            ),
            pytest.raises(SystemExit),
        ):
            main()

        # job_id should have been passed to ProgressTracker
        all_args = list(tracker_kwargs.get("args", [])) + list(tracker_kwargs.values())
        assert any("explicit-job-id" in str(a) for a in all_args), (
            f"job_id not found in ProgressTracker args: {tracker_kwargs}"
        )

    def test_default_progress_path_uses_job_id(self, tmp_path, capsys):
        """Default progress path = /tmp/context-intelligence-upload-{job_id}.json."""
        from amplifier_module_tool_context_intelligence_upload.cli import main
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        fake_sessions: list[Any] = [(tmp_path, {"session_id": "s1"})]
        fake_result = UploadResult(success=True, sessions_uploaded=1, events_uploaded=0)

        tracker_file_path: list[Path] = []

        def capture_tracker(job_id: str, file_path: Path, sessions_total: int) -> MagicMock:
            tracker_file_path.append(file_path)
            return MagicMock()

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
                    "my-job",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=fake_result,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker",
                side_effect=capture_tracker,
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert len(tracker_file_path) == 1
        assert "my-job" in str(tracker_file_path[0])
        assert str(tracker_file_path[0]).startswith("/tmp/")
