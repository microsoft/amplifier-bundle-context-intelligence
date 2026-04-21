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

    @pytest.fixture
    def detailed_help_output(self, capsys) -> str:
        """Capture and return the full --help output once per test."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        return capsys.readouterr().out

    def test_double_dash_help_exits_zero(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_double_dash_help_contains_examples(self, detailed_help_output):
        assert "Replay a single session directory" in detailed_help_output
        assert "Replay an entire project tree" in detailed_help_output
        assert "Target a recovery server" in detailed_help_output

    def test_double_dash_help_contains_progress_schema_fields(self, detailed_help_output):
        """Progress schema section must contain 'failed_at' and 'sessions_total' fields."""
        assert "failed_at" in detailed_help_output
        assert "sessions_total" in detailed_help_output

    def test_double_dash_help_contains_exit_codes(self, detailed_help_output):
        assert "EXIT CODES" in detailed_help_output

    def test_double_dash_help_contains_idempotency(self, detailed_help_output):
        assert "IDEMPOTENCY" in detailed_help_output

    def test_double_dash_help_contains_workspace_and_metadata_validation(
        self, detailed_help_output
    ):
        assert "WORKSPACE" in detailed_help_output
        assert "METADATA VALIDATION" in detailed_help_output

    def test_double_dash_help_contains_finding_server_url_section(self, detailed_help_output):
        """FINDING SERVER_URL AND API_KEY section must be present in detailed help."""
        assert "FINDING SERVER_URL AND API_KEY" in detailed_help_output

    def test_finding_server_url_section_appears_before_examples(self, detailed_help_output):
        """FINDING SERVER_URL AND API_KEY section must appear before the EXAMPLES section."""
        finding_pos = detailed_help_output.find("FINDING SERVER_URL AND API_KEY")
        assert finding_pos != -1, "FINDING SERVER_URL AND API_KEY not found in help output"
        examples_pos = detailed_help_output.find("EXAMPLES")
        assert examples_pos != -1, "EXAMPLES not found in help output"
        assert finding_pos < examples_pos, (
            "FINDING SERVER_URL AND API_KEY must appear before EXAMPLES"
        )

    def test_no_abbreviated_ci_example_com(self, detailed_help_output):
        """ci.example.com must not appear; use context-intelligence.example.com."""
        assert "ci.example.com" not in detailed_help_output

    def test_no_abbreviated_ci_api_key(self, detailed_help_output):
        """$CI_API_KEY must not appear; use $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY."""
        assert "$CI_API_KEY" not in detailed_help_output

    def test_context_intelligence_example_com_present(self, detailed_help_output):
        """context-intelligence.example.com must appear in detailed help."""
        assert "context-intelligence.example.com" in detailed_help_output

    def test_amplifier_context_intelligence_api_key_present(self, detailed_help_output):
        """$AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY must appear in detailed help."""
        assert "$AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY" in detailed_help_output


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

    def test_missing_server_url_defaults_to_none(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        # --server-url is optional; parser succeeds with server_url=None
        args = _build_parser().parse_args(["--path", "/some/path", "--api-key", "key"])
        assert args.server_url is None

    def test_missing_api_key_defaults_to_none(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        # --api-key is optional; parser succeeds with api_key=None
        args = _build_parser().parse_args(
            ["--path", "/some/path", "--server-url", "http://localhost"]
        )
        assert args.api_key is None


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


# ---------------------------------------------------------------------------
# main() — env var config resolution (resolve_config integration)
# ---------------------------------------------------------------------------


class TestEnvVarConfigResolution:
    """When --server-url and --api-key are omitted, resolve_config() should
    fall back to AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL and
    AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY environment variables."""

    def test_env_vars_used_when_flags_omitted(self, tmp_path, capsys):
        """main() succeeds with env vars and no --server-url/--api-key flags."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        env_overrides = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env-server:8100",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-secret-key",
        }
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 5,
        }
        fake_sessions = [(tmp_path, {"session_id": "s1"})]

        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    # NOTE: no --server-url, no --api-key
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        # Verify the env var values were passed to run_upload
        call_kwargs = mock_upload.call_args
        assert call_kwargs.kwargs["server_url"] == "http://env-server:8100"
        assert call_kwargs.kwargs["api_key"] == "env-secret-key"

    def test_flags_override_env_vars(self, tmp_path, capsys):
        """Explicit --server-url/--api-key flags take priority over env vars."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        env_overrides = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env-server:8100",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-secret-key",
        }
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 5,
        }
        fake_sessions = [(tmp_path, {"session_id": "s1"})]

        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://flag-server:9999",
                    "--api-key",
                    "flag-key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        call_kwargs = mock_upload.call_args
        assert call_kwargs.kwargs["server_url"] == "http://flag-server:9999"
        assert call_kwargs.kwargs["api_key"] == "flag-key"

    def test_exits_when_server_url_missing_everywhere(self, tmp_path, monkeypatch):
        """SystemExit when --server-url omitted AND no env var AND no settings.yaml."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--api-key",
                    "some-key",
                ],
            ),
            patch("context_intelligence.config.SETTINGS_PATH", tmp_path / "nosettings.yaml"),
            pytest.raises(SystemExit),
        ):
            main()

    def test_exits_when_api_key_missing_everywhere(self, tmp_path, monkeypatch):
        """SystemExit when --api-key omitted AND no env var AND no settings.yaml."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                ],
            ),
            patch("context_intelligence.config.SETTINGS_PATH", tmp_path / "nosettings.yaml"),
            pytest.raises(SystemExit),
        ):
            main()
